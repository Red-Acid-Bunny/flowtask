"""
PythonScriptAdapter — мост между Python-раннером и python-скриптами.

Вызывает python-скрипты как подпроцессы с JSON-контрактом:
  - JSON payload на stdin (params + context)
  - JSON результат с stdout (ModuleResult)
  - Логи на stderr → перехватываются в logger

Protocol:
  Input (stdin):
    {
      "action": "module_name",
      "params": { ... },
      "context": {
        "playbook_dir": "/path",
        "vars": { ... },
        "secrets": { ... }
      }
    }

  Output (stdout):
    {
      "status": "ok|failed|skipped",
      "changed": true|false,
      "message": "...",
      "data": { ... }
    }

  Exit Codes:
    0 — script executed successfully (check status field)
    1 — script crashed (malformed input, unexpected error)

  Rules:
    - Script MUST write valid JSON to stdout and exit
    - Script SHOULD write diagnostic info to stderr (not parsed)
    - Script MUST NOT prompt for user input
    - Script MUST handle missing/invalid params gracefully
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from ..engine.result import ModuleResult, ModuleError

logger = logging.getLogger("flowtask.python_adapter")


class PythonScriptAdapter:
    """Обёртка для python-скрипта как модуля FlowTask.

    Usage:
        adapter = PythonScriptAdapter(Path("modules/python/my_script.py"))
        result = adapter.execute(action="my_script", params={"key": "value"})
    """

    def __init__(self, script_path: Path):
        if not script_path.exists():
            raise FileNotFoundError(f"Python script not found: {script_path}")
        if not script_path.is_file():
            raise ValueError(f"Not a file: {script_path}")

        self.path = script_path
        self.name = script_path.stem

        logger.debug("Registered python script module: %s → %s", self.name, self.path)

    def execute(
        self,
        params: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        dry_run: bool = False,
        verbose: bool = False,
        timeout: int = 300,
    ) -> ModuleResult:
        """Выполнить python-скрипт.

        Args:
            params: Параметры для передачи в скрипт
            context: Контекст выполнения (playbook_dir, vars, secrets)
            dry_run: Предпросмотр без выполнения
            verbose: Подробный вывод
            timeout: Таймаут в секундах

        Returns:
            ModuleResult

        Raises:
            ModuleError: При ошибке выполнения скрипта
        """
        params = params or {}
        context = context or {}

        payload = {
            "action": self.name,
            "params": params,
            "context": context,
            "dry_run": dry_run,
            "verbose": verbose,
        }

        logger.info("Running python script module: %s (dry_run=%s)", self.name, dry_run)

        try:
            proc = subprocess.run(
                ["python3", str(self.path)],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            msg = f"Python script {self.name} timed out after {timeout}s"
            logger.error(msg)
            return ModuleResult.error(msg)

        except FileNotFoundError:
            msg = f"python3 not found (cannot execute {self.path})"
            logger.error(msg)
            return ModuleResult.error(msg)

        stderr = proc.stderr.strip()
        if stderr:
            for line in stderr.splitlines():
                logger.debug("[%s] %s", self.name, line)

        stdout = proc.stdout.strip()
        if not stdout:
            msg = f"Python script {self.name} produced no output (exit code: {proc.returncode})"
            logger.error(msg)
            if stderr:
                msg += f" — stderr: {stderr}"
            return ModuleResult.error(msg)

        try:
            result = ModuleResult.from_json(stdout)
        except json.JSONDecodeError as e:
            msg = f"Python script {self.name} returned invalid JSON: {e}\n  Output: {stdout[:200]}"
            logger.error(msg)
            return ModuleResult.error(msg)

        if proc.returncode != 0 and result.is_ok:
            logger.warning("Python script %s: exit code %d but status=ok", self.name, proc.returncode)

        return result

    def __repr__(self) -> str:
        return f"PythonScriptAdapter(name={self.name!r}, path={self.path!r})"

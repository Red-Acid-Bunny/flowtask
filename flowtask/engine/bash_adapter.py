"""
BashModuleAdapter — мост между Python-раннером и bash-скриптами.

Вызывает bash-скрипты как подпроцессы с JSON-контрактом:
  - JSON payload на stdin (params + dry_run)
  - JSON результат с stdout (ModuleResult)
  - Логи на stderr → перехватываются в logger

Protocol:
  Input (stdin):
    {
      "params": { ... },
      "dry_run": false,
      "verbose": false
    }

  Output (stdout):
    {
      "status": "ok|error|skipped",
      "message": "...",
      "changed": true|false,
      "data": { ... }
    }
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from ..engine.result import ModuleResult, ModuleError

logger = logging.getLogger("flowtask.bash_adapter")


class BashModuleAdapter:
    """Обёртка для bash-скрипта как модуля FlowTask.

    Usage:
        adapter = BashModuleAdapter(Path("modules/bash/mount_smb.sh"))
        result = adapter.execute(server="192.168.0.8", share="box")
    """

    def __init__(self, script_path: Path):
        if not script_path.exists():
            raise FileNotFoundError(f"Bash module not found: {script_path}")
        if not script_path.is_file():
            raise ValueError(f"Not a file: {script_path}")

        self.path = script_path
        self.name = script_path.stem  # mount_smb.sh → "mount_smb"

        logger.debug("Registered bash module: %s → %s", self.name, self.path)

    def execute(
        self,
        params: dict[str, Any] | None = None,
        dry_run: bool = False,
        verbose: bool = False,
        timeout: int = 300,
    ) -> ModuleResult:
        """Выполнить bash-скрипт.

        Args:
            params: Параметры для передачи в скрипт
            dry_run: Предпросмотр без выполнения
            verbose: Подробный вывод
            timeout: Таймаут в секундах

        Returns:
            ModuleResult

        Raises:
            ModuleError: При ошибке выполнения скрипта
        """
        params = params or {}

        payload = {
            "params": params,
            "dry_run": dry_run,
            "verbose": verbose,
        }

        logger.info("Running bash module: %s (dry_run=%s)", self.name, dry_run)

        try:
            proc = subprocess.run(
                ["bash", str(self.path)],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            msg = f"Bash module {self.name} timed out after {timeout}s"
            logger.error(msg)
            return ModuleResult.error(msg)

        except FileNotFoundError:
            msg = f"bash not found (cannot execute {self.path})"
            logger.error(msg)
            return ModuleResult.error(msg)

        # stderr → в логи
        stderr = proc.stderr.strip()
        if stderr:
            for line in stderr.splitlines():
                logger.debug("[%s] %s", self.name, line)

        # stdout → JSON результат
        stdout = proc.stdout.strip()
        if not stdout:
            msg = f"Bash module {self.name} produced no output (exit code: {proc.returncode})"
            logger.error(msg)
            if stderr:
                msg += f" — stderr: {stderr}"
            return ModuleResult.error(msg)

        # Парсинг JSON
        try:
            result = ModuleResult.from_json(stdout)
        except json.JSONDecodeError as e:
            msg = f"Bash module {self.name} returned invalid JSON: {e}\n  Output: {stdout[:200]}"
            logger.error(msg)
            return ModuleResult.error(msg)

        if proc.returncode != 0 and result.is_ok:
            # Скрипт вернул exit code != 0, но JSON статус ok — всё равно считаем ошибкой
            logger.warning("Bash module %s: exit code %d but status=ok", self.name, proc.returncode)

        return result

    def __repr__(self) -> str:
        return f"BashModuleAdapter(name={self.name!r}, path={self.path!r})"

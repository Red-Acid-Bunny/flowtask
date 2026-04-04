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

Security:
  - Пароль sudo передаётся ТОЛЬКО через pipe (stdin)
  - Пароль НИКОГДА не логируется и не попадает в аргументы
  - После выполнения пароль очищается (None)
"""

from __future__ import annotations

import base64
import json
import logging
import os
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
        self.name = script_path.stem

        logger.debug("Registered bash module: %s → %s", self.name, self.path)

    def execute(
        self,
        params: dict[str, Any] | None = None,
        dry_run: bool = False,
        verbose: bool = False,
        timeout: int = 300,
        become: bool = False,
        become_pass: str | None = None,
    ) -> ModuleResult:
        """Выполнить bash-скрипт.

        Args:
            params: Параметры для передачи в скрипт
            dry_run: Предпросмотр без выполнения
            verbose: Подробный вывод
            timeout: Таймаут в секундах
            become: Выполнить через sudo
            become_pass: Пароль sudo (передаётся через pipe, НИКОГДА не логируется)

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

        become_tag = " [become]" if become else ""
        logger.info("Running bash module: %s (dry_run=%s)%s", self.name, dry_run, become_tag)

        if become:
            # При become stdin занят паролем sudo (-S).
            # JSON кодируем в base64 и передаём первым аргументом.
            # Bash-модули читают: input=$(echo "${1:-}" | base64 -d) || input=$(cat)
            json_b64 = base64.b64encode(json.dumps(payload).encode()).decode()
            cmd = ["sudo", "-S", "bash", str(self.path), json_b64]
            input_data = f"{become_pass}\n"
        else:
            cmd = ["bash", str(self.path)]
            input_data = json.dumps(payload)

        try:
            proc = subprocess.run(
                cmd,
                input=input_data,
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

        finally:
            become_pass = None

        stderr = proc.stderr.strip()
        if stderr:
            for line in stderr.splitlines():
                logger.debug("[%s] %s", self.name, line)

        stdout = proc.stdout.strip()
        if not stdout:
            msg = f"Bash module {self.name} produced no output (exit code: {proc.returncode})"
            logger.error(msg)
            if stderr:
                msg += f" — stderr: {stderr}"
            return ModuleResult.error(msg)

        try:
            result = ModuleResult.from_json(stdout)
        except json.JSONDecodeError as e:
            msg = f"Bash module {self.name} returned invalid JSON: {e}\n  Output: {stdout[:200]}"
            logger.error(msg)
            return ModuleResult.error(msg)

        if proc.returncode != 0 and result.is_ok:
            logger.warning("Bash module %s: exit code %d but status=ok", self.name, proc.returncode)

        return result

    def __repr__(self) -> str:
        return f"BashModuleAdapter(name={self.name!r}, path={self.path!r})"

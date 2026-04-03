"""
Context — загрузка и объединение переменных.

Загружает из inventory/:
  - vars.yml         — базовые переменные (коммитятся в репо)
  - vars.local.yml   — локальные переопределения (.gitignore)
  - secrets.yml      — секреты (.gitignore)

Порядок объединения (последний побеждает):
  vars.yml → vars.local.yml

Секреты загружаются отдельно и доступны через context.secrets.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("flowtask.context")


def _deep_merge(base: dict, override: dict) -> dict:
    """Рекурсивное слияние словарей. override побеждает."""
    merged = base.copy()
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_yaml(path: Path) -> dict:
    """Загрузка YAML-файла. Пустой словарь если файл не существует."""
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


class Context:
    """Контекст выполнения — переменные и секреты.

    Usage:
        ctx = Context.from_inventory("inventory/")
        server = ctx.get("smb_server")
        password = ctx.get_secret("smb_password")
    """

    def __init__(
        self,
        vars: dict[str, Any] | None = None,
        secrets: dict[str, Any] | None = None,
        builtins: dict[str, Any] | None = None,
    ):
        self._vars: dict[str, Any] = vars or {}
        self._secrets: dict[str, Any] = secrets or {}
        self._builtins: dict[str, Any] = builtins or {}

    # --- Доступ к переменным ---

    def get(self, key: str, default: Any = None) -> Any:
        """Получить переменную из vars."""
        return self._vars.get(key, default)

    def get_secret(self, key: str, default: Any = None) -> Any:
        """Получить секрет."""
        return self._secrets.get(key, default)

    def get_builtin(self, key: str, default: Any = None) -> Any:
        """Получить встроенную переменную (today, timestamp и тд)."""
        return self._builtins.get(key, default)

    def get_any(self, key: str, default: Any = None) -> Any:
        """Получить из любого источника: builtins → vars → secrets."""
        if key in self._builtins:
            return self._builtins[key]
        if key in self._vars:
            return self._vars[key]
        if key in self._secrets:
            return self._secrets[key]
        return default

    def resolve(self, dotted_key: str, default: Any = None) -> Any:
        """Получить значение по точечному пути.

        Examples:
            resolve("vars.smb_server") → context.vars["smb_server"]
            resolve("secrets.smb_password") → context.secrets["smb_password"]
            resolve("today") → context.builtins["today"]
        """
        parts = dotted_key.split(".", 1)

        if len(parts) == 1:
            # Без префикса — поиск во всём
            return self.get_builtin(parts[0], self.get(parts[0], self.get_secret(parts[0], default)))

        namespace, key = parts
        if namespace == "vars":
            return self.get(key, default)
        elif namespace == "secrets":
            return self.get_secret(key, default)
        else:
            return default

    # --- Сеттеры (для тестов и расширений) ---

    def set(self, key: str, value: Any) -> None:
        self._vars[key] = value

    def set_secret(self, key: str, value: Any) -> None:
        self._secrets[key] = value

    def set_builtin(self, key: str, value: Any) -> None:
        self._builtins[key] = value

    # --- Свойства ---

    @property
    def vars(self) -> dict[str, Any]:
        return dict(self._vars)

    @property
    def secrets(self) -> dict[str, Any]:
        return dict(self._secrets)

    @property
    def builtins(self) -> dict[str, Any]:
        return dict(self._builtins)

    # --- Фабрики ---

    @classmethod
    def from_inventory(cls, inventory_dir: str | Path) -> Context:
        """Загрузить контекст из папки inventory/.

        Args:
            inventory_dir: Путь к папке с vars.yml, secrets.yml, vars.local.yml
        """
        inv = Path(inventory_dir)

        # Базовые переменные
        base_vars = _load_yaml(inv / "vars.yml")
        logger.debug("Loaded vars.yml: %d keys", len(base_vars))

        # Локальные переопределения
        local_vars = _load_yaml(inv / "vars.local.yml")
        if local_vars:
            logger.debug("Loaded vars.local.yml: %d keys (overrides)", len(local_vars))

        # Слияние: base + local
        merged_vars = _deep_merge(base_vars, local_vars)

        # Секреты
        secrets = _load_yaml(inv / "secrets.yml")
        if secrets:
            logger.debug("Loaded secrets.yml: %d keys", len(secrets))

        # Встроенные переменные
        from datetime import date, datetime
        builtins = {
            "today": date.today().isoformat(),        # 2026-04-03
            "now": datetime.now().strftime("%Y%m%d_%H%M%S"),  # 20260403_143000
            "timestamp": str(int(datetime.now().timestamp())),  # unix epoch
        }

        logger.info("Context loaded: vars=%d, secrets=%d, builtins=%d",
                     len(merged_vars), len(secrets), len(builtins))

        return cls(vars=merged_vars, secrets=secrets, builtins=builtins)

    def __repr__(self) -> str:
        return (f"Context(vars={len(self._vars)} keys, "
                f"secrets={len(self._secrets)} keys, "
                f"builtins={len(self._builtins)} keys)")

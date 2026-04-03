"""
ModuleResult — результат выполнения модуля.

Универсальный контракт между модулями (Python и Bash) и раннером.
Поддерживает сериализацию в JSON для JSON-протокола.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class ModuleResult:
    """Результат выполнения модуля.

    Attributes:
        status: 'ok', 'error', 'skipped', 'changed'
        message: Человекочитаемое описание результата
        changed: Были ли изменения (идемпотентность)
        data: Дополнительные данные (для следующих задач, логов)
    """

    status: str = "ok"
    message: str = ""
    changed: bool = False
    data: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        """Сериализация в JSON для передачи через stdout."""
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> ModuleResult:
        """Десериализация из JSON (ответ от bash-модуля)."""
        payload = json.loads(raw)
        return cls(
            status=payload.get("status", "ok"),
            message=payload.get("message", ""),
            changed=payload.get("changed", False),
            data=payload.get("data", {}),
        )

    @classmethod
    def ok(cls, message: str = "", data: dict[str, Any] | None = None) -> ModuleResult:
        """Успешное выполнение без изменений."""
        return cls(status="ok", message=message, changed=False, data=data or {})

    @classmethod
    def changed(cls, message: str = "", data: dict[str, Any] | None = None) -> ModuleResult:
        """Успешное выполнение с изменениями."""
        return cls(status="ok", message=message, changed=True, data=data or {})

    @classmethod
    def skipped(cls, message: str = "") -> ModuleResult:
        """Задача пропущена."""
        return cls(status="skipped", message=message, changed=False)

    @classmethod
    def error(cls, message: str, data: dict[str, Any] | None = None) -> ModuleResult:
        """Ошибка выполнения."""
        return cls(status="error", message=message, changed=False, data=data or {})

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"

    @property
    def is_error(self) -> bool:
        return self.status == "error"

    @property
    def is_skipped(self) -> bool:
        return self.status == "skipped"

    def __repr__(self) -> str:
        flag = "✓" if self.is_ok else ("⊘" if self.is_skipped else "✗")
        return f"ModuleResult({flag} status={self.status!r}, changed={self.changed}, message={self.message!r})"


class ModuleError(Exception):
    """Исключение при выполнении модуля.

    Содержит ModuleResult для передачи клиенту детальной информации.
    """

    def __init__(self, message: str, result: ModuleResult | None = None, **kwargs):
        self.result = result or ModuleResult.error(message, data=kwargs)
        super().__init__(message)

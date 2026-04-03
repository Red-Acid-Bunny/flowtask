"""
BaseModule — абстрактный базовый класс для Python-модулей.

Модули наследуют BaseModule, объявляют параметры через @param
и реализуют метод run() → ModuleResult.

Usage:
    class MyModule(BaseModule):
        src: str
        dest: str = "/tmp/"

        def run(self) -> ModuleResult:
            shutil.copytree(self.src, self.dest)
            return ModuleResult.changed("copied")
"""

from __future__ import annotations

import abc
import logging
import dataclasses
from typing import Any, Callable, get_type_hints

from ..engine.result import ModuleResult, ModuleError

logger = logging.getLogger("flowtask.module")


class ParamDescriptor:
    """Дескриптор для объявления параметров модуля.

    Используется для документирования и валидации параметров.
    Применяется как annotation + значение по умолчанию.

    Attributes:
        required: Обязателен ли параметр
        help: Описание параметра
        default: Значение по умолчанию
    """

    def __init__(self, default=..., *, required: bool | None = None, help: str = ""):
        # Автоопределение required: если default задан — не required
        if default is not ...:
            self.required = required if required is not None else False
            self.default = default
        else:
            self.required = required if required is not None else True
            self.default = ...

        self.help = help

    def __set_name__(self, owner, name):
        self.name = name
        self.private_name = f"_param_{name}"

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        return getattr(obj, self.private_name, self.default)

    def __set__(self, obj, value):
        setattr(obj, self.private_name, value)


def param(default=..., *, required: bool | None = None, help: str = ""):
    """Декоратор для объявления параметра модуля.

    Args:
        default: Значение по умолчанию. Если не указан — параметр required.
        required: Явно указать обязательность (None = автоопределение по default).
        help: Описание параметра.

    Examples:
        class MyModule(BaseModule):
            src: str = param(required=True, help="Source path")
            dest: str = param(default="/tmp/", help="Destination")
            verbose: bool = param(default=False, required=False)
    """
    return ParamDescriptor(default, required=required, help=help)


class ModuleMeta(abc.ABCMeta):
    """Метакласс для сбора метаданных параметров модуля."""

    def __new__(mcs, name, bases, namespace):
        cls = super().__new__(mcs, name, bases, namespace)
        # Собираем параметры из ParamDescriptor
        cls._param_descriptors: dict[str, ParamDescriptor] = {}
        # Собираем из type hints (не-ParamDescriptor поля)
        cls._param_hints: dict[str, type] = {}

        # Собираем из всех базовых классов
        for klass in reversed(cls.__mro__):
            for attr_name, attr_value in vars(klass).items():
                if isinstance(attr_value, ParamDescriptor):
                    cls._param_descriptors[attr_name] = attr_value

        # Type hints
        hints = get_type_hints(cls) if hasattr(cls, '__annotations__') else {}
        for key, hint in hints.items():
            if key not in cls._param_descriptors and not key.startswith('_'):
                cls._param_hints[key] = hint

        return cls


class BaseModule(abc.ABC, metaclass=ModuleMeta):
    """Базовый класс для всех Python-модулей FlowTask.

    Подклассы должны:
    1. Объявить параметры (через @param или type hints с дефолтами)
    2. Реализовать run() → ModuleResult

    Модуль инициализируется через ModuleLoader из dict с параметрами.
    """

    # Имя модуля (по умолчанию — имя класса в lowercase/snake_case)
    name: str = ""

    # Описание модуля
    description: str = ""

    def __init__(self, **params):
        """Инициализация модуля с параметрами.

        Args:
            **params: Словарь параметров из playbook
        """
        self._dry_run: bool = False
        self._verbose: bool = False

        # Устанавливаем параметры
        self._raw_params = params
        unknown = []

        for key, value in params.items():
            if key in ("dry_run", "verbose"):
                continue
            if hasattr(self.__class__, key) and isinstance(getattr(self.__class__, key), ParamDescriptor):
                setattr(self, key, value)
            elif key in self._param_hints:
                setattr(self, key, value)
            elif hasattr(self, key):
                setattr(self, key, value)
            else:
                unknown.append(key)

        if unknown:
            logger.warning("Module %s: unknown params: %s", self.module_name, unknown)

    @property
    def module_name(self) -> str:
        """Имя модуля для логов и регистра."""
        if self.name:
            return self.name
        # CamelCase → snake_case, strip leading underscores
        name = self.__class__.__name__.lstrip('_')
        import re
        name = re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()
        return name

    @property
    def param_schema(self) -> dict[str, dict[str, Any]]:
        """Схема параметров модуля (для документации и валидации)."""
        schema = {}
        for name, desc in self._param_descriptors.items():
            schema[name] = {
                "required": desc.required,
                "default": desc.default if desc.default is not ... else None,
                "help": desc.help,
                "has_default": desc.default is not ...,
            }
        return schema

    def validate_params(self) -> list[str]:
        """Проверить обязательные параметры. Возвращает список ошибок."""
        errors = []
        for name, desc in self._param_descriptors.items():
            if desc.required and desc.default is ...:
                value = getattr(self, name, ...)
                if value is ... or value is None:
                    errors.append(f"Missing required param: {name}")
        return errors

    @abc.abstractmethod
    def run(self) -> ModuleResult:
        """Выполнить модуль. Должен быть реализован в подклассе."""
        ...

    def execute(self, dry_run: bool = False, verbose: bool = False) -> ModuleResult:
        """Выполнить модуль с проверками (вызывается раннером).

        Args:
            dry_run: Предпросмотр без выполнения
            verbose: Подробный вывод

        Returns:
            ModuleResult
        """
        self._dry_run = dry_run
        self._verbose = verbose

        # Валидация параметров
        errors = self.validate_params()
        if errors:
            return ModuleResult.error("; ".join(errors))

        if dry_run:
            logger.info("[DRY-RUN] Would execute module: %s (params: %s)",
                        self.module_name, self._safe_params())
            return ModuleResult.ok(f"[DRY-RUN] Would execute: {self.module_name}")

        logger.debug("Executing module: %s", self.module_name)
        return self.run()

    def _safe_params(self) -> dict:
        """Параметры для логов (без секретных значений)."""
        return {k: "***" if "pass" in k.lower() or "secret" in k.lower() else v
                for k, v in self._raw_params.items()}

"""
ModuleLoader — обнаружение и загрузка модулей.

Сканирует директории и регистрирует модули:
  - Python-модули: flowtask/modules/*.py → классы наследующие BaseModule
  - Пользовательские Python: modules/*.py
  - Python-скрипты: modules/python/*.py → PythonScriptAdapter (JSON stdin/stdout)
  - Bash-модули: modules/bash/*.sh → BashModuleAdapter

Все модули доступны через единый интерфейс get(name) → callable.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
from pathlib import Path
from typing import Any

from .bash_adapter import BashModuleAdapter
from .python_adapter import PythonScriptAdapter
from ..modules.base import BaseModule

logger = logging.getLogger("flowtask.module_loader")


class ModuleNotFoundError(Exception):
    """Модуль не найден в реестре."""
    pass


class ModuleLoader:
    """Реестр и загрузчик модулей FlowTask.

    Usage:
        loader = ModuleLoader()
        loader.discover()

        module_cls = loader.get("mount_smb")
        instance = module_cls(**params)
        result = instance.execute()
    """

    def __init__(self, extra_modules_dirs: list[str | Path] | None = None):
        self._registry: dict[str, type[BaseModule] | BashModuleAdapter | PythonScriptAdapter] = {}

        # Стандартные директории для поиска
        builtin_python_dir = Path(__file__).parent.parent / "modules"
        user_modules_dir = Path("modules")

        self._search_dirs: list[Path] = []
        self._bash_dirs: list[Path] = []
        self._python_script_dirs: list[Path] = []

        # Встроенные Python-модули (flowtask/modules/)
        if builtin_python_dir.exists():
            self._search_dirs.append(builtin_python_dir)

        # Пользовательские Python-модули (modules/*.py)
        if user_modules_dir.exists():
            self._search_dirs.append(user_modules_dir)

        # Пользовательские python-скрипты (modules/python/)
        python_scripts_dir = user_modules_dir / "python"
        if python_scripts_dir.exists():
            self._python_script_dirs.append(python_scripts_dir)

        # Пользовательские bash-модули (modules/bash/)
        bash_dir = user_modules_dir / "bash"
        if bash_dir.exists():
            self._bash_dirs.append(bash_dir)

        # Дополнительные директории
        if extra_modules_dirs:
            for d in extra_modules_dirs:
                p = Path(d)
                if p.exists():
                    self._search_dirs.append(p)
                    bash = p / "bash"
                    if bash.exists():
                        self._bash_dirs.append(bash)
                    python_scripts = p / "python"
                    if python_scripts.exists():
                        self._python_script_dirs.append(python_scripts)

    def discover(self) -> list[str]:
        """Сканировать директории и зарегистрировать все модули.

        Returns:
            Список имён найденных модулей
        """
        found = set()

        # Python-модули (классы, наследующие BaseModule)
        for search_dir in self._search_dirs:
            for py_file in search_dir.glob("*.py"):
                if py_file.name.startswith("_"):
                    continue
                names = self._load_python_module(py_file)
                found.update(str(n) for n in names)  # ensure strings only

        # Python-скрипты (modules/python/*.py)
        for py_dir in self._python_script_dirs:
            for py_file in py_dir.glob("*.py"):
                if py_file.name.startswith("_"):
                    continue
                self._register_python_script(py_file)
                found.add(py_file.stem)

        # Bash-модули
        for bash_dir in self._bash_dirs:
            for sh_file in bash_dir.glob("*.sh"):
                if sh_file.name.startswith("_"):
                    continue
                self._register_bash_module(sh_file)
                found.add(sh_file.stem)

        logger.info("Discovered %d modules: %s", len(found), sorted(found))
        return sorted(found)

    def _load_python_module(self, path: Path) -> list[str]:
        """Загрузить Python-файл и зарегистрировать классы-наследники BaseModule.

        Returns:
            Список зарегистрированных имён модулей
        """
        module_name = f"flowtask.user_modules.{path.stem}"

        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                logger.warning("Cannot load module from %s: no spec", path)
                return []

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as e:
            logger.error("Failed to load Python module %s: %s", path, e)
            return []

        registered = []
        for attr_name, attr_value in inspect.getmembers(module, inspect.isclass):
            # Пропускаем импортированные классы — только те, что определены в этом файле
            if attr_value.__module__ != module_name:
                continue
            if issubclass(attr_value, BaseModule) and attr_value is not BaseModule:
                # Имя модуля — из поля name (только если это строка, не дескриптор)
                cls_name = attr_value.name
                if isinstance(cls_name, str) and cls_name:
                    instance_name = cls_name
                else:
                    # CamelCase → snake_case
                    import re
                    instance_name = re.sub(r'(?<!^)(?=[A-Z])', '_', attr_name).lower()

                if instance_name in self._registry:
                    logger.warning("Module '%s' already registered, overwriting with %s",
                                   instance_name, path)

                self._registry[instance_name] = attr_value
                logger.debug("Registered Python module: %s → %s.%s",
                             instance_name, module_name, attr_name)
                registered.append(instance_name)

        return registered

    def _register_python_script(self, path: Path) -> None:
        """Зарегистрировать python-скрипт."""
        name = path.stem
        if name in self._registry:
            logger.warning("Module '%s' already registered, overwriting with python script %s",
                           name, path)
        self._registry[name] = PythonScriptAdapter(path)

    def _register_bash_module(self, path: Path) -> None:
        """Зарегистрировать bash-скрипт."""
        name = path.stem
        if name in self._registry:
            logger.warning("Module '%s' already registered, overwriting with bash %s",
                           name, path)
        self._registry[name] = BashModuleAdapter(path)

    def get(self, name: str) -> type[BaseModule] | BashModuleAdapter | PythonScriptAdapter:
        """Получить модуль по имени.

        Args:
            name: Имя модуля (например "mount_smb", "copy")

        Returns:
            Класс модуля (BaseModule subclass) или BashModuleAdapter

        Raises:
            ModuleNotFoundError: Если модуль не найден
        """
        if name not in self._registry:
            available = ", ".join(sorted(self._registry.keys())) or "(none)"
            raise ModuleNotFoundError(
                f"Module '{name}' not found. Available: {available}"
            )
        return self._registry[name]

    def has(self, name: str) -> bool:
        """Проверить существование модуля."""
        return name in self._registry

    def list_modules(self) -> dict[str, str]:
        """Список всех зарегистрированных модулей.

        Returns:
            {name: type} — 'python' (класс), 'python_script' или 'bash'
        """
        result = {}
        for name, module in sorted(self._registry.items(), key=lambda x: str(x[0])):
            if isinstance(module, PythonScriptAdapter):
                result[name] = "python_script"
            elif isinstance(module, BashModuleAdapter):
                result[name] = "bash"
            elif inspect.isclass(module) and issubclass(module, BaseModule):
                result[name] = "python"
            else:
                result[name] = "unknown"
        return result

    def __repr__(self) -> str:
        return f"ModuleLoader(modules={len(self._registry)})"

"""
Template — строгая подстановка переменных.

Поддерживает шаблоны:
  {{ vars.key }}          → из vars
  {{ secrets.key }}       → из secrets
  {{ today }}             → встроенные (today, now, timestamp)
  {{ key }}               → автопоиск (builtins → vars → secrets)

Безопасность:
  - Неопределённые переменные вызывают ошибку (TemplateError)
  - Значения секретов маскируются в логах
  - Подготовлена инфраструктура для фильтров ({{ x | default('val') }})
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("flowtask.template")

# Regex для поиска {{ ... }} шаблонов
_TEMPLATE_PATTERN = re.compile(r"\{\{\s*(.+?)\s*\}\}")

# Множество ключей, которые считаются секретами (для маскирования в логах)
_SECRET_NAMESPACES = {"secrets"}


class TemplateError(Exception):
    """Ошибка в шаблоне."""
    pass


def _parse_expression(expr: str) -> tuple[str, list[tuple[str, list]]]:
    """Разобрать выражение с возможными фильтрами.

    Examples:
        "vars.smb_server" → ("vars.smb_server", [])
        "vars.x | default('val')" → ("vars.x", [("default('val')", [])])

    TODO: Реализовать выполнение фильтров для Variant B.
    """
    parts = expr.split("|", 1)
    key = parts[0].strip()
    filters = []
    if len(parts) > 1:
        # Пока только запоминаем фильтр, но не выполняем
        filters.append((parts[1].strip(), []))
    return key, filters


class Template:
    """Движок шаблонов для подстановки переменных.

    Usage:
        tmpl = Template(context)
        result = tmpl.render("{{ vars.smb_server }}/{{ vars.smb_path }}")
    """

    def __init__(self, context):
        """
        Args:
            context: Объект Context с переменными и секретами
        """
        self._context = context

    def render(self, text: str) -> str:
        """Подставить все {{ }} шаблоны в строке.

        Args:
            text: Строка с шаблонами

        Returns:
            Строка с подставленными значениями

        Raises:
            TemplateError: Если переменная не определена
        """
        if not text or "{{" not in text:
            return text

        def _replace(match: re.Match) -> str:
            key = match.group(1).strip()
            value = self._resolve(key)
            return str(value)

        return _TEMPLATE_PATTERN.sub(_replace, text)

    def render_dict(self, data: dict) -> dict:
        """Рекурсивно обработать словарь."""
        result = {}
        for key, value in data.items():
            result[key] = self._render_value(value)
        return result

    def render_list(self, data: list) -> list:
        """Рекурсивно обработать список."""
        return [self._render_value(item) for item in data]

    def render_any(self, data: Any) -> Any:
        """Обработать любое значение (dict, list, str, или как есть)."""
        return self._render_value(data)

    # Regex для полного совпадения строки с единственным шаблоном
    _SINGLE_TEMPLATE = re.compile(r"^\s*\{\{\s*(.+?)\s*\}\}\s*$")

    def _render_value(self, value: Any) -> Any:
        """Рекурсивная обработка значения."""
        if isinstance(value, str):
            # Если вся строка — единственный шаблон {{ key }},
            # и значение — не строка (list, dict, int...), вернуть как есть.
            # Это позволяет передавать массивы и объекты из vars/secrets
            # без превращения в Python-repr строку.
            m = self._SINGLE_TEMPLATE.match(value)
            if m:
                key = m.group(1).strip()
                resolved = self._resolve(key)
                if resolved is not None and not isinstance(resolved, str):
                    return resolved
            return self.render(value)
        elif isinstance(value, dict):
            return self.render_dict(value)
        elif isinstance(value, list):
            return self.render_list(value)
        return value

    def _resolve(self, key: str) -> Any:
        """Получить значение по ключу, определив тип ключа.

        Args:
            key: Ключ из шаблона (например "vars.smb_server" или "today")

        Returns:
            Значение

        Raises:
            TemplateError: Если переменная не определена
        """
        # Парсинг с поддержкой фильтров (для будущего Variant B)
        base_key, filters = _parse_expression(key)

        # TODO: применить фильтры когда Variant B будет реализован
        if filters:
            filter_names = [f[0] for f in filters]
            raise TemplateError(
                f"Variable '{key}' is not defined — filters not yet implemented: {', '.join(filter_names)}"
            )

        # Точечный путь — явно указанный namespace
        if "." in base_key:
            namespace, rest = base_key.split(".", 1)
            if namespace == "vars":
                if not self._context.has(rest):
                    raise TemplateError(f"Variable '{base_key}' is not defined")
                return self._context.get(rest)
            elif namespace == "secrets":
                if not self._context.has_secret(rest):
                    raise TemplateError(f"Variable '{base_key}' is not defined")
                value = self._context.get_secret(rest)
                logger.debug("Template: resolved secret '%s' → ***", base_key)
                return value
            else:
                raise TemplateError(f"Variable '{base_key}' is not defined")

        # Без namespace — автопоиск
        value = self._context.get_any(base_key)
        if value is None:
            raise TemplateError(f"Variable '{base_key}' is not defined")
        return value

    def safe_log(self, text: str) -> str:
        """Вернуть строку с замаскированными секретами для вывода в лог.

        Examples:
            safe_log("server={{ secrets.password }}")
            → "server=***"
        """
        def _mask(match: re.Match) -> str:
            key = match.group(1).strip()
            if key.startswith("secrets."):
                return "***"
            return match.group(0)

        return _TEMPLATE_PATTERN.sub(_mask, text)

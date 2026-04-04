"""
Template — безопасная подстановка переменных.

Поддерживает шаблоны:
  {{ vars.key }}          → из vars
  {{ secrets.key }}       → из secrets
  {{ today }}             → встроенные (today, now, timestamp)
  {{ key }}               → автопоиск (builtins → vars → secrets)

Безопасность:
  - Подставляются только известные ключи из контекста
  - Неизвестные ключи НЕ подставляются (оставляются как есть, с warning)
  - Значения секретов маскируются в логах
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
        """
        if not text or "{{" not in text:
            return text

        def _replace(match: re.Match) -> str:
            key = match.group(1).strip()
            value = self._resolve(key)
            if value is None:
                logger.warning("Template: unresolved key '%s'", key)
                return match.group(0)  # Оставить как есть
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
            Значение или None если ключ не найден
        """
        # Точечный путь — явно указанный namespace
        if "." in key:
            namespace, rest = key.split(".", 1)
            if namespace == "vars":
                return self._context.get(rest)
            elif namespace == "secrets":
                value = self._context.get_secret(rest)
                logger.debug("Template: resolved secret '%s' → ***", key)
                return value
            else:
                logger.warning(
                    "Template: unknown namespace '%s' in '%s'", namespace, key
                )
                return None

        # Без namespace — автопоиск
        return self._context.get_any(key)

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

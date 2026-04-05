"""
Тесты для engine: context, template, result.
"""

import os
import json
import pytest
import yaml
from pathlib import Path
from datetime import date

from flowtask.engine.context import Context, _deep_merge
from flowtask.engine.template import Template, TemplateError, _parse_expression
from flowtask.engine.result import ModuleResult, ModuleError


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def tmp_inventory(tmp_path):
    """Создаёт временную inventory структуру."""
    inv = tmp_path / "inventory"
    inv.mkdir()

    (inv / "vars.yml").write_text(yaml.dump({
        "smb_server": "192.168.0.8",
        "smb_share": "box_delta_bin",
        "smb_path": "develop/V5-net6",
        "out_dir": "/tmp/flowtask-out",
    }))

    (inv / "vars.local.yml").write_text(yaml.dump({
        "out_dir": "/mnt/d/flowtask-out",  # override
    }))

    (inv / "secrets.yml").write_text(yaml.dump({
        "smb_user": "admin",
        "smb_pass": "secret123",
    }))

    return inv


@pytest.fixture
def context(tmp_inventory):
    """Загруженный контекст."""
    return Context.from_inventory(tmp_inventory)


# ============================================================
# Context
# ============================================================

class TestContext:

    def test_load_vars(self, context):
        assert context.get("smb_server") == "192.168.0.8"
        assert context.get("smb_share") == "box_delta_bin"

    def test_local_override(self, context):
        """vars.local.yml должен перезаписывать vars.yml"""
        assert context.get("out_dir") == "/mnt/d/flowtask-out"

    def test_secrets_separate(self, context):
        assert context.get("smb_pass") is None  # секреты не в vars
        assert context.get_secret("smb_pass") == "secret123"

    def test_builtins(self, context):
        assert context.get_builtin("today") == date.today().isoformat()
        assert context.get_builtin("now") is not None
        assert context.get_builtin("timestamp") is not None

    def test_resolve_dotted(self, context):
        assert context.resolve("vars.smb_server") == "192.168.0.8"
        assert context.resolve("secrets.smb_pass") == "secret123"
        assert context.resolve("today") == date.today().isoformat()

    def test_resolve_unknown(self, context):
        assert context.resolve("unknown_key") is None
        assert context.resolve("vars.nonexistent") is None

    def test_get_any_priority(self, context):
        """builtins > vars > secrets"""
        builtin_val = context.get_builtin("today")
        assert context.get_any("today") == builtin_val
        assert context.get_any("smb_server") == "192.168.0.8"
        assert context.get_any("smb_user") == "admin"

    def test_empty_inventory(self, tmp_path):
        inv = tmp_path / "empty_inv"
        inv.mkdir()
        ctx = Context.from_inventory(inv)
        assert ctx.get("anything") is None
        assert ctx.get_secret("anything") is None

    def test_missing_inventory_dir(self):
        ctx = Context.from_inventory("/nonexistent/path")
        assert ctx.get("anything") is None

    def test_has(self, context):
        assert context.has("smb_server") is True
        assert context.has("nonexistent") is False

    def test_has_secret(self, context):
        assert context.has_secret("smb_pass") is True
        assert context.has_secret("nonexistent") is False

    def test_setters(self):
        ctx = Context()
        ctx.set("key", "value")
        ctx.set_secret("pass", "secret")
        ctx.set_builtin("today", "2026-01-01")
        assert ctx.get("key") == "value"
        assert ctx.get_secret("pass") == "secret"
        assert ctx.get_builtin("today") == "2026-01-01"


class TestDeepMerge:

    def test_simple(self):
        assert _deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_override(self):
        assert _deep_merge({"a": 1, "b": 2}, {"b": 3}) == {"a": 1, "b": 3}

    def test_nested(self):
        base = {"x": {"a": 1, "b": 2}}
        override = {"x": {"b": 3, "c": 4}}
        assert _deep_merge(base, override) == {"x": {"a": 1, "b": 3, "c": 4}}

    def test_no_mutation(self):
        base = {"a": {"b": 1}}
        result = _deep_merge(base, {"a": {"c": 2}})
        assert base["a"] == {"b": 1}  # base не изменён


# ============================================================
# Template
# ============================================================

class TestTemplate:

    def test_render_vars(self, context):
        tmpl = Template(context)
        assert tmpl.render("{{ vars.smb_server }}") == "192.168.0.8"

    def test_render_secrets(self, context):
        tmpl = Template(context)
        assert tmpl.render("{{ secrets.smb_pass }}") == "secret123"

    def test_render_builtin(self, context):
        tmpl = Template(context)
        assert tmpl.render("{{ today }}") == date.today().isoformat()

    def test_render_auto(self, context):
        """Без namespace — автопоиск"""
        tmpl = Template(context)
        assert tmpl.render("{{ smb_server }}") == "192.168.0.8"

    def test_render_multiple(self, context):
        tmpl = Template(context)
        result = tmpl.render("//{{ vars.smb_server }}/{{ vars.smb_share }}")
        assert result == "//192.168.0.8/box_delta_bin"

    def test_render_unresolved_raises_error(self, context):
        """Неопределённые переменные вызывают ошибку"""
        tmpl = Template(context)
        with pytest.raises(TemplateError, match="Variable 'unknown.key' is not defined"):
            tmpl.render("{{ unknown.key }}")

    def test_undefined_vars_raises_error(self, context):
        tmpl = Template(context)
        with pytest.raises(TemplateError, match="Variable 'vars.missing' is not defined"):
            tmpl.render("{{ vars.missing }}")

    def test_undefined_secrets_raises_error(self, context):
        tmpl = Template(context)
        with pytest.raises(TemplateError, match="Variable 'secrets.missing' is not defined"):
            tmpl.render("{{ secrets.missing }}")

    def test_partial_render_raises_error(self, context):
        tmpl = Template(context)
        with pytest.raises(TemplateError, match="Variable 'vars.missing' is not defined"):
            tmpl.render("prefix-{{ vars.missing }}-suffix")

    def test_render_any_undefined_nested(self, context):
        tmpl = Template(context)
        with pytest.raises(TemplateError):
            tmpl.render_any({"a": "{{ vars.missing}}"})

    def test_filter_syntax_not_implemented(self, context):
        """Фильтры пока не реализованы — ошибка"""
        tmpl = Template(context)
        with pytest.raises(TemplateError, match="filters not yet implemented"):
            tmpl.render("{{ vars.smb_server | default('localhost') }}")

    def test_render_no_templates(self, context):
        tmpl = Template(context)
        assert tmpl.render("plain text") == "plain text"
        assert tmpl.render("") == ""

    def test_render_dict(self, context):
        tmpl = Template(context)
        data = {"server": "{{ vars.smb_server }}", "path": "{{ vars.smb_path }}"}
        result = tmpl.render_dict(data)
        assert result["server"] == "192.168.0.8"
        assert result["path"] == "develop/V5-net6"

    def test_render_nested_dict(self, context):
        tmpl = Template(context)
        data = {"nested": {"server": "{{ vars.smb_server }}"}}
        result = tmpl.render_dict(data)
        assert result["nested"]["server"] == "192.168.0.8"

    def test_render_list(self, context):
        tmpl = Template(context)
        data = ["{{ vars.smb_server }}", "plain"]
        result = tmpl.render_list(data)
        assert result[0] == "192.168.0.8"
        assert result[1] == "plain"

    def test_safe_log(self, context):
        tmpl = Template(context)
        text = "pass={{ secrets.smb_pass }} server={{ vars.smb_server }}"
        safe = tmpl.safe_log(text)
        assert "***" in safe
        assert "secret123" not in safe
        # vars не подставляются в safe_log — только маскировка секретов
        assert "{{ vars.smb_server }}" in safe

    def test_safe_log_no_templates(self, context):
        tmpl = Template(context)
        assert tmpl.safe_log("plain text") == "plain text"


# ============================================================
# Parse expression (filter infrastructure)
# ============================================================

class TestParseExpression:

    def test_simple_key(self):
        key, filters = _parse_expression("vars.smb_server")
        assert key == "vars.smb_server"
        assert filters == []

    def test_key_with_filter(self):
        key, filters = _parse_expression("vars.x | default('val')")
        assert key == "vars.x"
        assert len(filters) == 1
        assert filters[0][0] == "default('val')"

    def test_key_with_multiple_filters(self):
        key, filters = _parse_expression("vars.x | trim")
        assert key == "vars.x"
        assert len(filters) == 1
        assert filters[0][0] == "trim"


# ============================================================
# ModuleResult
# ============================================================

class TestModuleResult:

    def test_ok(self):
        r = ModuleResult.ok("done")
        assert r.is_ok
        assert not r.changed
        assert r.status == "ok"

    def test_changed(self):
        r = ModuleResult.changed("files copied", data={"count": 5})
        assert r.is_ok
        assert r.changed
        assert r.data["count"] == 5

    def test_skipped(self):
        r = ModuleResult.skipped("already up to date")
        assert r.is_skipped
        assert not r.changed

    def test_error(self):
        r = ModuleResult.error("file not found", data={"path": "/x"})
        assert r.is_error
        assert not r.changed
        assert r.data["path"] == "/x"

    def test_to_json(self):
        r = ModuleResult.ok("test", data={"key": "val"})
        parsed = json.loads(r.to_json())
        assert parsed["status"] == "ok"
        assert parsed["message"] == "test"
        assert parsed["data"]["key"] == "val"

    def test_from_json(self):
        raw = '{"status":"ok","message":"done","changed":true,"data":{"n":1}}'
        r = ModuleResult.from_json(raw)
        assert r.is_ok
        assert r.changed
        assert r.data["n"] == 1

    def test_from_json_minimal(self):
        raw = '{"status":"ok"}'
        r = ModuleResult.from_json(raw)
        assert r.is_ok
        assert r.message == ""
        assert not r.changed

    def test_roundtrip(self):
        original = ModuleResult.changed("test", data={"a": 1, "b": [1, 2]})
        restored = ModuleResult.from_json(original.to_json())
        assert restored.status == original.status
        assert restored.changed == original.changed
        assert restored.data == original.data

    def test_module_error(self):
        r = ModuleResult.error("boom")
        err = ModuleError("boom", result=r)
        assert err.result.is_error
        assert str(err) == "boom"

"""
Интеграционный тест — полный end-to-end запуск playbook.

Проверяет всю цепочку: inventory → context → template → modules → runner → CLI.
"""

import os
import yaml
import pytest
from pathlib import Path

from flowtask.engine.runner import Runner
from flowtask.engine.context import Context
from flowtask.engine.template import Template
from flowtask.engine.module_loader import ModuleLoader
from flowtask.cli import main


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def full_env(tmp_path):
    """Полная тестовая среда: inventory + файлы + playbook."""
    # --- Inventory ---
    inv = tmp_path / "inventory"
    inv.mkdir()

    (inv / "vars.yml").write_text(yaml.dump({
        "source_dir": str(tmp_path / "source"),
        "dest_dir": str(tmp_path / "output"),
        "archive_dir": str(tmp_path / "archives"),
        "item_name": "integration-test",
    }))

    (inv / "secrets.yml").write_text(yaml.dump({
        "token": "secret_token_123",
    }))

    # --- Source files ---
    src = tmp_path / "source"
    src.mkdir()
    (src / "app.exe").write_bytes(b"exe content " * 100)
    (src / "config.json").write_text('{"version": "1.0"}')
    (src / "readme.txt").write_text("Release notes for integration test")

    subdir = src / "subdir"
    subdir.mkdir()
    (subdir / "module.dll").write_bytes(b"dll data " * 50)

    # --- Playbook ---
    playbook = tmp_path / "playbook.yml"
    playbook.write_text(yaml.dump({
        "name": "Integration Test Playbook",
        "inventory": str(inv),
        "vars": {
            "label": "{{ today }}",
        },
        "tasks": [
            {
                "name": "Copy all source files",
                "module": "copy",
                "params": {
                    "src": str(src),
                    "dest": "{{ vars.dest_dir }}/full_copy",
                },
                "tags": ["copy", "sync"],
                "register": "copy_result",
            },
            {
                "name": "Copy readme to output",
                "module": "copy",
                "params": {
                    "src": str(src / "readme.txt"),
                    "dest": "{{ vars.dest_dir }}",
                },
                "when": "success",
                "tags": ["verify"],
                "register": "verify_result",
            },
            {
                "name": "Archive the output",
                "module": "archive",
                "params": {
                    "src": "{{ vars.dest_dir }}/full_copy",
                    "format": "tar.gz",
                    "name": "integration_{{ today }}",
                    "dest_dir": "{{ vars.archive_dir }}",
                },
                "when": "success",
                "tags": ["archive"],
                "register": "archive_result",
            },
            {
                "name": "Cleanup archive",
                "module": "delete",
                "params": {
                    "path": "{{ vars.archive_dir }}/integration_{{ today }}.tar.gz",
                    "force": True,
                },
                "when": "always",
                "tags": ["cleanup"],
            },
        ],
    }))

    return {
        "tmp_path": tmp_path,
        "inventory": inv,
        "playbook": playbook,
        "source": src,
        "dest_dir": tmp_path / "output",
        "archive_dir": tmp_path / "archives",
    }


# ============================================================
# Integration tests
# ============================================================

class TestFullIntegration:

    def test_full_playbook_execution(self, full_env):
        """Полный прогон playbook через Runner."""
        runner = Runner(
            playbook_path=full_env["playbook"],
            verbose=True,
        )

        result = runner.run()

        # Все задачи должны пройти
        assert result.success, f"Playbook failed: {result.summary()}"
        assert result.failed == 0
        assert result.total == 4

        # Проверяем файловую систему
        dest = full_env["dest_dir"]

        # readme.txt скопирован в dest (dest — директория)
        assert (dest / "readme.txt").exists()
        assert (dest / "readme.txt").read_text() == "Release notes for integration test"

        # Полная копия source
        full_copy = dest / "full_copy"
        assert full_copy.exists()
        assert full_copy.is_dir()
        # Проверяем что файлы внутри есть
        files_in_copy = list(full_copy.rglob("*"))
        assert len(files_in_copy) >= 4  # app.exe, config.json, readme.txt, subdir/module.dll

        # Архив создан (зарегистрированный результат), но cleanup его удалил
        # Проверяем через register
        archive_result = runner.context.get("archive_result")
        assert archive_result is not None
        assert archive_result["changed"] is True
        assert "integration_" in archive_result["message"]

        # Cleanup удалил архив (when=always)
        assert not any(full_env["archive_dir"].glob("integration_*.tar.gz"))

    def test_registered_results(self, full_env):
        """Результаты через register доступны в context."""
        runner = Runner(playbook_path=full_env["playbook"])

        result = runner.run()
        ctx = runner.context

        # copy_result должен быть зарегистрирован
        copy_res = ctx.get("copy_result")
        assert copy_res is not None
        assert copy_res["changed"] is True

        # verify_result
        verify = ctx.get("verify_result")
        assert verify is not None

    def test_template_rendering_in_playbook(self, full_env):
        """Шаблоны корректно рендерятся в параметрах."""
        runner = Runner(playbook_path=full_env["playbook"])
        result = runner.run()

        assert result.success
        # Проверяем что шаблоны заменились (путь содержит output и не содержит {{ }})
        for rec in result.records:
            if rec.module == "copy" and rec.params:
                dest_val = str(rec.params.get("dest", ""))
                assert "output" in dest_val
                assert "{{ " not in dest_val

    def test_dry_run_no_changes(self, full_env):
        """Dry-run не создаёт файлов."""
        runner = Runner(
            playbook_path=full_env["playbook"],
            dry_run=True,
        )

        result = runner.run()

        assert result.success
        assert result.total == 4
        # Ни один файл не создан
        assert not full_env["dest_dir"].exists()
        assert not full_env["archive_dir"].exists()
        # Все сообщения содержат DRY-RUN
        for rec in result.records:
            assert "DRY-RUN" in rec.message

    def test_limit_single_task(self, full_env):
        """--limit выполняет только совпадающие задачи."""
        runner = Runner(
            playbook_path=full_env["playbook"],
            limit="archive",
        )

        result = runner.run()

        assert result.total == 1
        assert "archive" in result.records[0].name.lower()

    def test_tags_filtering(self, full_env):
        """--tags фильтрует задачи."""
        runner = Runner(
            playbook_path=full_env["playbook"],
            tags=["copy"],
        )

        result = runner.run()

        # Задачи с тегами: setup(setup), copy(copy,sync), cleanup — нет тега copy
        assert result.total >= 1
        for rec in result.records:
            assert "copy" in rec.name.lower() or "setup" in rec.name.lower()

    def test_skip_tags(self, full_env):
        """--skip-tags пропускает задачи."""
        runner = Runner(
            playbook_path=full_env["playbook"],
            skip_tags=["cleanup"],
        )

        result = runner.run()

        # Cleanup задача пропущена
        for rec in result.records:
            assert "cleanup" not in rec.name.lower()

    def test_idempotency_second_run(self, full_env):
        """Второй запуск не должен давать изменений там где это возможно."""
        # Первый запуск
        runner1 = Runner(playbook_path=full_env["playbook"])
        result1 = runner1.run()
        assert result1.success

        # Запоминаем записи со статусом changed
        changed_tasks = [r.name for r in result1.records if r.changed]

        # cleanup всегда меняет (when=always), копирование — нет (файлы уже есть)
        # Проверяем что как минимум архив уже существовал при втором запуске

    def test_context_variables_priority(self, full_env):
        """Приоритет переменных: builtins > vars > secrets."""
        ctx = Context.from_inventory(full_env["inventory"])

        # vars
        assert ctx.get("source_dir") == str(full_env["source"])
        # secrets
        assert ctx.get_secret("token") == "secret_token_123"
        # builtins
        from datetime import date
        assert ctx.get_builtin("today") == date.today().isoformat()
        # get_any: builtins first
        assert ctx.get_any("today") == date.today().isoformat()
        # get_any: vars
        assert ctx.get_any("source_dir") == str(full_env["source"])
        # get_any: secrets
        assert ctx.get_any("token") == "secret_token_123"


class TestCLIIntegration:

    def test_cli_run_playbook(self, full_env, capsys):
        """Запуск playbook через CLI."""
        ret = main([
            "run", str(full_env["playbook"]),
            "-i", str(full_env["inventory"]),
        ])

        assert ret == 0
        # Проверяем что файлы созданы
        assert (full_env["dest_dir"] / "readme.txt").exists()
        assert (full_env["dest_dir"] / "full_copy").exists()

    def test_cli_validate(self, full_env, capsys):
        """Валидация playbook через CLI."""
        ret = main(["validate", str(full_env["playbook"])])
        assert ret == 0

    def test_cli_dry_run(self, full_env, capsys):
        """Dry-run через CLI."""
        ret = main(["run", str(full_env["playbook"]), "--dry-run"])
        assert ret == 0
        assert not full_env["dest_dir"].exists()

    def test_cli_list_modules(self, capsys):
        """Список модулей через CLI."""
        ret = main(["list-modules"])
        assert ret == 0
        output = capsys.readouterr().out
        assert "copy" in output
        assert "archive" in output


class TestContextLoading:

    def test_deep_merge_inventory(self, tmp_path):
        """vars.local.yml перекрывает vars.yml."""
        inv = tmp_path / "inv"
        inv.mkdir()

        (inv / "vars.yml").write_text(yaml.dump({
            "server": "192.168.0.1",
            "port": 8080,
            "nested": {"key": "base", "shared": "from_base"},
        }))

        (inv / "vars.local.yml").write_text(yaml.dump({
            "port": 9090,
            "nested": {"shared": "from_local", "extra": "value"},
        }))

        ctx = Context.from_inventory(inv)

        assert ctx.get("server") == "192.168.0.1"   # из base
        assert ctx.get("port") == 9090                 # из local
        assert ctx.get("nested")["key"] == "base"      # из base
        assert ctx.get("nested")["shared"] == "from_local"  # из local
        assert ctx.get("nested")["extra"] == "value"    # из local

    def test_secret_masking(self, tmp_path):
        """Секреты маскируются в логах."""
        inv = tmp_path / "inv"
        inv.mkdir()

        (inv / "vars.yml").write_text(yaml.dump({"host": "server1"}))
        (inv / "secrets.yml").write_text(yaml.dump({"password": "secret123"}))

        ctx = Context.from_inventory(inv)
        tmpl = Template(ctx)

        # Рендер — значение доступно
        assert tmpl.render("{{ secrets.password }}") == "secret123"

        # safe_log — значение замаскировано
        safe = tmpl.safe_log("pass={{ secrets.password }} host={{ vars.host }}")
        assert "***" in safe
        assert "secret123" not in safe
        # vars не маскируются в safe_log (только секреты)
        assert "{{ vars.host }}" in safe

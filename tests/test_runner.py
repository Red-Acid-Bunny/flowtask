"""
Тесты для Runner — оркестратора выполнения playbook.
"""

import os
import pytest
import yaml
from pathlib import Path
from unittest.mock import MagicMock, patch

from flowtask.engine.runner import (
    Runner, Playbook, PlaybookResult, PlaybookError,
    TaskDef, TaskRecord,
)
from flowtask.engine.context import Context
from flowtask.engine.template import Template
from flowtask.engine.result import ModuleResult, ModuleError
from flowtask.engine.module_loader import ModuleLoader, ModuleNotFoundError
from flowtask.modules.base import BaseModule, param


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

    (inv / "secrets.yml").write_text(yaml.dump({
        "smb_user": "admin",
        "smb_pass": "secret123",
    }))

    return inv


@pytest.fixture
def context(tmp_inventory):
    """Загруженный контекст."""
    return Context.from_inventory(tmp_inventory)


@pytest.fixture
def sample_playbook(tmp_path, tmp_inventory):
    """Создаёт простой playbook файл."""
    pb = tmp_path / "playbook.yml"
    pb.write_text(yaml.dump({
        "name": "Test Playbook",
        "inventory": str(tmp_inventory),
        "tasks": [
            {
                "name": "Task 1 — copy",
                "module": "copy",
                "params": {
                    "src": "/tmp/src",
                    "dest": "{{ vars.out_dir }}",
                },
                "tags": ["files"],
            },
            {
                "name": "Task 2 — delete",
                "module": "delete",
                "params": {
                    "path": "/tmp/old",
                },
                "tags": ["cleanup"],
            },
        ],
    }))
    return pb


@pytest.fixture
def mock_module_cls():
    """Мок-модуль для тестов."""
    class MockModule(BaseModule):
        name = "mock_module"
        description = "Mock module for testing"

        value: str = param(required=True, help="Test value")

        def run(self) -> ModuleResult:
            return ModuleResult.ok(f"mock: {self.value}", data={"value": self.value})

    return MockModule


# ============================================================
# TaskDef
# ============================================================

class TestTaskDef:

    def test_from_dict_minimal(self):
        """Минимальное определение задачи."""
        td = TaskDef.from_dict({"module": "copy"})
        assert td.name == "copy"
        assert td.module == "copy"
        assert td.params == {}
        assert td.when is None
        assert not td.ignore_errors

    def test_from_dict_full(self):
        """Полное определение задачи."""
        td = TaskDef.from_dict({
            "name": "Mount SMB",
            "module": "mount_smb",
            "params": {"server": "192.168.0.8"},
            "when": "success",
            "register": "mount_result",
            "ignore_errors": True,
            "tags": ["smb", "network"],
        })
        assert td.name == "Mount SMB"
        assert td.module == "mount_smb"
        assert td.when == "success"
        assert td.register == "mount_result"
        assert td.ignore_errors is True
        assert td.tags == ["smb", "network"]

    def test_from_dict_with_loop(self):
        td = TaskDef.from_dict({
            "module": "copy",
            "loop": ["a", "b", "c"],
        })
        assert td.loop == ["a", "b", "c"]


# ============================================================
# Playbook
# ============================================================

class TestPlaybook:

    def test_from_dict_minimal(self):
        pb = Playbook.from_dict({"name": "Test", "tasks": []})
        assert pb.name == "Test"
        assert pb.inventory == "inventory/"
        assert pb.tasks == []

    def test_from_dict_full(self):
        pb = Playbook.from_dict({
            "name": "Deploy",
            "inventory": "config/",
            "vars": {"env": "prod"},
            "tasks": [
                {"name": "Task A", "module": "copy", "params": {"src": "/a"}},
            ],
        })
        assert pb.name == "Deploy"
        assert pb.inventory == "config/"
        assert pb.vars == {"env": "prod"}
        assert len(pb.tasks) == 1
        assert pb.tasks[0].name == "Task A"

    def test_from_file(self, sample_playbook):
        pb = Playbook.from_file(sample_playbook)
        assert pb.name == "Test Playbook"
        assert len(pb.tasks) == 2
        assert pb.tasks[0].module == "copy"
        assert pb.tasks[1].module == "delete"

    def test_from_file_not_found(self):
        with pytest.raises(FileNotFoundError, match="Playbook not found"):
            Playbook.from_file("/nonexistent/playbook.yml")

    def test_from_file_invalid_format(self, tmp_path):
        bad = tmp_path / "bad.yml"
        bad.write_text("just a string")
        with pytest.raises(ValueError, match="Invalid playbook format"):
            Playbook.from_file(bad)


# ============================================================
# TaskRecord
# ============================================================

class TestTaskRecord:

    def test_default(self):
        rec = TaskRecord(index=0, name="test", module="copy")
        assert rec.status == "pending"
        assert not rec.is_success
        assert not rec.is_error
        assert not rec.is_skipped

    def test_success(self):
        rec = TaskRecord(index=0, name="test", module="copy", status="ok")
        assert rec.is_success
        assert not rec.is_error

    def test_changed(self):
        rec = TaskRecord(index=0, name="test", module="copy", status="changed", changed=True)
        assert rec.is_success
        assert rec.changed

    def test_error(self):
        rec = TaskRecord(index=0, name="test", module="copy", status="error", error="boom")
        assert rec.is_error
        assert rec.error == "boom"

    def test_skipped(self):
        rec = TaskRecord(index=0, name="test", module="copy", status="skipped", message="when=false")
        assert rec.is_skipped


# ============================================================
# PlaybookResult
# ============================================================

class TestPlaybookResult:

    def test_success(self):
        result = PlaybookResult(
            name="Test",
            status="ok",
            total=2,
            ok=2,
        )
        assert result.success
        assert result.failed == 0

    def test_partial_failure(self):
        result = PlaybookResult(
            name="Test",
            status="error",
            total=3,
            ok=2,
            failed=1,
        )
        assert not result.success

    def test_summary_output(self):
        result = PlaybookResult(
            name="Deploy",
            total=3,
            ok=1,
            changed=1,
            skipped=0,
            failed=1,
            duration=1.5,
            records=[
                TaskRecord(index=0, name="OK task", module="copy", status="ok", duration=0.5),
                TaskRecord(index=1, name="Changed task", module="move", status="changed", changed=True, duration=0.7),
                TaskRecord(index=2, name="Failed task", module="delete", status="error", error="file not found", duration=0.3),
            ],
        )
        summary = result.summary()
        assert "PLAYBOOK: Deploy" in summary
        assert "OK task" in summary
        assert "Changed task" in summary
        assert "Failed task" in summary
        assert "file not found" in summary
        assert "Total: 3" in summary
        assert "OK: 1" in summary
        assert "Changed: 1" in summary
        assert "Failed: 1" in summary


# ============================================================
# Runner — when conditions
# ============================================================

class TestWhenConditions:

    def _make_runner(self, context, mock_module_cls):
        """Создать Runner с моком loader."""
        runner = Runner.__new__(Runner)
        runner._playbook_path = Path("test.yml")
        runner._inventory_dir = None
        runner._dry_run = False
        runner._verbose = False
        runner._stop_on_error = True
        runner._limit = None
        runner._tags = None
        runner._skip_tags = None
        runner._extra_modules_dirs = None
        runner._playbook = Playbook(name="test", tasks=[])
        runner._context = context
        runner._template = Template(context)
        runner._loader = MagicMock()
        runner._loader.get.return_value = mock_module_cls
        runner._prev_result = None
        return runner

    def test_when_none(self, context, mock_module_cls):
        """when=None → задача выполняется."""
        runner = self._make_runner(context, mock_module_cls)
        task = TaskDef(name="test", module="mock_module", params={"value": "hello"})
        should, reason = runner._should_run(task)
        assert should is True
        assert reason == ""

    def test_when_true(self, context, mock_module_cls):
        runner = self._make_runner(context, mock_module_cls)
        task = TaskDef(name="test", module="mock_module", when=True)
        should, _ = runner._should_run(task)
        assert should is True

    def test_when_false(self, context, mock_module_cls):
        runner = self._make_runner(context, mock_module_cls)
        task = TaskDef(name="test", module="mock_module", when=False)
        should, reason = runner._should_run(task)
        assert should is False
        assert "when=false" in reason

    def test_when_always(self, context, mock_module_cls):
        runner = self._make_runner(context, mock_module_cls)
        runner._prev_result = ModuleResult.error("boom")
        task = TaskDef(name="test", module="mock_module", when="always")
        should, _ = runner._should_run(task)
        assert should is True

    def test_when_success_after_ok(self, context, mock_module_cls):
        runner = self._make_runner(context, mock_module_cls)
        runner._prev_result = ModuleResult.ok("done")
        task = TaskDef(name="test", module="mock_module", when="success")
        should, _ = runner._should_run(task)
        assert should is True

    def test_when_success_after_error(self, context, mock_module_cls):
        runner = self._make_runner(context, mock_module_cls)
        runner._prev_result = ModuleResult.error("fail")
        task = TaskDef(name="test", module="mock_module", when="success")
        should, _ = runner._should_run(task)
        assert should is False

    def test_when_failure_after_ok(self, context, mock_module_cls):
        runner = self._make_runner(context, mock_module_cls)
        runner._prev_result = ModuleResult.ok("done")
        task = TaskDef(name="test", module="mock_module", when="failure")
        should, _ = runner._should_run(task)
        assert should is False

    def test_when_failure_after_error(self, context, mock_module_cls):
        runner = self._make_runner(context, mock_module_cls)
        runner._prev_result = ModuleResult.error("fail")
        task = TaskDef(name="test", module="mock_module", when="failure")
        should, _ = runner._should_run(task)
        assert should is True

    def test_when_changed_after_changed(self, context, mock_module_cls):
        runner = self._make_runner(context, mock_module_cls)
        runner._prev_result = ModuleResult.changed("updated", data={"count": 1})
        task = TaskDef(name="test", module="mock_module", when="changed")
        should, _ = runner._should_run(task)
        assert should is True

    def test_when_changed_after_ok(self, context, mock_module_cls):
        runner = self._make_runner(context, mock_module_cls)
        runner._prev_result = ModuleResult.ok("no change")
        task = TaskDef(name="test", module="mock_module", when="changed")
        should, _ = runner._should_run(task)
        assert should is False

    def test_when_success_first_task(self, context, mock_module_cls):
        """Первая задача с when=success всегда выполняется."""
        runner = self._make_runner(context, mock_module_cls)
        runner._prev_result = None
        task = TaskDef(name="first", module="mock_module", when="success")
        should, _ = runner._should_run(task)
        assert should is True

    def test_when_success_after_changed(self, context, mock_module_cls):
        """changed тоже считается success."""
        runner = self._make_runner(context, mock_module_cls)
        runner._prev_result = ModuleResult.changed("updated")
        task = TaskDef(name="after_changed", module="mock_module", when="success")
        should, _ = runner._should_run(task)
        assert should is True


# ============================================================
# Runner — filter tasks
# ============================================================

class TestFilterTasks:

    def _make_runner(self, **kwargs):
        runner = Runner.__new__(Runner)
        runner._playbook_path = Path("test.yml")
        runner._inventory_dir = None
        runner._dry_run = False
        runner._verbose = False
        runner._stop_on_error = True
        runner._limit = kwargs.get("limit")
        runner._tags = set(kwargs["tags"]) if "tags" in kwargs else None
        runner._skip_tags = set(kwargs["skip_tags"]) if "skip_tags" in kwargs else None
        runner._extra_modules_dirs = None
        runner._context = None
        runner._template = None
        runner._loader = None
        runner._prev_result = None
        return runner

    def test_no_filter(self):
        runner = self._make_runner()
        tasks = [
            TaskDef(name="Task A", module="copy"),
            TaskDef(name="Task B", module="move"),
        ]
        assert len(runner._filter_tasks(tasks)) == 2

    def test_limit_match(self):
        runner = self._make_runner(limit="copy")
        tasks = [
            TaskDef(name="Copy files", module="copy"),
            TaskDef(name="Delete files", module="delete"),
        ]
        filtered = runner._filter_tasks(tasks)
        assert len(filtered) == 1
        assert filtered[0].module == "copy"

    def test_limit_no_match(self):
        runner = self._make_runner(limit="nonexistent")
        tasks = [TaskDef(name="Task A", module="copy")]
        assert len(runner._filter_tasks(tasks)) == 0

    def test_tags_filter(self):
        runner = self._make_runner(tags=["smb"])
        tasks = [
            TaskDef(name="Mount", module="mount_smb", tags=["smb"]),
            TaskDef(name="Copy", module="copy", tags=["files"]),
            TaskDef(name="Sync", module="copy", tags=["smb", "files"]),
        ]
        filtered = runner._filter_tasks(tasks)
        assert len(filtered) == 2

    def test_skip_tags(self):
        runner = self._make_runner(skip_tags=["slow"])
        tasks = [
            TaskDef(name="Fast", module="copy", tags=["fast"]),
            TaskDef(name="Slow", module="archive", tags=["slow"]),
            TaskDef(name="Both", module="move", tags=["fast", "slow"]),
        ]
        filtered = runner._filter_tasks(tasks)
        assert len(filtered) == 1
        assert filtered[0].name == "Fast"

    def test_tags_and_skip_tags_combined(self):
        runner = self._make_runner(tags=["deploy"], skip_tags=["slow"])
        tasks = [
            TaskDef(name="A", module="copy", tags=["deploy"]),
            TaskDef(name="B", module="copy", tags=["deploy", "slow"]),
            TaskDef(name="C", module="copy", tags=["test"]),
        ]
        filtered = runner._filter_tasks(tasks)
        assert len(filtered) == 1
        assert filtered[0].name == "A"


# ============================================================
# Runner — execute task
# ============================================================

class TestExecuteTask:

    def _make_runner(self, context, loader):
        """Создать Runner с указанным loader."""
        runner = Runner.__new__(Runner)
        runner._playbook_path = Path("test.yml")
        runner._inventory_dir = None
        runner._dry_run = False
        runner._verbose = False
        runner._stop_on_error = True
        runner._limit = None
        runner._tags = None
        runner._skip_tags = None
        runner._extra_modules_dirs = None
        runner._playbook = Playbook(name="test", tasks=[])
        runner._context = context
        runner._template = Template(context)
        runner._loader = loader
        runner._prev_result = None
        return runner

    def test_execute_python_module(self, context, mock_module_cls):
        loader = MagicMock()
        loader.get.return_value = mock_module_cls

        runner = self._make_runner(context, loader)
        task = TaskDef(name="Test mock", module="mock_module", params={"value": "hello"})

        record = runner._execute_task(0, task)

        assert record.status == "ok"
        assert not record.changed
        assert record.message == "mock: hello"
        assert record.data == {"value": "hello"}
        assert record.duration > 0

    def test_execute_with_template(self, context, mock_module_cls):
        """Параметры шаблонизируются перед передачей в модуль."""
        loader = MagicMock()
        loader.get.return_value = mock_module_cls

        runner = self._make_runner(context, loader)
        task = TaskDef(
            name="Test template",
            module="mock_module",
            params={"value": "{{ vars.smb_server }}"},
        )

        record = runner._execute_task(0, task)

        assert record.status == "ok"
        assert record.message == "mock: 192.168.0.8"
        assert record.params["value"] == "192.168.0.8"

    def test_execute_skipped(self, context, mock_module_cls):
        loader = MagicMock()

        runner = self._make_runner(context, loader)
        task = TaskDef(name="Skipped", module="mock_module", when=False)

        record = runner._execute_task(0, task)

        assert record.is_skipped
        assert "when=false" in record.message
        # Модуль не должен был вызываться
        loader.get.assert_not_called()

    def test_execute_module_not_found(self, context):
        loader = MagicMock()
        loader.get.side_effect = ModuleNotFoundError("Module 'xyz' not found")

        runner = self._make_runner(context, loader)
        task = TaskDef(name="Bad module", module="xyz")

        record = runner._execute_task(0, task)

        assert record.is_error
        assert "xyz" in record.error

    def test_execute_exception(self, context):
        """Неожиданное исключение в модуле."""
        class BrokenModule(BaseModule):
            name = "broken"
            def run(self) -> ModuleResult:
                raise RuntimeError("unexpected crash")

        loader = MagicMock()
        loader.get.return_value = BrokenModule

        runner = self._make_runner(context, loader)
        task = TaskDef(name="Broken", module="broken")

        record = runner._execute_task(0, task)

        assert record.is_error
        assert "RuntimeError" in record.error

    def test_execute_register(self, context, mock_module_cls):
        """Результат сохраняется в context через register."""
        loader = MagicMock()
        loader.get.return_value = mock_module_cls

        runner = self._make_runner(context, loader)
        task = TaskDef(
            name="Register test",
            module="mock_module",
            params={"value": "hello"},
            register="my_result",
        )

        record = runner._execute_task(0, task)

        assert record.status == "ok"
        # Проверяем, что результат сохранён в context
        reg_data = context.get("my_result")
        assert reg_data is not None
        assert reg_data["status"] == "ok"
        assert reg_data["data"]["value"] == "hello"

    def test_execute_dry_run(self, context, mock_module_cls):
        """Dry-run не вызывает run()."""
        loader = MagicMock()
        loader.get.return_value = mock_module_cls

        runner = self._make_runner(context, loader)
        runner._dry_run = True
        task = TaskDef(name="Dry run", module="mock_module", params={"value": "test"})

        record = runner._execute_task(0, task)

        # В dry-run execute() возвращает ok со специальным сообщением
        assert record.status == "ok"
        assert "DRY-RUN" in record.message


# ============================================================
# Runner — full run
# ============================================================

class TestRunnerRun:

    def test_run_simple_playbook(self, sample_playbook, tmp_path):
        """Полный цикл выполнения playbook с реальными модулями."""
        # Создаём файлы для модулей copy/delete
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "file1.txt").write_text("test")

        pb_data = yaml.safe_load(sample_playbook.read_text())
        # Меняем src/dest на реальные пути
        pb_data["tasks"][0]["params"]["src"] = str(src_dir)
        pb_data["tasks"][0]["params"]["dest"] = str(tmp_path / "dest")
        pb_data["tasks"][1]["params"]["path"] = str(tmp_path / "old_file")
        (tmp_path / "old_file").write_text("old")

        modified_pb = tmp_path / "modified_playbook.yml"
        modified_pb.write_text(yaml.dump(pb_data))

        runner = Runner(
            playbook_path=modified_pb,
            dry_run=False,
            verbose=True,
            stop_on_error=True,
        )

        result = runner.run()

        assert result.name == "Test Playbook"
        assert result.total == 2
        assert result.failed == 0
        assert result.success
        assert result.duration > 0
        assert len(result.records) == 2

    def test_run_with_stop_on_error(self, sample_playbook, tmp_path):
        """stop_on_error останавливает после первой ошибки."""
        pb_data = {
            "name": "Stop on Error",
            "inventory": str(sample_playbook.parent / "inventory"),
            "tasks": [
                {
                    "name": "Good task",
                    "module": "copy",
                    "params": {
                        "src": str(tmp_path / "nonexistent_source"),
                        "dest": str(tmp_path / "dest"),
                    },
                },
                {
                    "name": "Never reached",
                    "module": "delete",
                    "params": {"path": str(tmp_path / "x")},
                },
            ],
        }

        pb = tmp_path / "stop_error.yml"
        pb.write_text(yaml.dump(pb_data))

        runner = Runner(playbook_path=pb, stop_on_error=True)
        result = runner.run()

        # Первая задача может выполниться (copy с glob, вернёт ok если нет файлов, 
        # или changed если файлы есть). Проверяем что 2-я задача не выполнялась.
        # Но copy вернёт ok (no files matched) — проверяем что выполнены обе
        # Вместо этого используем несуществующий модуль
        pb_data["tasks"][0]["module"] = "nonexistent_module_xyz"
        pb.write_text(yaml.dump(pb_data))

        runner = Runner(playbook_path=pb, stop_on_error=True)
        result = runner.run()

        assert result.failed >= 1
        # stop_on_error=true → только 1 задача выполнена (ошибка)
        assert result.total == 1

    def test_run_with_ignore_errors(self, tmp_path, tmp_inventory):
        """ignore_errors позволяет продолжить после ошибки."""
        # Создаём валидный src для 2-й задачи
        valid_src = tmp_path / "valid_src"
        valid_src.mkdir()
        (valid_src / "file.txt").write_text("data")

        pb_data = {
            "name": "Ignore Errors",
            "inventory": str(tmp_inventory),
            "tasks": [
                {
                    "name": "Failing task",
                    "module": "nonexistent_module_xyz",
                    "ignore_errors": True,
                },
                {
                    "name": "Next task",
                    "module": "copy",
                    "params": {
                        "src": str(valid_src),
                        "dest": str(tmp_path / "dest"),
                    },
                },
            ],
        }

        pb = tmp_path / "ignore_errors.yml"
        pb.write_text(yaml.dump(pb_data))

        runner = Runner(playbook_path=pb, stop_on_error=True)
        result = runner.run()

        # Обе задачи выполнены (первая с ошибкой, но игнорированной)
        assert result.total == 2
        assert result.failed == 1  # первая задача — error
        # Вторая задача — ok/changed (файл скопирован)
        assert result.ok + result.changed == 1

    def test_run_dry_run(self, sample_playbook):
        """Dry-run не выполняет модули."""
        runner = Runner(playbook_path=sample_playbook, dry_run=True)
        result = runner.run()

        assert result.total == 2
        assert result.failed == 0
        # Все задачи должны быть ok с DRY-RUN в сообщении
        for rec in result.records:
            assert "DRY-RUN" in rec.message

    def test_run_limit(self, sample_playbook):
        """limit фильтрует задачи по имени."""
        runner = Runner(playbook_path=sample_playbook, limit="copy")
        result = runner.run()

        assert result.total == 1
        assert "copy" in result.records[0].name.lower()

    def test_run_tags(self, sample_playbook):
        """tags фильтрует задачи по тегам."""
        runner = Runner(playbook_path=sample_playbook, tags=["files"])
        result = runner.run()

        assert result.total == 1
        assert result.records[0].module == "copy"

    def test_run_when_failure(self, tmp_path, tmp_inventory):
        """when=failure → задача выполняется только после ошибки."""
        cleanup_ran = []

        class CleanupModule(BaseModule):
            name = "cleanup"
            def run(self) -> ModuleResult:
                cleanup_ran.append(True)
                return ModuleResult.ok("cleaned")

        # Создаём модуль в отдельном файле в tmp_path
        modules_dir = tmp_path / "my_modules"
        modules_dir.mkdir()
        (modules_dir / "cleanup.py").write_text(
            'from flowtask.modules.base import BaseModule, param\n'
            'from flowtask.engine.result import ModuleResult\n'
            'class Cleanup(BaseModule):\n'
            '    name = "cleanup"\n'
            '    def run(self):\n'
            '        return ModuleResult.ok("cleaned")\n'
        )

        # Создаём playbook с несуществующим модулем и cleanup при failure
        pb_data = {
            "name": "When Failure Test",
            "inventory": str(tmp_inventory),
            "tasks": [
                {
                    "name": "This will fail",
                    "module": "nonexistent_module_xyz",
                    "ignore_errors": True,
                },
                {
                    "name": "Cleanup on failure",
                    "module": "cleanup",
                    "when": "failure",
                },
                {
                    "name": "Should not run",
                    "module": "cleanup",
                    "when": "success",
                },
            ],
        }

        pb = tmp_path / "when_failure.yml"
        pb.write_text(yaml.dump(pb_data))

        runner = Runner(
            playbook_path=pb,
            stop_on_error=False,
            extra_modules_dirs=[str(modules_dir)],
        )

        result = runner.run()

        # Первая задача — error (ignore_errors=true → продолжаем)
        # Вторая — when=failure → должна выполниться (prev=error) → ok
        # Третья — when=success → prev теперь ok (от cleanup) → тоже выполняется
        statuses = [r.status for r in result.records]
        assert statuses[0] == "error"
        assert statuses[1] == "ok"  # cleanup ran
        assert statuses[2] == "ok"  # prev=ok (from cleanup), so success=true


# ============================================================
# Runner — validate
# ============================================================

class TestValidate:

    def test_valid_playbook(self, sample_playbook):
        runner = Runner(playbook_path=sample_playbook)
        errors = runner.validate()
        assert errors == []

    def test_missing_module(self, tmp_path, tmp_inventory):
        pb_data = {
            "name": "Bad",
            "inventory": str(tmp_inventory),
            "tasks": [
                {"name": "Bad", "module": "totally_nonexistent_xyz_module"},
            ],
        }
        pb = tmp_path / "bad.yml"
        pb.write_text(yaml.dump(pb_data))

        runner = Runner(playbook_path=pb)
        errors = runner.validate()
        assert any("totally_nonexistent" in e for e in errors)

    def test_missing_playbook(self):
        runner = Runner(playbook_path="/nonexistent/playbook.yml")
        errors = runner.validate()
        assert any("not found" in e.lower() for e in errors)

    def test_missing_inventory(self, tmp_path):
        # Явно указываем несуществующий inventory
        pb_data = {"name": "No Inv", "inventory": str(tmp_path / "nonexistent_inv"), "tasks": [{"name": "T", "module": "copy"}]}
        pb = tmp_path / "noinv.yml"
        pb.write_text(yaml.dump(pb_data))

        runner = Runner(playbook_path=pb)
        errors = runner.validate()
        assert any("Inventory" in e for e in errors)


# ============================================================
# Playbook vars override
# ============================================================

class TestPlaybookVars:

    def test_playbook_vars_override_inventory(self, tmp_path, tmp_inventory):
        """vars из playbook перекрывают inventory."""
        pb_data = {
            "name": "Vars Override",
            "inventory": str(tmp_inventory),
            "vars": {
                "out_dir": "/custom/path",  # override inventory
            },
            "tasks": [
                {
                    "name": "Check override",
                    "module": "copy",
                    "params": {
                        "src": str(tmp_path / "x"),
                        "dest": "{{ vars.out_dir }}",
                    },
                },
            ],
        }

        pb = tmp_path / "vars_override.yml"
        pb.write_text(yaml.dump(pb_data))

        runner = Runner(playbook_path=pb, dry_run=True)
        result = runner.run()

        assert result.total == 1
        # В dry-run параметр должен быть /custom/path (override)
        assert result.records[0].params["dest"] == "/custom/path"

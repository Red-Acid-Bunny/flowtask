"""
Тесты для BaseModule, ModuleLoader, BashModuleAdapter.
"""

import json
import pytest
import subprocess
from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

from flowtask.modules.base import BaseModule, param, ParamDescriptor
from flowtask.engine.module_loader import ModuleLoader, ModuleNotFoundError
from flowtask.engine.bash_adapter import BashModuleAdapter
from flowtask.engine.result import ModuleResult


# ============================================================
# Test Python modules for loader tests
# ============================================================

class _DummyModule(BaseModule):
    """Test module — does nothing."""
    src: str = param(required=True, help="Source path")
    dest: str = param(default="/tmp/", help="Destination")

    def run(self) -> ModuleResult:
        return ModuleResult.ok(f"copied {self.src} to {self.dest}")


class _SimpleModule(BaseModule):
    """Test module with simple attributes."""
    message: str = "hello"

    def run(self) -> ModuleResult:
        return ModuleResult.ok(self.message)


class _DefaultModule(BaseModule):
    """Module with all defaults."""
    value: int = 42
    flag: bool = True

    def run(self) -> ModuleResult:
        return ModuleResult.ok(f"value={self.value}")


# ============================================================
# BaseModule
# ============================================================

class TestBaseModule:

    def test_basic_init(self):
        m = _DummyModule(src="/a", dest="/b")
        assert m.src == "/a"
        assert m.dest == "/b"

    def test_default_value(self):
        m = _DummyModule(src="/a")  # dest has default
        assert m.dest == "/tmp/"

    def test_missing_required(self):
        m = _DummyModule()  # no src
        errors = m.validate_params()
        assert len(errors) == 1
        assert "src" in errors[0]

    def test_module_name_from_class(self):
        m = _DummyModule(src="/a")
        assert m.module_name == "dummy_module"

    def test_custom_name(self):
        class NamedModule(BaseModule):
            name = "my_custom"

            def run(self) -> ModuleResult:
                return ModuleResult.ok()

        assert NamedModule().module_name == "my_custom"

    def test_param_schema(self):
        schema = _DummyModule(src="/a").param_schema
        assert "src" in schema
        assert schema["src"]["required"] is True
        assert "dest" in schema
        assert schema["dest"]["required"] is False
        assert schema["dest"]["default"] == "/tmp/"

    def test_execute_success(self):
        m = _DummyModule(src="/a", dest="/b")
        result = m.execute()
        assert result.is_ok
        assert "copied" in result.message

    def test_execute_dry_run(self):
        m = _DummyModule(src="/a")
        result = m.execute(dry_run=True)
        assert result.is_ok
        assert "DRY-RUN" in result.message
        assert not result.changed

    def test_execute_validation_error(self):
        m = _DummyModule()  # missing required src
        result = m.execute()
        assert result.is_error
        assert "Missing" in result.message

    def test_simple_attributes(self):
        m = _SimpleModule(message="test")
        assert m.message == "test"
        result = m.execute()
        assert result.is_ok
        assert result.message == "test"

    def test_default_attributes(self):
        m = _DefaultModule()
        assert m.value == 42
        assert m.flag is True


class TestParamDescriptor:

    def test_required(self):
        p = param(required=True, help="test")
        assert p.required is True

    def test_with_default(self):
        p = param(default="hello")
        assert p.required is False  # auto: default → not required
        assert p.default == "hello"

    def test_with_default_and_not_required(self):
        p = param(default="hello", required=False, help="test")
        assert p.required is False
        assert p.default == "hello"


# ============================================================
# ModuleLoader
# ============================================================

class TestModuleLoader:

    def test_discover_builtin(self):
        """Must discover modules from flowtask/modules/"""
        loader = ModuleLoader()
        # На этом этапе встроенных модулей нет (их добавим в задаче 4)
        # но loader должен работать без ошибок
        modules = loader.discover()
        assert isinstance(modules, list)

    def test_register_python_module(self, tmp_path):
        """Регистрация Python-модуля из пользовательской директории."""
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()

        (modules_dir / "test_mod.py").write_text(dedent(f'''
            from flowtask.modules.base import BaseModule
            from flowtask.engine.result import ModuleResult

            class TestMod(BaseModule):
                def run(self) -> ModuleResult:
                    return ModuleResult.ok("test")
        '''))

        loader = ModuleLoader(extra_modules_dirs=[str(modules_dir)])
        found = loader.discover()

        assert "test_mod" in found
        assert loader.has("test_mod")

    def test_register_bash_module(self, tmp_path):
        """Регистрация bash-модуля."""
        bash_dir = tmp_path / "bash"
        bash_dir.mkdir()

        (bash_dir / "hello.sh").write_text(dedent('''
            #!/bin/bash
            input=$(cat)
            echo '{"status":"ok","message":"hello","changed":false}'
        '''))

        loader = ModuleLoader(extra_modules_dirs=[tmp_path])
        found = loader.discover()

        assert "hello" in found
        assert loader.has("hello")
        mod = loader.get("hello")
        assert isinstance(mod, BashModuleAdapter)

    def test_get_not_found(self):
        loader = ModuleLoader()
        with pytest.raises(ModuleNotFoundError):
            loader.get("nonexistent_module")

    def test_list_modules(self, tmp_path):
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        (modules_dir / "bash").mkdir()

        (modules_dir / "py_mod.py").write_text(dedent('''
            from flowtask.modules.base import BaseModule
            from flowtask.engine.result import ModuleResult
            class PyMod(BaseModule):
                def run(self) -> ModuleResult:
                    return ModuleResult.ok()
        '''))
        (modules_dir / "bash" / "sh_mod.sh").write_text(
            'input=$(cat); echo \'{"status":"ok"}\''
        )

        loader = ModuleLoader(extra_modules_dirs=[str(modules_dir)])
        loader.discover()
        mod_list = loader.list_modules()

        assert "py_mod" in mod_list
        assert mod_list["py_mod"] == "python"
        assert "sh_mod" in mod_list
        assert mod_list["sh_mod"] == "bash"

    def test_skip_underscore_files(self, tmp_path):
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()

        (modules_dir / "_internal.py").write_text('# should be skipped')
        (modules_dir / "bash").mkdir()
        (modules_dir / "bash" / "_helper.sh").write_text('# should be skipped')

        loader = ModuleLoader(extra_modules_dirs=[str(modules_dir)])
        found = loader.discover()
        assert "_internal" not in found
        assert "_helper" not in found

    def test_discover_extra_dirs(self, tmp_path):
        """Множественные дополнительных директорий."""
        dir1 = tmp_path / "plugins1"
        dir1.mkdir()
        dir2 = tmp_path / "plugins2"
        dir2.mkdir()

        (dir1 / "mod_a.py").write_text(dedent('''
            from flowtask.modules.base import BaseModule
            from flowtask.engine.result import ModuleResult
            class ModA(BaseModule):
                def run(self) -> ModuleResult:
                    return ModuleResult.ok()
        '''))
        (dir2 / "mod_b.py").write_text(dedent('''
            from flowtask.modules.base import BaseModule
            from flowtask.engine.result import ModuleResult
            class ModB(BaseModule):
                def run(self) -> ModuleResult:
                    return ModuleResult.ok()
        '''))

        loader = ModuleLoader(extra_modules_dirs=[str(dir1), str(dir2)])
        found = loader.discover()
        assert "mod_a" in found
        assert "mod_b" in found


# ============================================================
# BashModuleAdapter
# ============================================================

class TestBashModuleAdapter:

    def test_successful_execution(self, tmp_path):
        script = tmp_path / "ok.sh"
        script.write_text('echo \'{"status":"ok","message":"done","changed":true}\'\n')

        adapter = BashModuleAdapter(script)
        result = adapter.execute(params={"key": "val"})
        assert result.is_ok
        assert result.changed
        assert result.message == "done"

    def test_error_execution(self, tmp_path):
        script = tmp_path / "err.sh"
        script.write_text(
            '>&2 echo "[ERROR] something failed"\n'
            'echo \'{"status":"error","message":"something failed"}\'\n'
            'exit 1\n'
        )

        adapter = BashModuleAdapter(script)
        result = adapter.execute()
        assert result.is_error
        assert "failed" in result.message

    def test_dry_run_flag(self, tmp_path):
        script = tmp_path / "dry.sh"
        script.write_text(dedent('''
            input=$(cat)
            dry=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['dry_run'])")
            if [ "$dry" = "True" ]; then
                echo '{"status":"ok","message":"dry-run","changed":false}'
            else
                echo '{"status":"ok","message":"real","changed":true}'
            fi
        '''))

        adapter = BashModuleAdapter(script)

        result_dry = adapter.execute(dry_run=True)
        assert "dry-run" in result_dry.message
        assert not result_dry.changed

        result_real = adapter.execute(dry_run=False)
        assert "real" in result_real.message
        assert result_real.changed

    def test_params_passed(self, tmp_path):
        script = tmp_path / "params.sh"
        script.write_text(dedent('''
            input=$(cat)
            echo "$input" | python3 -c "
import sys, json
data = json.load(sys.stdin)
server = data['params']['server']
print(json.dumps({'status': 'ok', 'message': server, 'data': {'server': server}}))
"
        '''))

        adapter = BashModuleAdapter(script)
        result = adapter.execute(params={"server": "192.168.1.1"})
        assert result.data["server"] == "192.168.1.1"

    def test_stderr_captured(self, tmp_path, caplog):
        import logging
        script = tmp_path / "log.sh"
        script.write_text(
            '>&2 echo "[INFO] step 1"\n'
            '>&2 echo "[INFO] step 2"\n'
            'echo \'{"status":"ok","message":"done"}\'\n'
        )

        adapter = BashModuleAdapter(script)
        with caplog.at_level(logging.DEBUG, logger="flowtask.bash_adapter"):
            adapter.execute()

        assert "step 1" in caplog.text
        assert "step 2" in caplog.text

    def test_no_output(self, tmp_path):
        script = tmp_path / "empty.sh"
        script.write_text('# empty script\n')

        adapter = BashModuleAdapter(script)
        result = adapter.execute()
        assert result.is_error
        assert "no output" in result.message

    def test_invalid_json(self, tmp_path):
        script = tmp_path / "badjson.sh"
        script.write_text('echo "not json at all"\n')

        adapter = BashModuleAdapter(script)
        result = adapter.execute()
        assert result.is_error
        assert "invalid JSON" in result.message

    def test_timeout(self, tmp_path):
        script = tmp_path / "slow.sh"
        script.write_text('sleep 10\n')

        adapter = BashModuleAdapter(script)
        result = adapter.execute(timeout=1)
        assert result.is_error
        assert "timed out" in result.message

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            BashModuleAdapter(Path("/nonexistent/script.sh"))

    def test_adapter_repr(self, tmp_path):
        script = tmp_path / "test.sh"
        script.write_text('echo ok')
        adapter = BashModuleAdapter(script)
        assert "test" in repr(adapter)

    def test_become_uses_sudo(self, tmp_path):
        """become=True добавляет sudo к команде."""
        import subprocess
        script = tmp_path / "whoami.sh"
        script.write_text(dedent('''
            input=$(cat)
            whoami
            echo '{"status":"ok","message":"done","changed":true}'
        '''))

        adapter = BashModuleAdapter(script)
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["sudo", "-S", "bash", str(script)],
                returncode=0,
                stdout='{"status":"ok","message":"done","changed":true}',
                stderr="",
            )
            result = adapter.execute(become=True, become_pass="testpass")

            call_args = mock_run.call_args
            assert call_args[0][0][0] == "sudo"
            assert call_args[0][0][1] == "-S"

    def test_become_password_passed_via_pipe(self, tmp_path):
        """become_pass передаётся через stdin."""
        import subprocess
        script = tmp_path / "read_stdin.sh"
        script.write_text(dedent('''
            read -r first_line
            echo '{"status":"ok","message":"got_password","changed":true}'
        '''))

        adapter = BashModuleAdapter(script)
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["sudo", "-S", "bash", str(script)],
                returncode=0,
                stdout='{"status":"ok","message":"got_password","changed":true}',
                stderr="",
            )
            result = adapter.execute(become=True, become_pass="testpass123")

            call_kwargs = mock_run.call_args[1]
            assert "testpass123" in call_kwargs["input"]
            assert result.is_ok
            assert result.message == "got_password"

    def test_password_not_logged(self, tmp_path, caplog):
        """Пароль sudo НИКОГДА не попадает в логи."""
        import logging
        import subprocess
        script = tmp_path / "safe.sh"
        script.write_text(dedent('''
            input=$(cat)
            echo '{"status":"ok","message":"done","changed":true}'
        '''))

        adapter = BashModuleAdapter(script)
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["sudo", "-S", "bash", str(script)],
                returncode=0,
                stdout='{"status":"ok","message":"done","changed":true}',
                stderr="",
            )
            with caplog.at_level(logging.DEBUG, logger="flowtask.bash_adapter"):
                adapter.execute(become=True, become_pass="super_secret_password_123")

        assert "super_secret_password_123" not in caplog.text
        assert "become" in caplog.text.lower()

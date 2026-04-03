"""
Тесты для CLI — argparse entry point.
"""

import pytest
from pathlib import Path
from unittest.mock import patch

from flowtask.cli import (
    main, build_parser, cmd_run, cmd_validate,
    cmd_list_modules, cmd_version, _setup_logging,
)
from flowtask import __version__


# ============================================================
# Parser tests
# ============================================================

class TestBuildParser:

    def test_parser_exists(self):
        parser = build_parser()
        assert parser.prog == "flowtask"

    def test_run_command(self):
        parser = build_parser()
        args = parser.parse_args(["run", "playbook.yml"])
        assert args.command == "run"
        assert args.playbook == "playbook.yml"
        assert args.dry_run is False
        assert args.verbose is False

    def test_run_dry_run(self):
        parser = build_parser()
        args = parser.parse_args(["run", "playbook.yml", "--dry-run"])
        assert args.dry_run is True

    def test_run_short_flags(self):
        parser = build_parser()
        args = parser.parse_args(["run", "pb.yml", "-n", "-v"])
        assert args.dry_run is True
        assert args.verbose is True

    def test_run_inventory(self):
        parser = build_parser()
        args = parser.parse_args(["run", "pb.yml", "-i", "custom_inv/"])
        assert args.inventory == "custom_inv/"

    def test_run_limit(self):
        parser = build_parser()
        args = parser.parse_args(["run", "pb.yml", "--limit", "mount"])
        assert args.limit == "mount"

    def test_run_limit_short(self):
        parser = build_parser()
        args = parser.parse_args(["run", "pb.yml", "-l", "copy"])
        assert args.limit == "copy"

    def test_run_tags(self):
        parser = build_parser()
        args = parser.parse_args(["run", "pb.yml", "--tags", "smb", "network"])
        assert args.tags == ["smb", "network"]

    def test_run_skip_tags(self):
        parser = build_parser()
        args = parser.parse_args(["run", "pb.yml", "--skip-tags", "slow"])
        assert args.skip_tags == ["slow"]

    def test_run_continue_on_error(self):
        parser = build_parser()
        args = parser.parse_args(["run", "pb.yml", "--continue-on-error"])
        assert args.continue_on_error is True

    def test_validate_command(self):
        parser = build_parser()
        args = parser.parse_args(["validate", "playbook.yml"])
        assert args.command == "validate"
        assert args.playbook == "playbook.yml"

    def test_list_modules_command(self):
        parser = build_parser()
        args = parser.parse_args(["list-modules"])
        assert args.command == "list-modules"

    def test_list_modules_verbose(self):
        parser = build_parser()
        args = parser.parse_args(["list-modules", "--verbose"])
        assert args.verbose is True

    def test_version_command(self):
        parser = build_parser()
        args = parser.parse_args(["version"])
        assert args.command == "version"

    def test_no_command(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.command is None

    def test_version_flag(self):
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--version"])
        assert exc_info.value.code == 0


# ============================================================
# main() tests
# ============================================================

class TestMain:

    def test_no_command_returns_0(self):
        ret = main([])
        assert ret == 0

    def test_version_command(self, capsys):
        ret = main(["version"])
        assert ret == 0
        assert __version__ in capsys.readouterr().out

    def test_run_nonexistent_playbook(self, capsys):
        ret = main(["run", "/nonexistent/playbook.yml"])
        assert ret == 1

    def test_validate_nonexistent_playbook(self, capsys):
        ret = main(["validate", "/nonexistent/playbook.yml"])
        assert ret == 1


# ============================================================
# cmd_run integration tests
# ============================================================

class TestCmdRun:

    def test_run_simple_playbook(self, tmp_path, capsys):
        """Полный запуск playbook через CLI."""
        import yaml

        # Создаём inventory
        inv = tmp_path / "inventory"
        inv.mkdir()
        (inv / "vars.yml").write_text(yaml.dump({
            "smb_server": "192.168.0.8",
            "out_dir": str(tmp_path / "out"),
        }))

        # Создаём файл для копирования
        src = tmp_path / "src_file.txt"
        src.write_text("test data")

        # Создаём playbook
        pb = tmp_path / "playbook.yml"
        pb.write_text(yaml.dump({
            "name": "CLI Test",
            "inventory": str(inv),
            "tasks": [
                {
                    "name": "Copy file",
                    "module": "copy",
                    "params": {
                        "src": str(src),
                        "dest": str(tmp_path / "dest_file.txt"),
                    },
                },
            ],
        }))

        ret = main(["run", str(pb), "-v"])
        assert ret == 0

    def test_run_dry_run(self, tmp_path, capsys):
        """Dry-run через CLI."""
        import yaml

        inv = tmp_path / "inventory"
        inv.mkdir()
        (inv / "vars.yml").write_text(yaml.dump({"key": "val"}))

        pb = tmp_path / "pb.yml"
        pb.write_text(yaml.dump({
            "name": "Dry Run Test",
            "inventory": str(inv),
            "tasks": [
                {
                    "name": "Test task",
                    "module": "copy",
                    "params": {"src": "/tmp/x", "dest": "/tmp/y"},
                },
            ],
        }))

        ret = main(["run", str(pb), "--dry-run"])
        assert ret == 0


# ============================================================
# cmd_validate tests
# ============================================================

class TestCmdValidate:

    def test_validate_valid(self, tmp_path, capsys):
        import yaml

        inv = tmp_path / "inventory"
        inv.mkdir()
        (inv / "vars.yml").write_text(yaml.dump({}))

        pb = tmp_path / "pb.yml"
        pb.write_text(yaml.dump({
            "name": "Valid",
            "inventory": str(inv),
            "tasks": [{"name": "T", "module": "copy"}],
        }))

        ret = main(["validate", str(pb)])
        assert ret == 0
        assert "valid" in capsys.readouterr().out.lower()

    def test_validate_invalid(self, tmp_path, capsys):
        import yaml

        inv = tmp_path / "inventory"
        inv.mkdir()
        (inv / "vars.yml").write_text(yaml.dump({}))

        pb = tmp_path / "bad.yml"
        pb.write_text(yaml.dump({
            "name": "Bad",
            "inventory": str(inv),
            "tasks": [{"name": "Bad module", "module": "nonexistent_xyz"}],
        }))

        ret = main(["validate", str(pb)])
        assert ret == 1


# ============================================================
# cmd_list_modules tests
# ============================================================

class TestCmdListModules:

    def test_list_modules(self, capsys):
        ret = main(["list-modules"])
        assert ret == 0
        output = capsys.readouterr().out
        assert "Python modules" in output or "modules" in output.lower()
        # Должны быть встроенные модули
        assert "copy" in output

    def test_list_modules_verbose(self, capsys):
        ret = main(["list-modules", "-v"])
        assert ret == 0
        output = capsys.readouterr().out
        # Verbose — должны быть параметры
        assert "copy" in output


# ============================================================
# _setup_logging tests
# ============================================================

class TestSetupLogging:

    def test_setup_verbose(self):
        import logging
        _setup_logging(verbose=True)
        root = logging.getLogger("flowtask")
        assert root.level == logging.DEBUG

    def test_setup_normal(self):
        import logging
        _setup_logging(verbose=False)
        root = logging.getLogger("flowtask")
        assert root.level == logging.INFO

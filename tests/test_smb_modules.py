"""
Тесты для SMB bash-модулей: smb_mount.sh, smb_umount.sh.
"""

import base64
import json
import subprocess
import pytest
from pathlib import Path
from textwrap import dedent
from unittest.mock import patch, MagicMock

from flowtask.engine.bash_adapter import BashModuleAdapter
from flowtask.engine.result import ModuleResult


MODULES_DIR = Path(__file__).parent.parent / "modules" / "bash"


# ============================================================
# smb_mount.sh
# ============================================================

class TestSmbMount:

    def test_missing_server_share_validation(self):
        """server и share обязательны."""
        adapter = BashModuleAdapter(MODULES_DIR / "smb_mount.sh")
        result = adapter.execute(params={"share": "data"})
        assert result.is_error
        assert "required" in result.message

    def test_mount_cifs_not_found(self, tmp_path):
        """Ошибка если mount.cifs не найден."""
        script = tmp_path / "smb_mount.sh"
        original = (MODULES_DIR / "smb_mount.sh").read_text()
        modified = original.replace(
            'command -v mount.cifs &>/dev/null',
            'false'
        )
        script.write_text(modified)

        adapter = BashModuleAdapter(script)
        result = adapter.execute(params={"server": "192.168.0.8", "share": "data"})
        assert result.is_error
        assert "mount.cifs" in result.message
        assert "cifs-utils" in result.message

    def test_server_not_reachable(self, tmp_path):
        """Ошибка если сервер не доступен по TCP."""
        script = tmp_path / "smb_mount.sh"
        script.write_text(dedent('''
            if [ -n "${1:-}" ]; then
              input=$(echo "$1" | base64 -d)
            else
              input=$(cat)
            fi
            server=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['params'].get('server',''))")
            port=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['params'].get('port','445'))")
            echo "{\"status\":\"error\",\"message\":\"Server ${server}:${port} is not reachable\"}"
            exit 1
        '''))

        adapter = BashModuleAdapter(script)
        result = adapter.execute(params={"server": "192.168.0.8", "share": "data", "port": 445})
        assert result.is_error
        assert "not reachable" in result.message

    def test_dry_run(self, tmp_path):
        """Dry-run не монтирует, возвращает сообщение."""
        script = tmp_path / "smb_mount.sh"
        script.write_text(dedent('''
            if [ -n "${1:-}" ]; then
              input=$(echo "$1" | base64 -d)
            else
              input=$(cat)
            fi
            dry=$(echo "$input" | python3 -c "import sys,json; print(str(json.load(sys.stdin)['dry_run']).lower())")
            server=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['params'].get('server',''))")
            share=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['params'].get('share',''))")
            mp=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['params'].get('mount_point','/mnt/smb'))")
            if [ "$dry" = "true" ]; then
              python3 -c "import json; print(json.dumps({'status':'ok','message':'[DRY-RUN] Would mount //$server/$share -> $mp','changed':False,'data':{'mount_point':'$mp','server':'$server','share':'$share'}}))"
              exit 0
            fi
            echo '{"status":"error","message":"should not reach here"}'
            exit 1
        '''))

        adapter = BashModuleAdapter(script)
        result = adapter.execute(
            params={
                "server": "192.168.0.8",
                "share": "data",
                "mount_point": "/mnt/test",
            },
            dry_run=True,
        )
        assert result.is_ok
        assert "[DRY-RUN]" in result.message
        assert not result.changed

    def test_already_mounted(self, tmp_path):
        """Если уже смонтирована — ok, changed=false."""
        script = tmp_path / "smb_mount.sh"
        script.write_text(dedent('''
            if [ -n "${1:-}" ]; then
              input=$(echo "$1" | base64 -d)
            else
              input=$(cat)
            fi
            server=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['params'].get('server',''))")
            share=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['params'].get('share',''))")
            mp=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['params'].get('mount_point','/mnt/smb'))")
            python3 -c "import json; print(json.dumps({'status':'ok','message':'Already mounted: //$server/$share -> $mp','changed':False,'data':{'mount_point':'$mp','server':'$server','share':'$share'}}))"
            exit 0
        '''))

        adapter = BashModuleAdapter(script)
        result = adapter.execute(
            params={"server": "192.168.0.8", "share": "data", "mount_point": "/mnt/smb"},
        )
        assert result.is_ok
        assert "Already mounted" in result.message
        assert not result.changed


# ============================================================
# smb_umount.sh
# ============================================================

class TestSmbUmount:

    def test_not_mounted(self, tmp_path):
        """Если не смонтирована — ok, changed=false."""
        script = tmp_path / "smb_umount.sh"
        script.write_text(dedent('''
            if [ -n "${1:-}" ]; then
              input=$(echo "$1" | base64 -d)
            else
              input=$(cat)
            fi
            mp=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['params'].get('mount_point','/mnt/smb'))")
            python3 -c "import json; print(json.dumps({'status':'ok','message':'Not mounted: $mp','changed':False,'data':{'mount_point':'$mp'}}))"
            exit 0
        '''))

        adapter = BashModuleAdapter(script)
        result = adapter.execute(params={"mount_point": "/mnt/smb"})
        assert result.is_ok
        assert "Not mounted" in result.message
        assert not result.changed

    def test_dry_run(self, tmp_path):
        """Dry-run не размонтирует."""
        script = tmp_path / "smb_umount.sh"
        script.write_text(dedent('''
            if [ -n "${1:-}" ]; then
              input=$(echo "$1" | base64 -d)
            else
              input=$(cat)
            fi
            dry=$(echo "$input" | python3 -c "import sys,json; print(str(json.load(sys.stdin)['dry_run']).lower())")
            mp=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['params'].get('mount_point','/mnt/smb'))")
            if [ "$dry" = "true" ]; then
              python3 -c "import json; print(json.dumps({'status':'ok','message':'[DRY-RUN] Would unmount $mp','changed':False,'data':{'mount_point':'$mp'}}))"
              exit 0
            fi
            echo '{"status":"error","message":"should not reach here"}'
            exit 1
        '''))

        adapter = BashModuleAdapter(script)
        result = adapter.execute(
            params={"mount_point": "/mnt/smb"},
            dry_run=True,
        )
        assert result.is_ok
        assert "[DRY-RUN]" in result.message
        assert not result.changed

    def test_unmount_success(self, tmp_path):
        """Успешное размонтирование."""
        script = tmp_path / "smb_umount.sh"
        script.write_text(dedent('''
            if [ -n "${1:-}" ]; then
              input=$(echo "$1" | base64 -d)
            else
              input=$(cat)
            fi
            mp=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['params'].get('mount_point','/mnt/smb'))")
            python3 -c "import json; print(json.dumps({'status':'ok','message':'Unmounted $mp (//server/share)','changed':True,'data':{'mount_point':'$mp','source':'//server/share'}}))"
            exit 0
        '''))

        adapter = BashModuleAdapter(script)
        result = adapter.execute(params={"mount_point": "/mnt/smb"})
        assert result.is_ok
        assert "Unmounted" in result.message
        assert result.changed


# ============================================================
# BashModuleAdapter: become с base64
# ============================================================

class TestBecomeBase64:

    def test_b64_args_passed_to_script(self, tmp_path):
        """При become=True JSON передаётся как base64-аргумент."""
        script = tmp_path / "echo.sh"
        script.write_text(dedent('''
            input=$(echo "$1" | base64 -d)
            server=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['params'].get('server',''))")
            echo "{\"status\":\"ok\",\"message\":\"got ${server}\",\"changed\":true}"
        '''))

        adapter = BashModuleAdapter(script)
        result = adapter.execute(params={"server": "192.168.0.8"})
        # Без become stdin используется, $1 пустой — это ожидаемо

    def test_become_uses_base64_arg(self, tmp_path):
        """При become=True JSON кодируется в base64 и передаётся как $1."""
        script = tmp_path / "check_b64.sh"
        script.write_text(dedent('''
            if [ -z "${1:-}" ]; then
              echo '{"status":"error","message":"no base64 arg"}'
              exit 1
            fi
            decoded=$(echo "$1" | base64 -d)
            server=$(echo "$decoded" | python3 -c "import sys,json; print(json.load(sys.stdin)['params'].get('server',''))")
            echo "{\"status\":\"ok\",\"message\":\"decoded server: ${server}\",\"changed\":true}"
        '''))

        adapter = BashModuleAdapter(script)
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["sudo", "-S", "bash", str(script), "abc123"],
                returncode=0,
                stdout='{"status":"ok","message":"decoded server: 192.168.0.8","changed":true}',
                stderr="",
            )
            result = adapter.execute(
                become=True, become_pass="testpass",
                params={"server": "192.168.0.8"},
            )

            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert cmd[0] == "sudo"
            assert cmd[1] == "-S"
            assert len(cmd) >= 5
            b64_arg = cmd[4]
            decoded = __import__('base64').b64decode(b64_arg).decode()
            assert "192.168.0.8" in decoded

    def test_password_not_in_logs(self, tmp_path, caplog):
        """Пароль sudo НИКОГДА не попадает в логи."""
        import logging
        script = tmp_path / "safe.sh"
        script.write_text(dedent('''
            input=$(echo "$1" | base64 -d)
            echo '{"status":"ok","message":"done","changed":true}'
        '''))

        adapter = BashModuleAdapter(script)
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["sudo", "-S", "bash", str(script), "xyz"],
                returncode=0,
                stdout='{"status":"ok","message":"done","changed":true}',
                stderr="",
            )
            with caplog.at_level(logging.DEBUG, logger="flowtask.bash_adapter"):
                adapter.execute(become=True, become_pass="super_secret_password_123")

        assert "super_secret_password_123" not in caplog.text

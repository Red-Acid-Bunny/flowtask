"""Mount SMB share via CIFS."""

import subprocess
import logging
import tempfile
import os
from pathlib import Path

from ..modules.base import BaseModule, param
from ..engine.result import ModuleResult

logger = logging.getLogger("flowtask.modules.mount_smb")


class MountSmb(BaseModule):
    """Монтирование SMB/CIFS шары."""

    description = "Mount SMB/CIFS share via mount.cifs"

    server: str = param(required=True, help="SMB server address (e.g. 192.168.0.8)")
    share: str = param(required=True, help="Share name (e.g. box_delta_bin)")
    mount_point: str = param(default="/mnt/smb", help="Local mount point")
    user: str = param(default="", help="Username (empty = guest)")
    password: str = param(default="", help="Password (empty = guest)")
    domain: str = param(default="", help="Domain")
    uid: int = param(default=-1, help="UID for mount (default: current)")
    gid: int = param(default=-1, help="GID for mount (default: current)")
    version: str = param(default="3.0", help="SMB version (1.0, 2.0, 3.0, 3.1.1)")

    def run(self) -> ModuleResult:
        mp = Path(self.mount_point)

        # Проверка — уже смонтирована?
        if mp.is_mount():
            return ModuleResult.ok(
                f"Already mounted: {self.mount_point}",
                data={"mount_point": str(mp), "changed": False},
            )

        # Создание точки монтирования
        mp.mkdir(parents=True, exist_ok=True)

        # Формирование опций
        opts = f"vers={self.version},iocharset=utf8"
        if self.uid >= 0:
            opts += f",uid={self.uid}"
        if self.gid >= 0:
            opts += f",gid={self.gid}"

        # Credentials file — единственный надёжный способ передать пароль
        # через sudo. sudo сбрасывает env (PASSWD), а mount.cifs читает
        # пароль с /dev/tty. credentials= файл решает обе проблемы.
        # Важно: НЕ добавлять username=/domain= в опции монтирования
        # одновременно с credentials= — mount.cifs будет игнорировать
        # пароль из файла и запрашивать интерактивно.
        cred_file = None
        if self.user:
            if self.password:
                # Создаём временный файл с учётными данными
                cred_file = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".cifscred", delete=False,
                )
                cred_file.write(f"username={self.user}\n")
                cred_file.write(f"password={self.password}\n")
                if self.domain:
                    cred_file.write(f"domain={self.domain}\n")
                cred_file.close()
                os.chmod(cred_file.name, 0o600)
                opts += f",credentials={cred_file.name}"
            else:
                # Без пароля — гостевой доступ с указанным username
                opts += f",username={self.user},guest"
                if self.domain:
                    opts += f",domain={self.domain}"
        else:
            opts += ",guest"

        cmd = ["sudo", "mount", "-t", "cifs",
               f"//{self.server}/{self.share}",
               str(mp), "-o", opts]

        logger.info("Mounting //%s/%s → %s", self.server, self.share, mp)

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return ModuleResult.error(f"Mount timed out (30s)")
        except FileNotFoundError:
            return ModuleResult.error(
                "mount.cifs not found. Install: sudo apt install cifs-utils"
            )
        finally:
            # Удаляем файл с учётными данными
            if cred_file:
                try:
                    os.unlink(cred_file.name)
                except OSError:
                    pass

        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            return ModuleResult.error(f"Mount failed: {err}")

        return ModuleResult.changed(
            f"Mounted //{self.server}/{self.share} → {mp}",
            data={"mount_point": str(mp), "server": self.server, "share": self.share},
        )

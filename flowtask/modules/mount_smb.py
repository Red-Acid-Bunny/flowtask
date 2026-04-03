"""Mount SMB share via CIFS."""

import subprocess
import logging
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
        if self.user:
            opts += f",username={self.user}"
            if self.domain:
                opts += f",domain={self.domain}"
        # guest если нет user

        cmd = ["sudo", "mount", "-t", "cifs",
               f"//{self.server}/{self.share}",
               str(mp), "-o", opts]

        logger.info("Mounting //%s/%s → %s", self.server, self.share, mp)

        try:
            import os
            env = None
            if self.password and self.user:
                # Передаём пароль через переменную окружения PASSWD
                env = {**os.environ, "PASSWD": self.password}

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
                env=env, stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return ModuleResult.error(f"Mount timed out (30s)")
        except FileNotFoundError:
            return ModuleResult.error(
                "mount.cifs not found. Install: sudo apt install cifs-utils"
            )

        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            return ModuleResult.error(f"Mount failed: {err}")

        return ModuleResult.changed(
            f"Mounted //{self.server}/{self.share} → {mp}",
            data={"mount_point": str(mp), "server": self.server, "share": self.share},
        )

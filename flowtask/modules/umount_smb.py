"""Unmount SMB share."""

import subprocess
import logging
from pathlib import Path

from ..modules.base import BaseModule, param
from ..engine.result import ModuleResult

logger = logging.getLogger("flowtask.modules.umount_smb")


class UmountSmb(BaseModule):
    """Отмонтирование SMB/CIFS шары."""

    description = "Unmount SMB/CIFS share"

    mount_point: str = param(required=True, help="Mount point to unmount")
    lazy: bool = param(default=False, help="Lazy unmount (-l) if busy")

    def run(self) -> ModuleResult:
        mp = Path(self.mount_point)

        if not mp.is_mount():
            return ModuleResult.ok(
                f"Not mounted: {mp}",
                data={"mount_point": str(mp), "changed": False},
            )

        flag = "-l" if self.lazy else ""
        cmd = ["sudo", "umount", flag, str(mp)]

        logger.info("Unmounting %s", mp)

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15,
            )
        except subprocess.TimeoutExpired:
            return ModuleResult.error(f"Unmount timed out (15s)")

        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            return ModuleResult.error(f"Unmount failed: {err}")

        return ModuleResult.changed(
            f"Unmounted: {mp}",
            data={"mount_point": str(mp)},
        )

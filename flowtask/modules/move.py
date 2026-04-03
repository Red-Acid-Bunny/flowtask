"""Move files and directories."""

import glob
import logging
import shutil
from pathlib import Path

from ..modules.base import BaseModule, param
from ..engine.result import ModuleResult

logger = logging.getLogger("flowtask.modules.move")


class Move(BaseModule):
    """Перемещение файлов и директорий.

    Поддерживает glob-маски в src.
    """

    description = "Move/rename files/directories (supports glob patterns)"

    src: str = param(required=True, help="Source path or glob pattern")
    dest: str = param(required=True, help="Destination path")
    overwrite: bool = param(default=False, help="Overwrite existing files")

    def run(self) -> ModuleResult:
        src_path = Path(self.src)
        dest_path = Path(self.dest)

        # Glob-разрешение
        if any(c in self.src for c in ("*", "?", "[")):
            sources = sorted(glob.glob(self.src))
            if not sources:
                return ModuleResult.ok(
                    f"No files matched: {self.src}",
                    data={"moved": 0},
                )
        else:
            sources = [self.src]

        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # Determine if dest is a directory path or a rename path
        is_rename = (len(sources) == 1 and not dest_path.exists()
                      and dest_path.parent.exists() and dest_path.suffix)

        if not is_rename:
            dest_path.mkdir(parents=True, exist_ok=True)

        moved = 0
        errors = []

        for src in sources:
            sp = Path(src)

            if not sp.exists():
                errors.append(f"Not found: {sp}")
                continue

            if dest_path.is_dir() or len(sources) > 1:
                target = dest_path / sp.name
            else:
                target = dest_path

            if target.exists() and not self.overwrite:
                logger.debug("Skip (exists): %s", sp.name)
                continue

            try:
                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                shutil.move(str(sp), str(target))
                moved += 1
                logger.debug("Moved: %s → %s", sp, target)
            except (OSError, shutil.Error) as e:
                errors.append(f"{sp} → {target}: {e}")

        if errors and moved == 0:
            return ModuleResult.error("; ".join(errors))

        return ModuleResult.changed(
            f"Moved {moved} items" + (f", {len(errors)} errors" if errors else ""),
            data={"moved": moved, "errors": errors},
        )

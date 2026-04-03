"""Delete files and directories."""

import glob
import logging
import shutil
from pathlib import Path

from ..modules.base import BaseModule, param
from ..engine.result import ModuleResult

logger = logging.getLogger("flowtask.modules.delete")


class Delete(BaseModule):
    """Удаление файлов и директорий.

    Поддерживает glob-маски. Без force=True не удаляет несуществующие пути.
    """

    description = "Delete files/directories (supports glob patterns)"

    path: str = param(required=True, help="Path or glob pattern to delete")
    recursive: bool = param(default=True, help="Delete directories recursively")
    force: bool = param(default=False, help="Ignore nonexistent paths")

    def run(self) -> ModuleResult:
        # Glob-разрешение
        if any(c in self.path for c in ("*", "?", "[")):
            targets = sorted(glob.glob(self.path))
            if not targets:
                if self.force:
                    return ModuleResult.ok(f"No matches: {self.path}")
                return ModuleResult.error(f"No matches: {self.path}")
        else:
            targets = [self.path]

        deleted = 0
        errors = []

        for target_path in targets:
            p = Path(target_path)

            if not p.exists():
                if not self.force:
                    errors.append(f"Not found: {p}")
                continue

            try:
                if p.is_dir():
                    if self.recursive:
                        shutil.rmtree(p)
                    else:
                        # Удалить только если пустая
                        try:
                            p.rmdir()
                        except OSError as e:
                            errors.append(f"Directory not empty: {p}")
                            continue
                else:
                    p.unlink()
                deleted += 1
                logger.debug("Deleted: %s", p)

            except OSError as e:
                errors.append(f"{p}: {e}")

        if errors and deleted == 0:
            return ModuleResult.error("; ".join(errors))

        changed = deleted > 0
        return ModuleResult(
            status="ok",
            message=f"Deleted {deleted} items" + (f", {len(errors)} errors" if errors else ""),
            changed=changed,
            data={"deleted": deleted, "errors": errors},
        )

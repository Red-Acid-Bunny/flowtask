"""Copy files and directories."""

import glob
import logging
import shutil
from pathlib import Path

from ..modules.base import BaseModule, param
from ..engine.result import ModuleResult

logger = logging.getLogger("flowtask.modules.copy")


class Copy(BaseModule):
    """Копирование файлов и директорий.

    Поддерживает glob-маски в src. Если src — директория,
    копируется рекурсивно.
    """

    description = "Copy files/directories (supports glob patterns)"

    src: str = param(required=True, help="Source path or glob pattern")
    dest: str = param(required=True, help="Destination path")
    overwrite: bool = param(default=True, help="Overwrite existing files")
    recursive: bool = param(default=True, help="Recursive copy for directories")

    def run(self) -> ModuleResult:
        src_path = Path(self.src)
        dest_path = Path(self.dest)

        # Glob-разрешение
        if any(c in self.src for c in ("*", "?", "[")):
            sources = sorted(glob.glob(self.src))
            if not sources:
                return ModuleResult.ok(
                    f"No files matched pattern: {self.src}",
                    data={"pattern": self.src, "copied": 0},
                )
        else:
            sources = [self.src]

        dest_path.mkdir(parents=True, exist_ok=True)

        copied = 0
        overwritten = 0
        errors = []

        for src in sources:
            sp = Path(src)

            if not sp.exists():
                errors.append(f"Source not found: {sp}")
                continue

            # Определение имени назначения
            if dest_path.is_dir() or len(sources) > 1:
                target = dest_path / sp.name
            else:
                target = dest_path

            # Идемпотентность: проверяем, нужно ли копировать
            if target.exists() and not self.overwrite:
                logger.debug("Skip (exists, overwrite=False): %s", sp.name)
                continue

            try:
                if sp.is_dir():
                    if target.exists():
                        shutil.rmtree(target)
                    shutil.copytree(str(sp), str(target))
                    file_count = sum(1 for _ in Path(target).rglob("*") if _.is_file())
                    copied += file_count
                else:
                    if target.exists():
                        overwritten += 1
                    shutil.copy2(str(sp), str(target))
                    copied += 1

                logger.debug("Copied: %s → %s", sp, target)

            except (OSError, shutil.Error) as e:
                errors.append(f"{sp} → {target}: {e}")

        if errors and copied == 0:
            return ModuleResult.error("; ".join(errors), data={"errors": errors})

        changed = copied > 0 or overwritten > 0
        msg_parts = []
        if copied:
            msg_parts.append(f"{copied} items copied")
        if overwritten:
            msg_parts.append(f"{overwritten} overwritten")
        if errors:
            msg_parts.append(f"{len(errors)} errors")

        return ModuleResult(
            status="ok" if not errors else "ok",
            message="; ".join(msg_parts) if msg_parts else "Nothing to copy",
            changed=changed,
            data={
                "copied": copied,
                "overwritten": overwritten,
                "errors": errors,
            },
        )

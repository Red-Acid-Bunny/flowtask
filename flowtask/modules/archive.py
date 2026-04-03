"""Create archives (zip, tar.gz, tar.xz)."""

import logging
import shutil
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path

from ..modules.base import BaseModule, param
from ..engine.result import ModuleResult

logger = logging.getLogger("flowtask.modules.archive")


class Archive(BaseModule):
    """Создание архивов.

    Поддерживаемые форматы: zip, tar.gz, tar.xz.
    """

    description = "Create archive (zip, tar.gz, tar.xz)"

    src: str = param(required=True, help="Source path to archive")
    dest_dir: str = param(default="", help="Output directory (default: parent of src)")
    format: str = param(default="zip", help="Archive format: zip, tar.gz, tar.xz")
    name: str = param(default="", help="Archive name (default: source_name + timestamp)")

    _SUPPORTED_FORMATS = {"zip", "tar.gz", "tar.xz"}

    def run(self) -> ModuleResult:
        src_path = Path(self.src)

        if not src_path.exists():
            return ModuleResult.error(f"Source not found: {src_path}")

        # Валидация формата
        if self.format not in self._SUPPORTED_FORMATS:
            return ModuleResult.error(
                f"Unsupported format: {self.format}. Supported: {', '.join(self._SUPPORTED_FORMATS)}"
            )

        # Определение имени архива
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = self.name or f"{src_path.name}_{timestamp}"

        # Определение директории назначения
        dest = Path(self.dest_dir) if self.dest_dir else src_path.parent
        dest.mkdir(parents=True, exist_ok=True)

        archive_path = dest / f"{base_name}.{self.format}"

        # Идемпотентность
        if archive_path.exists():
            return ModuleResult.ok(
                f"Archive already exists: {archive_path.name}",
                data={"path": str(archive_path), "size": archive_path.stat().st_size},
            )

        try:
            if self.format == "zip":
                self._create_zip(src_path, archive_path)
            elif self.format in ("tar.gz", "tar.xz"):
                self._create_tar(src_path, archive_path)
        except Exception as e:
            # Удалить битый архив
            archive_path.unlink(missing_ok=True)
            return ModuleResult.error(f"Archive failed: {e}")

        size = archive_path.stat().st_size
        logger.info("Created archive: %s (%s)", archive_path.name, _human_size(size))

        return ModuleResult.changed(
            f"Archived: {archive_path.name}",
            data={
                "path": str(archive_path),
                "format": self.format,
                "size": size,
                "size_human": _human_size(size),
            },
        )

    def _create_zip(self, src: Path, dest: Path) -> None:
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            if src.is_dir():
                for item in src.rglob("*"):
                    if item.is_file():
                        arcname = item.relative_to(src.parent)
                        zf.write(item, arcname)
            else:
                zf.write(src, src.name)

    def _create_tar(self, src: Path, dest: Path) -> None:
        mode = "w:gz" if self.format == "tar.gz" else "w:xz"
        with tarfile.open(dest, mode) as tf:
            if src.is_dir():
                tf.add(src, arcname=src.name)
            else:
                tf.add(src, arcname=src.name)


def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size) < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"

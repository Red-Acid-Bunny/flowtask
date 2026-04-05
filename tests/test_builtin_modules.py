"""
Тесты для встроенных модулей: copy, move, delete, archive.
"""

import os
import pytest
from pathlib import Path
from textwrap import dedent

from flowtask.modules.copy import Copy
from flowtask.modules.move import Move
from flowtask.modules.delete import Delete
from flowtask.modules.archive import Archive
from flowtask.engine.module_loader import ModuleLoader
from flowtask.engine.result import ModuleResult


# ============================================================
# Copy
# ============================================================

class TestCopy:

    def test_copy_file(self, tmp_path):
        src = tmp_path / "src" / "file.txt"
        src.parent.mkdir()
        src.write_text("hello")

        dest = tmp_path / "dest"
        m = Copy(src=str(src), dest=str(dest))
        result = m.execute()
        assert result.is_ok
        assert result.changed
        assert (dest / "file.txt").read_text() == "hello"

    def test_copy_directory(self, tmp_path):
        src = tmp_path / "src_dir"
        src.mkdir()
        (src / "a.txt").write_text("a")
        (src / "sub" / "b.txt").parent.mkdir()
        (src / "sub" / "b.txt").write_text("b")

        dest = tmp_path / "dest"
        m = Copy(src=str(src), dest=str(dest))
        result = m.execute()
        assert result.is_ok
        assert result.changed
        assert (dest / "src_dir" / "sub" / "b.txt").read_text() == "b"

    def test_copy_glob(self, tmp_path):
        (tmp_path / "f1.txt").write_text("1")
        (tmp_path / "f2.txt").write_text("2")
        (tmp_path / "f3.log").write_text("log")

        dest = tmp_path / "dest"
        m = Copy(src=str(tmp_path / "*.txt"), dest=str(dest))
        result = m.execute()
        assert result.changed
        assert (dest / "f1.txt").exists()
        assert (dest / "f2.txt").exists()
        assert not (dest / "f3.log").exists()

    def test_copy_no_overwrite(self, tmp_path):
        src = tmp_path / "src" / "file.txt"
        src.parent.mkdir()
        src.write_text("new")

        dest = tmp_path / "dest" / "file.txt"
        dest.parent.mkdir()
        dest.write_text("old")

        # overwrite=False → skip existing file
        m = Copy(src=str(src), dest=str(dest.parent), overwrite=False)
        result = m.execute()
        assert not result.changed
        assert dest.read_text() == "old"  # not overwritten

    def test_copy_glob_no_match(self, tmp_path):
        m = Copy(src=str(tmp_path / "*.xyz"), dest=str(tmp_path / "dest"))
        result = m.execute()
        assert result.is_ok
        assert not result.changed

    def test_copy_idempotent(self, tmp_path):
        """Копирование того же файла — changed=True (shutil всегда копирует)."""
        src = tmp_path / "src" / "file.txt"
        src.parent.mkdir()
        src.write_text("hello")

        dest = tmp_path / "dest"
        m = Copy(src=str(src), dest=str(dest))
        r1 = m.execute()
        r2 = m.execute()  # второй раз
        assert r1.changed
        assert r2.changed  # shutil.copy2 всегда перезаписывает

    def test_copy_nonexistent(self, tmp_path):
        m = Copy(src=str(tmp_path / "nonexistent"), dest=str(tmp_path / "dest"))
        result = m.execute()
        assert result.is_error

    def test_copy_error_status(self, tmp_path):
        """Copy возвращает status='error' когда все файлы не найдены."""
        m = Copy(src=str(tmp_path / "nonexistent1"), dest=str(tmp_path / "dest"))
        result = m.execute()
        assert result.status == "error"

    def test_dry_run(self, tmp_path):
        src = tmp_path / "src" / "file.txt"
        src.parent.mkdir()
        src.write_text("hello")

        dest = tmp_path / "dest"
        m = Copy(src=str(src), dest=str(dest))
        result = m.execute(dry_run=True)
        assert "DRY-RUN" in result.message
        assert not dest.exists()


# ============================================================
# Move
# ============================================================

class TestMove:

    def test_move_file(self, tmp_path):
        src = tmp_path / "file.txt"
        src.write_text("hello")
        dest_dir = tmp_path / "moved"

        m = Move(src=str(src), dest=str(dest_dir))
        result = m.execute()
        assert result.changed
        assert not src.exists()
        assert (dest_dir / "file.txt").read_text() == "hello"

    def test_move_directory(self, tmp_path):
        src = tmp_path / "dir"
        src.mkdir()
        (src / "a.txt").write_text("a")

        dest = tmp_path / "new_dir"
        m = Move(src=str(src), dest=str(dest))
        result = m.execute()
        assert result.changed
        assert not src.exists()
        # shutil.move("dir", "new_dir") → new_dir/dir/ (dir is moved inside dest)
        assert (dest / "dir" / "a.txt").read_text() == "a"

    def test_move_overwrite(self, tmp_path):
        src = tmp_path / "file.txt"
        src.write_text("new")
        dest = tmp_path / "file.txt"
        dest.write_text("old")

        # move file TO a file name (rename)
        m = Move(src=str(src), dest=str(tmp_path / "renamed.txt"))
        result = m.execute()
        assert result.changed

    def test_move_glob(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")

        dest = tmp_path / "dest"
        m = Move(src=str(tmp_path / "*.txt"), dest=str(dest))
        result = m.execute()
        assert result.changed
        assert (dest / "a.txt").exists()
        assert (dest / "b.txt").exists()
        assert not (tmp_path / "a.txt").exists()

    def test_move_nonexistent(self, tmp_path):
        m = Move(src=str(tmp_path / "nope"), dest=str(tmp_path / "dest"))
        result = m.execute()
        assert result.is_error

    def test_dry_run(self, tmp_path):
        src = tmp_path / "file.txt"
        src.write_text("hello")

        m = Move(src=str(src), dest=str(tmp_path / "dest"))
        result = m.execute(dry_run=True)
        assert "DRY-RUN" in result.message
        assert src.exists()


# ============================================================
# Delete
# ============================================================

class TestDelete:

    def test_delete_file(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hello")

        m = Delete(path=str(f))
        result = m.execute()
        assert result.changed
        assert not f.exists()

    def test_delete_directory(self, tmp_path):
        d = tmp_path / "dir"
        d.mkdir()
        (d / "a.txt").write_text("a")
        (d / "sub" / "b.txt").parent.mkdir()
        (d / "sub" / "b.txt").write_text("b")

        m = Delete(path=str(d))
        result = m.execute()
        assert result.changed
        assert not d.exists()

    def test_delete_glob(self, tmp_path):
        (tmp_path / "a.log").write_text("a")
        (tmp_path / "b.log").write_text("b")
        (tmp_path / "c.txt").write_text("c")

        m = Delete(path=str(tmp_path / "*.log"))
        result = m.execute()
        assert result.changed
        assert result.data["deleted"] == 2
        assert (tmp_path / "c.txt").exists()

    def test_delete_force_nonexistent(self, tmp_path):
        m = Delete(path=str(tmp_path / "nope"), force=True)
        result = m.execute()
        assert result.is_ok
        assert not result.changed

    def test_delete_nonexistent_no_force(self, tmp_path):
        m = Delete(path=str(tmp_path / "nope"), force=False)
        result = m.execute()
        assert result.is_error

    def test_dry_run(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hello")

        m = Delete(path=str(f))
        result = m.execute(dry_run=True)
        assert "DRY-RUN" in result.message
        assert f.exists()


# ============================================================
# Archive
# ============================================================

class TestArchive:

    def test_archive_zip_file(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hello world")

        m = Archive(src=str(f), format="zip")
        result = m.execute()
        assert result.changed
        assert result.data["format"] == "zip"
        archive = Path(result.data["path"])
        assert archive.exists()
        assert archive.suffix == ".zip"

    def test_archive_zip_directory(self, tmp_path):
        d = tmp_path / "mydir"
        d.mkdir()
        (d / "a.txt").write_text("a")
        (d / "sub" / "b.txt").parent.mkdir()
        (d / "sub" / "b.txt").write_text("b")

        dest = tmp_path / "archives"
        m = Archive(src=str(d), dest_dir=str(dest), format="zip", name="backup")
        result = m.execute()
        assert result.changed

        import zipfile
        archive_path = Path(result.data["path"])
        with zipfile.ZipFile(archive_path) as zf:
            names = zf.namelist()
            assert "mydir/a.txt" in names
            assert "mydir/sub/b.txt" in names

    def test_archive_tar_gz(self, tmp_path):
        d = tmp_path / "data"
        d.mkdir()
        (d / "f.txt").write_text("x")

        m = Archive(src=str(d), format="tar.gz")
        result = m.execute()
        assert result.changed
        assert result.data["path"].endswith(".tar.gz")

    def test_archive_tar_xz(self, tmp_path):
        d = tmp_path / "data"
        d.mkdir()
        (d / "f.txt").write_text("x")

        m = Archive(src=str(d), format="tar.xz")
        result = m.execute()
        assert result.changed
        assert result.data["path"].endswith(".tar.xz")

    def test_archive_unsupported_format(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("x")

        m = Archive(src=str(f), format="rar")
        result = m.execute()
        assert result.is_error
        assert "Unsupported" in result.message

    def test_archive_nonexistent(self, tmp_path):
        m = Archive(src=str(tmp_path / "nope"), format="zip")
        result = m.execute()
        assert result.is_error

    def test_archive_custom_name(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("x")

        m = Archive(src=str(f), format="zip", name="my_archive")
        result = m.execute()
        assert "my_archive" in result.data["path"]

    def test_archive_custom_dest(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("x")
        dest = tmp_path / "output"

        m = Archive(src=str(f), dest_dir=str(dest), format="zip")
        result = m.execute()
        assert str(dest) in result.data["path"]

    def test_dry_run(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("x")

        m = Archive(src=str(f), format="zip")
        result = m.execute(dry_run=True)
        assert "DRY-RUN" in result.message

    def test_archive_overwrite(self, tmp_path):
        """overwrite=True перезаписывает существующий архив."""
        f = tmp_path / "f.txt"
        f.write_text("original")

        dest = tmp_path / "output"
        m = Archive(src=str(f), dest_dir=str(dest), format="zip", name="backup", overwrite=True)
        result1 = m.execute()
        assert result1.changed

        f.write_text("updated")
        m2 = Archive(src=str(f), dest_dir=str(dest), format="zip", name="backup", overwrite=True)
        result2 = m2.execute()
        assert result2.changed

        import zipfile
        archive_path = Path(result2.data["path"])
        with zipfile.ZipFile(archive_path) as zf:
            content = zf.read("f.txt").decode()
        assert content == "updated"

    def test_archive_idempotent_without_overwrite(self, tmp_path):
        """Без overwrite существующий архив не перезаписывается."""
        f = tmp_path / "f.txt"
        f.write_text("original")

        m = Archive(src=str(f), format="zip", name="backup")
        result1 = m.execute()
        assert result1.changed

        m2 = Archive(src=str(f), format="zip", name="backup")
        result2 = m2.execute()
        assert not result2.changed
        assert "already exists" in result2.message


# ============================================================
# ModuleLoader discovers built-in modules
# ============================================================

class TestBuiltinDiscovery:

    def test_all_python_modules_discovered(self):
        loader = ModuleLoader()
        modules = loader.discover()

        assert "copy" in modules
        assert "move" in modules
        assert "delete" in modules
        assert "archive" in modules

    def test_module_types(self):
        loader = ModuleLoader()
        loader.discover()

        mod_list = loader.list_modules()
        assert mod_list["copy"] == "python"
        assert mod_list["move"] == "python"
        assert mod_list["delete"] == "python"
        assert mod_list["archive"] == "python"

    def test_bash_modules_discovered(self):
        loader = ModuleLoader()
        modules = loader.discover()

        assert "rsync" in modules

    def test_get_module_returns_class(self):
        loader = ModuleLoader()
        loader.discover()

        cls = loader.get("copy")
        # get() returns the class, either from builtin or user modules
        assert hasattr(cls, 'run')

    def test_archive_params(self):
        m = Archive(src="/tmp/test")
        schema = m.param_schema
        assert "src" in schema
        assert schema["src"]["required"] is True
        assert "format" in schema
        assert schema["format"]["required"] is False

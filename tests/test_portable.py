import struct
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

from build_portable import EXE, NAME, verify_tree, write_zip
from build_onefile import build_onefile
from catalog.database import CatalogError, open_catalog
from catalog.launch_paths import default_database


class PortableTests(unittest.TestCase):
    def test_source_path_is_next_to_project(self):
        with patch.object(sys, "frozen", False, create=True):
            self.assertEqual(default_database(), Path(__file__).resolve().parents[1] / "catalog.db")

    def test_frozen_path_is_next_to_executable_outside_working_directory(self):
        with TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            folder = base / "Копия каталога"
            for libraries in (folder / "_internal", base / "Temp" / "_MEI12345"):
                with self.subTest(libraries=libraries), patch.object(
                    sys, "frozen", True, create=True
                ), patch.object(sys, "executable", str(folder / EXE)), patch.object(
                    sys, "_MEIPASS", str(libraries), create=True
                ):
                    self.assertEqual(default_database(), folder / "catalog.db")
                    self.assertNotEqual(folder, Path.cwd())

    def test_onefile_build_refuses_existing_user_folder_and_zip(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "Однофайловая сборка"
            output.mkdir()
            user_db = output / "catalog.db"
            user_db.write_bytes(b"existing catalog")
            with self.assertRaises(FileExistsError):
                build_onefile(output)
            self.assertEqual(user_db.read_bytes(), b"existing catalog")
            user_db.unlink()
            output.rmdir()
            archive = output.with_name(f"{output.name}-Windows-x64.zip")
            archive.write_bytes(b"existing zip")
            with self.assertRaises(FileExistsError):
                build_onefile(output)
            self.assertEqual(archive.read_bytes(), b"existing zip")

    def test_package_and_zip_reject_database_and_require_windows_plugin(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / NAME
            plugin = bundle / "_internal" / "platforms" / "qwindows.dll"
            plugin.parent.mkdir(parents=True)
            header = bytearray(158)
            header[:2] = b"MZ"
            struct.pack_into("<I", header, 60, 64)
            header[64:68] = b"PE\0\0"
            struct.pack_into("<H", header, 68, 0x8664)
            struct.pack_into("<H", header, 156, 2)
            (bundle / EXE).write_bytes(header)
            with self.assertRaisesRegex(ValueError, "плагин"):
                verify_tree(bundle)
            plugin.write_bytes(b"test")
            members = verify_tree(bundle)
            archive = root / "program.zip"
            write_zip(bundle, archive, members)
            with ZipFile(archive) as result:
                self.assertEqual(set(result.namelist()), {f"{NAME}/{path.as_posix()}" for path in members})
            user_db = bundle / "catalog.db"
            user_db.write_bytes(b"test data")
            with self.assertRaisesRegex(ValueError, "Неожиданные файлы"):
                verify_tree(bundle)
            self.assertEqual(user_db.read_bytes(), b"test data")
            user_db.unlink()
            (plugin.parent / "opaque.bin").write_bytes(b"SQLite format 3\0")
            with self.assertRaisesRegex(ValueError, "SQLite"):
                verify_tree(bundle)

    def test_write_open_never_creates_a_missing_database(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.db"
            with self.assertRaisesRegex(CatalogError, "папка доступна для записи") as error:
                with open_catalog(path, writable=True):
                    self.fail("A missing database must never be created")
            self.assertIn(str(path.resolve()), str(error.exception))
            self.assertFalse(path.exists())

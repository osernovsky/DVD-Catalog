import os
import sqlite3
import stat
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

from catalog.database import CatalogError, create_schema, next_disc_number, open_catalog, search, validate
from catalog.disc_scanner import (
    ScanCancelled, ScanControl, Volume, WindowsOpticalDrives, find_readable_volumes, scan_and_add,
)


def ignore_progress(path, files, folders):
    pass


class FixtureDrive:
    def __init__(self, root):
        self.medium = Volume(root, "Тестовый том", 17, "UDF")

    def suppress_drive_prompts(self):
        return nullcontext()

    def roots(self):
        return [self.medium.root]

    def volume(self, root):
        return self.medium


class DiscScannerTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        self.db = base / "catalog.db"
        with closing(sqlite3.connect(self.db)) as db, db:
            create_schema(db)
            db.executemany("INSERT INTO discs VALUES (?, 'Тестовый том', 'DVD-R')", [(35,), (37,), (1517,)])
        self.disc = base / "disc"
        (self.disc / "Проект" / "Вложено" / "Пустая").mkdir(parents=True)
        (self.disc / "Корень").write_bytes(b"abc")
        (self.disc / ".DS_Store").write_bytes(b"x")
        (self.disc / "Проект" / "Часть.part1.RAR").write_bytes(b"data")
        (self.disc / "Проект" / "Вложено" / "Съёмка.AVI").write_bytes(b"x" * 8)
        self.drives = FixtureDrive(self.disc)
        self.baseline = self.db.read_bytes()

    def add(self, number=1518, control=None, progress=ignore_progress):
        return scan_and_add(self.db, number, self.drives.medium, self.drives,
                            control or ScanControl(), progress)

    def assert_unchanged(self):
        self.assertEqual(self.db.read_bytes(), self.baseline)
        self.assertFalse(self.db.with_suffix(".db-journal").exists())

    def test_staged_structure_metadata_and_search(self):
        native_scandir = os.scandir
        huge = 2**32 + 23

        class ExtendedStat:
            def __init__(self, child):
                self.name, self.path = child.name, child.path
                self.child = child

            def stat(self, *, follow_symlinks):
                value = self.child.stat(follow_symlinks=follow_symlinks)
                fields = {key: getattr(value, key) for key in ("st_mode", "st_mtime", "st_file_attributes")}
                return SimpleNamespace(**fields, st_size=huge)

        @contextmanager
        def simulated_scandir(path):
            with native_scandir(path) as children:
                yield (ExtendedStat(child) if child.name.endswith(".RAR") else child for child in children)

        with open_catalog(self.db) as db:
            self.assertEqual(next_disc_number(db), 1518)
        with patch("catalog.disc_scanner.os.scandir", simulated_scandir):
            self.assertEqual(self.add(), (1518, 4, 3))
        with open_catalog(self.db) as db:
            discs = [tuple(row) for row in db.execute("SELECT * FROM discs ORDER BY disc_num")]
            self.assertEqual([row[0] for row in discs], [35, 37, 1517, 1518])
            self.assertEqual(discs[-1], (1518, "Тестовый том", "Optical"))
            self.assertEqual(discs[-2][2], "DVD-R")
            folders = {row[0]: row[1] for row in db.execute("SELECT name, size_bytes FROM entries WHERE kind='Folder'")}
            self.assertEqual(folders, {"Проект": huge + 8, "Вложено": 8, "Пустая": 0})
            found, = search(db, "ЧАСТЬ.PART1.rar", include_extension=True)
            self.assertEqual((found["size_bytes"], found["full_path"], found["extension"]),
                             (huge, "Проект\\Часть.part1.RAR", "RAR"))
            self.assertIsNotNone(found["modified_date"])
            self.assertEqual(next(search(db, "РОЕКТ"))["kind"], "Folder")
            self.assertEqual(next(search(db, "СЪЁМКА.avi"))["full_path"], "Проект\\Вложено\\Съёмка.AVI")
            self.assertEqual(next(search(db, ".DS_STORE", include_extension=True))["extension"], "")
            self.assertEqual(next(search(db, "Корень"))["full_path"], "Корень")
            self.assertEqual(validate(db)["integrity_check"], "ok")
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "delete")

    def test_drive_discovery_and_windows_media_errors(self):
        class NativeDrive:
            @staticmethod
            def GetDriveTypeW(root):
                return 5

        drives = WindowsOpticalDrives.__new__(WindowsOpticalDrives)
        root = Path("E:/")

        def no_media(*args):
            import ctypes
            ctypes.set_last_error(1112)
            return False

        drives.api = NativeDrive()
        drives.api.GetVolumeInformationW = no_media
        with patch.object(drives, "roots", return_value=[]):
            with self.assertRaisesRegex(CatalogError, "не найдены"):
                find_readable_volumes(drives, ScanControl())
        with patch.object(drives, "roots", return_value=[root]):
            with self.assertRaisesRegex(CatalogError, "Вставьте записанный диск"):
                find_readable_volumes(drives, ScanControl())
            drives.api.GetVolumeInformationW = lambda *args: True
            with patch("catalog.disc_scanner.os.scandir", side_effect=PermissionError("denied")):
                with self.assertRaisesRegex(CatalogError, "корневую папку"):
                    find_readable_volumes(drives, ScanControl())
        self.assert_unchanged()

    def test_enumeration_stat_and_reparse_fail_without_writes(self):
        @contextmanager
        def enumeration_failure(path):
            def children():
                raise OSError("read failed during enumeration")
                yield
            yield children()

        def no_stat(**kwargs):
            raise OSError("stat failed")

        @contextmanager
        def bad_entry(path, attributes=None):
            child = SimpleNamespace(name="File", path=str(self.disc / "File"), stat=no_stat)
            if attributes is not None:
                child.stat = lambda **kwargs: SimpleNamespace(st_file_attributes=attributes)
            yield [child]

        for listing, message in (
            (enumeration_failure, "enumeration"),
            (bad_entry, "stat failed"),
            (lambda path: bad_entry(path, stat.FILE_ATTRIBUTE_REPARSE_POINT), "повторной обработки"),
        ):
            with self.subTest(message=message), patch("catalog.disc_scanner.os.scandir", listing):
                with self.assertRaisesRegex(CatalogError, message):
                    self.add()
                self.assert_unchanged()
        self.assertEqual(self.add(), (1518, 4, 3))

    def test_ejection_or_cancel_before_commit_rolls_back(self):
        original = self.drives.medium
        for failure in ("eject", "swap", "cancel"):
            control = ScanControl()
            def fail():
                if failure == "eject":
                    raise CatalogError("Привод исчез")
                if failure == "swap":
                    return original.__class__(original.root, original.label, 18, original.filesystem)
                control.cancel()
                return original

            sequence = iter((lambda: original, lambda: original, fail))
            with self.subTest(failure=failure), patch.object(
                self.drives, "volume", side_effect=lambda root: next(sequence)()
            ) as probe:
                with self.assertRaises((CatalogError, ScanCancelled)):
                    self.add(control=control)
                self.assertEqual(probe.call_count, 3)
                self.assert_unchanged()
        self.assertEqual(self.add(), (1518, 4, 3))

    def test_cancel_during_scan_and_concurrent_number(self):
        control = ScanControl()
        with self.assertRaises(ScanCancelled):
            self.add(control=control, progress=lambda *args: control.cancel())
        self.assert_unchanged()
        with self.assertRaisesRegex(CatalogError, "уже есть"):
            self.add(number=1517)
        self.assert_unchanged()
        barrier = Barrier(2)

        def compete():
            barrier.wait(timeout=5)
            try:
                return self.add()
            except CatalogError as error:
                return str(error)

        with ThreadPoolExecutor(2) as pool:
            first, second = pool.submit(compete), pool.submit(compete)
            results = (first.result(10), second.result(10))
        self.assertIn((1518, 4, 3), results)
        self.assertIn("Диск №1518 уже есть в каталоге.", results)
        with open_catalog(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM entries").fetchone()[0], 7)
            self.assertEqual(validate(db)["foreign_key_errors"], 0)

    def test_sqlite_failure_after_inserts_rolls_back(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("""
                CREATE TRIGGER fail_file BEFORE INSERT ON entries WHEN new.extension = 'AVI'
                BEGIN SELECT RAISE(ABORT, 'simulated insert failure'); END
            """)
        self.baseline = self.db.read_bytes()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "simulated insert failure"):
            self.add()
        self.assert_unchanged()

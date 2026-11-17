import shutil
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from catalog.database import CatalogError, next_disc_number, open_catalog, search
from catalog.disc_scanner import ScanCancelled, ScanControl, scan_and_add
from prepare_release import check_empty_database, create_empty_database
from test_disc_scanner import FixtureDrive, ignore_progress


class ReleaseDatabaseTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fresh = self.root / "catalog.db"
        create_empty_database(self.fresh)

    def test_generation_preserves_existing_database(self):
        with self.assertRaises(FileExistsError):
            create_empty_database(self.fresh)
        check_empty_database(self.fresh)

    def test_first_disc_failure_success_and_restoration_on_copy(self):
        working = self.root / "копия каталога.db"
        shutil.copyfile(self.fresh, working)
        media = self.root / "диск"
        media.mkdir()
        (media / "Пример.txt").write_bytes(b"test")
        drives = FixtureDrive(media)

        control = ScanControl()
        with self.assertRaises(ScanCancelled):
            scan_and_add(working, 1, drives.medium, drives, control, lambda *args: control.cancel())
        with patch("catalog.disc_scanner.os.scandir", side_effect=OSError("read failed")):
            with self.assertRaises(CatalogError):
                scan_and_add(working, 1, drives.medium, drives, ScanControl(), ignore_progress)
        check_empty_database(working)

        with closing(sqlite3.connect(working)) as db, db:
            db.execute("""CREATE TRIGGER reject_entry BEFORE INSERT ON entries
                          BEGIN SELECT RAISE(ABORT, 'test write failure'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "test write failure"):
            scan_and_add(working, 1, drives.medium, drives, ScanControl(), ignore_progress)
        self.assertEqual(drives.ejected, [])
        check_empty_database(working)
        with closing(sqlite3.connect(working)) as db, db:
            db.execute("DROP TRIGGER reject_entry")

        result = scan_and_add(working, 1, drives.medium, drives, ScanControl(), ignore_progress)
        self.assertEqual(result, (1, 1, 0, None))
        with self.assertRaisesRegex(CatalogError, "уже есть"):
            scan_and_add(working, 1, drives.medium, drives, ScanControl(), ignore_progress)
        self.assertEqual(drives.ejected, [drives.medium])

        restored = self.root / "восстановленная база.db"
        shutil.copyfile(working, restored)
        with open_catalog(restored) as db:
            match, = search(db, "ПРИМЕР")
            self.assertEqual((match["disc_num"], match["full_path"]), (1, "Пример.txt"))
            self.assertEqual(next_disc_number(db), 2)
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        check_empty_database(self.fresh)
        self.assertFalse(list(self.root.glob("*.db-*")))

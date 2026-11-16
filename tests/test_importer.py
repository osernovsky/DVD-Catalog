import errno
import io
import os
import shutil
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from xml.etree import ElementTree as ET

from catalog.database import CatalogError, open_catalog, search, validate
from catalog.importer import FilteredInput, import_catalog, iter_items


def item(name="Ньюрексан", *, disc=35, kind="File", parent="\\", ext=None,
         size=4_702_610_286, label="Архив", media="CD-R"):
    element = ET.Element("ITEM", ItemType=kind)
    fields = {
        "NAME": name, "EXT": ext, "SIZE": str(size), "DATE": "2026-11-16",
        "TIME": "10:25:42", "DISK_NAME": label, "DISK_TYPE": media,
        "PATH": parent, "DISK_NUM": str(disc),
    }
    for key, value in fields.items():
        if value is not None:
            ET.SubElement(element, key).text = value
    return element


class ImportTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.source = self.directory / "whereisit.xml"
        self.target = self.directory / "catalog.db"

    def write_report(self, *items):
        root = ET.Element("REPORT")
        root.extend(items)
        ET.ElementTree(root).write(self.source, encoding="utf-8", xml_declaration=True)

    def import_fixture(self):
        return import_catalog(self.source, self.target, verify_full_archive=False)

    def assert_unpublished(self):
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.directory.glob(".catalog-import-*")), [])

    def test_structure_and_search(self):
        folders = []
        parent = "\\"
        for number in range(12):
            folder = f"Папка {number}"
            folders.append(item(folder, kind="Folder", parent=parent))
            parent = parent.rstrip("\\") + "\\" + folder
        deep_path = parent
        dotfile = item(None, ext="DS_Store")
        dotfile.remove(dotfile.find("DATE"))
        dotfile.remove(dotfile.find("TIME"))
        self.write_report(
            item("Съёмка", parent=deep_path, ext="AVI"), *reversed(folders),
            item(), item(disc=37, media="DVD+R"), dotfile,
            item("1920x1080", kind="Folder", disc=37, media="DVD+R"),
        )
        report = self.import_fixture()
        self.assertEqual(report["discs"], 2)
        self.assertEqual(report["folders"], 13)
        self.assertEqual(report["files"], 4)
        self.assertEqual(report["entries"], 17)
        self.assertEqual(report["root_files"], 3)
        self.assertEqual(report["extensionless_files"], 2)
        self.assertEqual(report["max_parent_depth"], 12)
        self.assertEqual(report["missing_numbers"], ["1–34", "36"])
        self.assertEqual((report["last_disc"], report["next_disc"]), (37, 38))
        self.assertEqual(list(self.directory.glob("catalog.db*")), [self.target])
        copy = self.directory / "Перенос #1.db"
        shutil.copyfile(self.target, copy)
        with open_catalog(copy) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(validate(db)["integrity_check"], "ok")
            hits = list(search(db, "ьЮРЕК"))
            self.assertEqual([row["disc_num"] for row in hits], [35, 37])
            self.assertEqual([row["full_path"] for row in hits], ["Ньюрексан"] * 2)
            video, = search(db, ".avi")
            self.assertEqual(video["full_path"], deep_path[1:] + "\\Съёмка.AVI")
            self.assertEqual(video["size_bytes"], 4_702_610_286)
            self.assertEqual((video["modified_date"], video["modified_time"]),
                             ("2026-11-16", "10:25:42"))
            hidden, = search(db, ".ds_store")
            self.assertIsNone(hidden["modified_date"])
            self.assertIsNone(hidden["modified_time"])
            folder, = search(db, "X1080")
            self.assertEqual(folder["kind"], "Folder")
            discs = db.execute("SELECT * FROM discs ORDER BY disc_num")
            self.assertEqual([tuple(row) for row in discs],
                             [(35, "Архив", "CD-R"), (37, "Архив", "DVD+R")])

    def test_filters_only_forbidden_c0(self):
        self.write_report(item("Нью\x00рек\x01сан\t\n"))
        original = self.source.read_bytes()
        report = self.import_fixture()
        self.assertEqual(report["removed_xml_bytes"], 2)
        self.assertEqual(self.source.read_bytes(), original)
        with open_catalog(self.target) as db:
            match, = search(db, "НЬЮРЕКСАН")
            self.assertEqual(match["display_name"], "Ньюрексан\t\n")
        source = FilteredInput(io.BytesIO(b"\x00\x01\t\n\r\x7f"))
        self.assertEqual(source.read(2), b"\t\n")
        self.assertEqual(source.read(2), b"\r\x7f")
        self.assertEqual(source.read(2), b"")

    def test_invalid_xml_and_invalid_utf8(self):
        self.write_report(item())
        xml = self.source.read_bytes()
        for broken in (xml[:-4], xml.replace(b"</NAME>", b"\xff</NAME>"),
                       xml.replace(b"</NAME>", b"&#1;</NAME>")):
            with self.subTest(broken=broken[-40:]):
                self.source.write_bytes(broken)
                with self.assertRaisesRegex(CatalogError, "Ошибка XML"):
                    self.import_fixture()
                self.assert_unpublished()

    def test_duplicate_logical_path_is_rejected(self):
        self.write_report(item("Дубль", ext="TXT"), item("Дубль.TXT"))
        with self.assertRaisesRegex(CatalogError, "диск №35.*UNIQUE"):
            self.import_fixture()
        self.assert_unpublished()

    def test_conflicting_disc_metadata_is_rejected(self):
        self.write_report(item(), item("Другой", label="Другой том"))
        with self.assertRaisesRegex(CatalogError, "диск №35.*Противоречивые"):
            self.import_fixture()
        self.assert_unpublished()

    def test_orphan_is_rejected(self):
        self.write_report(item(parent="\\Отсутствующая папка"))
        with self.assertRaisesRegex(CatalogError, "Диск №35.*отсутствует папка"):
            self.import_fixture()
        self.assert_unpublished()

    def test_incomplete_archive_is_rejected_by_default(self):
        self.write_report(item())
        with self.assertRaisesRegex(CatalogError, "Расхождения с XML_AUDIT"):
            import_catalog(self.source, self.target)
        self.assert_unpublished()

    def test_interruption_and_retry(self):
        self.write_report(item(), item("Следующая запись"))

        def interrupt(source):
            yield next(iter_items(source))
            raise KeyboardInterrupt

        with patch("catalog.importer.iter_items", interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.import_fixture()
        self.assert_unpublished()
        self.assertEqual(self.import_fixture()["files"], 2)

    def test_sqlite_full_during_import(self):
        self.write_report(item())
        with patch("catalog.importer.import_items",
                   side_effect=sqlite3.OperationalError("database or disk is full")):
            with self.assertRaisesRegex(sqlite3.OperationalError, "disk is full"):
                self.import_fixture()
        self.assert_unpublished()

    def test_publish_failure_leaves_no_database(self):
        self.write_report(item())
        with patch("catalog.importer.os.rename", side_effect=OSError(errno.ENOSPC, "disk full")):
            with self.assertRaises(OSError):
                self.import_fixture()
        self.assert_unpublished()

    def test_repeat_refuses_to_replace_existing_database(self):
        self.write_report(item())
        self.import_fixture()
        original = self.target.read_bytes()
        self.write_report(item("Замена"))
        with self.assertRaisesRegex(CatalogError, "База уже существует"):
            self.import_fixture()
        self.assertEqual(self.target.read_bytes(), original)

    def test_concurrent_destination_is_preserved(self):
        self.write_report(item())
        rename = os.rename

        def publish(source, destination):
            destination.write_bytes(b"concurrent publisher")
            rename(source, destination)

        with patch("catalog.importer.os.rename", publish):
            with self.assertRaises(FileExistsError):
                self.import_fixture()
        self.assertEqual(self.target.read_bytes(), b"concurrent publisher")
        self.assertEqual(list(self.directory.glob(".catalog-import-*")), [])

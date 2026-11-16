import os
import shutil
import sqlite3
import unittest
from contextlib import closing
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
from unittest.mock import patch
from xml.etree import ElementTree as ET

if find_spec("PySide6") is None:
    raise unittest.SkipTest("Для тестов интерфейса установите зависимости из requirements.txt")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from catalog.database import CatalogError, delete_disc, disc_numbers, folder_entries, open_catalog, validate
from catalog.add_disc_dialog import AddDiscDialog
from catalog.file_types import GROUP_NAMES, file_group
from catalog.gui import CatalogWindow
from catalog.importer import import_catalog
from catalog.theme import GROUP, ROW_HEIGHT, SELECTION, SURFACE, apply_theme
from test_importer import item
from test_disc_scanner import FixtureDrive


class CatalogGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_theme(cls.app)

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.settings_path = self.directory / "windows.ini"
        self.path = self.directory / "copy.db"
        archive = self.directory / "report.xml"
        baseline = self.directory / "fixture.db"
        root = ET.Element("REPORT")
        root.extend((
            item("Ньюрексан", disc=2, kind="Folder"),
            item("Съёмка", disc=2, ext="AVI", parent="\\Ньюрексан"),
            item("Ньюрексан", disc=10, kind="Folder"),
            item(None, disc=10, ext="DS_Store"),
            item("Корень", disc=37, size=9),
            *(item(f"Кадр {number:03d}", disc=37) for number in range(540)),
        ))
        ET.ElementTree(root).write(archive, encoding="utf-8")
        import_catalog(archive, baseline, verify_full_archive=False)
        shutil.copyfile(baseline, self.path)

    def open_window(self):
        settings = QSettings(str(self.settings_path), QSettings.Format.IniFormat)
        window = CatalogWindow(self.path, settings)
        self.addCleanup(window.close)
        window.show()
        self.app.processEvents()
        return window

    def test_branch_queries_use_disc_and_parent(self):
        with open_catalog(self.path) as db:
            self.assertEqual(disc_numbers(db), (2, 10, 37))
            folder, = folder_entries(db, 2, "")
            self.assertEqual((folder["display_name"], folder["has_children"]), ("Ньюрексан", 1))
            video, = folder_entries(db, 2, "Ньюрексан")
            self.assertEqual((video["extension"], video["size_bytes"]), ("AVI", 4_702_610_286))
            self.assertEqual(list(folder_entries(db, 10, "Ньюрексан")), [])

    def test_light_theme_keeps_dense_views_and_inactive_selection(self):
        window = self.open_window()
        window.show_search()
        dialog = window.search_window
        for view in (window.discs, window.tree, dialog.table):
            self.assertEqual(view.parentWidget().objectName(), "catalogPanel")
            self.assertEqual(view.palette().color(QPalette.ColorRole.Base), QColor(SURFACE))
            self.assertEqual(view.palette().color(QPalette.ColorGroup.Inactive, QPalette.ColorRole.Highlight),
                             QColor(SELECTION))
        self.assertEqual(dialog.query.font().pointSizeF(), 10)
        self.assertGreaterEqual(dialog.table.verticalHeader().defaultSectionSize(), ROW_HEIGHT)
        self.assertFalse(dialog.table.showGrid())
        self.assertTrue(window.add_button.isEnabled())

    def test_buttons_follow_splitter_and_find_button_is_twice_its_natural_width(self):
        window = self.open_window()

        def check_alignment():
            for button, view in ((window.add_button, window.discs), (window.search_button, window.tree)):
                self.assertEqual(button.geometry().left(), view.parentWidget().geometry().left())
                self.assertEqual(button.width(), view.parentWidget().width())

        check_alignment()
        widths = window.splitter.sizes()
        window.splitter.setSizes([widths[0] + 60, widths[1] - 60])
        self.app.processEvents()
        self.assertGreater(window.add_button.width(), widths[0])
        self.assertLess(window.search_button.width(), widths[1])
        check_alignment()
        window.resize(window.width() + 40, window.height())
        self.app.processEvents()
        check_alignment()
        window.show_search()
        find = window.search_window.find_button
        self.assertEqual(find.width(), 2 * find.sizeHint().width())

    def test_tree_is_lazy_and_sorts_size_numerically(self):
        window = self.open_window()
        self.assertEqual([window.discs.item(row).text() for row in range(3)], ["2", "10", "37"])
        folder = window.tree.topLevelItem(0)
        self.assertEqual(folder.icon(0).cacheKey(), window.icons[0].cacheKey())
        self.assertEqual(folder.childCount(), 0)
        folder.setExpanded(True)
        self.assertEqual(folder.child(0).text(0), "Съёмка.AVI")
        self.assertEqual(folder.child(0).icon(0).cacheKey(), window.icons[1].cacheKey())
        folder.setExpanded(False)
        folder.setExpanded(True)
        self.assertEqual(folder.childCount(), 1)
        window.discs.setCurrentRow(1)
        self.assertEqual(window.tree.topLevelItem(0).text(0), ".DS_Store")
        window.discs.setCurrentRow(2)
        window.tree.sortItems(2, Qt.SortOrder.AscendingOrder)
        self.assertEqual(window.tree.topLevelItem(0).text(0), "Корень")

    def wait_for_results(self, dialog):
        self.assertIsNotNone(dialog.worker)
        deadline = monotonic() + 5
        while dialog.worker is not None and monotonic() < deadline:
            self.app.processEvents()
            sleep(0.005)
        self.assertIsNone(dialog.worker)

    def test_add_disc_updates_existing_tree_and_search(self):
        window = self.open_window()
        volume = self.directory / "disc"
        (volume / "Фильмы").mkdir(parents=True)
        (volume / "Фильмы" / "Ньюрексан.MOV").write_bytes(b"file")
        window.show_search()
        dialog = window.search_window
        dialog.query.setText("НЬЮРЕКСАН")
        dialog.start_search()
        self.wait_for_results(dialog)
        self.assertEqual(dialog.results.total, 2)

        proposals = []
        def create_dialog(path, parent):
            addition = AddDiscDialog(path, parent, drives=FixtureDrive(volume))

            def start_when_ready():
                if addition.worker is not None:
                    QTimer.singleShot(5, start_when_ready)
                elif addition.start.isEnabled():
                    proposals.append(addition.number.text())
                    addition.start.click()
                else:
                    addition.reject()

            QTimer.singleShot(5, start_when_ready)
            return addition

        with patch("catalog.gui.AddDiscDialog", side_effect=create_dialog), patch(
            "catalog.gui.QMessageBox.information"
        ) as success:
            window.add_button.click()
        self.assertEqual(proposals, ["38"])
        self.assertIn("Диск №38 добавлен", success.call_args.args[2])
        self.assertEqual(window.discs.currentItem().text(), "38")
        folder = window.tree.topLevelItem(0)
        folder.setExpanded(True)
        self.assertEqual(folder.child(0).text(0), "Ньюрексан.MOV")
        self.assertEqual(dialog.results.total, 0)
        dialog.start_search()
        self.wait_for_results(dialog)
        self.assertEqual(dialog.results.total, 3)
        dialog.close()

    def test_scan_progress_and_cancel_leave_event_loop_responsive(self):
        window = self.open_window()
        addition = AddDiscDialog(self.path, window, drives=FixtureDrive(self.directory))
        addition.show()
        addition.discover()
        self.wait_for_results(addition)
        baseline = self.path.read_bytes()
        event_loop_ran = Event()

        def pending(path, number, volume, drives, control, progress):
            progress("Тестовый путь", 4, 1)
            deadline = monotonic() + 3
            while monotonic() < deadline:
                control.check()
                sleep(0.01)
            raise CatalogError("Отмена не поступила")

        def cancel_when_displayed():
            if addition.worker is not None:
                if "Тестовый путь" not in addition.status.text():
                    QTimer.singleShot(5, cancel_when_displayed)
                    return
                event_loop_ran.set()
                addition.cancel_button.click()

        with patch("catalog.add_disc_dialog.scan_and_add", side_effect=pending):
            addition.start.click()
            QTimer.singleShot(5, cancel_when_displayed)
            self.wait_for_results(addition)
        self.assertTrue(event_loop_ran.is_set())
        self.assertFalse(addition.isVisible())
        self.assertIsNone(addition.added)
        self.assertEqual(self.path.read_bytes(), baseline)

    def test_add_dialog_chooses_readable_volume_and_rejects_occupied_number(self):
        window = self.open_window()
        first, second = self.directory / "first", self.directory / "second"
        first.mkdir()
        second.mkdir()
        (second / "Второй диск.TXT").write_bytes(b"demo")
        drives = FixtureDrive(first)
        other = FixtureDrive(second).medium
        addition = AddDiscDialog(self.path, window, drives=drives)
        addition.show()
        with patch.object(drives, "roots", return_value=[first, second]), patch.object(
            drives, "volume", side_effect=lambda root: other if root == second else drives.medium
        ):
            addition.discover()
            self.wait_for_results(addition)
            self.assertTrue(addition.drive.isEnabled())
            self.assertEqual(addition.drive.count(), 2)
            self.assertEqual(addition.number.text(), "38")
            addition.drive.setCurrentIndex(1)
            addition.number.setText("37")
            addition.start.click()
            self.wait_for_results(addition)
            self.assertIn("№37 уже есть", addition.status.text())
            self.assertTrue(addition.start.isEnabled())
            addition.number.setText("36")
            addition.start.click()
            self.wait_for_results(addition)
        self.assertEqual(addition.added, (36, 1, 0))
        with open_catalog(self.path) as db:
            self.assertEqual(disc_numbers(db), (2, 10, 36, 37))
            added, = folder_entries(db, 36, "")
            self.assertEqual(added["display_name"], "Второй диск.TXT")

    def test_search_via_enter_handles_casefold_and_all_batches(self):
        window = self.open_window()
        window.show_search()
        dialog = window.search_window
        dialog.query.setText("ьЮРЕК")
        QTest.keyClick(dialog.query, Qt.Key.Key_Return)
        self.wait_for_results(dialog)
        self.assertEqual(dialog.results.total, 2)
        self.assertEqual(dialog.results.rowCount(), 3)
        self.assertEqual([dialog.results.index(i, 5).data() for i in (1, 2)], [2, 10])
        self.assertEqual(dialog.results.index(1, 1).data(), "Папка")
        dialog.query.setText("КАДР")
        dialog.find_button.click()
        self.wait_for_results(dialog)
        self.assertEqual(dialog.results.total, 540)
        self.assertEqual(dialog.results.index(540, 4).data(), "Кадр 539")
        dialog.query.setText("   ")
        dialog.find_button.click()
        self.assertIsNone(dialog.worker)
        self.assertEqual(dialog.results.rowCount(), 0)
        self.assertIn("введите запрос", dialog.windowTitle())

    def test_eight_groups_preserve_rows_and_toggle(self):
        archive = self.directory / "types.xml"
        self.path = self.directory / "types.db"
        ext_names = ("AVI", "avi", "WAV", "RAR", "rar", "jpg", "PSD", "psb", "AI", "pdf", "xyz", None)
        root = ET.Element("REPORT")
        root.extend((
            item("Пример", kind="Folder"),
            *(item(f"Пример.part{number}", ext=ext) for number, ext in enumerate(ext_names)),
            item(None, ext="DS_Store"),
        ))
        ET.ElementTree(root).write(archive, encoding="utf-8")
        import_catalog(archive, self.path, verify_full_archive=False)
        window = self.open_window()
        window.show_search()
        dialog = window.search_window
        dialog.query.setText("РИМЕР")
        dialog.start_search()
        self.wait_for_results(dialog)
        model = dialog.results
        self.assertEqual(model.total, 13)
        self.assertEqual(model.rowCount(), 21)
        self.assertEqual(dialog.windowTitle(), "Поиск — найдено 13")
        self.assertEqual(model.index(0, 0).data(Qt.ItemDataRole.BackgroundRole).color(), QColor(GROUP))
        self.assertEqual(
            [model.index(row, 0).data() for row in model.starts],
            [f"{name} ({count})" for name, count in zip(GROUP_NAMES, (1, 2, 1, 2, 1, 3, 1, 2))],
        )
        for group, (_, records) in enumerate(model.groups):
            for offset, row in enumerate(records, start=1):
                idx = model.index(model.starts[group] + offset, 0)
                self.assertEqual(idx.data(), row["display_name"])
                self.assertEqual(idx.data(Qt.ItemDataRole.DecorationRole).cacheKey(),
                                 model.icons[group].cacheKey())
        archive_header = model.starts[3]
        rect = dialog.table.visualRect(model.index(archive_header, 0))
        QTest.mouseClick(dialog.table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        self.assertFalse(model.expanded[3])
        self.assertEqual((model.total, model.rowCount()), (13, 19))
        self.assertEqual(dialog.table.columnSpan(model.starts[4], 0), 6)
        QTest.keyClick(dialog.table, Qt.Key.Key_Right)
        self.assertTrue(model.expanded[3])
        self.assertEqual(model.rowCount(), 21)
        dialog.query.setText(".DS_Store")
        dialog.start_search()
        self.wait_for_results(dialog)
        self.assertEqual(model.index(0, 0).data(), "Прочее (1)")
        self.assertEqual(model.index(1, 0).data(), ".DS_Store")

    def test_group_rules_and_dpi_icon_cache(self):
        window = self.open_window()
        cases = (("Folder", "AVI", 0), ("File", ".AVI", 1), ("File", "aVi", 1),
                 ("File", ".RAR", 3), ("File", "rar", 3), ("File", "", 7), ("File", "DS_Store", 7))
        for kind, extension, group in cases:
            self.assertEqual(file_group(kind, extension), group)
        self.assertEqual(len({icon.cacheKey() for icon in window.icons}), 8)
        for icon in window.icons:
            pixmap = icon.pixmap(QSize(16, 16), 1.5)
            self.assertEqual((pixmap.width(), pixmap.devicePixelRatio()), (24, 1.5))

    def test_restarts_empty_query_and_close_discard_old_work(self):
        window = self.open_window()
        window.show_search()
        dialog = window.search_window
        dialog.query.setText("Кадр")
        dialog.start_search()
        dialog.query.setText("СЪЁМКА")
        dialog.start_search()
        self.wait_for_results(dialog)
        self.assertEqual(dialog.results.total, 1)
        self.assertEqual(dialog.results.index(1, 0).data(), "Съёмка.AVI")
        dialog.query.setText("Кадр")
        dialog.start_search()
        dialog.query.clear()
        dialog.start_search()
        self.wait_for_results(dialog)
        self.assertEqual(dialog.results.total, 0)
        self.assertIn("введите запрос", dialog.windowTitle())
        dialog.query.setText("Кадр")
        dialog.start_search()
        dialog.close()
        window.show_search()
        self.wait_for_results(dialog)
        self.assertEqual(dialog.results.rowCount(), 0)

    def answer_deletion(self, window, button):
        observed = []

        def respond():
            prompt = self.app.activeModalWidget()
            observed.append((prompt.text(), prompt.defaultButton().text(), prompt.informativeText()))
            prompt.button(button).click()

        QTimer.singleShot(0, respond)
        window.confirm_deletion(window.discs.item(0))
        self.assertIn("№2", observed[0][0])
        self.assertEqual(observed[0][1], "Отмена")
        self.assertIn("только записи в базе", observed[0][2])

    def test_cancel_and_confirm_deletion_on_copy(self):
        window = self.open_window()
        original = self.path.read_bytes()
        self.answer_deletion(window, QMessageBox.StandardButton.Cancel)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(window.discs.count(), 3)
        window.show_search()
        window.search_window.query.setText("ньюрексан")
        window.search_window.start_search()
        self.wait_for_results(window.search_window)
        self.answer_deletion(window, QMessageBox.StandardButton.Yes)
        self.assertEqual(window.search_window.results.rowCount(), 0)
        self.assertEqual(window.discs.count(), 2)
        with open_catalog(self.path) as db:
            self.assertEqual(disc_numbers(db), (10, 37))
            counts = db.execute("SELECT disc_num, COUNT(*) FROM entries GROUP BY disc_num")
            self.assertEqual([tuple(row) for row in counts], [(10, 2), (37, 541)])
            self.assertEqual(validate(db)["integrity_check"], "ok")
        self.assertEqual((self.directory / "fixture.db").read_bytes(), original)

    def test_delete_rolls_back_if_cascade_fails(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("""
                CREATE TRIGGER block_folder_removal BEFORE DELETE ON entries
                WHEN old.kind = 'Folder' BEGIN SELECT RAISE(ABORT, 'test'); END
            """)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "test"):
            delete_disc(self.path, 2)
        self.assertEqual(self.path.read_bytes(), before)
        with open_catalog(self.path) as db:
            self.assertEqual(disc_numbers(db), (2, 10, 37))
            self.assertEqual(validate(db)["integrity_check"], "ok")

    def test_unreadable_catalogs_are_not_replaced(self):
        settings = QSettings(str(self.settings_path), QSettings.Format.IniFormat)
        missing = self.directory / "missing.db"
        with self.assertRaises(sqlite3.OperationalError):
            CatalogWindow(missing, settings)
        self.assertFalse(missing.exists())
        corrupt = self.directory / "broken.db"
        corrupt.write_bytes(b"not a database")
        with self.assertRaises(sqlite3.DatabaseError):
            CatalogWindow(corrupt, settings)
        self.assertEqual(corrupt.read_bytes(), b"not a database")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA user_version = 99")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(CatalogError, "версия схемы: 99"):
            CatalogWindow(self.path, settings)
        self.assertEqual(self.path.read_bytes(), before)

    def test_window_sizes_and_columns_are_restored(self):
        window = self.open_window()
        window.resize(730, 500)
        window.move(40, 55)
        window.splitter.setSizes([207, 495])
        split = window.splitter.sizes()
        window.tree.setColumnWidth(0, 320)
        window.show_search()
        window.search_window.resize(685, 359)
        window.search_window.move(73, 82)
        window.search_window.table.setColumnWidth(0, 239)
        window.search_window.close()
        window.close()

        restored = self.open_window()
        self.assertEqual(restored.size(), window.size())
        self.assertEqual(restored.pos(), window.pos())
        self.assertEqual(restored.splitter.sizes(), split)
        self.assertEqual(restored.tree.columnWidth(0), 320)
        restored.show_search()
        self.assertEqual(restored.search_window.size(), window.search_window.size())
        self.assertEqual(restored.search_window.pos(), window.search_window.pos())
        self.assertEqual(restored.search_window.table.columnWidth(0), 239)

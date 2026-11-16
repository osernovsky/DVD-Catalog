"""Главное окно DVD Catalog для Windows."""

import sqlite3

from PySide6.QtCore import QByteArray, QSettings, QSize, Qt, Slot
from PySide6.QtWidgets import (
    QApplication, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .database import CatalogError, delete_disc, disc_numbers, folder_entries, open_catalog
from .file_icons import file_icons
from .file_types import file_group
from .search_window import ALIGN_RIGHT, SearchWindow, format_date, format_size
from .theme import OUTER_MARGIN, PANEL_GAP, ROW_HEIGHT, apply_theme, panel

DATA_ROLE = Qt.ItemDataRole.UserRole
READ_ERRORS = (CatalogError, OSError, sqlite3.Error)


class TreeEntry(QTreeWidgetItem):
    def __init__(self, row, icons):
        disc, kind, name, path, size, date, time, extension, has_children = row
        timestamp = format_date(date, time)
        super().__init__([name, extension, format_size(size), timestamp])
        self.path = path
        self.sort_values = (name.casefold(), extension.casefold(), size, timestamp)
        self.setIcon(0, icons[file_group(kind, extension)])
        self.setToolTip(0, name)
        self.setTextAlignment(2, ALIGN_RIGHT)
        if has_children:
            self.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)

    def __lt__(self, other):
        column = self.treeWidget().sortColumn()
        return (self.sort_values[column], self.path) < (other.sort_values[column], other.path)


class CatalogWindow(QMainWindow):
    def __init__(self, path, settings):
        super().__init__()
        self.path = path
        self.settings = settings
        self.setWindowTitle("DVD Catalog")
        self.resize(1040, 625)
        self.setMinimumSize(490, 310)
        self.search_window = None

        self.add_button = QPushButton("ДОБАВИТЬ ДИСК")
        self.add_button.setEnabled(False)
        self.add_button.setToolTip("Добавление физических дисков появится на следующем этапе")
        self.search_button = QPushButton("ПОИСК")
        self.search_button.setProperty("primaryAction", True)
        self.search_button.clicked.connect(self.show_search)

        self.discs = QListWidget()
        self.discs.setAccessibleName("Диски")
        self.discs.setUniformItemSizes(True)
        self.discs.setMinimumWidth(72)
        self.discs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.discs.customContextMenuRequested.connect(self.show_disc_menu)

        self.tree = QTreeWidget()
        self.tree.setAccessibleName("Содержимое диска")
        self.tree.setHeaderLabels(["Имя", "Расширение", "Размер, байт", "Дата"])
        self.tree.setUniformRowHeights(True)
        self.tree.setIconSize(QSize(16, 16))
        self.tree.setMouseTracking(True)
        self.tree.header().setStretchLastSection(False)
        for column, width in enumerate((440, 100, 116, 150)):
            self.tree.setColumnWidth(column, width)
        self.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.tree.setSortingEnabled(True)
        self.tree.itemExpanded.connect(self.expand_folder)
        self.icons = file_icons()

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        for button, view in ((self.add_button, self.discs), (self.search_button, self.tree)):
            column = QWidget()
            column_layout = QVBoxLayout(column)
            column_layout.setContentsMargins(0, 0, 0, 0)
            column_layout.setSpacing(PANEL_GAP)
            column_layout.addWidget(button)
            column_layout.addWidget(panel(view), 1)
            self.splitter.addWidget(column)
        self.splitter.setHandleWidth(PANEL_GAP)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([146, 873])
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(OUTER_MARGIN, OUTER_MARGIN, OUTER_MARGIN, OUTER_MARGIN)
        layout.addWidget(self.splitter, 1)
        self.setCentralWidget(body)

        self.restoreGeometry(settings.value("main/geometry", QByteArray(), type=QByteArray))
        self.splitter.restoreState(settings.value("main/splitter", QByteArray(), type=QByteArray))
        self.tree.header().restoreState(settings.value("main/columns", QByteArray(), type=QByteArray))

        self.discs.currentItemChanged.connect(self.show_disc)
        with open_catalog(path, timeout=1) as db:
            numbers = disc_numbers(db)
            # Проверяем также доступность таблицы элементов перед открытием окна.
            db.execute("SELECT display_name, extension FROM entries LIMIT 1").fetchone()
        for number in numbers:
            item = QListWidgetItem(str(number), self.discs)
            item.setData(DATA_ROLE, number)
            item.setSizeHint(QSize(0, max(ROW_HEIGHT, self.discs.fontMetrics().height() + 8)))
        if numbers:
            self.discs.setCurrentRow(0)

    def read_branch(self, disc_num, path):
        with open_catalog(self.path, timeout=1) as db:
            return [TreeEntry(row, self.icons) for row in folder_entries(db, disc_num, path)]

    def show_disc(self, item):
        self.tree.clear()
        if item is not None:
            try:
                self.tree.addTopLevelItems(self.read_branch(item.data(DATA_ROLE), ""))
            except READ_ERRORS as error:
                QMessageBox.warning(self, "Ошибка чтения", f"Не удалось открыть диск.\n{error}")

    def expand_folder(self, item):
        if item.childIndicatorPolicy() != QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator:
            return
        disc_num = self.discs.currentItem().data(DATA_ROLE)
        try:
            children = self.read_branch(disc_num, item.path)
        except READ_ERRORS as error:
            item.setExpanded(False)
            QMessageBox.warning(self, "Ошибка чтения", f"Не удалось открыть папку.\n{error}")
            return
        item.addChildren(children)
        item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicatorWhenChildless)

    @Slot()
    def show_search(self):
        if self.search_window is None:
            self.search_window = SearchWindow(self.path, self.settings, self.icons, self)
        self.search_window.show()
        self.search_window.raise_()
        self.search_window.activateWindow()
        self.search_window.query.setFocus()
        self.search_window.query.selectAll()

    def show_disc_menu(self, position):
        item = self.discs.itemAt(position)
        if item is None:
            return
        menu = QMenu(self)
        remove = menu.addAction("Удалить диск из каталога…")
        if menu.exec(self.discs.viewport().mapToGlobal(position)) == remove:
            self.confirm_deletion(item)

    def confirm_deletion(self, item):
        number = item.data(DATA_ROLE)
        prompt = QMessageBox(self)
        prompt.setIcon(QMessageBox.Icon.Warning)
        prompt.setWindowTitle("Удаление диска")
        prompt.setText(f"Удалить диск №{number} из каталога?")
        prompt.setInformativeText(
            "Будут удалены только записи в базе. Файлы на физическом CD/DVD останутся на диске."
        )
        prompt.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        prompt.button(QMessageBox.StandardButton.Yes).setText("Удалить")
        prompt.button(QMessageBox.StandardButton.Cancel).setText("Отмена")
        prompt.setDefaultButton(QMessageBox.StandardButton.Cancel)
        prompt.setEscapeButton(QMessageBox.StandardButton.Cancel)
        if prompt.exec() != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_disc(self.path, number)
        except READ_ERRORS as error:
            QMessageBox.warning(self, "Ошибка удаления", f"Не удалось удалить диск №{number}.\n{error}")
            return
        self.discs.takeItem(self.discs.row(item))
        if self.search_window is not None:
            self.search_window.invalidate()

    def closeEvent(self, event):
        self.settings.setValue("main/geometry", self.saveGeometry())
        self.settings.setValue("main/splitter", self.splitter.saveState())
        self.settings.setValue("main/columns", self.tree.header().saveState())
        if self.search_window is not None:
            if self.search_window.isVisible():
                self.search_window.save_settings()
            self.search_window.shutdown()
        self.settings.sync()
        super().closeEvent(event)


def run_gui(path):
    app = QApplication([])
    apply_theme(app)
    settings = QSettings("DVD Catalog", "DVD Catalog")
    try:
        if not path.is_file():
            raise CatalogError("Файл базы не найден")
        window = CatalogWindow(path, settings)
    except READ_ERRORS as error:
        QMessageBox.critical(None, "DVD Catalog", f"Не удалось открыть каталог.\n{path}\n{error}")
        return 1
    window.show()
    return app.exec()

"""Окно поиска и фоновое чтение результатов."""

import sqlite3
from bisect import bisect_right

from PySide6.QtCore import QAbstractTableModel, QByteArray, QModelIndex, QSize, QThread, Qt, Signal, Slot
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QLineEdit, QMessageBox, QPushButton, QStyle, QTableView,
    QVBoxLayout,
)

from .database import CatalogError, open_catalog, search
from .file_types import GROUP_NAMES, file_group
from .theme import CONTROL_GAP, OUTER_MARGIN, PANEL_GAP, ROW_HEIGHT, panel

ALIGN_RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


def format_size(size):
    return f"{size:,}".replace(",", "\u202f")


def format_date(date, time):
    return " ".join(filter(None, (date, time)))


class SearchResults(QAbstractTableModel):
    headers = ("Название", "Расширение / тип", "Размер, байт", "Дата", "Путь внутри диска", "Диск")

    def __init__(self, icons, parent):
        super().__init__(parent)
        self.icons = icons
        self.parent_widget = parent
        self.groups = ()
        self.starts = []
        self.expanded = []
        self.visible_rows = 0
        self.total = 0
        self.group_font = QFont(parent.font())
        self.group_font.setBold(True)
        self.arrows = (
            parent.style().standardIcon(QStyle.StandardPixmap.SP_ArrowRight),
            parent.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown),
        )

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.visible_rows

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole
                and 0 <= section < len(self.headers)):
            return self.headers[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        column = index.column()
        group_index = bisect_right(self.starts, index.row()) - 1
        group, rows = self.groups[group_index]
        offset = index.row() - self.starts[group_index]
        if not offset:
            if role == Qt.ItemDataRole.BackgroundRole:
                return self.parent_widget.palette().brush(QPalette.ColorRole.AlternateBase)
            if column == 0:
                if role == Qt.ItemDataRole.DisplayRole:
                    return f"{GROUP_NAMES[group]} ({len(rows)})"
                if role == Qt.ItemDataRole.DecorationRole:
                    return self.arrows[self.expanded[group_index]]
                if role == Qt.ItemDataRole.FontRole:
                    return self.group_font
                if role == Qt.ItemDataRole.AccessibleDescriptionRole:
                    return "Группа раскрыта" if self.expanded[group_index] else "Группа свёрнута"
            return None
        row = rows[offset - 1]
        if role == Qt.ItemDataRole.DecorationRole and column == 0:
            return self.icons[group]
        if role == Qt.ItemDataRole.TextAlignmentRole and column in (2, 5):
            return ALIGN_RIGHT
        if role == Qt.ItemDataRole.ToolTipRole and column in (0, 4):
            return row[2 if column == 0 else 3]
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        disc, kind, name, path, size, date, time, extension = row
        if column == 2:
            return format_size(size)
        if column == 3:
            return format_date(date, time)
        type_label = "Папка" if kind == "Folder" else (extension or "Файл")
        return (name, type_label, None, None, path, disc)[column]

    def replace(self, grouped_rows):
        self.beginResetModel()
        self.groups = tuple((group, rows) for group, rows in enumerate(grouped_rows) if rows)
        self.total = sum(len(rows) for _, rows in self.groups)
        self.expanded = [True] * len(self.groups)
        self.update_offsets()
        self.endResetModel()

    def update_offsets(self):
        row = 0
        self.starts = []
        for expanded, (_, rows) in zip(self.expanded, self.groups, strict=True):
            self.starts.append(row)
            row += 1 + (len(rows) if expanded else 0)
        self.visible_rows = row

    def toggle(self, row, expanded=None):
        if row not in self.starts:
            return False
        group_index = self.starts.index(row)
        was_expanded = self.expanded[group_index]
        if expanded is None:
            expanded = not was_expanded
        if expanded != was_expanded:
            end = row + len(self.groups[group_index][1])
            if expanded:
                self.beginInsertRows(QModelIndex(), row + 1, end)
            else:
                self.beginRemoveRows(QModelIndex(), row + 1, end)
            self.expanded[group_index] = expanded
            self.update_offsets()
            if expanded:
                self.endInsertRows()
            else:
                self.endRemoveRows()
            header = self.index(row, 0)
            self.dataChanged.emit(header, header)
        return True

    def clear(self):
        self.replace(())


class GroupedResultsView(QTableView):
    def __init__(self, results):
        super().__init__()
        self.setModel(results)
        self.clicked.connect(self.toggle_group)
        results.modelReset.connect(self.span_headers)

    @Slot(QModelIndex)
    def toggle_group(self, index):
        self.model().toggle(index.row())

    @Slot()
    def span_headers(self):
        self.clearSpans()
        for row in self.model().starts:
            self.setSpan(row, 0, 1, self.model().columnCount())

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Left, Qt.Key.Key_Right):
            expanded = {Qt.Key.Key_Left: False, Qt.Key.Key_Right: True}.get(key)
            if self.model().toggle(self.currentIndex().row(), expanded):
                event.accept()
                return
        super().keyPressEvent(event)


class SearchThread(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, path, query, parent):
        super().__init__(parent)
        self.path = path
        self.query = query

    def run(self):
        groups = [[] for _ in GROUP_NAMES]
        try:
            with open_catalog(self.path, timeout=1) as db:
                db.set_progress_handler(self.isInterruptionRequested, 1000)
                cursor = search(db, self.query, include_extension=True)
                while not self.isInterruptionRequested():
                    batch = cursor.fetchmany(512)
                    if not batch:
                        break
                    for row in batch:
                        groups[file_group(row[1], row[7])].append(row)
        except (CatalogError, OSError, sqlite3.Error) as error:
            if not self.isInterruptionRequested():
                self.failed.emit(str(error))
        else:
            if not self.isInterruptionRequested():
                self.ready.emit(groups)


class SearchWindow(QDialog):
    def __init__(self, path, settings, icons, parent):
        super().__init__(parent)
        self.path = path
        self.settings = settings
        self.worker = None
        self.discard_results = False
        self.pending_query = None
        self.setWindowTitle("Поиск")
        self.resize(950, 470)
        self.setMinimumSize(440, 240)
        self.query = QLineEdit()
        self.query.setAccessibleName("Строка поиска")
        self.query.setPlaceholderText("Введите имя или его часть")
        self.find_button = QPushButton("Найти", self)
        self.find_button.setProperty("primaryAction", True)
        self.find_button.setDefault(True)
        self.find_button.setMinimumWidth(2 * self.find_button.sizeHint().width())
        self.find_button.clicked.connect(self.start_search)
        self.query.returnPressed.connect(self.start_search)

        self.results = SearchResults(icons, self)
        self.table = GroupedResultsView(self.results)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setIconSize(QSize(16, 16))
        self.table.setWordWrap(False)
        self.table.setMouseTracking(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(max(ROW_HEIGHT, self.table.fontMetrics().height() + 8))
        for column, width in enumerate((203, 140, 116, 149, 223, 58)):
            self.table.setColumnWidth(column, width)

        form = QHBoxLayout()
        form.setSpacing(CONTROL_GAP)
        form.addWidget(self.query, 1)
        form.addWidget(self.find_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(OUTER_MARGIN, OUTER_MARGIN, OUTER_MARGIN, OUTER_MARGIN)
        layout.setSpacing(PANEL_GAP)
        layout.addLayout(form)
        layout.addWidget(panel(self.table))
        self.restoreGeometry(settings.value("search/geometry", QByteArray(), type=QByteArray))
        self.table.horizontalHeader().restoreState(
            settings.value("search/columns", QByteArray(), type=QByteArray)
        )
        self.finished.connect(self.search_closed)

    @Slot()
    def start_search(self):
        self.invalidate()
        query = self.query.text()
        if not query.strip():
            self.setWindowTitle("Поиск — введите запрос")
            self.query.setFocus()
            return
        self.setWindowTitle("Поиск — выполняется…")
        if self.worker is None:
            self.launch_search(query)
        else:
            self.pending_query = query

    def launch_search(self, query):
        self.discard_results = False
        self.worker = SearchThread(self.path, query, self)
        self.worker.ready.connect(self.show_results)
        self.worker.failed.connect(self.search_failed)
        self.worker.finished.connect(self.search_finished)
        self.worker.start()

    @Slot(object)
    def show_results(self, groups):
        if not self.discard_results:
            self.results.replace(groups)
            self.setWindowTitle(f"Поиск — найдено {self.results.total:,}".replace(",", "\u202f"))

    @Slot(str)
    def search_failed(self, message):
        if not self.discard_results:
            self.invalidate()
            QMessageBox.warning(self, "Ошибка поиска", f"Не удалось прочитать базу.\n{message}")

    @Slot()
    def search_finished(self):
        self.worker.deleteLater()
        self.worker = None
        if self.pending_query is not None:
            query, self.pending_query = self.pending_query, None
            self.launch_search(query)

    def invalidate(self):
        self.discard_results = True
        self.pending_query = None
        if self.worker is not None:
            self.worker.requestInterruption()
        self.results.clear()
        self.setWindowTitle("Поиск")

    @Slot()
    def search_closed(self):
        self.invalidate()
        self.save_settings()

    @Slot()
    def save_settings(self):
        self.settings.setValue("search/geometry", self.saveGeometry())
        self.settings.setValue("search/columns", self.table.horizontalHeader().saveState())
        self.settings.sync()

    def shutdown(self):
        self.invalidate()
        if self.worker is not None:
            self.worker.requestInterruption()
            self.worker.wait()

"""Диалог добавления записанного оптического носителя."""

import sqlite3
from time import monotonic

from PySide6.QtCore import QRegularExpression, QThread, Qt, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from .database import CatalogError, check_disc_number, next_disc_number, open_catalog
from .disc_scanner import ScanCancelled, ScanControl, WindowsOpticalDrives, find_readable_volumes, scan_and_add


class DiscWorker(QThread):
    progress = Signal(str, int, int)

    def __init__(self, drives, operation, parent):
        super().__init__(parent)
        self.drives = drives
        self.operation = operation
        self.control = ScanControl()
        self.result = None
        self.error = None
        self.last_progress = float("-inf")

    def report_progress(self, path, files, folders):
        now = monotonic()
        if now - self.last_progress >= 0.08:
            self.last_progress = now
            self.progress.emit(path, files, folders)

    def run(self):
        try:
            with self.drives.suppress_drive_prompts():
                self.result = self.operation(self.control, self.report_progress)
                self.control.check()
        except ScanCancelled:
            pass
        except (CatalogError, OSError, sqlite3.Error) as error:
            self.error = str(error)


class AddDiscDialog(QDialog):
    def __init__(self, path, parent, *, drives=None):
        super().__init__(parent)
        self.path = path
        self.drives = WindowsOpticalDrives() if drives is None else drives
        self.worker = None
        self.closing = False
        self.volumes = []
        self.added = None
        self.setWindowTitle("Добавить диск")
        self.setMinimumWidth(420)

        self.drive = QComboBox()
        self.number = QLineEdit()
        self.number.setValidator(QRegularExpressionValidator(QRegularExpression("[0-9]{1,19}"), self))
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.start = QPushButton("Сканировать")
        self.start.setProperty("primaryAction", True)
        self.start.setDefault(True)
        self.start.clicked.connect(self.scan)
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.clicked.connect(self.reject)
        buttons = QDialogButtonBox()
        buttons.addButton(self.start, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(self.cancel_button, QDialogButtonBox.ButtonRole.RejectRole)
        form = QFormLayout()
        form.addRow("Привод", self.drive)
        form.addRow("Номер диска", self.number)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.status)
        layout.addWidget(buttons)

    def discover(self):
        def operation(control, progress):
            volumes = find_readable_volumes(self.drives, control)
            with open_catalog(self.path, timeout=1) as db:
                return volumes, next_disc_number(db)

        self.run_worker(operation, self.discovery_finished, "Проверка оптических приводов…")

    def run_worker(self, operation, handler, message):
        self.start.setEnabled(False)
        self.drive.setEnabled(False)
        self.number.setEnabled(False)
        self.status.setText(message)
        self.worker = DiscWorker(self.drives, operation, self)
        self.worker.progress.connect(self.show_progress)
        self.worker.finished.connect(handler)
        self.worker.start()

    def finish_worker(self):
        worker = self.worker
        self.worker = None
        worker.deleteLater()
        if self.closing:
            super().reject()
            return None
        if worker.error:
            self.status.setText(worker.error)
            return None
        return worker.result

    def discovery_finished(self):
        result = self.finish_worker()
        if result is None:
            return
        self.volumes, proposal = result
        for volume in self.volumes:
            self.drive.addItem(f"{volume.root}   {volume.label or 'Без метки'}")
        self.number.setText(str(proposal) if proposal is not None else "")
        self.status.setText("Проверьте номер перед сканированием.")
        self.enable_inputs()
        self.number.setFocus()
        self.number.selectAll()

    def enable_inputs(self):
        self.start.setEnabled(True)
        self.number.setEnabled(True)
        self.drive.setEnabled(len(self.volumes) > 1)

    def scan(self):
        if self.worker is not None or not self.volumes:
            return
        if not self.number.hasAcceptableInput():
            self.status.setText("Введите положительный номер диска.")
            return
        number = int(self.number.text())
        volume = self.volumes[self.drive.currentIndex()]

        def operation(control, progress):
            with open_catalog(self.path, timeout=1) as db:
                check_disc_number(db, number)
            return scan_and_add(self.path, number, volume, self.drives, control, progress)

        self.run_worker(operation, self.scan_finished, "Сканирование диска…")

    def scan_finished(self):
        result = self.finish_worker()
        if result is not None:
            self.added = result
            self.accept()
        elif not self.closing:
            self.enable_inputs()

    def show_progress(self, path, files, folders):
        if not self.closing:
            self.status.setText(f"{path}\nФайлов: {files} · Папок: {folders}")

    def reject(self):
        if self.worker is None:
            super().reject()
        elif self.worker.control.cancel():
            self.closing = True
            self.cancel_button.setEnabled(False)
            self.status.setText("Отмена… Ожидание завершения текущей операции.")

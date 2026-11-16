"""Чтение метаданных оптического тома Windows и добавление в каталог."""

import ctypes
import ntpath
import os
import stat
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import NamedTuple

from .database import MAX_SQLITE_INTEGER, CatalogError, insert_optical_disc

DRIVE_CDROM = 5
SEM_FAILCRITICALERRORS = 1
NO_MEDIA_ERRORS = {21, 1112}


class ScanCancelled(Exception):
    pass


class ScanControl:
    """Синхронизирует отмену с переходом к COMMIT."""

    def __init__(self):
        self.lock = Lock()
        self.cancelled = False
        self.committing = False

    def cancel(self):
        with self.lock:
            if self.committing:
                return False
            self.cancelled = True
            return True

    def check(self):
        with self.lock:
            if self.cancelled:
                raise ScanCancelled

    def begin_commit(self):
        with self.lock:
            if self.cancelled:
                raise ScanCancelled
            self.committing = True


@dataclass(frozen=True)
class Volume:
    root: Path
    label: str
    serial: int
    filesystem: str


class Entry(NamedTuple):
    kind: str
    parent: str
    name: str
    extension: str
    size: int
    date: str
    time: str
    folded: str


@dataclass
class ScanResult:
    entries: list[Entry]
    files: int
    folders: int


class WindowsOpticalDrives:
    def __init__(self):
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        dword_pointer = ctypes.POINTER(wintypes.DWORD)
        signatures = {
            "GetLogicalDrives": (wintypes.DWORD, []),
            "GetDriveTypeW": (wintypes.UINT, [wintypes.LPCWSTR]),
            "GetVolumeInformationW": (wintypes.BOOL, [
                wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, dword_pointer,
                dword_pointer, dword_pointer, wintypes.LPWSTR, wintypes.DWORD,
            ]),
            "GetThreadErrorMode": (wintypes.DWORD, []),
            "SetThreadErrorMode": (wintypes.BOOL, [wintypes.DWORD, dword_pointer]),
        }
        for name, (result, arguments) in signatures.items():
            function = getattr(api, name)
            function.restype = result
            function.argtypes = arguments
        self.api = api

    @contextmanager
    def suppress_drive_prompts(self):
        previous = self.api.GetThreadErrorMode()
        if not self.api.SetThreadErrorMode(previous | SEM_FAILCRITICALERRORS, None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            self.api.SetThreadErrorMode(previous, None)

    def roots(self):
        mask = self.api.GetLogicalDrives()
        if not mask:
            raise ctypes.WinError(ctypes.get_last_error())
        roots = (Path(f"{chr(65 + bit)}:\\") for bit in range(26) if mask & (1 << bit))
        return [root for root in roots if self.api.GetDriveTypeW(str(root)) == DRIVE_CDROM]

    def volume(self, root):
        if self.api.GetDriveTypeW(str(root)) != DRIVE_CDROM:
            raise CatalogError(f"Оптический привод {root} недоступен.")
        label = ctypes.create_unicode_buffer(261)
        filesystem = ctypes.create_unicode_buffer(261)
        serial = wintypes.DWORD()
        if not self.api.GetVolumeInformationW(
            str(root), label, len(label), ctypes.byref(serial), None, None, filesystem, len(filesystem)
        ):
            code = ctypes.get_last_error()
            if code in NO_MEDIA_ERRORS:
                raise CatalogError(f"Вставьте записанный диск в привод {root}.")
            raise CatalogError(f"Не удалось прочитать том {root}\n{ctypes.WinError(code)}")
        try:
            with os.scandir(root) as entries:
                next(entries, None)
        except OSError as error:
            raise CatalogError(f"Не удалось прочитать корневую папку {root}\n{error}") from error
        return Volume(root, label.value, serial.value, filesystem.value)


def find_readable_volumes(drives, control):
    control.check()
    roots = drives.roots()
    if not roots:
        raise CatalogError("Оптические приводы CD/DVD не найдены.")
    volumes = []
    errors = []
    for root in roots:
        control.check()
        try:
            volumes.append(drives.volume(root))
        except CatalogError as error:
            errors.append(str(error))
    if not volumes:
        raise CatalogError("\n".join(errors))
    return volumes


def scan_tree(root, control, progress):
    pending = [(root, "")]
    totals = {"": 0}
    entries = []
    folders = []
    files = 0
    while pending:
        directory, parent = pending.pop()
        control.check()
        progress(str(directory), files, len(folders))
        location = directory
        try:
            if os.lstat(directory).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise CatalogError(f"Точка повторной обработки не поддерживается: {directory}")
            with os.scandir(directory) as children:
                for child in children:
                    control.check()
                    location = child.path
                    name = child.name
                    if name in ("", ".", "..") or any(c in name for c in "\\/\0"):
                        raise CatalogError(f"Недопустимое имя: {location}")
                    info = child.stat(follow_symlinks=False)
                    if info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                        raise CatalogError(f"Точка повторной обработки не поддерживается: {location}")
                    modified = datetime.fromtimestamp(info.st_mtime)
                    date, time = modified.date().isoformat(), modified.time().isoformat(timespec="seconds")
                    if stat.S_ISDIR(info.st_mode):
                        relative = f"{parent}\\{name}" if parent else name
                        totals[relative] = 0
                        folders.append((len(entries), relative))
                        pending.append((Path(location), relative))
                        entries.append(Entry("Folder", parent, name, "", 0, date, time, name.casefold()))
                    elif stat.S_ISREG(info.st_mode) and 0 <= info.st_size <= MAX_SQLITE_INTEGER:
                        stem, suffix = ntpath.splitext(name)
                        if len(suffix) <= 1:
                            stem, suffix = name, ""
                        totals[parent] += info.st_size
                        files += 1
                        entries.append(Entry("File", parent, stem, suffix[1:], info.st_size,
                                             date, time, name.casefold()))
                    else:
                        raise CatalogError(f"Неподдерживаемый объект или размер: {location}")
                    if len(entries) % 100 == 0:
                        progress(str(location), files, len(folders))
                    location = directory
        except (OSError, OverflowError, ValueError) as error:
            raise CatalogError(f"Не удалось прочитать метаданные или список файлов: {location}\n{error}") from error

    for index, relative in reversed(folders):
        control.check()
        size = totals[relative]
        if size > MAX_SQLITE_INTEGER:
            raise CatalogError(f"Суммарный размер слишком велик: {relative}")
        entry = entries[index]
        entries[index] = entry._replace(size=size)
        totals[entry.parent] += size
    return ScanResult(entries, files, len(folders))


def scan_and_add(path, number, volume, drives, control, progress):
    def check_volume():
        control.check()
        if drives.volume(volume.root) != volume:
            raise CatalogError(f"Носитель в приводе {volume.root} изменился. Начните сканирование заново.")

    def before_commit():
        check_volume()
        control.begin_commit()

    check_volume()
    result = scan_tree(volume.root, control, progress)
    check_volume()
    progress("Сохранение каталога…", result.files, result.folders)
    insert_optical_disc(path, number, volume.label, result.entries,
                        check_cancelled=control.check, before_commit=before_commit)
    return number, result.files, result.folders

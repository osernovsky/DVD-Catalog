"""Сборка и проверка ZIP с портативной программой на Windows x64."""

import os
import platform
import re
import shutil
import struct
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

NAME = "DVD-Catalog"
EXE = f"{NAME}.exe"
PROJECT = Path(__file__).resolve().parent
EXCLUDED = re.compile(r"\.(db3?|sqlite3?|ctf|bak|xml)(?:$|-)|-(journal|wal|shm)$", re.I)


def verify_tree(package):
    if {item.name for item in package.iterdir()} != {EXE, "_internal"}:
        raise ValueError(f"Неожиданные файлы в {package}. Папка сохранена без изменений.")
    members = []
    for item in package.rglob("*"):
        if item.is_symlink() or item.is_junction() or EXCLUDED.search(item.name):
            raise ValueError(f"Недопустимый элемент поставки: {item}")
        if item.is_file():
            with item.open("rb") as stream:
                if stream.read(16) == b"SQLite format 3\0":
                    raise ValueError(f"В поставке найдена база SQLite: {item}")
            members.append(item.relative_to(package))
    if Path(EXE) not in members or not any(
        path.name.lower() == "qwindows.dll" and path.parent.name == "platforms" for path in members
    ):
        raise ValueError("В сборке отсутствует EXE или плагин Qt для Windows.")
    with (package / EXE).open("rb") as stream:
        dos = stream.read(64)
        if dos[:2] != b"MZ":
            raise ValueError("Некорректный заголовок EXE.")
        stream.seek(struct.unpack_from("<I", dos, 60)[0])
        pe = stream.read(94)
        if (pe[:4] != b"PE\0\0" or struct.unpack_from("<H", pe, 4)[0] != 0x8664
                or struct.unpack_from("<H", pe, 92)[0] != 2):
            raise ValueError("Требуется оконный EXE Windows x64.")
    return sorted(members)


def write_zip(package, archive, members):
    expected = {f"{NAME}/{item.as_posix()}" for item in members}
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as output:
        for item in members:
            output.write(package / item, f"{NAME}/{item.as_posix()}")
    with ZipFile(archive) as output:
        if set(output.namelist()) != expected or output.testzip() is not None:
            raise ValueError("Проверка ZIP завершилась ошибкой.")


@contextmanager
def publication_stage(dist):
    # На Windows TemporaryDirectory ограничивает доступ владельцем. При
    # переименовании внутри тома этот ACL сохранился бы у готового архива.
    stage = dist / f".portable-{uuid4().hex}"
    stage.mkdir()
    try:
        yield stage
    finally:
        if stage.resolve() != stage or stage.parent != dist:
            raise ValueError(f"Временная папка сборки изменилась: {stage}")
        shutil.rmtree(stage)


def main():
    if os.name != "nt" or platform.machine().upper() != "AMD64" or struct.calcsize("P") != 8:
        raise SystemExit("Требуется Windows x64 и 64-разрядный Python.")
    dist = PROJECT / "dist"
    if dist.resolve() != dist:
        raise SystemExit(f"Ожидалась локальная папка проекта: {dist}")
    dist.mkdir(exist_ok=True)
    package = dist / NAME
    archive = dist / f"{NAME}-Windows-x64.zip"
    if package.is_symlink() or package.is_junction() or archive.is_symlink():
        raise SystemExit("Сборочный результат должен находиться в обычной папке dist.")
    if package.exists():
        verify_tree(package)
    with publication_stage(dist) as stage:
        windows = Path(os.environ["SystemRoot"])
        build_path = os.pathsep.join(map(str, (windows / "System32", windows, Path(sys.executable).parent)))
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--name", NAME, "--onedir", "--windowed",
            "--noupx", "--contents-directory", "_internal", "--distpath", str(stage),
            "--workpath", str(stage / "work"), "--specpath", str(stage),
            "--exclude-module", "PySide6.QtNetwork",
            str(PROJECT / "portable_launcher.py"),
        ], cwd=PROJECT, env={
            **os.environ, "PATH": build_path, "PYINSTALLER_CONFIG_DIR": str(stage / "cache"),
        }, check=True)
        fresh = stage / NAME
        # Qt Windows использует системный ICU. Сторонний icuuc.dll с версионными
        # именами функций перекрывает системный DLL и ломает загрузку QtCore.
        for library in (fresh / "_internal").glob("icu*.dll"):
            if re.fullmatch(r"icu(?:in|uc|dt)?[0-9]*\.dll", library.name, re.I):
                library.unlink()
        members = verify_tree(fresh)
        fresh_zip = stage / archive.name
        write_zip(fresh, fresh_zip, members)
        previous = stage / "previous"
        if package.exists():
            verify_tree(package)
            package.rename(previous)
        try:
            fresh.rename(package)
            try:
                fresh_zip.replace(archive)
            except OSError:
                package.rename(fresh)
                raise
        except OSError:
            if previous.exists():
                previous.rename(package)
            raise
    print(f"{len(members)} файлов\n{package / EXE}\n{archive}")


if __name__ == "__main__":
    main()

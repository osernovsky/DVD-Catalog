"""Собирает один Windows EXE и отдельную новую пустую базу SQLite."""

import argparse
import os
import platform
import re
import struct
import subprocess
import sys
from pathlib import Path, PurePosixPath

from build_portable import EXCLUDED, EXE, NAME, PROJECT, publication_stage, verify_executable, write_zip
from prepare_release import create_empty_database


def verify_onefile(executable):
    from PyInstaller.archive.readers import CArchiveReader

    verify_executable(executable)
    archive = CArchiveReader(str(executable))
    names = {name.replace("\\", "/") for name in archive.toc}
    required = {"python3.dll", "Qt6Core.dll", "Qt6Widgets.dll", "LICENSE",
                "THIRD_PARTY_NOTICES.txt", "third-party-licenses/LGPL-3.0.txt"}
    if not required.issubset(names) or not any(
        PurePosixPath(name).name.lower() == "qwindows.dll" for name in names
    ):
        raise ValueError("В EXE отсутствуют необходимые библиотеки или лицензии.")
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or EXCLUDED.search(name):
            raise ValueError(f"Недопустимый элемент внутри EXE: {name}")
        if re.fullmatch(r"icu(?:in|uc|dt)?[0-9]*\.dll", path.name, re.I):
            raise ValueError("В EXE обнаружен сторонний ICU, несовместимый с Qt Windows.")


def build_onefile(output):
    if os.name != "nt" or platform.machine().upper() != "AMD64" or struct.calcsize("P") != 8:
        raise ValueError("Требуется Windows x64 и 64-разрядный Python.")
    output = output.absolute()
    archive_path = output.with_name(f"{output.name}-Windows-x64.zip")
    if os.path.lexists(output) or os.path.lexists(archive_path):
        raise FileExistsError("Папка или ZIP уже существуют. Укажите новую папку --output.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with publication_stage(output.parent.resolve()) as stage:
        windows = Path(os.environ["SystemRoot"])
        build_path = os.pathsep.join(map(str, (windows / "System32", windows, Path(sys.executable).parent)))
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(stage),
            "--workpath", str(stage / "work"), str(PROJECT / "onefile.spec"),
        ], cwd=PROJECT, env={
            **os.environ, "PATH": build_path, "PYINSTALLER_CONFIG_DIR": str(stage / "cache"),
        }, check=True)
        verify_onefile(stage / EXE)
        package = stage / NAME
        package.mkdir()
        (stage / EXE).rename(package / EXE)
        create_empty_database(package / "catalog.db")
        zip_path = stage / archive_path.name
        write_zip(package, zip_path, [Path(EXE), Path("catalog.db")])
        # Существующая пользовательская папка и ZIP никогда не заменяются.
        package.rename(output)
        try:
            zip_path.rename(archive_path)
        except OSError:
            output.rename(package)
            raise
    return output, archive_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT / "dist/DVD-Catalog-OneFile")
    args = parser.parse_args()
    output, archive = build_onefile(args.output)
    print(output / EXE)
    print(archive)


if __name__ == "__main__":
    main()

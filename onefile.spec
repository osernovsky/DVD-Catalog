"""Портативный EXE с библиотеками и лицензиями внутри файла."""

import re
from pathlib import Path

project = Path(SPECPATH)
analysis = Analysis(
    [str(project / "portable_launcher.py")],
    pathex=[str(project)],
    binaries=[],
    datas=[
        (str(project / "LICENSE"), "."),
        (str(project / "release/THIRD_PARTY_NOTICES.txt"), "."),
        (str(project / "release/licenses"), "third-party-licenses"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6.QtNetwork"],
    noarchive=False,
)
# Qt Windows использует системный ICU. Сторонний ICU удаляется до упаковки.
analysis.binaries = [
    item for item in analysis.binaries
    if not re.fullmatch(r"icu(?:in|uc|dt)?[0-9]*\.dll", Path(item[0]).name, re.I)
]
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="DVD-Catalog",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

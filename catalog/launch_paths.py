"""Путь к пользовательской базе в исходниках и портативной сборке."""

import sys
from pathlib import Path


def default_database():
    folder = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
              else Path(__file__).resolve().parents[1])
    return folder / "catalog.db"

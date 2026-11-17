"""Команды DVD Catalog для импорта, проверки, поиска и запуска интерфейса."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .database import CatalogError, disc_entries, missing_numbers, open_catalog, search, statistics, validate
from .importer import ARCHIVE_PART_SUFFIXES, import_catalog
from .launch_paths import default_database


def print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("import", "check", "disc", "search"):
        action = commands.add_parser(command)
        action.add_argument("--db", type=Path, default=Path("catalog.db"))
        if command == "import":
            action.add_argument("--xml", type=Path, default=Path("input/whereisit.xml"))
        elif command == "disc":
            action.add_argument("number", type=int)
        elif command == "search":
            action.add_argument("query")
    gui = commands.add_parser("gui", help="Открыть графический каталог")
    gui.add_argument("--db", type=Path, default=default_database())
    args = parser.parse_args()
    if args.command == "search" and not args.query:
        parser.error("Введите непустую строку поиска")

    try:
        if args.command == "gui":
            try:
                from .gui import run_gui
            except ModuleNotFoundError as error:
                if error.name != "PySide6":
                    raise
                raise CatalogError("Установите PySide6: python -m pip install -r requirements.txt") from error
            return run_gui(args.db)
        if args.command == "import":
            print_json(import_catalog(args.xml, args.db))
            return 0
        with open_catalog(args.db) as db:
            if args.command == "check":
                checks = validate(db)
                numbers, totals = statistics(db)
                print_json({
                    "discs": len(numbers), "missing_numbers": missing_numbers(numbers),
                    "last_disc": numbers[-1] if numbers else None,
                    "next_disc": numbers[-1] + 1 if numbers else 1,
                    **totals, **checks,
                    "journal_mode": db.execute("PRAGMA journal_mode").fetchone()[0],
                    "database_bytes": args.db.stat().st_size,
                    "archive_examples": [
                        dict(row)
                        for number, suffix in ARCHIVE_PART_SUFFIXES.items()
                        for row in disc_entries(db, number)
                        if row["kind"] == "File" and row["display_name"].casefold().endswith(suffix)
                    ],
                })
            elif args.command == "disc":
                disc = db.execute("SELECT * FROM discs WHERE disc_num = ?", (args.number,)).fetchone()
                if disc is None:
                    raise CatalogError(f"Диск №{args.number} отсутствует")
                print_json({"disc": dict(disc), "entries": [dict(row) for row in disc_entries(db, args.number)]})
            else:
                for row in search(db, args.query):
                    print(json.dumps(dict(row), ensure_ascii=False))
    except (CatalogError, OSError, sqlite3.Error) as error:
        print(error, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Операция прервана пользователем", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())

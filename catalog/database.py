"""Схема и запросы к переносимой базе каталога."""

import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

SCHEMA_VERSION = 1
SCHEMA = r"""
CREATE TABLE discs (
    disc_num INTEGER PRIMARY KEY CHECK (disc_num > 0),
    volume_label TEXT NOT NULL,
    media_type TEXT NOT NULL CHECK (media_type <> '')
) STRICT;

CREATE TABLE entries (
    id INTEGER PRIMARY KEY,
    disc_num INTEGER NOT NULL REFERENCES discs(disc_num) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('File', 'Folder')),
    parent_path TEXT NOT NULL,
    name TEXT NOT NULL,
    extension TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    modified_date TEXT,
    modified_time TEXT,
    display_name TEXT GENERATED ALWAYS AS (
        name || CASE WHEN extension <> '' THEN '.' || extension ELSE '' END
    ) VIRTUAL,
    full_path TEXT GENERATED ALWAYS AS (
        CASE WHEN parent_path <> '' THEN parent_path || '\' ELSE '' END || display_name
    ) VIRTUAL,
    name_folded TEXT NOT NULL,
    CHECK (display_name <> ''),
    CHECK (kind = 'File' OR extension = ''),
    UNIQUE (disc_num, kind, full_path)
) STRICT;

CREATE INDEX entries_by_parent ON entries(disc_num, parent_path);
"""

RESULT_COLUMNS = "disc_num, kind, display_name, full_path, size_bytes, modified_date, modified_time"
SELECT_RESULTS = f"SELECT {RESULT_COLUMNS} FROM entries"
INSERT_ENTRY = """
INSERT INTO entries (
    disc_num, kind, parent_path, name, extension, size_bytes,
    modified_date, modified_time, name_folded
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
MAX_SQLITE_INTEGER = 2**63 - 1


class CatalogError(Exception):
    """Ошибка формата или проверки каталога."""


def create_schema(db):
    db.execute("PRAGMA foreign_keys = ON")
    if db.execute("PRAGMA journal_mode = DELETE").fetchone()[0] != "delete":
        raise CatalogError("Не удалось установить journal_mode=DELETE")
    db.execute("PRAGMA synchronous = FULL")
    db.executescript(SCHEMA)
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


@contextmanager
def open_catalog(path: Path, *, writable=False, timeout=5):
    mode = "rw" if writable else "ro"
    uri = path.resolve().as_uri() + f"?mode={mode}"
    with closing(sqlite3.connect(uri, uri=True, timeout=timeout)) as db:
        db.execute("PRAGMA foreign_keys = ON")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            raise CatalogError(f"Неподдерживаемая версия схемы: {version}")
        db.row_factory = sqlite3.Row
        yield db


def search(db, query, *, include_extension=False):
    columns = RESULT_COLUMNS + ", extension" if include_extension else RESULT_COLUMNS
    return db.execute(
        f"""SELECT {columns} FROM entries
        WHERE instr(name_folded, ?) > 0
        ORDER BY disc_num, name_folded, full_path, kind
        """,
        (query.casefold(),),
    )


def disc_entries(db, disc_num):
    return db.execute(
        SELECT_RESULTS + " WHERE disc_num = ? ORDER BY full_path, kind", (disc_num,)
    )


def disc_numbers(db):
    return tuple(row[0] for row in db.execute("SELECT disc_num FROM discs ORDER BY disc_num"))


def folder_entries(db, disc_num, parent_path):
    return db.execute(
        f"""SELECT {RESULT_COLUMNS}, extension,
            kind = 'Folder' AND EXISTS (
                SELECT 1 FROM entries AS child
                WHERE child.disc_num = entries.disc_num AND child.parent_path = entries.full_path
            ) AS has_children
        FROM entries
        WHERE disc_num = ? AND parent_path = ?
        ORDER BY kind DESC, name_folded, full_path""",
        (disc_num, parent_path),
    )


def delete_disc(path: Path, disc_num):
    with open_catalog(path, writable=True, timeout=1) as db, db:
        deleted = db.execute("DELETE FROM discs WHERE disc_num = ?", (disc_num,))
        if deleted.rowcount != 1:
            raise CatalogError(f"Диск №{disc_num} отсутствует")


def next_disc_number(db):
    maximum = db.execute("SELECT COALESCE(MAX(disc_num), 0) FROM discs").fetchone()[0]
    return maximum + 1 if maximum < MAX_SQLITE_INTEGER else None


def check_disc_number(db, number):
    if not 1 <= number <= MAX_SQLITE_INTEGER:
        raise CatalogError("Введите положительный номер диска в диапазоне SQLite.")
    if db.execute("SELECT 1 FROM discs WHERE disc_num = ?", (number,)).fetchone():
        raise CatalogError(f"Диск №{number} уже есть в каталоге.")


def insert_optical_disc(path, number, label, entries, *, check_cancelled, before_commit):
    """Принимает метаданные полного успешного обхода."""
    def parameters():
        for entry in entries:
            check_cancelled()
            yield (number, *entry)

    with open_catalog(path, writable=True, timeout=1) as db, db:
        check_cancelled()
        db.execute("PRAGMA synchronous = FULL")
        db.execute("BEGIN IMMEDIATE")
        check_disc_number(db, number)
        db.execute("INSERT INTO discs VALUES (?, ?, 'Optical')", (number, label))
        db.executemany(INSERT_ENTRY, parameters())
        before_commit()


def validate(db):
    integrity = [row[0] for row in db.execute("PRAGMA integrity_check")]
    if integrity != ["ok"]:
        raise CatalogError(f"PRAGMA integrity_check: {integrity!r}")
    violations = [tuple(row) for row in db.execute("PRAGMA foreign_key_check")]
    if violations:
        raise CatalogError(f"Нарушены внешние ключи: {violations!r}")
    orphans = db.execute("""
        SELECT e.disc_num, e.full_path, e.parent_path
        FROM entries AS e
        WHERE e.parent_path <> '' AND NOT EXISTS (
            SELECT 1 FROM entries AS p
            WHERE p.disc_num = e.disc_num AND p.kind = 'Folder' AND p.full_path = e.parent_path
        )
        ORDER BY e.disc_num, e.full_path
        """).fetchall()
    if orphans:
        details = "\n".join(
            f"Диск №{disc_num}: {path!r}, отсутствует папка {parent!r}"
            for disc_num, path, parent in orphans
        )
        raise CatalogError(f"Непривязанных элементов: {len(orphans)}\n{details}")
    duplicates = db.execute("""
        SELECT disc_num, kind, full_path, COUNT(*)
        FROM entries GROUP BY disc_num, kind, full_path HAVING COUNT(*) > 1
        """).fetchall()
    if duplicates:
        raise CatalogError(f"Повторы логических путей: {[tuple(row) for row in duplicates]!r}")
    return {"integrity_check": "ok", "foreign_key_errors": 0, "orphans": 0, "duplicates": 0}


def statistics(db):
    numbers = disc_numbers(db)
    totals = db.execute(r"""
        SELECT COUNT(*) AS entries,
               COUNT(*) FILTER (WHERE kind = 'Folder') AS folders,
               COUNT(*) FILTER (WHERE kind = 'File') AS files,
               COUNT(*) FILTER (WHERE kind = 'File' AND parent_path = '') AS root_files,
               COUNT(*) FILTER (WHERE kind = 'File' AND extension = '') AS extensionless_files,
               MAX(CASE WHEN parent_path = '' THEN 0 ELSE
                   1 + length(parent_path) - length(replace(parent_path, '\', ''))
               END) AS max_parent_depth,
               MAX(size_bytes) AS max_size_bytes,
               SUM(size_bytes) FILTER (WHERE kind = 'File') AS total_file_bytes
        FROM entries
        """).fetchone()
    keys = ("entries", "folders", "files", "root_files", "extensionless_files",
            "max_parent_depth", "max_size_bytes", "total_file_bytes")
    return numbers, dict(zip(keys, totals, strict=True))


def missing_numbers(numbers):
    """Пропуски до последнего диска, с объединением последовательных номеров."""
    gaps = []
    previous = 0
    for number in numbers:
        if number == previous + 2:
            gaps.append(str(previous + 1))
        elif number > previous + 2:
            gaps.append(f"{previous + 1}–{number - 1}")
        previous = number
    return gaps

"""Создаёт архивы первого запуска и обновления из чистой оконной сборки."""

import argparse
import hashlib
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from zipfile import ZipFile

from build_portable import NAME, PROJECT, publication_stage, verify_tree, write_zip
from catalog.database import SCHEMA_VERSION, create_schema, next_disc_number, open_catalog

TAG = "v1.0.0-beta.2"
INSTALL = f"{NAME}-{TAG}-Windows-x64.zip"
UPDATE = f"{NAME}-{TAG}-Update-Windows-x64.zip"
DB = Path("catalog.db")


def create_empty_database(path):
    with path.open("xb"):
        pass
    with closing(sqlite3.connect(path)) as db, db:
        create_schema(db)
    check_empty_database(path)


def check_empty_database(path):
    with open_catalog(path) as db:
        counts = [db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                  for table in ("discs", "entries")]
        if (counts != [0, 0] or next_disc_number(db) != 1
                or db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or db.execute("PRAGMA foreign_key_check").fetchone() is not None
                or db.execute("PRAGMA journal_mode").fetchone()[0] != "delete"
                or db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION):
            raise ValueError("Недопустимое состояние пустой базы для релиза.")
    if any(path.parent.glob(path.name + "-*")):
        raise ValueError("Рядом с пустой базой обнаружены служебные файлы SQLite.")


def prepare_release(bundle, output):
    bundle = bundle.resolve()
    library_files = verify_tree(bundle)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in (INSTALL, UPDATE)):
        raise FileExistsError("Релизные архивы уже существуют. Укажите новую папку вывода.")

    with publication_stage(output) as stage:
        package = stage / NAME
        shutil.copytree(bundle, package)
        shutil.copyfile(PROJECT / "LICENSE", package / "LICENSE")
        shutil.copyfile(PROJECT / "release/THIRD_PARTY_NOTICES.txt",
                        package / "THIRD_PARTY_NOTICES.txt")
        readme = (PROJECT / "README.md").read_text(encoding="utf-8")
        guide, section, _ = readme.partition("\n## Сборка и тесты\n")
        if not section:
            raise ValueError("В README.md отсутствует раздел «Сборка и тесты».")
        guide = "\n".join(line.removeprefix("## ").removeprefix("# ")
                          for line in guide.splitlines())
        (package / "README.txt").write_text(guide.strip() + "\n", encoding="utf-8")
        extra = [Path("LICENSE"), Path("README.txt"), Path("THIRD_PARTY_NOTICES.txt")]
        licenses = Path("_internal/third-party-licenses")
        shutil.copytree(PROJECT / "release/licenses", package / licenses)
        extra.extend(p.relative_to(package) for p in (package / licenses).rglob("*") if p.is_file())
        members = sorted([*library_files, *extra])
        write_zip(package, stage / UPDATE, members)
        create_empty_database(package / DB)
        write_zip(package, stage / INSTALL, sorted([*members, DB]))
        with ZipFile(stage / INSTALL) as first, ZipFile(stage / UPDATE) as update:
            db_member = f"{NAME}/{DB}"
            first_files = {item.filename: (item.file_size, item.CRC) for item in first.infolist()}
            update_files = {item.filename: (item.file_size, item.CRC) for item in update.infolist()}
            if first_files.pop(db_member, None) is None or first_files != update_files:
                raise ValueError("Комплекты должны различаться только наличием пустой базы.")
            if first.read(db_member) != (package / DB).read_bytes():
                raise ValueError("Содержимое пустой базы в ZIP изменилось.")
        for name in (INSTALL, UPDATE):
            (stage / name).rename(output / name)
    return [output / name for name in (INSTALL, UPDATE)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=PROJECT / "dist/release")
    args = parser.parse_args()
    for archive in prepare_release(args.bundle, args.output):
        with archive.open("rb") as stream, ZipFile(archive) as bundle:
            print(archive.name, len(bundle.namelist()), archive.stat().st_size,
                  hashlib.file_digest(stream, "sha256").hexdigest())


if __name__ == "__main__":
    main()

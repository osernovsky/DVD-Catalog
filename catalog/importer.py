"""Потоковый импорт полного экспорта WhereIsIt."""

import codecs
import os
import re
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET

from .database import INSERT_ENTRY, CatalogError, create_schema, missing_numbers, search, statistics, validate

FORBIDDEN_BYTES = bytes(value for value in range(32) if value not in (9, 10, 13))
ARCHIVE_NUMBERS = tuple(number for number in range(1, 1518) if number != 36)
ARCHIVE_SIZE = 72_044_907
ARCHIVE_REMOVED = 1078
ARCHIVE_TOTALS = {
    "entries": 166_282, "folders": 16_322, "files": 149_960, "root_files": 506,
    "extensionless_files": 1744, "max_parent_depth": 11, "max_size_bytes": 4_702_610_286,
    "total_file_bytes": 6_089_351_489_878,
}
ARCHIVE_PART_SUFFIXES = {1514: ".part1.rar", 1515: ".part2.rar"}
FIELDS = frozenset(
    ("NAME", "EXT", "SIZE", "DATE", "TIME", "DISK_NUM", "DISK_NAME", "DISK_TYPE", "PATH",
     "DESCRIPTION", "CRC")
)


class FilteredInput:
    def __init__(self, stream):
        self.stream = stream
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.removed = 0
        self.bytes_read = 0

    def read(self, size):
        while True:
            block = self.stream.read(size)
            if not block:
                self.decoder.decode(b"", final=True)
                return b""
            filtered = block.translate(None, FORBIDDEN_BYTES)
            self.bytes_read += len(block)
            self.removed += len(block) - len(filtered)
            self.decoder.decode(filtered)
            if filtered:
                return filtered


def iter_items(source):
    parser = ET.XMLParser(encoding="utf-8")
    events = ET.iterparse(source, events=("start", "end"), parser=parser)
    depth = 0
    for event, element in events:
        if event == "start":
            depth += 1
            if depth == 1:
                if element.tag != "REPORT":
                    raise CatalogError("Ожидался корневой элемент REPORT")
                root = element
            elif depth == 2 and element.tag != "ITEM":
                raise CatalogError(f"Неожиданный элемент REPORT: {element.tag!r}")
        else:
            if depth == 2:
                yield element
                root.remove(element)
                element.clear()
            depth -= 1


def integer_field(fields, key, minimum):
    value = fields.get(key, "")
    if not re.fullmatch(r"[0-9]{1,19}", value):
        raise CatalogError(f"Недопустимое значение {key}: {value!r}")
    number = int(value)
    if not minimum <= number <= 2**63 - 1:
        raise CatalogError(f"Значение {key} вне диапазона SQLite: {value!r}")
    return number


def valid_component(value):
    return value not in ("", ".", "..") and "\\" not in value and "/" not in value


def read_item(item):
    fields = {}
    kind = item.get("ItemType")
    if kind not in ("File", "Folder"):
        raise CatalogError(f"Неизвестный ItemType: {kind!r}")
    for child in item:
        if child.tag not in FIELDS or child.tag in fields or len(child):
            raise CatalogError(f"Неожиданное или повторное поле: {child.tag!r}")
        fields[child.tag] = child.text or ""

    disc_num = integer_field(fields, "DISK_NUM", 1)
    size = integer_field(fields, "SIZE", 0)
    volume = fields.get("DISK_NAME")
    media = fields.get("DISK_TYPE")
    if volume is None or not media:
        raise CatalogError("Нет DISK_NAME или DISK_TYPE")

    path = fields.get("PATH", "")
    parent = path[1:]
    if not path.startswith("\\") or (parent and not all(
        valid_component(part) for part in parent.split("\\")
    )):
        raise CatalogError(f"Недопустимый родительский PATH: {path!r}")

    name = fields.get("NAME", "")
    ext = fields.get("EXT", "")
    display_name = f"{name}.{ext}" if ext else name
    if not valid_component(display_name) or (kind == "Folder" and ext):
        raise CatalogError(f"Недопустимое имя или расширение: NAME={name!r}, EXT={ext!r}")

    entry = (disc_num, kind, parent, name, ext, size, fields.get("DATE") or None,
             fields.get("TIME") or None, display_name.casefold())
    return disc_num, (volume, media), entry


def import_items(db, source):
    discs = {}
    counts = Counter()
    index = 0
    latest_disc = None
    try:
        for index, item in enumerate(iter_items(source), start=1):
            try:
                disc_num, metadata, entry = read_item(item)
                if disc_num not in discs:
                    db.execute("INSERT INTO discs VALUES (?, ?, ?)", (disc_num, *metadata))
                    discs[disc_num] = metadata
                elif discs[disc_num] != metadata:
                    raise CatalogError(
                        f"Противоречивые DISK_NAME/DISK_TYPE: {discs[disc_num]!r} и {metadata!r}"
                    )
                db.execute(INSERT_ENTRY, entry)
            except (CatalogError, sqlite3.Error) as error:
                number = item.findtext("DISK_NUM", "?")
                raise CatalogError(
                    f"ITEM #{index}, диск №{number}, PATH={item.findtext('PATH')!r}, "
                    f"NAME={item.findtext('NAME')!r}, EXT={item.findtext('EXT')!r}: {error}"
                ) from error
            counts[entry[1]] += 1
            latest_disc = disc_num
    except (ET.ParseError, UnicodeError) as error:
        raise CatalogError(
            f"Ошибка XML после {index} записей, последний обработанный диск "
            f"№{latest_disc if latest_disc is not None else '?'}: {error}"
        ) from error
    if not counts:
        raise CatalogError("Экспорт не содержит элементов ITEM")
    return tuple(sorted(discs)), counts


def verify_archive(db, source, numbers, totals):
    differences = []
    expected_metrics = {"source_bytes": ARCHIVE_SIZE, "removed_xml_bytes": ARCHIVE_REMOVED,
                        **ARCHIVE_TOTALS}
    actual_metrics = {"source_bytes": source.bytes_read, "removed_xml_bytes": source.removed,
                      **totals}
    for key, expected in expected_metrics.items():
        actual = actual_metrics[key]
        if actual != expected:
            differences.append(f"{key}: {actual}, ожидалось {expected}")
    if numbers != ARCHIVE_NUMBERS:
        missing = sorted(set(ARCHIVE_NUMBERS).difference(numbers))
        extra = sorted(set(numbers).difference(ARCHIVE_NUMBERS))
        differences.append(f"Не совпали номера дисков. Недостающие: {missing}, лишние: {extra}")
    for number, suffix in ARCHIVE_PART_SUFFIXES.items():
        if not any(row[0] == number and row[1] == "File" and row[2].casefold().endswith(suffix)
                   for row in search(db, suffix)):
            differences.append(f"На диске №{number} отсутствует часть архива {suffix!r}")
    if differences:
        raise CatalogError("Расхождения с ожидаемыми показателями архива WhereIsIt:\n" +
                           "\n".join(differences))


def import_catalog(xml_path: Path, destination: Path, *, verify_full_archive=True):
    if os.path.lexists(destination):
        raise CatalogError(f"База уже существует: {destination}")
    with TemporaryDirectory(prefix=".catalog-import-", dir=destination.parent) as directory:
        temporary_db = Path(directory) / "catalog.db"
        with xml_path.open("rb") as stream, closing(sqlite3.connect(temporary_db)) as db:
            source = FilteredInput(stream)
            create_schema(db)
            with db:
                imported_numbers, counts = import_items(db, source)
            numbers, totals = statistics(db)
            if (numbers != imported_numbers or totals["files"] != counts["File"]
                    or totals["folders"] != counts["Folder"]):
                raise CatalogError("Число записей в SQLite не совпало с разобранным XML")
            checks = validate(db)
            if verify_full_archive:
                verify_archive(db, source, numbers, totals)
            report = {
                "source_bytes": source.bytes_read,
                "removed_xml_bytes": source.removed,
                "discs": len(numbers), "missing_numbers": missing_numbers(numbers),
                "last_disc": numbers[-1], "next_disc": numbers[-1] + 1,
                **totals, **checks,
            }
        report["database_bytes"] = temporary_db.stat().st_size
        # На Windows os.rename атомарно публикует файл на том же томе и отказывает,
        # если назначение появилось после первоначальной проверки.
        if os.name != "nt":
            raise CatalogError("Публикация базы поддерживается только на Windows")
        os.rename(temporary_db, destination)
    return report

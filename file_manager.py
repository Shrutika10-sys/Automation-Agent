"""Append every retrieved Gem to one persistent CSV."""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_CSV_NAME = "gems.csv"
_CSV_HEADER = ["Sl No.", "Gem Name", "Description", "Instructions"]
_LEGACY_HEADER = ["Name", "Description", "Instructions"]
_RUN_DIR = re.compile(r"^run (\d+)$")


class FileManagerError(Exception):
    """Raised when the existing output CSV cannot safely be appended to."""


class FileManager:
    """Append every successfully retrieved Gem to output/gems.csv."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.csv_path = output_dir / _CSV_NAME
        self._next_number = 1
        self.saved_count = 0

    def prepare(self) -> None:
        """Initialize central output, migrating old per-run CSVs once if needed."""

        self.output_dir.mkdir(parents=True, exist_ok=True)
        if self.csv_path.exists() and self.csv_path.stat().st_size > 0:
            self.saved_count = self._existing_record_count()
        else:
            legacy_records = self._read_legacy_records()
            self.saved_count = self._write_records(legacy_records) if legacy_records else 0
            if legacy_records:
                logger.info("Migrated %s legacy Gem records into %s", self.saved_count, self.csv_path)
        self._next_number = self.saved_count + 1
        logger.info("Appending all runs to %s", self.csv_path)

    def save(self, name: str, description: str, instructions: str) -> Path:
        """Append this Gem to the central CSV; failed Gems never add a row."""

        is_new = not self.csv_path.exists() or self.csv_path.stat().st_size == 0
        serial = self._next_number
        with self.csv_path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            if is_new:
                writer.writerow(_CSV_HEADER)
            writer.writerow([serial, name, description, instructions])
            handle.flush()
        self._next_number += 1
        self.saved_count += 1
        logger.info("CSV row saved: %s (#%s)", name, serial)
        return self.csv_path

    def _read_legacy_records(self) -> list[tuple[str, str, str]]:
        run_dirs: list[tuple[int, Path]] = []
        for child in self.output_dir.iterdir():
            match = _RUN_DIR.match(child.name)
            if child.is_dir() and match is not None:
                run_dirs.append((int(match.group(1)), child))
        run_dirs.sort(key=lambda entry: entry[0])

        records: list[tuple[str, str, str]] = []
        for _, run_dir in run_dirs:
            for path in sorted(run_dir.glob("*.csv"), key=lambda item: item.name.casefold()):
                records.extend(self._read_legacy_file(path))
        return records

    @staticmethod
    def _read_legacy_file(path: Path) -> list[tuple[str, str, str]]:
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle)
                header = next(reader, None)
                if header == _LEGACY_HEADER:
                    name_column, description_column, instructions_column = 0, 1, 2
                    expected_columns = 3
                elif header == _CSV_HEADER:
                    name_column, description_column, instructions_column = 1, 2, 3
                    expected_columns = 4
                else:
                    raise FileManagerError(
                        f"Unexpected CSV header in legacy file {path}; "
                        f"expected {', '.join(_LEGACY_HEADER)}."
                    )

                records: list[tuple[str, str, str]] = []
                for record_number, row in enumerate(reader, start=2):
                    if len(row) != expected_columns:
                        raise FileManagerError(
                            f"Invalid record {record_number} in legacy file {path}; "
                            f"expected {expected_columns} columns."
                        )
                    records.append(
                        (row[name_column], row[description_column], row[instructions_column])
                    )
                return records
        except (OSError, UnicodeError, csv.Error) as exc:
            raise FileManagerError(f"Could not read legacy CSV {path}: {exc}") from exc

    def _write_records(self, records: list[tuple[str, str, str]]) -> int:
        with self.csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(_CSV_HEADER)
            for serial, record in enumerate(records, start=1):
                writer.writerow([serial, *record])
            handle.flush()
        return len(records)

    def _existing_record_count(self) -> int:
        if not self.csv_path.exists() or self.csv_path.stat().st_size == 0:
            return 0

        try:
            with self.csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle)
                header = next(reader, None)
                if header != _CSV_HEADER:
                    raise FileManagerError(
                        f"Unexpected CSV header in {self.csv_path}; "
                        f"expected {', '.join(_CSV_HEADER)}."
                    )

                count = 0
                for record_number, row in enumerate(reader, start=2):
                    expected_serial = str(count + 1)
                    if len(row) != len(_CSV_HEADER) or row[0] != expected_serial:
                        raise FileManagerError(
                            f"Invalid record {record_number} in {self.csv_path}; "
                            "expected four columns and continuous Sl No. values."
                        )
                    count += 1
                return count
        except (OSError, UnicodeError, csv.Error) as exc:
            raise FileManagerError(
                f"Could not read existing output CSV {self.csv_path}: {exc}"
            ) from exc

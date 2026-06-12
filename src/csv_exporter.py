"""収集データのCSV出力"""
import csv
import os
from typing import TextIO

from models import PresentationInfo

FIELDNAMES = [
    "大会年度",
    "セッションID",
    "発表ID",
    "発表タイトル",
    "発表サブタイトル",
    "キーワード",
    "要約",
]


class CSVExporter:
    def __init__(self, output_path: str):
        self._output_path = output_path
        self._file: TextIO | None = None
        self._writer: csv.DictWriter | None = None

    def open(self) -> None:
        os.makedirs(os.path.dirname(self._output_path), exist_ok=True)
        # UTF-8 BOM付き（Excelでの文字化け防止）
        self._file = open(self._output_path, "w", encoding="utf-8-sig", newline="")
        self._writer = csv.DictWriter(self._file, fieldnames=FIELDNAMES)
        self._writer.writeheader()

    def write(self, p: PresentationInfo) -> None:
        if self._writer is None:
            raise RuntimeError("CSVExporter is not open. Call open() first.")
        self._writer.writerow(
            {
                "大会年度": p.year,
                "セッションID": p.session_id,
                "発表ID": p.presentation_id,
                "発表タイトル": p.title,
                "発表サブタイトル": p.subtitle or "",
                "キーワード": p.keywords,
                "要約": p.abstract,
            }
        )
        self._file.flush()

    def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None
            self._writer = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()

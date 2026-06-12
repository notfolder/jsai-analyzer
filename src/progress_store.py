"""クロール進捗の保存・再開管理"""
import json
import os
from datetime import datetime, timezone
from typing import Set


class ProgressStore:
    def __init__(self, filepath: str = "output/progress.json"):
        self._filepath = filepath
        self._visited: Set[str] = set()
        self._completed_events: Set[int] = set()

    def load(self) -> None:
        if not os.path.exists(self._filepath):
            return
        try:
            with open(self._filepath, encoding="utf-8") as f:
                data = json.load(f)
            self._visited = set(data.get("visited_urls", []))
            self._completed_events = set(data.get("completed_events", []))
        except (json.JSONDecodeError, OSError):
            pass  # 壊れていたら無視して新規扱い

    def save(self) -> None:
        os.makedirs(os.path.dirname(self._filepath), exist_ok=True)
        data = {
            "visited_urls": sorted(self._visited),
            "completed_events": sorted(self._completed_events),
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        with open(self._filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def is_visited(self, url: str) -> bool:
        return url in self._visited

    def mark_visited(self, url: str) -> None:
        self._visited.add(url)

    def is_event_completed(self, year: int) -> bool:
        return year in self._completed_events

    def mark_event_completed(self, year: int) -> None:
        self._completed_events.add(year)

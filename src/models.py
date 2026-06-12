"""データモデル定義"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EventInfo:
    """大会情報"""
    year: int
    name: str
    top_url: str


@dataclass
class ScheduleDay:
    """日程情報"""
    year: int
    date_str: str      # 例: "20240528"
    label: str         # 例: "2024年5月28日(火)"
    is_poster: bool
    url: str


@dataclass
class SessionInfo:
    """セッション情報"""
    year: int
    session_id: str         # 例: "1E1" または "1B3-GS-2"
    session_raw_id: str     # URLパスに含まれる生ID (例: "1E01-05")
    session_title: str
    url: str
    presentation_urls: list = field(default_factory=list)


@dataclass
class PresentationInfo:
    """発表情報"""
    year: int
    session_id: str         # 例: "1E1"
    presentation_id: str    # 例: "1E1-01"
    title: str
    subtitle: Optional[str]
    keywords: str
    abstract: str
    url: str

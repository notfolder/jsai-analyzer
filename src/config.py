"""クローラー設定"""
from dataclasses import dataclass


@dataclass
class CrawlerConfig:
    base_url: str = "https://confit.atlas.jp"
    min_delay_sec: float = 1.5       # リクエスト間の最小待機秒数
    max_delay_sec: float = 3.5       # リクエスト間の最大待機秒数
    max_retry: int = 3               # 最大リトライ回数
    retry_backoff_sec: float = 5.0   # リトライ時の追加待機（指数バックオフ）
    page_load_timeout_ms: int = 30000
    networkidle_timeout_ms: int = 15000
    headless: bool = True
    output_dir: str = "output"
    start_year: int = 2018
    end_year: int = 2026

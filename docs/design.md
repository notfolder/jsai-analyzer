# 設計書：人工知能学会全国大会 発表情報クローラー

## 1. システム構成図

```
┌─────────────────────────────────────────────────────────────────┐
│                         crawler.py (メインエントリ)              │
└───────────────────────────┬─────────────────────────────────────┘
                            │
          ┌─────────────────┼──────────────────┐
          ▼                 ▼                  ▼
┌─────────────────┐ ┌──────────────┐ ┌────────────────┐
│  EventScraper   │ │ProgressStore │ │  CSVExporter   │
│  (ページ解析)   │ │ (進捗管理)   │ │ (CSV出力)      │
└────────┬────────┘ └──────────────┘ └────────────────┘
         │
         │ Playwright API
         ▼
┌─────────────────┐
│   Browser       │
│ (Chromium)      │
└────────┬────────┘
         │ HTTP
         ▼
┌─────────────────┐
│  confit.atlas   │
│    .jp          │
└─────────────────┘
```

---

## 2. モジュール構成

```
jsai-analyzer/
├── docs/
│   ├── requirements.md         # 要件定義書
│   └── design.md               # 設計書（本書）
├── src/
│   ├── crawler.py              # メインエントリポイント
│   ├── event_scraper.py        # スクレイピングロジック
│   ├── models.py               # データモデル定義
│   ├── progress_store.py       # 進捗管理
│   └── csv_exporter.py         # CSV出力
├── output/                     # 出力ディレクトリ（自動生成）
├── requirements.txt            # 依存ライブラリ
└── README.md
```

---

## 3. クラス設計

### 3.1 データモデル (`models.py`)

```python
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class EventInfo:
    """大会情報"""
    year: int                  # 例: 2024
    name: str                  # 例: "2024年度 人工知能学会全国大会（第38回）"
    top_url: str               # 例: "https://confit.atlas.jp/guide/event/jsai2024/top"

@dataclass
class ScheduleDay:
    """日程情報"""
    year: int
    date_str: str              # 例: "20240528"
    label: str                 # 例: "2024年5月28日(火)"
    is_poster: bool            # ポスターセッションか否か
    url: str

@dataclass
class SessionInfo:
    """セッション情報"""
    year: int
    session_id: str            # 例: "1E1"（タイトルの[...]内から抽出）
    session_raw_id: str        # 例: "1E01-05"（URLから）
    session_title: str         # 例: "基礎・理論-制約充足・最適化・定性推論"
    url: str
    presentation_urls: list[str] = field(default_factory=list)

@dataclass
class PresentationInfo:
    """発表情報"""
    year: int
    session_id: str            # 例: "1E1"
    presentation_id: str       # 例: "1E1-01"
    title: str
    subtitle: Optional[str]
    keywords: str              # コンマ区切り
    abstract: str
    url: str
```

### 3.2 スクレイピングクラス (`event_scraper.py`)

```python
class EventScraper:
    """Playwrightを使用したJSAIサイトのスクレイパー"""

    def __init__(self, config: CrawlerConfig):
        self.config = config

    async def fetch_events(self) -> list[EventInfo]:
        """大会一覧ページから2018年度以降の大会情報を取得"""

    async def fetch_schedule_days(self, event: EventInfo) -> list[ScheduleDay]:
        """大会トップページから全日程URLを取得"""

    async def fetch_sessions(self, day: ScheduleDay) -> list[SessionInfo]:
        """日程タイムテーブルページから全セッション情報を取得"""

    async def fetch_presentations(self, session: SessionInfo) -> list[PresentationInfo]:
        """セッションページから全発表情報を取得"""

    async def fetch_presentation_detail(self, url: str, year: int, session_id: str) -> Optional[PresentationInfo]:
        """個別発表ページから発表詳細情報を取得"""

    async def _get_page_with_retry(self, page, url: str, max_retry: int = 3) -> bool:
        """リトライ付きページ読み込み"""

    async def _random_delay(self):
        """ランダム待機（1〜3秒）"""
```

### 3.3 進捗管理クラス (`progress_store.py`)

```python
class ProgressStore:
    """クロール進捗の保存・読み込み"""

    def __init__(self, filepath: str = "output/progress.json"):
        ...

    def is_visited(self, url: str) -> bool:
        """指定URLが訪問済みか確認"""

    def mark_visited(self, url: str):
        """URLを訪問済みとしてマーク"""

    def save(self):
        """進捗をファイルに保存"""

    def load(self):
        """進捗をファイルから読み込み"""
```

### 3.4 CSV出力クラス (`csv_exporter.py`)

```python
class CSVExporter:
    """収集データのCSV出力"""

    FIELDNAMES = [
        "大会年度", "セッションID", "発表ID",
        "発表タイトル", "発表サブタイトル", "キーワード", "要約"
    ]

    def __init__(self, output_path: str):
        ...

    def write(self, presentation: PresentationInfo):
        """発表情報を1件書き込む（追記モード）"""

    def flush(self):
        """バッファをフラッシュ"""
```

---

## 4. 処理フロー

### 4.1 全体フロー

```
START
  │
  ├─ 進捗ファイルを読み込む（存在する場合）
  │
  ├─ Playwright ブラウザを起動（Chromium ヘッドレス）
  │
  ├─ [FR-01] 大会一覧ページをクロール
  │   └─ 2018年度以降の EventInfo リストを取得
  │
  ├─ EventInfo ごとにループ
  │   │
  │   ├─ [FR-02] 大会トップページをクロール
  │   │   └─ ScheduleDay リストを取得（口頭 + ポスター）
  │   │
  │   └─ ScheduleDay ごとにループ
  │       │
  │       ├─ [FR-03] タイムテーブルページをクロール
  │       │   └─ SessionInfo リストを取得
  │       │
  │       └─ SessionInfo ごとにループ
  │           │
  │           ├─ セッションページをクロール
  │           │   └─ 発表URLリストを取得
  │           │
  │           └─ 発表URL ごとにループ
  │               │
  │               ├─ 訪問済み？ → YES → スキップ
  │               │               NO  ↓
  │               ├─ [FR-04] 発表ページをクロール
  │               │   └─ PresentationInfo を取得
  │               │
  │               ├─ [FR-05] CSV に書き込む
  │               │
  │               └─ 訪問済みとしてマーク
  │
  └─ ブラウザを閉じる
END
```

### 4.2 発表ページ解析フロー

```
発表ページHTML
  │
  ├─ タイトル抽出
  │   └─ <h1> タグ内テキストから "[1E1-01] タイトル" を分離
  │       ├─ 発表ID  → "[...]" 内の文字列
  │       └─ タイトル → "[...]" 以降の文字列
  │
  ├─ サブタイトル抽出
  │   └─ タイトル直下の <p> または <h2> タグを探索
  │       → 存在しない場合は None
  │
  ├─ キーワード抽出
  │   └─ "キーワード：" で始まるテキストを検索
  │       → カンマ区切りで保存
  │
  └─ 要約抽出
      └─ キーワード行の次のテキストブロックを取得
```

---

## 5. HTML 解析仕様

### 5.1 大会一覧ページ

- **セレクタ**: `a[href*="/guide/event/jsai"]`
- **条件**: href が `/guide/event/jsai{4桁年}/top` のパターンに一致
- **正規表現**: `r'/guide/event/jsai(\d{4})/top'`

### 5.2 大会トップページ（日程リンク取得）

- **セレクタ**: `a[href*="/table/"]`
- **条件**: href が `/guide/event/jsai{YYYY}/table/{YYYYMMDD}` または `{YYYYMMDD}_poster` のパターン

### 5.3 タイムテーブルページ（セッションリンク取得）

- **セレクタ**: `a[href*="/session/"]`
- **抽出情報**:
  - URL: `href` 属性（トークンパラメータを含む）
  - セッション表示名: `textContent`（例：`[1E1] 基礎・理論-制約充足・最適化・定性推論`）
- **正規表現（セッションID）**: `r'\[([^\]]+)\]'` → 最初にマッチした `[...]` 内
- **正規表現（session raw ID）**: `/session/([^/]+)/` のパスから取得

### 5.4 セッションページ（発表リンク取得）

- **セレクタ**: `a[href*="/subject/"]`
- **除外**: `mailto:` を含む href は除外
- **抽出情報**:
  - 発表URL: `href` 属性
  - 発表タイトル（仮）: `textContent`

### 5.5 発表詳細ページ

#### タイトル・発表ID抽出

```python
# <h1> タグまたはタイトル領域の heading を取得
# テキスト例: "[1E1-01] 複数種類のフェロモンを用いたcASによる制約充足問題の解法"
import re
heading_text = page.locator("article h1, .subject-title h1").first.text_content()
match = re.match(r'\[([^\]]+)\]\s*(.*)', heading_text.strip())
presentation_id = match.group(1)  # "1E1-01"
title = match.group(2)            # "複数種類のフェロモンを用いたcASによる..."
```

#### キーワード抽出

```python
# "キーワード：" で始まるテキストを探す
# 例: "キーワード：制約充足問題、蟻コロニー最適化"
keyword_el = page.locator("p:has-text('キーワード'), p:has-text('キーワード：')")
keyword_text = keyword_el.text_content()
keywords = re.sub(r'^キーワード[：:]\s*', '', keyword_text.strip())
```

#### 要約抽出

```python
# キーワード直後または要約ブロックを取得
# セレクタ候補: article の末尾 p, .abstract, .summary
abstract_el = page.locator("article > p:last-child, .abstract-text")
abstract = abstract_el.text_content().strip() if abstract_el else ""
```

#### セッションID抽出（発表URLから）

```python
# URL 例: /guide/event/jsai2018/subject/1E1-01/tables?cryptoId=
match = re.search(r'/subject/([^/]+)/', url)
subject_id = match.group(1)   # "1E1-01"
# セッションIDは発表IDの数字以前の部分
session_id = re.match(r'([A-Z0-9]+-[A-Z]+)', subject_id).group(1)  # "1E1"
# または単純に最後のハイフン以降を削除
session_id = subject_id.rsplit('-', 1)[0]  # "1E1"
```

---

## 6. 設定パラメータ (`config.py`)

```python
@dataclass
class CrawlerConfig:
    base_url: str = "https://confit.atlas.jp"
    min_delay_sec: float = 1.0       # リクエスト間の最小待機秒数
    max_delay_sec: float = 3.0       # リクエスト間の最大待機秒数
    max_retry: int = 3               # 最大リトライ回数
    retry_backoff_sec: float = 5.0   # リトライ時の追加待機（指数バックオフ）
    page_load_timeout_ms: int = 30000  # ページロードタイムアウト（ms）
    networkidle_timeout_ms: int = 15000  # networkidle待機タイムアウト（ms）
    headless: bool = True            # ヘッドレスモード
    output_dir: str = "output"       # 出力ディレクトリ
    start_year: int = 2018           # クロール開始年度
```

---

## 7. 依存ライブラリ (`requirements.txt`)

```
playwright==1.44.0       # ブラウザ自動化
asyncio                  # 非同期処理（標準ライブラリ）
aiofiles==23.2.1         # 非同期ファイル操作
```

---

## 8. ディレクトリ・ファイル詳細

### 8.1 progress.json スキーマ

```json
{
  "visited_urls": [
    "https://confit.atlas.jp/guide/event/jsai2018/subject/1E1-01/tables?cryptoId=",
    "..."
  ],
  "completed_events": [2018, 2019],
  "last_updated": "2024-01-01T12:00:00"
}
```

### 8.2 出力CSV 例

```csv
大会年度,セッションID,発表ID,発表タイトル,発表サブタイトル,キーワード,要約
2018,1E1,1E1-01,複数種類のフェロモンを用いたcASによる制約充足問題の解法,,制約充足問題、蟻コロニー最適化,大規模な制約充足問題を解く手法の1つとして...
2018,1E1,1E1-02,粒子群最適化を用いた巡回セールスマン問題の解法,,巡回セールスマン問題、粒子群最適化,...
```

---

## 9. エラー処理方針

| エラー種別 | 対処方法 |
|-----------|---------|
| ページ読み込みタイムアウト | 最大3回リトライ後、スキップ＋エラーログ記録 |
| 要素が見つからない | `None`/空文字として処理継続 |
| ネットワークエラー | 指数バックオフ（5秒→10秒→20秒）でリトライ |
| AWS WAF CAPTCHA | 検出時はログ警告を出力し、処理を一時停止（手動対応を促す） |
| キーボード割り込み | 進捗を保存して安全に終了 |

---

## 10. 実行方法（想定）

```bash
# 環境構築
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# クロール実行（全年度）
python src/crawler.py

# 特定年度のみ実行
python src/crawler.py --start-year 2023 --end-year 2024

# 中断後の再開（progress.json を利用）
python src/crawler.py --resume
```

---

## 11. 変更履歴

| 日付 | バージョン | 変更内容 |
|------|-----------|---------|
| 2026-06-12 | 1.0 | 初版作成 |

# JSAI Analyzer — 人工知能学会全国大会 発表情報クローラー

## セットアップ

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## 実行方法

```bash
cd src

# 全年度（2018〜2025）をクロール
python crawler.py

# 特定年度のみ
python crawler.py --start-year 2023 --end-year 2024

# 中断後に再開
python crawler.py --resume

# ブラウザを表示して確認しながら実行（デバッグ用）
python crawler.py --no-headless --start-year 2024 --end-year 2024
```

## オプション

| オプション | デフォルト | 説明 |
|-----------|-----------|------|
| `--start-year` | 2018 | クロール開始年度 |
| `--end-year` | 2025 | クロール終了年度 |
| `--resume` | False | progress.json から再開 |
| `--output-dir` | output | 出力ディレクトリ |
| `--no-headless` | False | ブラウザ表示モード |
| `--min-delay` | 1.5 | リクエスト間最小待機秒 |
| `--max-delay` | 3.5 | リクエスト間最大待機秒 |

## 出力ファイル

```
output/
├── jsai_presentations_YYYYMMDD_HHMMSS.csv  # 収集データ（UTF-8 BOM付き）
├── progress.json                            # 進捗管理（再開用）
└── crawler.log                              # ログ
```

## CSVカラム

`大会年度, セッションID, 発表ID, 発表タイトル, 発表サブタイトル, キーワード, 要約`

## ドキュメント

- [要件定義書](docs/requirements.md)
- [設計書](docs/design.md)

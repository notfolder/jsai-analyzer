# JSAI データ前処理・解析計画

## データ概要

- ソース: `jsai.csv`（プロジェクトルート）
- 件数: 9,406件（2018〜2026年度）
- カラム: 大会年度, セッションID, 発表ID, 発表タイトル, 発表サブタイトル, キーワード, 要約

### 年度別件数

| 年度 | 件数 |
|------|------|
| 2018 | 813 |
| 2019 | 795 |
| 2020 | 958 |
| 2021 | 764 |
| 2022 | 1,053 |
| 2023 | 1,327 |
| 2024 | 1,428 |
| 2025 | 1,803 |
| 2026 | 465 |

### データ品質

- キーワード空: 2,296件 (24.4%)  ← BERTopic で補完
- 要約空: 80件 (0.9%)
- タイトル空: 0件

---

## 解析方針: 年度ごとのテーマ移り変わり

**目標**: 2018〜2026年の9年間で、JSAIの研究テーマがどのように推移したかを定量的に把握する。

---

## 実装ステップ

### Step 1: SQLite DB 作成 【完了後: `jsai.db`】

スクリプト: `src/build_db.py`

- `jsai.csv` を読み込み `jsai.db` に `presentations` テーブルを作成
- キーワードは `、` 区切りで正規化し `keywords_normalized` カラムに格納
- `keywords` テーブルを作成（1行1キーワードの正規化形式）

```sql
-- presentations テーブル
CREATE TABLE presentations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    year          INTEGER NOT NULL,
    session_id    TEXT,
    pres_id       TEXT NOT NULL,
    title         TEXT,
    subtitle      TEXT,
    keywords_orig TEXT,          -- 元のキーワード文字列
    abstract      TEXT,
    topic_id      INTEGER,       -- Step 3 で追加
    topic_label   TEXT           -- Step 3 で追加
);

-- keywords テーブル（元キーワードを1行1語に展開）
CREATE TABLE keywords (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    pres_id  TEXT NOT NULL,
    year     INTEGER NOT NULL,
    keyword  TEXT NOT NULL,
    source   TEXT NOT NULL  -- 'original' | 'bertopic'
);
```

---

### Step 2: BERTopic によるトピック付与 【完了後: `models/jsai_bertopic/`】

スクリプト: `src/run_bertopic.py`

- 入力テキスト: `タイトル + " " + 要約`（キーワード空の発表も含め全件）
- モデル: `paraphrase-multilingual-mpnet-base-v2`（多言語BERT）
- デバイス: Apple M4 MPS を自動検出（`PYTORCH_ENABLE_MPS_FALLBACK=1`）
- UMAP: `n_neighbors=15, n_components=5, random_state=42`
- HDBSCAN: `min_cluster_size=15, prediction_data=True`
- `nr_topics="auto"`（類似トピックを自動マージ）

出力:
- `presentations.topic_id`, `presentations.topic_label` を DB に書き込み
- `keywords` テーブルに `source='bertopic'` でトピックキーワードを追加
- モデルを `models/jsai_bertopic/` に保存

#### Step 2 初回実行結果（2026-06-14）

| 項目 | 結果 |
|------|------|
| 処理時間 | 約114秒 |
| 検出トピック数 | 86（137→87→86 に自動マージ） |
| 外れ値(-1) | 3,210件 (34.1%) |

**問題点:**
1. トピックラベルにノイズワードが混入  
   - `0_ai_調整中_会社紹介_nvidia` ← 「調整中」「会社紹介」はセッションタイトルのプレースホルダ  
   - `1_llm_大規模言語モデル_本研究では_the` ← 「本研究では」は論文常套句  
   - `2_the_image_images_of` ← 英語冠詞・前置詞  
2. 外れ値34.1% が多い（`min_cluster_size=15` がやや大きすぎる）

#### Step 2 修正内容（v2）

**A. ストップワード除去:**  
- `CountVectorizer` に日本語ストップワードリストを渡すカスタム `vectorizer_model` を追加
- 日本語テキスト用の正規表現トークナイザー（形態素解析なし）を使用
- 除去対象: 論文常套句（本研究では・近年 等）/ イベントノイズ（調整中・会社紹介 等）/ 英語冠詞・前置詞

**B. min_cluster_size を 10 に変更:**  
- 15 → 10 に引き下げ、外れ値率の低減と細粒度なトピック検出を期待

**その他改善:**
- 埋め込みベクトルを `embeddings_cache.npy` に保存して再実行時の再計算を省略

#### Step 2 v2 実行結果（2026-06-14 · 修正後）→ 失敗

| 項目 | 結果 |
|------|------|
| 処理時間 | 約14秒（キャッシュ利用） |
| 検出トピック数 | **25**（207→26 に過剰マージ） |
| 外れ値(-1) | 2,942件 (31.3%) |

**問題:** `nr_topics="auto"` が 207→26 に過剰マージし、Topic 0 に 5,880件 (62%) が集中してしまった。

**追加修正（v3）:**
- `nr_topics=None` に変更（自動マージ廃止）
- ストップワードをさらに拡充（`その結果`, `data`, `model`, `method`, `system` 等の汎用英語語）

#### Step 2 v3 実行結果（2026-06-14 · 最終）→ 採用

| 項目 | 結果 |
|------|------|
| 処理時間 | 約14秒（キャッシュ利用） |
| 検出トピック数 | **206** |
| 外れ値(-1) | 2,821件 (30.0%) |

**上位トピック（v3）:**
| ID | 件数 | 代表キーワード |
|----|------|------|
| 0 | 325 | llm, 大規模言語モデル, llms, judge |
| 1 | 240 | financial, esg, stock, market |
| 2 | 227 | gps, traffic, view, road |
| 3 | 185 | game, games, hanabi, bgm |
| 4 | 167 | semantic, target, viewpoint, mapping |
| 5 | 138 | emotion, emotional, recognition |

**年度別トレンドの主要発見:**
- 2018〜2023: LLM トピックは上位に現れず（多様な研究テーマが並立）
- 2024: Topic 0 (LLM) が 86件で初めて年度1位に
- 2025: Topic 0 (LLM) が 158件に急増
- 2026: Topic 0 (LLM) が 58件（開催期間短縮のため件数少ない）

**残課題:**
- 一部に企業名ノイズが混入: `株式会社_インテル株式会社`, `nablas_株式会社` 等
- `どうぞお気軽にお立ち寄りください` 等の展示会フレーズが残存

---

### Step 3: 分析・可視化 【完了後: `notebooks/` または `outputs/`】

#### 3-1. SQL による年度×トピック集計

```sql
SELECT year, topic_label, COUNT(*) AS cnt
FROM presentations
WHERE topic_id != -1   -- 外れ値除外
GROUP BY year, topic_label
ORDER BY year, cnt DESC;
```

#### 3-2. BERTopic `topics_over_time` による時系列可視化

```python
import sqlite3
from bertopic import BERTopic

model = BERTopic.load("./models/jsai_bertopic")
con = sqlite3.connect("jsai.db")

rows = con.execute(
    "SELECT title || ' ' || COALESCE(abstract,''), year, topic_id "
    "FROM presentations ORDER BY id"
).fetchall()

texts      = [r[0] for r in rows]
timestamps = [str(r[1]) for r in rows]
topics     = [r[2] for r in rows]

tot = model.topics_over_time(texts, timestamps, topics=topics)
model.visualize_topics_over_time(tot, top_n_topics=20).show()
```

#### 3-3. 想定される分析観点

- 「深層学習 / CNN / 画像認識」系トピックのピーク年度
- 「Transformer / BERT / 言語モデル」系の台頭時期
- 「LLM / 生成AI / エージェント」系の急増時期（2023→）
- セッション種別（GS/OS/KS）ごとのトピック分布の違い

---

## ファイル構成

```
jsai-analyzer/
├── jsai.csv                  # 元データ
├── jsai.db                   # Step 1 で生成
├── docs/
│   └── plan.md               # このファイル
├── src/
│   ├── build_db.py           # Step 1: DB構築
│   ├── run_bertopic.py       # Step 2: トピック付与
│   └── output/               # クローラー出力
└── models/
    └── jsai_bertopic/        # Step 2 で保存したBERTopicモデル
```

---

## 依存ライブラリ（Step 2 実行前に要インストール）

```bash
pip install bertopic sentence-transformers umap-learn hdbscan
```

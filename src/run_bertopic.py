"""
Step 2: BERTopic によるトピック付与

実行:
  PYTORCH_ENABLE_MPS_FALLBACK=1 python3 src/run_bertopic.py

出力:
  - jsai.db の presentations.topic_id / topic_label を更新
  - jsai.db の keywords テーブルに source='bertopic' を追加
  - models/jsai_bertopic/ にモデルを保存
"""
import re
import sqlite3
import time
from pathlib import Path

import numpy as np
import torch
from bertopic import BERTopic
from hdbscan import HDBSCAN
from sentence_transformers import SentenceTransformer
from sklearn.feature_extraction.text import CountVectorizer
from umap import UMAP

ROOT            = Path(__file__).parent.parent
DB_PATH         = ROOT / "jsai.db"
MODEL_DIR       = ROOT / "models" / "jsai_bertopic"
EMBEDDINGS_CACHE = ROOT / "models" / "embeddings_cache.npy"

# ─── ストップワード定義 ───────────────────────────────────────
# 論文常套句・イベントノイズ・英語機能語を除外
JA_STOPWORDS = [
    # 論文常套句
    "本研究", "本稿", "本論文", "本手法", "提案手法", "本研究では",
    "本稿では", "本論文では", "そこで", "そこで本研究では", "近年",
    "また", "さらに", "しかし", "ため", "こと", "もの", "それ",
    "これ", "その", "この", "など", "という", "について", "において",
    "により", "による", "対して", "として", "よって", "ただし",
    "なお", "ある", "いる", "する", "なる", "れる", "られる",
    "できる", "ない", "ため", "よう", "から", "まで",
    "その結果", "実験結果", "提案する", "検討する", "提示する", "構築する",
    "実装する", "設計する", "開発する", "改善する", "評価する", "対象とする",
    "問題点", "課題", "目的", "方法", "手法", "結果", "效果", "有効性",
    # イベント・セッションノイズ
    "調整中", "会社紹介", "総合討論", "パネル討論", "総括", "未定",
    "ランチョン", "スポンサー", "セミナー", "招待講演", "特別講演",
    "クロージング", "オープニング", "来賓挨拶", "記念企画", "補足資料",
    # 英語：機能語・一般動詞・汎用名詞
    "the", "of", "and", "in", "to", "is", "for", "on", "with",
    "an", "are", "as", "at", "be", "by", "from", "or", "that",
    "this", "we", "our", "which", "has", "can", "using", "used",
    "based", "its", "not", "it", "but", "they", "their", "also",
    "have", "been", "than", "more", "such", "into", "show", "shows",
    "propose", "proposed", "method", "approach", "results", "result",
    "model", "models", "data", "task", "tasks", "system", "systems",
    "paper", "work", "study", "problem", "problems", "use",
    "performance", "evaluation", "experiments", "experiment",
    "training", "test", "dataset", "datasets", "high", "low",
    "large", "small", "new", "each", "two", "three", "first",
    "second", "both", "all", "set", "number", "between",
]

def ja_tokenizer(text: str) -> list[str]:
    """日本語テキストをトークン化（形態素解析なし・正規表現ベース）。
    日本語の2文字以上の連続と英字3文字以上の単語を抽出する。
    """
    return re.findall(
        r'[\u3041-\u3096\u30A0-\u30FF\u4E00-\u9FFF\u3400-\u4DBF]{2,}'
        r'|[a-zA-Z]{3,}',
        text,
    )

# ─── デバイス選択 ────────────────────────────────────────────
if torch.backends.mps.is_available():
    device = "mps"
    print("デバイス: Apple GPU (MPS)")
elif torch.cuda.is_available():
    device = "cuda"
    print(f"デバイス: CUDA ({torch.cuda.get_device_name(0)})")
else:
    device = "cpu"
    print("デバイス: CPU")

# ─── DB から発表データを取得 ──────────────────────────────────
con  = sqlite3.connect(DB_PATH)
rows = con.execute(
    "SELECT id, pres_id, year, title, abstract FROM presentations ORDER BY id"
).fetchall()
print(f"\n発表データ取得: {len(rows):,}件")

# タイトル + 要約を結合（要約が空の場合はタイトルのみ）
def make_text(title, abstract):
    t = (title or "").strip()
    a = (abstract or "").strip()
    return f"{t} {a}".strip() if a else t

texts      = [make_text(r[3], r[4]) for r in rows]
pres_ids   = [r[1] for r in rows]
years      = [r[2] for r in rows]
db_ids     = [r[0] for r in rows]

# ─── BERTopic モデルを構築 ────────────────────────────────────
print("\nモデルを初期化中...")

embedding_model = SentenceTransformer(
    "paraphrase-multilingual-mpnet-base-v2",
    device=device,
)

# ─── 埋め込みベクトル（キャッシュがあれば再利用） ────────────
EMBEDDINGS_CACHE.parent.mkdir(parents=True, exist_ok=True)
if EMBEDDINGS_CACHE.exists():
    print(f"\n[1/4] 埋め込みキャッシュを読み込み: {EMBEDDINGS_CACHE}")
    embeddings = np.load(str(EMBEDDINGS_CACHE))
    print(f"      shape={embeddings.shape}")
else:
    print("\n[1/4] テキストのベクトル化中（初回・キャッシュなし）...")
    t1 = time.time()
    embeddings = embedding_model.encode(
        texts,
        batch_size=64,
        show_progress_bar=True,
        convert_to_numpy=True,
    )
    np.save(str(EMBEDDINGS_CACHE), embeddings)
    print(f"      完了: {time.time()-t1:.1f}秒  shape={embeddings.shape}")
    print(f"      キャッシュ保存: {EMBEDDINGS_CACHE}")

umap_model = UMAP(
    n_neighbors=15,
    n_components=5,
    min_dist=0.0,
    metric="cosine",
    random_state=42,
    low_memory=False,
)

hdbscan_model = HDBSCAN(
    min_cluster_size=10,   # 15→10: 外れ値率を下げる
    metric="euclidean",
    cluster_selection_method="eom",
    prediction_data=True,
)

# ─── カスタム Vectorizer（ストップワード・日本語トークナイザー） ──
vectorizer_model = CountVectorizer(
    tokenizer=ja_tokenizer,
    stop_words=JA_STOPWORDS,
    min_df=3,       # 3件未満のトークンを除外
    max_df=0.9,     # 90%超の発表に出現する超頻出語を除外
)

topic_model = BERTopic(
    embedding_model=embedding_model,
    umap_model=umap_model,
    hdbscan_model=hdbscan_model,
    vectorizer_model=vectorizer_model,
    nr_topics=None,        # 自動マージしない（自然なトピック数を保つ）
    calculate_probabilities=False,
    verbose=True,
)

# ─── fit_transform（キャッシュ済み embeddings を渡す） ─────────
print("\n[2/4] UMAP 次元削減 → HDBSCAN → c-TF-IDF ...")
# DB の bertopic キーワードをクリア（再実行時のために）
con.execute("DELETE FROM keywords WHERE source='bertopic'")
con.execute("UPDATE presentations SET topic_id=NULL, topic_label=NULL")
con.commit()

t2 = time.time()
topics, _ = topic_model.fit_transform(texts, embeddings=embeddings)
elapsed = time.time() - t2
print(f"\n[2-4] UMAP + HDBSCAN + c-TF-IDF 完了: {elapsed:.1f}秒")

# トピック情報を表示
topic_info = topic_model.get_topic_info()
n_topics   = len(topic_info[topic_info["Topic"] != -1])
n_outlier  = sum(1 for t in topics if t == -1)
print(f"検出トピック数: {n_topics}")
print(f"外れ値 (-1):   {n_outlier:,}件 ({n_outlier/len(topics)*100:.1f}%)")

print("\n=== 上位20トピック ===")
print(f"{'ID':>4}  {'件数':>5}  {'代表キーワード'}")
for _, row in topic_info[topic_info["Topic"] != -1].head(20).iterrows():
    print(f"  {row['Topic']:4d}  {row['Count']:5d}  {row['Name']}")

# ─── DB に書き込み ────────────────────────────────────────────
print("\nDB に書き込み中...")

# topic_id → topic_label のマッピング
topic_labels = {
    row["Topic"]: row["Name"]
    for _, row in topic_info.iterrows()
}

# presentations テーブルを更新
update_pres = [
    (topics[i], topic_labels.get(topics[i], ""), db_ids[i])
    for i in range(len(rows))
]
con.executemany(
    "UPDATE presentations SET topic_id=?, topic_label=? WHERE id=?",
    update_pres,
)

# keywords テーブルに BERTopic のトピックキーワードを追加（外れ値以外）
kw_rows = []
for i, topic_id in enumerate(topics):
    if topic_id == -1:
        continue
    topic_words = topic_model.get_topic(topic_id)
    if not topic_words:
        continue
    # スコア上位5語を登録
    for word, score in topic_words[:5]:
        kw_rows.append((pres_ids[i], years[i], word, "bertopic"))

# 重複を避けるため (pres_id, keyword) の組み合わせで一意化
seen = set()
kw_rows_unique = []
for row in kw_rows:
    key = (row[0], row[2])
    if key not in seen:
        seen.add(key)
        kw_rows_unique.append(row)

con.executemany(
    "INSERT INTO keywords (pres_id, year, keyword, source) VALUES (?,?,?,?)",
    kw_rows_unique,
)

con.commit()
print(f"  presentations 更新: {len(update_pres):,}件")
print(f"  keywords 追加 (bertopic): {len(kw_rows_unique):,}件")

# ─── モデルを保存 ─────────────────────────────────────────────
MODEL_DIR.mkdir(parents=True, exist_ok=True)
topic_model.save(
    str(MODEL_DIR),
    serialization="safetensors",
    save_ctfidf=True,
    save_embedding_model=embedding_model,
)
print(f"\nモデルを保存: {MODEL_DIR}")

# ─── 年度×トピック件数サマリー ────────────────────────────────
print("\n=== 年度別トピック分布（上位5トピック/年） ===")
for year in range(2018, 2027):
    yr_rows = con.execute(
        "SELECT topic_label, COUNT(*) AS cnt FROM presentations "
        "WHERE year=? AND topic_id != -1 "
        "GROUP BY topic_label ORDER BY cnt DESC LIMIT 5",
        (year,),
    ).fetchall()
    if not yr_rows:
        continue
    print(f"\n  {year}年:")
    for label, cnt in yr_rows:
        print(f"    {cnt:4d}件  {label}")

con.close()
print("\n=== Step 2 完了 ===")

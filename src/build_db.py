"""
Step 1: jsai.csv -> jsai.db (SQLite)

実行:
  python3 src/build_db.py

出力:
  jsai.db  (プロジェクトルート)
"""
import csv
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent.parent
CSV_PATH = ROOT / "jsai.csv"
DB_PATH  = ROOT / "jsai.db"

# キーワード区切り文字の正規化（「、」読点が主、カンマ・スペースも対応）
def split_keywords(raw: str) -> list[str]:
    if not raw or not raw.strip():
        return []
    s = raw.strip()
    # 全角セミコロン・半角セミコロン→読点に統一してから分割
    s = s.replace("；", "、").replace(";", "、")
    # カンマ（全角・半角）→読点
    s = s.replace("，", "、").replace(",", "、")
    parts = [p.strip() for p in s.split("、") if p.strip()]
    # スペース区切りのみの場合（読点なし）
    if len(parts) == 1 and " " in parts[0]:
        parts = [p.strip() for p in parts[0].split() if p.strip()]
    return parts


def build_db():
    if DB_PATH.exists():
        DB_PATH.unlink()
        print(f"既存の {DB_PATH.name} を削除しました")

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    cur.executescript("""
        CREATE TABLE presentations (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            year          INTEGER NOT NULL,
            session_id    TEXT,
            pres_id       TEXT NOT NULL,
            title         TEXT,
            subtitle      TEXT,
            keywords_orig TEXT,
            abstract      TEXT,
            topic_id      INTEGER,
            topic_label   TEXT
        );

        CREATE TABLE keywords (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            pres_id  TEXT NOT NULL,
            year     INTEGER NOT NULL,
            keyword  TEXT NOT NULL,
            source   TEXT NOT NULL
        );

        CREATE INDEX idx_presentations_year     ON presentations(year);
        CREATE INDEX idx_presentations_pres_id  ON presentations(pres_id);
        CREATE INDEX idx_presentations_topic_id ON presentations(topic_id);
        CREATE INDEX idx_keywords_pres_id       ON keywords(pres_id);
        CREATE INDEX idx_keywords_year          ON keywords(year);
        CREATE INDEX idx_keywords_keyword       ON keywords(keyword);
        CREATE INDEX idx_keywords_source        ON keywords(source);
    """)

    rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8-sig")))
    print(f"読み込み: {len(rows)}件")

    pres_rows = []
    kw_rows   = []

    for r in rows:
        year     = int(r["大会年度"])
        pres_id  = r["発表ID"].strip()
        kw_orig  = r.get("キーワード", "").strip()

        pres_rows.append((
            year,
            r.get("セッションID", "").strip() or None,
            pres_id,
            r.get("発表タイトル", "").strip() or None,
            r.get("発表サブタイトル", "").strip() or None,
            kw_orig or None,
            r.get("要約", "").strip() or None,
        ))

        for kw in split_keywords(kw_orig):
            kw_rows.append((pres_id, year, kw, "original"))

    cur.executemany(
        "INSERT INTO presentations (year, session_id, pres_id, title, subtitle, "
        "keywords_orig, abstract) VALUES (?,?,?,?,?,?,?)",
        pres_rows,
    )
    cur.executemany(
        "INSERT INTO keywords (pres_id, year, keyword, source) VALUES (?,?,?,?)",
        kw_rows,
    )

    con.commit()

    # 確認
    total_p  = cur.execute("SELECT COUNT(*) FROM presentations").fetchone()[0]
    total_k  = cur.execute("SELECT COUNT(*) FROM keywords").fetchone()[0]
    no_kw    = cur.execute(
        "SELECT COUNT(*) FROM presentations WHERE keywords_orig IS NULL"
    ).fetchone()[0]

    print(f"\n=== DB 作成完了: {DB_PATH} ===")
    print(f"  presentations: {total_p:,}件")
    print(f"  keywords:      {total_k:,}件 (original のみ)")
    print(f"  キーワード未登録発表: {no_kw:,}件 ({no_kw/total_p*100:.1f}%)")

    print("\n年度別件数:")
    print(f"  {'年度':>4}  {'発表数':>6}  {'KW空':>5}")
    for row in cur.execute(
        "SELECT year, COUNT(*) AS cnt, "
        "SUM(CASE WHEN keywords_orig IS NULL THEN 1 ELSE 0 END) AS no_kw "
        "FROM presentations GROUP BY year ORDER BY year"
    ):
        print(f"  {row[0]}  {row[1]:6,}  {row[2]:5,}")

    con.close()


if __name__ == "__main__":
    build_db()

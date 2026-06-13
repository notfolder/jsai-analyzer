"""
複数のCSVファイルをマージして重複除去し、統合CSVを出力するスクリプト。

使い方:
    python src/merge_csv.py                            # src/output/ + output/ を自動検出
    python src/merge_csv.py -o output/merged.csv      # 出力先指定
    python src/merge_csv.py file1.csv file2.csv ...   # ファイル指定
"""
import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

FIELDNAMES = ["大会年度", "セッションID", "発表ID", "発表タイトル", "発表サブタイトル", "キーワード", "要約"]


def load_csv(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # 欠損フィールドを空文字で補完
            rows.append({k: row.get(k, "") for k in FIELDNAMES})
    return rows


def merge_and_dedup(file_paths: list[Path]) -> list[dict]:
    """優先度: 後から読んだファイルで上書き（より新しいデータを優先）"""
    merged: dict[tuple, dict] = {}  # (年度, 発表ID) -> row

    for path in file_paths:
        rows = load_csv(path)
        for row in rows:
            key = (row["大会年度"], row["発表ID"])
            if key not in merged:
                merged[key] = row
            else:
                # 既存より新しいデータで、要約やキーワードが充実していれば上書き
                existing = merged[key]
                new_has_more = (
                    len(row.get("要約", "")) > len(existing.get("要約", "")) or
                    len(row.get("キーワード", "")) > len(existing.get("キーワード", ""))
                )
                if new_has_more:
                    merged[key] = row

    # 年度 → 発表ID でソート
    return sorted(merged.values(), key=lambda r: (r["大会年度"], r["発表ID"]))


def main():
    parser = argparse.ArgumentParser(description="複数CSVをマージして重複除去")
    parser.add_argument("files", nargs="*", help="対象CSVファイル (省略時は output/ と src/output/ を自動検出)")
    parser.add_argument("-o", "--output", help="出力ファイルパス (省略時は output/ に自動命名)")
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    repo_root = script_dir.parent

    if args.files:
        file_paths = [Path(f) for f in args.files]
    else:
        # src/output/ と output/ の両方を自動検出
        file_paths = sorted(
            list((script_dir / "output").glob("jsai_presentations_*.csv")) +
            list((repo_root / "output").glob("jsai_presentations_*.csv"))
        )
        if not file_paths:
            print("CSVファイルが見つかりません。ファイルを明示的に指定してください。")
            sys.exit(1)

    print(f"対象ファイル ({len(file_paths)}件):")
    for p in file_paths:
        rows = load_csv(p)
        print(f"  {p}: {len(rows)}件")

    merged = merge_and_dedup(file_paths)

    # 年度別集計
    from collections import Counter
    year_counts = Counter(r["大会年度"] for r in merged)
    print(f"\n統合後 合計: {len(merged)}件（重複除去済み）")
    print("年度別:")
    for y in sorted(year_counts):
        print(f"  {y}: {year_counts[y]}件")

    # 出力先の決定
    if args.output:
        out_path = Path(args.output)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = script_dir / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"jsai_merged_{timestamp}.csv"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(merged)

    print(f"\n出力完了: {out_path}")


if __name__ == "__main__":
    main()

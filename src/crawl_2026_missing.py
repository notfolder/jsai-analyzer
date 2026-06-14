"""
JSAI2026 の未取得発表を追加クロールするスクリプト。

従来のクローラーはタイムテーブルページからセッションを取得していたが、
pub.confit.atlas.jp では一部の部屋しかタイムテーブルに表示されない問題があった。

本スクリプトは /sessions/program/ カテゴリページから全セッションURLを収集し、
既にクロール済みの発表をスキップして未取得分のみを取得する。

リジューム対応:
    - 出力CSV は output/jsai_2026_missing.csv に固定（追記モード）
    - 取得済み発表URL は output/missing_progress.json で管理
    - 途中で中断しても再実行で続きから再開
    - セッションURL一覧も output/missing_session_urls.json にキャッシュ

使い方:
    cd /Users/notfolder/Documents/jsai-analyzer
    .venv/bin/python src/crawl_2026_missing.py           # 実行（リジューム自動）
    .venv/bin/python src/crawl_2026_missing.py --dry-run # セッション数確認のみ
    .venv/bin/python src/crawl_2026_missing.py --reset   # キャッシュを破棄して最初から
"""

import argparse
import asyncio
import csv
import json
import logging
import os
import random
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

# ─── 設定 ──────────────────────────────────────────────────────────────────────

YEAR = 2026
BASE_URL = "https://pub.confit.atlas.jp"
EVENT_URL = f"{BASE_URL}/ja/event/jsai{YEAR}"
PREFETCH_URL = f"https://conf.ai-gakkai.or.jp/jsai{YEAR}/"

SESSIONS_TOP_URL = f"{EVENT_URL}/sessions"

MIN_DELAY = 1.5
MAX_DELAY = 3.5
MAX_RETRY = 3
RETRY_BACKOFF = 5.0
PAGE_TIMEOUT = 30_000
NETWORK_IDLE_TIMEOUT = 15_000

FIELDNAMES = ["大会年度", "セッションID", "発表ID", "発表タイトル", "発表サブタイトル", "キーワード", "要約"]

SCRIPT_DIR = Path(__file__).parent
OUTPUT_DIR = SCRIPT_DIR / "output"
PROGRESS_PATH = OUTPUT_DIR / "progress.json"           # 既存クローラーの進捗（読み取り専用）
MISSING_PROGRESS_PATH = OUTPUT_DIR / "missing_progress.json"  # このスクリプト専用進捗
SESSION_CACHE_PATH = OUTPUT_DIR / "missing_session_urls.json" # セッションURLキャッシュ
OUTPUT_CSV = OUTPUT_DIR / "jsai_2026_missing.csv"      # 固定出力先（追記）

# ─── ロガー設定 ────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(OUTPUT_DIR / "crawl_2026_missing.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("crawl_2026_missing")

# ─── 進捗管理 ──────────────────────────────────────────────────────────────────


def load_visited(path: Path) -> set[str]:
    """訪問済みURLをセットで返す（旧 progress.json と新 missing_progress.json の両方）"""
    visited: set[str] = set()
    for p in [PROGRESS_PATH, MISSING_PROGRESS_PATH]:
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            visited |= set(data.get("visited_urls", []))
        except Exception:
            pass
    return visited


def save_missing_progress(newly_visited: list[str]) -> None:
    """missing_progress.json に取得済みURLを追記保存"""
    existing: set[str] = set()
    if MISSING_PROGRESS_PATH.exists():
        try:
            data = json.loads(MISSING_PROGRESS_PATH.read_text(encoding="utf-8"))
            existing = set(data.get("visited_urls", []))
        except Exception:
            pass
    merged = sorted(existing | set(newly_visited))
    from datetime import timezone
    MISSING_PROGRESS_PATH.write_text(
        json.dumps({"visited_urls": merged,
                    "last_updated": datetime.now(timezone.utc).isoformat()},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_session_cache() -> list[str] | None:
    """セッションURLキャッシュを読み込む（なければNone）"""
    if not SESSION_CACHE_PATH.exists():
        return None
    try:
        data = json.loads(SESSION_CACHE_PATH.read_text(encoding="utf-8"))
        urls = data.get("session_urls", [])
        logger.info("セッションURLキャッシュ読み込み: %d件 (%s)",
                    len(urls), SESSION_CACHE_PATH)
        return urls
    except Exception:
        return None


def save_session_cache(session_urls: list[str]) -> None:
    """セッションURLをキャッシュに保存"""
    from datetime import timezone
    SESSION_CACHE_PATH.write_text(
        json.dumps({"session_urls": session_urls,
                    "saved_at": datetime.now(timezone.utc).isoformat()},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("セッションURLキャッシュ保存: %d件 → %s", len(session_urls), SESSION_CACHE_PATH)


# ─── ページ読み込みヘルパー ────────────────────────────────────────────────────


async def load_page(page, url: str) -> bool:
    for attempt in range(1, MAX_RETRY + 1):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            try:
                await page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT)
            except Exception:
                pass
            return True
        except Exception as exc:
            wait = RETRY_BACKOFF * (2 ** (attempt - 1))
            logger.warning("読み込み失敗 (attempt %d/%d): %s — %s — %.0f秒待機",
                           attempt, MAX_RETRY, url, exc, wait)
            if attempt < MAX_RETRY:
                await asyncio.sleep(wait)
    return False


async def random_delay() -> None:
    await asyncio.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


# ─── スクレイピング関数群 ──────────────────────────────────────────────────────


async def get_program_category_urls(page) -> list[dict]:
    """セッション一覧ページから全プログラムカテゴリのURLを取得"""
    logger.info("セッションカテゴリ一覧を取得: %s", SESSIONS_TOP_URL)
    ok = await load_page(page, SESSIONS_TOP_URL)
    if not ok:
        logger.error("セッション一覧ページ読み込み失敗")
        return []

    categories = await page.evaluate("""() => {
        return Array.from(document.querySelectorAll('a[href*="/sessions/program/"]'))
            .map(a => ({ href: a.href, text: a.textContent.trim() }));
    }""")
    logger.info("カテゴリ数: %d", len(categories))
    return categories


async def get_session_urls_from_category(page, category_url: str, category_name: str) -> list[str]:
    """カテゴリページから全セッションURLを取得"""
    ok = await load_page(page, category_url)
    if not ok:
        logger.warning("カテゴリページ読み込み失敗: %s", category_url)
        return []

    links = await page.evaluate("""() => {
        return Array.from(document.querySelectorAll('a[href*="/session/"]'))
            .map(a => a.href)
            .filter(h => !h.includes('login'));
    }""")
    unique = list(dict.fromkeys(links))  # 順序保持しつつ重複除去
    logger.info("  [%s] セッション数: %d", category_name, len(unique))
    return unique


async def get_presentation_urls_from_session(page, session_url: str) -> list[str]:
    """セッションページから全発表URLを取得"""
    ok = await load_page(page, session_url)
    if not ok:
        logger.warning("セッションページ読み込み失敗: %s", session_url)
        return []

    links = await page.evaluate("""() => {
        return Array.from(document.querySelectorAll('a[href*="/presentation/"]'))
            .map(a => a.href)
            .filter(h => !h.includes('login') && !h.startsWith('mailto'));
    }""")
    return list(dict.fromkeys(links))


async def get_presentation_detail(page, url: str) -> dict | None:
    """個別発表ページから詳細情報を取得"""
    ok = await load_page(page, url)
    if not ok:
        logger.warning("発表ページ読み込み失敗: %s", url)
        return None

    data = await page.evaluate("""() => {
        // タイトル h1
        const h1 = document.querySelector('main h1.thd, .bf-content h1');
        let titleRaw = '';
        let subtitle = '';
        if (h1) {
            const clone = h1.cloneNode(true);
            const subClone = clone.querySelector('span.tdf, span.d-blk.tdf');
            if (subClone) {
                subtitle = subClone.textContent.trim();
                subClone.remove();
            }
            const idClone = clone.querySelector('span.tbd');
            const idText = idClone ? idClone.textContent.trim() : '';
            if (idClone) idClone.remove();
            const rawTitle = clone.textContent.trim();
            titleRaw = idText ? idText + ' ' + rawTitle : rawTitle;
        }

        // キーワード: h2.tcapt の次の p 要素
        const kwHeader = document.querySelector('h2.tcapt');
        const kwEl = kwHeader ? kwHeader.nextElementSibling : null;
        const kwRaw = kwEl ? kwEl.textContent.trim() : '';

        // 要約
        const absEl = document.querySelector('.pd-v-md .mg-t-md');
        const absEl2 = document.querySelector('.box-s-bkg + .mg-t-md, .box-s-bkg ~ .mg-t-md');
        const abstract = (absEl || absEl2) ? (absEl || absEl2).textContent.trim() : '';

        return { titleRaw, kwRaw, abstract, subtitle };
    }""")

    title_raw: str = data.get("titleRaw", "").strip()
    kw_raw: str = data.get("kwRaw", "").strip()
    abstract: str = data.get("abstract", "").strip()
    subtitle: str = data.get("subtitle", "").strip()

    # "[1E1-01] タイトル" → presentation_id, title に分割
    m = re.match(r"\[([^\]]+)\]\s*(.*)", title_raw, re.DOTALL)
    if m:
        presentation_id = m.group(1).strip()
        title = m.group(2).strip()
    else:
        um = re.search(r"/presentation/([^/?#]+)", url)
        presentation_id = um.group(1) if um else ""
        title = title_raw

    # セッションIDを発表IDから導出 (末尾の -数字 を除く)
    session_id = re.sub(r"-\d+[a-z]?$", "", presentation_id) if presentation_id else ""

    # "キーワード：..." → キーワード部分を取り出す
    keywords = re.sub(r"^キーワード[：:]\s*", "", kw_raw).strip()

    return {
        "大会年度": str(YEAR),
        "セッションID": session_id,
        "発表ID": presentation_id,
        "発表タイトル": title,
        "発表サブタイトル": subtitle,
        "キーワード": keywords,
        "要約": abstract,
        "_url": url,
    }


# ─── メイン処理 ────────────────────────────────────────────────────────────────


async def run(dry_run: bool = False, reset: bool = False) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # --reset: キャッシュを削除して最初からやり直し
    if reset:
        for p in [MISSING_PROGRESS_PATH, SESSION_CACHE_PATH]:
            if p.exists():
                p.unlink()
                logger.info("削除: %s", p)

    visited = load_visited(PROGRESS_PATH)  # 旧+新進捗の合計
    logger.info("既訪問URL数（スキップ対象）: %d", len(visited))

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            locale="ja-JP",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()
        page.set_default_timeout(PAGE_TIMEOUT)

        # JSAI公式サイトをプリフェッチ（pub.confit.atlas.jp への認証Cookie取得）
        logger.info("プリフェッチ: %s", PREFETCH_URL)
        await load_page(page, PREFETCH_URL)
        await asyncio.sleep(2)

        # ─── Step 1/2: セッションURL収集（キャッシュがあればスキップ） ──────
        unique_session_urls = load_session_cache()
        if unique_session_urls is None:
            categories = await get_program_category_urls(page)
            if not categories:
                logger.error("カテゴリ取得失敗。終了")
                return

            all_session_urls: list[str] = []
            for cat in categories:
                await random_delay()
                urls = await get_session_urls_from_category(page, cat["href"], cat["text"])
                all_session_urls.extend(urls)

            unique_session_urls = list(dict.fromkeys(all_session_urls))
            logger.info("ユニークセッション数: %d", len(unique_session_urls))
            save_session_cache(unique_session_urls)
        else:
            categories = [{"text": "(キャッシュ)", "href": ""}]  # dry-run 表示用

        # ─── Step 3: 全発表URLを収集 ─────────────────────────────────────
        all_pres_urls: list[str] = []
        for i, sess_url in enumerate(unique_session_urls, 1):
            logger.info("セッション [%d/%d]: %s", i, len(unique_session_urls), sess_url)
            await random_delay()
            pres_urls = await get_presentation_urls_from_session(page, sess_url)
            all_pres_urls.extend(pres_urls)

        unique_pres_urls = list(dict.fromkeys(all_pres_urls))
        new_pres_urls = [u for u in unique_pres_urls if u not in visited]

        logger.info("発表URL合計: %d  |  未取得（新規）: %d  |  スキップ: %d",
                    len(unique_pres_urls), len(new_pres_urls),
                    len(unique_pres_urls) - len(new_pres_urls))

        if dry_run:
            logger.info("[dry-run] 実際のクロールはスキップします")
            print(f"\n--- dry-run 結果 ---")
            print(f"セッション数    : {len(unique_session_urls)}")
            print(f"発表URL合計     : {len(unique_pres_urls)}")
            print(f"新規（未取得）  : {len(new_pres_urls)}")
            print(f"スキップ（既存）: {len(unique_pres_urls) - len(new_pres_urls)}")
            await browser.close()
            return

        if not new_pres_urls:
            logger.info("新規発表が0件です。終了")
            await browser.close()
            return

        # ─── Step 4: 各発表の詳細を取得してCSVに追記 ────────────────────
        # 出力CSVが既に存在する場合は追記（リジューム）、なければ新規作成
        csv_exists = OUTPUT_CSV.exists()
        saved = 0
        newly_visited: list[str] = []

        with open(OUTPUT_CSV, "a" if csv_exists else "w",
                  encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            if not csv_exists:
                writer.writeheader()
            else:
                logger.info("既存CSV に追記: %s", OUTPUT_CSV)

            for i, pres_url in enumerate(new_pres_urls, 1):
                logger.info("発表 [%d/%d]: %s", i, len(new_pres_urls), pres_url)
                await random_delay()

                detail = await get_presentation_detail(page, pres_url)
                if detail:
                    writer.writerow({k: detail.get(k, "") for k in FIELDNAMES})
                    f.flush()
                    saved += 1
                    newly_visited.append(pres_url)
                    logger.info("  → [%s] %s", detail["発表ID"], detail["発表タイトル"][:50])

                    # 10件ごとに進捗を保存（中断時のロス最小化）
                    if len(newly_visited) % 10 == 0:
                        save_missing_progress(newly_visited)
                else:
                    logger.warning("  → スキップ（取得失敗）: %s", pres_url)

        logger.info("=== 完了: %d 件保存 → %s ===", saved, OUTPUT_CSV)

        # ─── Step 5: 進捗を保存 ──────────────────────────────────────────
        if newly_visited:
            save_missing_progress(newly_visited)
            logger.info("missing_progress.json 更新完了（新規: %d件）", len(newly_visited))

        await browser.close()

    print(f"\n出力ファイル: {OUTPUT_CSV}")
    print(f"取得件数: {saved} 件")
    print(f"\n次のステップ:")
    print(f"  1. マージ: .venv/bin/python src/merge_csv.py  → jsai.csv を更新")
    print(f"  2. DB更新: .venv/bin/python src/build_db.py   → jsai.db を再構築")


def main() -> None:
    parser = argparse.ArgumentParser(description="JSAI2026 未取得発表の追加クロール")
    parser.add_argument("--dry-run", action="store_true",
                        help="発表の詳細取得をスキップしてセッション/発表数のみ確認")
    parser.add_argument("--reset", action="store_true",
                        help="セッションURLキャッシュと進捗をリセットして最初からやり直す")
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run, reset=args.reset))


if __name__ == "__main__":
    main()

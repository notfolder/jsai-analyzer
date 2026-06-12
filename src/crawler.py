"""メインエントリポイント"""
import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime

from config import CrawlerConfig
from csv_exporter import CSVExporter
from event_scraper import EventScraper
from progress_store import ProgressStore

# ─── ロガー設定 ────────────────────────────────────────────────────────────────

def setup_logging(log_dir: str) -> None:
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "crawler.log")

    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )


logger = logging.getLogger("crawler")


# ─── メイン処理 ────────────────────────────────────────────────────────────────

async def run(config: CrawlerConfig, resume: bool) -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_csv = os.path.join(config.output_dir, f"jsai_presentations_{timestamp}.csv")
    progress_path = os.path.join(config.output_dir, "progress.json")

    progress = ProgressStore(progress_path)
    if resume:
        progress.load()
        logger.info("進捗を読み込みました: %s", progress_path)
    else:
        logger.info("新規クロールを開始します")

    scraper = EventScraper(config)
    await scraper.start()

    total_saved = 0

    try:
        with CSVExporter(output_csv) as exporter:
            logger.info("出力先: %s", output_csv)

            # Step 1: 大会一覧を取得
            events = await scraper.fetch_events()
            if not events:
                logger.error("大会情報を取得できませんでした")
                return

            for event in events:
                if progress.is_event_completed(event.year):
                    logger.info("[%d] スキップ（完了済み）", event.year)
                    continue

                logger.info("== [%d] %s ==", event.year, event.name)

                # Step 2: 日程一覧を取得
                await asyncio.sleep(scraper._config.min_delay_sec)
                days = await scraper.fetch_schedule_days(event)

                for day in days:
                    logger.info("  日程: %s (%s)", day.label, day.date_str)

                    # Step 3: セッション一覧を取得
                    await asyncio.sleep(scraper._config.min_delay_sec)
                    sessions = await scraper.fetch_sessions(day)

                    for session in sessions:
                        logger.info("    セッション: %s", session.session_title[:60])

                        # Step 4: 各セッションの発表を取得
                        await asyncio.sleep(scraper._config.min_delay_sec)

                        # セッションページを読み込んで発表URLを収集
                        ok = await scraper._load(session.url)
                        if not ok:
                            logger.warning("セッションページ失敗: %s", session.url)
                            continue

                        links = await scraper._page.evaluate("""() => {
                            return Array.from(document.querySelectorAll('a[href*="/subject/"]'))
                                .map(a => a.href)
                                .filter(h => !h.startsWith('mailto'));
                        }""")

                        # 重複除去
                        seen_urls: set[str] = set()
                        unique_urls: list[str] = []
                        for url in links:
                            if url not in seen_urls:
                                seen_urls.add(url)
                                unique_urls.append(url)

                        # Step 5: 各発表を取得
                        for pres_url in unique_urls:
                            if progress.is_visited(pres_url):
                                logger.debug("      スキップ（訪問済み）: %s", pres_url)
                                continue

                            await scraper._random_delay()
                            pres = await scraper.fetch_presentation_detail(
                                pres_url, event.year, session.session_id
                            )

                            if pres:
                                exporter.write(pres)
                                total_saved += 1
                                logger.info(
                                    "      [%s] %s",
                                    pres.presentation_id,
                                    pres.title[:50],
                                )

                            progress.mark_visited(pres_url)

                        # セッションごとに進捗を保存
                        progress.save()

                # 大会完了をマーク
                progress.mark_event_completed(event.year)
                progress.save()
                logger.info("[%d] 完了", event.year)

    except KeyboardInterrupt:
        logger.info("中断されました。進捗を保存します...")
        progress.save()

    finally:
        await scraper.stop()

    logger.info("クロール完了。保存件数: %d件 → %s", total_saved, output_csv)


# ─── CLI ─────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="人工知能学会全国大会 発表情報クローラー"
    )
    parser.add_argument(
        "--start-year", type=int, default=2018,
        help="クロール開始年度 (デフォルト: 2018)",
    )
    parser.add_argument(
        "--end-year", type=int, default=2025,
        help="クロール終了年度 (デフォルト: 2025)",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="progress.json を読み込んで途中から再開する",
    )
    parser.add_argument(
        "--output-dir", default="output",
        help="出力ディレクトリ (デフォルト: output)",
    )
    parser.add_argument(
        "--no-headless", action="store_true",
        help="ブラウザを表示モードで起動する（デバッグ用）",
    )
    parser.add_argument(
        "--min-delay", type=float, default=1.5,
        help="リクエスト間の最小待機秒数 (デフォルト: 1.5)",
    )
    parser.add_argument(
        "--max-delay", type=float, default=3.5,
        help="リクエスト間の最大待機秒数 (デフォルト: 3.5)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    config = CrawlerConfig(
        start_year=args.start_year,
        end_year=args.end_year,
        output_dir=args.output_dir,
        headless=not args.no_headless,
        min_delay_sec=args.min_delay,
        max_delay_sec=args.max_delay,
    )

    setup_logging(config.output_dir)

    asyncio.run(run(config, resume=args.resume))


if __name__ == "__main__":
    main()

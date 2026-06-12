"""Playwrightを使ったJSAIサイトのスクレイパー"""
import asyncio
import logging
import re
import random
from typing import Optional
from urllib.parse import urljoin, urlparse

from playwright.async_api import Page, Browser, async_playwright

from config import CrawlerConfig
from models import EventInfo, ScheduleDay, SessionInfo, PresentationInfo

logger = logging.getLogger(__name__)

BASE_URL = "https://confit.atlas.jp"
EVENTS_URL = f"{BASE_URL}/guide/organizer/jsai/events"

# 年度別トークン（セッションURLのクエリパラメータ）
# タイムテーブルページを読んだ際に実際のリンクに含まれるので自動取得するが
# フォールバック用に既知の値を保持する
_KNOWN_TOKENS: dict[int, str] = {
    2018: "kviObfLBIi",
    2019: "",
    2020: "",
    2021: "",
    2022: "",
    2023: "",
    2024: "shkTXsFRBo",
    2025: "",
}


class EventScraper:
    def __init__(self, config: CrawlerConfig):
        self._config = config
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None

    # ------------------------------------------------------------------
    # ブラウザ管理
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self._config.headless,
        )
        context = await self._browser.new_context(
            locale="ja-JP",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        self._page = await context.new_page()
        self._page.set_default_timeout(self._config.page_load_timeout_ms)

    async def stop(self) -> None:
        if self._browser:
            await self._browser.close()
        if hasattr(self, "_playwright"):
            await self._playwright.stop()

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    async def fetch_events(self) -> list[EventInfo]:
        """大会一覧ページから2018年度以降の大会情報を取得"""
        ok = await self._load(EVENTS_URL)
        if not ok:
            logger.error("大会一覧ページの読み込み失敗: %s", EVENTS_URL)
            return []

        links = await self._page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a[href*="/guide/event/jsai"]'))
                .map(a => ({ href: a.href, text: a.textContent.trim() }))
                .filter(a => a.text.length > 0);
        }""")

        events: list[EventInfo] = []
        seen_years: set[int] = set()
        for link in links:
            m = re.search(r"/guide/event/jsai(\d{4})/top", link["href"])
            if not m:
                continue
            year = int(m.group(1))
            if year < self._config.start_year or year > self._config.end_year:
                continue
            if year in seen_years:
                continue
            seen_years.add(year)
            events.append(EventInfo(
                year=year,
                name=link["text"],
                top_url=link["href"],
            ))

        events.sort(key=lambda e: e.year)
        logger.info("大会数: %d件", len(events))
        return events

    async def fetch_schedule_days(self, event: EventInfo) -> list[ScheduleDay]:
        """大会トップページから全日程URLを取得"""
        ok = await self._load(event.top_url)
        if not ok:
            logger.error("大会トップページ読み込み失敗: %s", event.top_url)
            return []

        links = await self._page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a[href*="/table/"]'))
                .map(a => ({ href: a.href, text: a.textContent.trim() }));
        }""")

        days: list[ScheduleDay] = []
        seen: set[str] = set()
        for link in links:
            m = re.search(r"/table/(\d{8}(_poster)?)", link["href"])
            if not m:
                continue
            date_str = m.group(1)
            if date_str in seen:
                continue
            seen.add(date_str)
            days.append(ScheduleDay(
                year=event.year,
                date_str=date_str,
                label=link["text"],
                is_poster="_poster" in date_str,
                url=link["href"],
            ))

        days.sort(key=lambda d: d.date_str)
        logger.info("[%d] 日程数: %d件", event.year, len(days))
        return days

    async def fetch_sessions(self, day: ScheduleDay) -> list[SessionInfo]:
        """日程タイムテーブルページから全セッション情報を取得"""
        ok = await self._load(day.url)
        if not ok:
            logger.error("タイムテーブルページ読み込み失敗: %s", day.url)
            return []

        links = await self._page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a[href*="/session/"]'))
                .map(a => ({ href: a.href, text: a.textContent.trim() }))
                .filter(a => !a.href.startsWith('mailto'));
        }""")

        sessions: list[SessionInfo] = []
        seen: set[str] = set()
        for link in links:
            href = link["href"]
            # /session/{raw_id}/tables からraw_idを抽出
            m = re.search(r"/session/([^/]+)/", href)
            if not m:
                continue
            raw_id = m.group(1)
            if href in seen:
                continue
            seen.add(href)

            # タイトル先頭の "[1E1]" や "[1B3-GS-2]" からセッションIDを取得
            session_id = self._extract_bracket_id(link["text"]) or raw_id
            sessions.append(SessionInfo(
                year=day.year,
                session_id=session_id,
                session_raw_id=raw_id,
                session_title=link["text"],
                url=href,
            ))

        logger.info("[%d %s] セッション数: %d件", day.year, day.date_str, len(sessions))
        return sessions

    async def fetch_presentations(self, session: SessionInfo) -> list[PresentationInfo]:
        """セッションページから全発表情報を収集して返す"""
        ok = await self._load(session.url)
        if not ok:
            logger.error("セッションページ読み込み失敗: %s", session.url)
            return []

        links = await self._page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a[href*="/subject/"]'))
                .map(a => a.href)
                .filter(h => !h.startsWith('mailto'));
        }""")

        # 重複除去（"PDF ダウンロード" リンクなど同一URLが複数出る）
        seen: set[str] = set()
        unique_urls: list[str] = []
        for url in links:
            if url not in seen:
                seen.add(url)
                unique_urls.append(url)

        presentations: list[PresentationInfo] = []
        for url in unique_urls:
            await self._random_delay()
            p = await self.fetch_presentation_detail(url, session.year, session.session_id)
            if p:
                presentations.append(p)

        return presentations

    async def fetch_presentation_detail(
        self, url: str, year: int, session_id: str
    ) -> Optional[PresentationInfo]:
        """個別発表ページから発表詳細情報を取得"""
        ok = await self._load(url)
        if not ok:
            logger.warning("発表ページ読み込み失敗: %s", url)
            return None

        data = await self._page.evaluate("""() => {
            // タイトル (article内の h1[title="講演名"])
            const titleEl = document.querySelector('article h1[title="講演名"], article .title h1');
            const titleRaw = titleEl ? titleEl.textContent.trim() : '';

            // キーワード
            const kwEl = document.querySelector('article .keyword, article p[title="キーワード"]');
            const kwRaw = kwEl ? kwEl.textContent.trim() : '';

            // 要約 (.summary または .outline)
            const abEl = document.querySelector('article .summary, article .outline');
            const abstract = abEl ? abEl.textContent.trim() : '';

            // サブタイトル（subtitle クラスが存在する年度向け）
            const subEl = document.querySelector('article .subtitle, article h2.subtitle');
            const subtitle = subEl ? subEl.textContent.trim() : '';

            return { titleRaw, kwRaw, abstract, subtitle };
        }""")

        title_raw: str = data.get("titleRaw", "").strip()
        kw_raw: str = data.get("kwRaw", "").strip()
        abstract: str = data.get("abstract", "").strip()
        subtitle: str = data.get("subtitle", "").strip() or None

        # "[1E1-01] タイトル" → presentation_id, title に分割
        m = re.match(r"\[([^\]]+)\]\s*(.*)", title_raw, re.DOTALL)
        if m:
            presentation_id = m.group(1).strip()
            title = m.group(2).strip()
        else:
            # フォールバック: URLからIDを取得
            um = re.search(r"/subject/([^/]+)/", url)
            presentation_id = um.group(1) if um else ""
            title = title_raw

        # "キーワード：..." → キーワード部分を取り出す
        keywords = re.sub(r"^キーワード[：:]\s*", "", kw_raw).strip()

        # URLに含まれる発表IDからセッションIDを補完
        um = re.search(r"/subject/([^/]+)/", url)
        if um:
            subject_raw = um.group(1)
            # 末尾の "-数字" を除いた部分がセッションID
            derived_session_id = re.sub(r"-\d+$", "", subject_raw)
            if not session_id or session_id == subject_raw:
                session_id = derived_session_id

        return PresentationInfo(
            year=year,
            session_id=session_id,
            presentation_id=presentation_id,
            title=title,
            subtitle=subtitle,
            keywords=keywords,
            abstract=abstract,
            url=url,
        )

    # ------------------------------------------------------------------
    # 内部ユーティリティ
    # ------------------------------------------------------------------

    async def _load(self, url: str) -> bool:
        """リトライ付きページ読み込み。成功したらTrue"""
        config = self._config
        for attempt in range(1, config.max_retry + 1):
            try:
                await self._page.goto(url, wait_until="domcontentloaded",
                                      timeout=config.page_load_timeout_ms)
                # networkidle を待機（タイムアウトしても続行）
                try:
                    await self._page.wait_for_load_state(
                        "networkidle",
                        timeout=config.networkidle_timeout_ms,
                    )
                except Exception:
                    pass  # networkidle でタイムアウトしても DOM は取得できる
                return True
            except Exception as exc:
                wait = config.retry_backoff_sec * (2 ** (attempt - 1))
                logger.warning(
                    "ページ読み込みエラー (attempt %d/%d): %s — %s — 待機 %.0f秒",
                    attempt, config.max_retry, url, exc, wait,
                )
                if attempt < config.max_retry:
                    await asyncio.sleep(wait)
        return False

    async def _random_delay(self) -> None:
        """リクエスト間ランダム待機"""
        delay = random.uniform(
            self._config.min_delay_sec,
            self._config.max_delay_sec,
        )
        await asyncio.sleep(delay)

    @staticmethod
    def _extract_bracket_id(text: str) -> Optional[str]:
        """テキスト先頭の "[xxxxx]" 内の文字列を返す"""
        m = re.match(r"\[([^\]]+)\]", text.strip())
        return m.group(1) if m else None

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

# 2026年度以降の新サイト
PUB_BASE_URL = "https://pub.confit.atlas.jp"
PUB_MIN_YEAR = 2026  # pub.confit.atlas.jp に移行した年度

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
        """大会一覧ページから大会情報を取得（2026年度以降は新サイトを直接追加）"""
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

        # 2026年度以降は pub.confit.atlas.jp を直接追加
        for year in range(max(self._config.start_year, PUB_MIN_YEAR),
                          self._config.end_year + 1):
            if year not in seen_years:
                events.append(EventInfo(
                    year=year,
                    name=f"{year}年度 人工知能学会全国大会",
                    top_url=f"{PUB_BASE_URL}/ja/event/jsai{year}",
                ))
                seen_years.add(year)

        events.sort(key=lambda e: e.year)
        logger.info("大会数: %d件", len(events))
        return events

    async def fetch_schedule_days(self, event: EventInfo) -> list[ScheduleDay]:
        """大会トップページから全日程URLを取得"""
        is_pub = event.year >= PUB_MIN_YEAR
        if is_pub:
            # pub.confit.atlas.jp はJSAI公式サイトを先に踏まないと403になる
            prefetch_url = f"https://conf.ai-gakkai.or.jp/jsai{event.year}/"
            logger.info("[%d] pub用プリフェッチ: %s", event.year, prefetch_url)
            await self._load(prefetch_url)

        ok = await self._load(event.top_url)
        if not ok:
            logger.error("大会トップページ読み込み失敗: %s", event.top_url)
            return []

        links = await self._page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a[href*="/table/"]'))
                .map(a => ({ href: a.href, text: a.textContent.trim() }));
        }""")

        is_pub = event.year >= PUB_MIN_YEAR
        # 旧サイト: YYYYMMDD(_poster)?  新サイト: YYYY-MM-DD
        table_re = re.compile(
            r"/table/(\d{4}-\d{2}-\d{2})" if is_pub
            else r"/table/(\d{8}(_poster)?)"
        )

        days: list[ScheduleDay] = []
        seen: set[str] = set()
        for link in links:
            m = table_re.search(link["href"])
            if not m:
                continue
            date_str = m.group(1)
            if date_str in seen:
                continue
            seen.add(date_str)
            # 新サイトはポスター専用ページなし（ポスターも同一日程ページ内）
            is_poster = False if is_pub else ("_poster" in date_str)
            days.append(ScheduleDay(
                year=event.year,
                date_str=date_str,
                label=link["text"],
                is_poster=is_poster,
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

        is_pub = day.year >= PUB_MIN_YEAR

        if is_pub:
            # 新サイト: /session/{raw_id} 形式（末尾スラッシュなし・トークンなし）
            links = await self._page.evaluate("""() => {
                return Array.from(document.querySelectorAll('a[href*="/session/"]'))
                    .map(a => ({ href: a.href, text: a.textContent.trim() }))
                    .filter(a => !a.href.includes('login'));
            }""")
            session_re = re.compile(r"/session/([^/?#]+)$")
        else:
            # 旧サイト: /session/{raw_id}/tables?token 形式
            links = await self._page.evaluate("""() => {
                return Array.from(document.querySelectorAll('a[href*="/session/"]'))
                    .map(a => ({ href: a.href, text: a.textContent.trim() }))
                    .filter(a => !a.href.startsWith('mailto'));
            }""")
            session_re = re.compile(r"/session/([^/]+)/")

        sessions: list[SessionInfo] = []
        seen: set[str] = set()
        for link in links:
            href = link["href"]
            m = session_re.search(href)
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

        is_pub = session.year >= PUB_MIN_YEAR
        # 新サイトは /presentation/{id}、旧サイトは /subject/{id}
        link_selector = 'a[href*="/presentation/"]' if is_pub else 'a[href*="/subject/"]'

        links = await self._page.evaluate(f"""() => {{
            return Array.from(document.querySelectorAll('{link_selector}'))
                .map(a => a.href)
                .filter(h => !h.includes('login') && !h.startsWith('mailto'));
        }}""")

        # 重複除去（PDFダウンロードリンクなど同一URLが複数出る場合）
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

        is_pub = year >= PUB_MIN_YEAR

        if is_pub:
            # 新サイト (pub.confit.atlas.jp) 用セレクタ
            # タイトル: main h1.thd 内に [ID]スパン + タイトルテキスト + サブタイトルスパン
            # キーワード: h2.tcapt の次の p.tsm
            # 要約: .pd-v-md 直下の .mg-t-md
            data = await self._page.evaluate("""() => {
                // タイトルh1
                const h1 = document.querySelector('main h1.thd, .bf-content h1');
                const idSpan = h1 ? h1.querySelector('span.tbd') : null;
                const subSpan = h1 ? h1.querySelector('span.tdf, span.d-blk.tdf') : null;

                let titleRaw = '';
                let subtitle = '';
                if (h1) {
                    // サブタイトルを先に退避してから残りをタイトルとする
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

                // キーワード: h2.tcapt の次のp要素
                const kwHeader = document.querySelector('h2.tcapt');
                const kwEl = kwHeader ? kwHeader.nextElementSibling : null;
                const kwRaw = kwEl ? kwEl.textContent.trim() : '';

                // 要約: .pd-v-md 直下の最初の .mg-t-md
                const absEl = document.querySelector('.pd-v-md .mg-t-md, .pd-v-md > div:not(.l-flex):not([class*="pd"]):not([class*="mg-t-base"]):not([class*="bdb"])  > .mg-t-md');
                // フォールバック: h2.tcaptの後ろのdiv
                const absEl2 = document.querySelector('.box-s-bkg + .mg-t-md, .box-s-bkg ~ .mg-t-md');
                const abstract = (absEl || absEl2) ? (absEl || absEl2).textContent.trim() : '';

                return { titleRaw, kwRaw, abstract, subtitle };
            }""")
        else:
            # 旧サイト (confit.atlas.jp) 用セレクタ
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
            if year >= PUB_MIN_YEAR:
                um = re.search(r"/presentation/([^/?#]+)", url)
            else:
                um = re.search(r"/subject/([^/]+)/", url)
            presentation_id = um.group(1) if um else ""
            title = title_raw

        # "キーワード：..." → キーワード部分を取り出す
        keywords = re.sub(r"^キーワード[：:]\s*", "", kw_raw).strip()

        # URLに含まれる発表IDからセッションIDを補完
        if year >= PUB_MIN_YEAR:
            um = re.search(r"/presentation/([^/?#]+)", url)
            if um:
                subject_raw = um.group(1)
                derived_session_id = re.sub(r"-\d+$", "", subject_raw)
                # 2026新サイトはraw_idがハッシュなので常に発表URLベースのIDを使用
                session_id = derived_session_id
        else:
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

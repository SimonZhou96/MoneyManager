#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Free browser-based search provider using Bing and Baidu — no API quota needed."""

from __future__ import annotations

import os
import re
import logging
import time
import random
from typing import Dict, List, Optional
from urllib.parse import quote_plus

from .models import ScreeningSignalRow, SearchDocument

logger = logging.getLogger(__name__)


def _random_sleep(min_ms: int, max_ms: int) -> None:
    """Random delay to avoid bot detection."""
    if max_ms <= min_ms:
        time.sleep(min_ms / 1000.0)
        return
    time.sleep(random.randint(min_ms, max_ms) / 1000.0)


# ── Stealth script ────────────────────────────────────────────

_STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', {
    get: () => [
        {filename: 'internal-pdf-viewer', name: 'Chrome PDF Plugin'},
        {filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', name: 'Chrome PDF Viewer'},
        {filename: 'internal-nacl-plugin', name: 'Native Client'}
    ]
});
Object.defineProperty(navigator, 'languages', {
    get: () => ['zh-CN', 'zh', 'en']
});
window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
"""


# ── Global browser singleton (shared across all provider instances) ──

_global_browser: Optional[object] = None
_global_playwright: Optional[object] = None
_global_browser_lock = None


def _get_global_browser():
    """Return a shared browser instance to avoid Chromium pool exhaustion."""
    global _global_browser, _global_playwright, _global_browser_lock
    import threading

    if _global_browser_lock is None:
        _global_browser_lock = threading.Lock()

    with _global_browser_lock:
        if _global_browser is not None:
            return _global_browser, _global_playwright

        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        _global_playwright = pw
        _global_browser = pw.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-address-pool",
            ],
        )
        logger.info("BingBaidu: global browser pool initialized (shared singleton)")
        return _global_browser, _global_playwright


# ── Provider ──────────────────────────────────────────────────

class BingBaiduSearchProvider:
    """Free search via headless browser scraping Bing and Baidu result pages.

    Implements the same ``search()`` / ``search_companies_batch()`` contract
    as :class:`TavilySearchProvider` so it slots directly into the fallback chain.
    """

    name = "bing_baidu"

    def __init__(
        self,
        headless: bool = True,
        timeout_sec: int = 30,
    ):
        self._headless = headless
        self._timeout_sec = max(10, int(timeout_sec))
        self._browser = None
        self._playwright = None

    # ── public API ──────────────────────────────────────────

    @property
    def is_available(self) -> bool:
        return self._ensure_playwright()

    def _ensure_playwright(self) -> bool:
        """Lazy-check that Playwright is importable (no browser launch yet)."""
        if self._browser is not None:
            return True
        try:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright
            return True
        except ImportError:
            logger.warning("BingBaiduSearchProvider: playwright not installed")
            return False

    def _get_browser(self):
        """Return the shared global browser singleton (lazy-init with retry)."""
        if self._browser is not None:
            return self._browser

        for attempt in range(3):
            try:
                browser, pw = _get_global_browser()
                self._browser = browser
                self._playwright = lambda: pw  # compat
                self._pw_instance = pw
                return self._browser
            except Exception as exc:
                logger.warning("BingBaidu: browser init attempt %d/3: %s", attempt + 1, exc)
                if attempt < 2:
                    import time
                    time.sleep(2.0)
        raise RuntimeError("BingBaidu: failed to init browser after 3 attempts")

    def _new_page(self):
        browser = self._get_browser()
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
        )
        page = context.new_page()
        page.add_init_script(_STEALTH_SCRIPT)
        return page, context

    # ── SearchProvider contract ──────────────────────────────

    def search(self, query: str, max_results: int) -> List[SearchDocument]:
        """Search Bing, fall back to Baidu if not enough results."""
        if not query.strip():
            return []
        if not self._ensure_playwright():
            return []

        max_results = max(1, int(max_results))

        # 1) Bing
        documents = self._search_bing(query, max_results)
        logger.info("BingBaidu: Bing returned %d results for '%s'", len(documents), query[:60])

        # 2) Baidu supplement
        if len(documents) < max_results:
            baidu_docs = self._search_baidu(query, max_results - len(documents))
            logger.info("BingBaidu: Baidu returned %d results for '%s'", len(baidu_docs), query[:60])
            documents.extend(baidu_docs)

        return documents

    def search_companies_batch(
        self,
        market: str,
        rows: List[ScreeningSignalRow],
        max_results: int,
    ) -> Dict[str, List[SearchDocument]]:
        """Batch company search using Bing/Baidu-optimized query building.

        Uses small batches (≤3 stocks per query) to avoid signal dilution,
        then does a per-stock fallback pass for unmatched stocks.
        """
        if not rows:
            return {}

        from .search_providers import (
            _assign_documents_to_stocks,
            _company_batch_max_results,
            _debug_company_batch_search,
        )

        grouped: Dict[str, List[SearchDocument]] = {row.code: [] for row in rows}

        # Phase 1: Batch search with small batches (max 3 stocks)
        for query_rows in _split_rows_for_browser(market, rows):
            query = _build_browser_company_batch_query(market, query_rows)
            batch_max = _company_batch_max_results(max_results, len(query_rows))
            documents = self.search(query, batch_max)
            assigned = _assign_documents_to_stocks(documents, query_rows)
            _debug_company_batch_search(market, query_rows, query, batch_max, documents, assigned)
            for code, code_documents in assigned.items():
                grouped.setdefault(code, []).extend(code_documents)

        # Phase 2: Per-stock fallback for unmatched stocks
        unmatched = [r for r in rows if not grouped.get(r.code)]
        if unmatched:
            logger.info("BingBaidu: %d/%d unmatched, doing per-stock fallback",
                        len(unmatched), len(rows))
            for row in unmatched:
                try:
                    query = _build_browser_company_batch_query(market, [row])
                    documents = self.search(query, max_results)
                    assigned = _assign_documents_to_stocks(documents, [row])
                    grouped.setdefault(row.code, []).extend(assigned.get(row.code, []))
                except Exception as exc:
                    logger.warning("BingBaidu: per-stock fallback failed for %s: %s", row.code, exc)

        return grouped

    # ── Bing ─────────────────────────────────────────────────

    def _search_bing(self, query: str, max_results: int) -> List[SearchDocument]:
        documents: List[SearchDocument] = []
        try:
            page, context = self._new_page()
        except Exception as exc:
            logger.warning("BingBaidu: failed to create browser page: %s", exc)
            return documents

        url = f"https://www.bing.com/search?q={quote_plus(query)}&setlang=zh-cn"
        try:
            page.goto(url, timeout=self._timeout_sec * 1000, wait_until="domcontentloaded")
            _random_sleep(1500, 2500)
            page.wait_for_selector("#b_results", timeout=15000)
            _random_sleep(500, 1000)
            html = page.inner_html("#b_results")
            documents = self._parse_bing_html(html, max_results)
        except Exception as exc:
            logger.warning("BingBaidu: Bing search failed: %s", exc)
        finally:
            context.close()
        return documents

    def _parse_bing_html(self, html: str, max_results: int) -> List[SearchDocument]:
        from bs4 import BeautifulSoup

        documents: List[SearchDocument] = []
        filtered = 0
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return documents

        for li in soup.select("li.b_algo"):
            if len(documents) >= max_results:
                break

            anchor = li.select_one("h2 a")
            if not anchor:
                continue
            url = (anchor.get("href") or "").strip()
            title = anchor.get_text(strip=True)
            if not url:
                continue

            snippet_el = li.select_one(".b_caption p") or li.select_one(".b_paractl")
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""

            doc = SearchDocument(title=title, url=url, content=snippet, score=None, query="")
            if not _is_news_quality_result(doc):
                filtered += 1
                continue

            documents.append(doc)

        if filtered:
            logger.info("BingBaidu: filtered %d low-quality Bing results (kept %d)", filtered, len(documents))
        logger.debug("BingBaidu: parsed %d Bing results", len(documents))
        return documents

    # ── Baidu ────────────────────────────────────────────────

    def _search_baidu(self, query: str, max_results: int) -> List[SearchDocument]:
        documents: List[SearchDocument] = []
        try:
            page, context = self._new_page()
        except Exception as exc:
            logger.warning("BingBaidu: failed to create browser page: %s", exc)
            return documents

        url = f"https://www.baidu.com/s?wd={quote_plus(query)}"
        try:
            page.goto(url, timeout=self._timeout_sec * 1000, wait_until="domcontentloaded")
            _random_sleep(2000, 3000)
            page.wait_for_selector("#content_left", timeout=15000)
            _random_sleep(500, 1000)
            html = page.inner_html("#content_left")
            documents = self._parse_baidu_html(html, max_results)
        except Exception as exc:
            logger.warning("BingBaidu: Baidu search failed: %s", exc)
        finally:
            context.close()
        return documents

    def _parse_baidu_html(self, html: str, max_results: int) -> List[SearchDocument]:
        from bs4 import BeautifulSoup

        documents: List[SearchDocument] = []
        filtered = 0
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return documents

        for container in soup.select(".result, .c-container"):
            if len(documents) >= max_results:
                break

            anchor = container.select_one("h3 a, h3.t a")
            if not anchor:
                anchor = container.select_one("a[data-click]")
            if not anchor:
                continue

            url = (anchor.get("href") or "").strip()
            title = anchor.get_text(strip=True)

            # Some Baidu links are javascript pseudo-links
            if not url or url.startswith("javascript:"):
                data_click = anchor.get("data-click") or ""
                if data_click:
                    from urllib.parse import unquote
                    for part in data_click.split("&"):
                        if part.startswith("mu="):
                            url = unquote(part[3:])
                            break

            if not url or url.startswith("javascript:"):
                continue

            snippet_el = (
                container.select_one(".c-abstract")
                or container.select_one(".content-right")
                or container.select_one(".c-span-last")
                or container.select_one(".c-row")
            )
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            # Filter noise
            if len(snippet) < 10:
                snippet = ""

            doc = SearchDocument(title=title, url=url, content=snippet, score=None, query="")
            if not _is_news_quality_result(doc):
                filtered += 1
                continue

            documents.append(doc)

        if filtered:
            logger.info("BingBaidu: filtered %d low-quality Baidu results (kept %d)", filtered, len(documents))
        logger.debug("BingBaidu: parsed %d Baidu results", len(documents))
        return documents

    # ── cleanup ──────────────────────────────────────────

    def close(self):
        """Release local references.  The global browser singleton stays alive."""
        self._browser = None
        self._pw_instance = None

    def __del__(self):
        self._browser = None
        self._pw_instance = None


# ── Quality filter ───────────────────────────────────────

# Title patterns that indicate a stock overview / quote page (NOT real news)
_LOW_QUALITY_TITLE_PATTERNS: list[str] = [
    "首頁概覽", "首页概览",           # Overview page
    "公司概况", "公司資料",          # Company profile
    "基本數據概覽",                  # Basic data overview
    "走勢圖", "走势图",              # Price chart
    "實時行情", "实时行情",          # Real-time quote
]

# URL patterns for quote/overview pages
_LOW_QUALITY_URL_KEYWORDS: list[str] = [
    "/quote/", "/quotes/",
    "basicdata", "brief",
    "overview", "summary",
]


def _is_news_quality_result(doc: SearchDocument) -> bool:
    """Return True if the document looks like a real news article, not a quote page.

    Filters out:
    - Stock overview/quote pages (e.g. "股价、新闻、报价和记录 - Yahoo 财经")
    - Bare price chart pages with no news content
    - Empty/skeleton result entries
    """
    title = doc.title
    url = doc.url.lower()
    content = doc.content

    # 1) Empty content is never useful
    if not content or len(content) < 15:
        return False

    # 2) Yahoo Finance / Google Finance / etnet "quote+news" aggregator pages
    if any(kw in title for kw in ["股價、新聞、報價", "股价、新闻、报价",
                                    "Stock Price & News", "股價、新聞",
                                    "免费即时股票报价", "股票报价"]):
        return False

    # 3) Pure quote/chart pages from finance portals
    if "股票股价" in title and any(kw in title for kw in ["实时行情", "財報_數據報告", "走势图"]):
        return False

    # 4) Stock overview patterns in title
    for pat in _LOW_QUALITY_TITLE_PATTERNS:
        if pat in title:
            return False

    # 5) Non-news URL patterns
    for kw in _LOW_QUALITY_URL_KEYWORDS:
        if kw in url:
            if len(content) < 100:
                return False

    # 6) Content is too short: require at least one sentence about events
    if len(content) < 30:
        return False

    # 7) Title-only "股市" page without any event/article indicator
    if "股市" in title and "Investing.com" in title:
        if len(content) < 50:
            return False

    return True


# ── Browser-optimized query builders ─────────────────────
# Bing/Baidu need stock-code-forward queries WITHOUT terms like "HKEX"
# that dominate search results and crowd out small-cap stocks.


_MARKET_BROWSER_PREFIX: dict[str, str] = {
    "HK": "港股",
    "US": "美股",
    "A": "A股",
}


def _split_rows_for_browser(
    market: str,
    rows,
    max_chars: int = 120,
):
    batches = []
    current = []
    for row in rows:
        candidate = [*current, row]
        if current and len(_build_browser_company_batch_query(market, candidate)) > max_chars:
            batches.append(current)
            current = [row]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


def _build_browser_company_batch_query(
    market: str,
    rows,
) -> str:
    """Build a Bing/Baidu-friendly query: stock names/codes first, market hint only.

    Example: 港股: MEDTIDE 03880 公告 新闻 财报
    """
    import re
    prefix = _MARKET_BROWSER_PREFIX.get(str(market).upper(), str(market))

    parts = []
    for row in rows:
        code = str(row.code or "").strip()
        name = str(row.name or "").strip()

        # Normalize code: "HK.03880" → "03880" for search
        ticker = code
        if "." in ticker:
            _, ticker = ticker.split(".", 1)

        # Use name as primary term since it's more specific than code
        name_clean = re.sub(r"\s+", " ", name).strip()
        if name_clean:
            parts.append(name_clean)

        # Also include ticker without leading zero
        if ticker and ticker.isdigit():
            parts.append(ticker)
            no_zero = ticker.lstrip("0")
            if no_zero and no_zero != ticker and len(no_zero) >= 3:
                parts.append(no_zero)
        elif ticker:
            parts.append(ticker)

    # Core: name/code terms → market label → keywords
    core = " ".join(parts[:12])  # Limit terms to avoid overly long queries
    query = f"{prefix}: {core} 公告 新闻 财报"

    # Truncate to reasonable length
    if len(query) > 400:
        query = query[:397].rstrip() + "..."

    return query


# ── End module-level helpers ────────────────────────────

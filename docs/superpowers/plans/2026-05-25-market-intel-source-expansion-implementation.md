# Market Intel Source Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand `market_intel` news coverage with stable HTTP sources, source metadata, provider health visibility, and Evidence Pack ranking without changing rule-chain pass/fail decisions.

**Architecture:** Keep `market_intel` as the source-integration boundary. Add source registry metadata, split Cailianpress out of the generic news provider, add Sina and TradingView providers, expose source metadata through the existing Market Intel API/UI, and rank/dedupe evidence inside `EvidencePackBuilder`. Search-like providers and browser/cookie-dependent sources are represented as disabled registry entries until their contracts are verified.

**Tech Stack:** Python 3 dataclasses, FastAPI, `requests`, Python `unittest`, React 18, TypeScript, Vite, existing source-text frontend tests.

---

## Scope Check

This plan implements the first stable source-expansion stage from `2026-05-25-market-intel-source-expansion-design.md`.

It does implement:

- source registry and environment-driven provider ordering;
- Cailianpress response-shape fix plus keyword/HTML parser helpers;
- Sina JSONP parser and provider;
- TradingView list/detail parser and bounded detail fetch;
- provider source metadata endpoint;
- Evidence Pack dedupe/ranking improvements;
- frontend source-health display.

It does not implement live Iwencai, Eastmoney Miaoxiang, or Xueqiu browser/cookie acquisition. Those providers are explicitly listed in the registry as disabled by default, so API/UI can show why they are unavailable without adding login/session risk to automated screening.

Before implementation, run:

```bash
git status --short
```

Expected: There may be unrelated local changes such as `.idea` and `__pycache__`. Do not stage or revert them. Stage only files listed in each task.

## File Structure

- Create: `stock_screener/market_intel/providers/source_registry.py`
  - Source/provider metadata, env flags, ordering, and source-health payloads.
- Create: `stock_screener/market_intel/providers/cailianpress.py`
  - Cailianpress provider and parser helpers.
- Modify: `stock_screener/market_intel/providers/news.py`
  - Compatibility alias to keep old imports working.
- Create: `stock_screener/market_intel/providers/sina.py`
  - Sina finance live-feed provider and JSONP parser.
- Create: `stock_screener/market_intel/providers/tradingview.py`
  - TradingView news-flow provider and bounded detail handling.
- Modify: `stock_screener/market_intel/providers/factory.py`
  - Build providers from registry ordering and env flags.
- Modify: `stock_screener/market_intel/providers/base.py`
  - Add better dedupe helpers if needed by Evidence Pack ranking.
- Modify: `stock_screener/market_intel/evidence.py`
  - Add source-aware dedupe and ranking.
- Modify: `stock_screener/web/market_intel.py`
  - Add `/api/market-intel/sources`.
- Modify: `stock_screener/web_frontend/src/features/marketIntel/api.ts`
  - Add `getMarketIntelSources`.
- Modify: `stock_screener/web_frontend/src/features/marketIntel/types.ts`
  - Add source metadata types.
- Modify: `stock_screener/web_frontend/src/features/marketIntel/MarketIntelPage.tsx`
  - Render provider/source health.
- Modify: `stock_screener/.env.example`
  - Document source-expansion flags.
- Modify: `stock_screener/deploy/README.md`
  - Document provider order, disabled-by-default providers, and cost policy.
- Modify: `stock_screener/tests/test_market_intel_providers.py`
  - Provider parser/factory coverage.
- Modify: `stock_screener/tests/test_market_intel_evidence.py`
  - Evidence ranking/dedupe coverage.
- Modify: `stock_screener/tests/test_market_intel_api.py`
  - Source metadata endpoint coverage.
- Modify: `stock_screener/tests/test_market_intel_frontend.py`
  - Source-health UI contract coverage.

## Task 1: Source Registry and Provider Factory

**Files:**
- Create: `stock_screener/market_intel/providers/source_registry.py`
- Modify: `stock_screener/market_intel/providers/factory.py`
- Modify: `stock_screener/tests/test_market_intel_providers.py`

- [ ] **Step 1: Write failing registry and factory tests**

Append to `stock_screener/tests/test_market_intel_providers.py`:

```python
class MarketIntelSourceRegistryTests(unittest.TestCase):
    def test_source_registry_exposes_default_enabled_and_disabled_sources(self):
        from market_intel.providers.source_registry import list_source_configs

        configs = {item.provider: item for item in list_source_configs()}

        self.assertTrue(configs["cailianpress"].default_enabled)
        self.assertTrue(configs["sina"].default_enabled)
        self.assertTrue(configs["tradingview"].default_enabled)
        self.assertFalse(configs["iwencai"].default_enabled)
        self.assertFalse(configs["eastmoney_search"].default_enabled)
        self.assertFalse(configs["xueqiu"].default_enabled)
        self.assertFalse(configs["xueqiu"].automated_safe)
        self.assertTrue(configs["xueqiu"].requires_browser)

    def test_source_registry_payload_is_frontend_safe(self):
        from market_intel.providers.source_registry import source_status_payload

        rows = source_status_payload()
        cailianpress = next(item for item in rows if item["provider"] == "cailianpress")
        xueqiu = next(item for item in rows if item["provider"] == "xueqiu")

        self.assertEqual(cailianpress["source"], "财联社")
        self.assertEqual(cailianpress["status"], "enabled")
        self.assertEqual(xueqiu["status"], "disabled")
        self.assertEqual(xueqiu["disabled_reason"], "requires browser/cookie support")

    def test_factory_uses_env_order_and_enabled_flags(self):
        import os
        from unittest.mock import patch

        from market_intel.providers.factory import build_market_intel_providers

        env = {
            "MARKET_INTEL_PROVIDER_ORDER": "tradingview,cailianpress,sina,xueqiu",
            "MARKET_INTEL_ENABLE_TRADINGVIEW": "1",
            "MARKET_INTEL_ENABLE_CAILIANPRESS": "1",
            "MARKET_INTEL_ENABLE_SINA": "0",
            "MARKET_INTEL_ENABLE_XUEQIU": "1",
            "MARKET_INTEL_BROWSER_PROVIDERS_ALLOWED": "0",
        }
        with patch.dict(os.environ, env, clear=False):
            providers = build_market_intel_providers(enable_live=True)

        self.assertEqual([provider.provider_name for provider in providers], ["tradingview", "cailianpress"])
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_market_intel_providers.MarketIntelSourceRegistryTests -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'market_intel.providers.source_registry'`.

- [ ] **Step 3: Implement the source registry**

Create `stock_screener/market_intel/providers/source_registry.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Dict, List


@dataclass(frozen=True)
class SourceConfig:
    provider: str
    source: str
    reliability_tier: str
    markets: List[str]
    item_types: List[str]
    freshness_minutes: int
    requires_auth: bool = False
    requires_browser: bool = False
    default_enabled: bool = True
    automated_safe: bool = True
    env_flag: str = ""
    disabled_reason: str = ""
    aliases: List[str] = field(default_factory=list)

    def enabled(self) -> bool:
        if self.requires_browser and not _env_flag("MARKET_INTEL_BROWSER_PROVIDERS_ALLOWED", False):
            return False
        return _env_flag(self.env_flag, self.default_enabled)

    def to_dict(self) -> Dict[str, object]:
        enabled = self.enabled()
        disabled_reason = ""
        if not enabled:
            disabled_reason = self.disabled_reason
            if self.requires_browser and not _env_flag("MARKET_INTEL_BROWSER_PROVIDERS_ALLOWED", False):
                disabled_reason = "requires browser/cookie support"
            elif not disabled_reason:
                disabled_reason = "disabled by configuration"
        return {
            "provider": self.provider,
            "source": self.source,
            "reliability_tier": self.reliability_tier,
            "markets": list(self.markets),
            "item_types": list(self.item_types),
            "freshness_minutes": self.freshness_minutes,
            "requires_auth": self.requires_auth,
            "requires_browser": self.requires_browser,
            "default_enabled": self.default_enabled,
            "automated_safe": self.automated_safe,
            "enabled": enabled,
            "status": "enabled" if enabled else "disabled",
            "disabled_reason": disabled_reason,
        }


SOURCE_CONFIGS: List[SourceConfig] = [
    SourceConfig(
        provider="cailianpress",
        source="财联社",
        reliability_tier="high",
        markets=["A", "HK", "US"],
        item_types=["market_news", "news"],
        freshness_minutes=15,
        env_flag="MARKET_INTEL_ENABLE_CAILIANPRESS",
        aliases=["news", "cls"],
    ),
    SourceConfig(
        provider="sina",
        source="新浪财经",
        reliability_tier="medium",
        markets=["A", "HK", "US"],
        item_types=["market_news"],
        freshness_minutes=15,
        env_flag="MARKET_INTEL_ENABLE_SINA",
    ),
    SourceConfig(
        provider="tradingview",
        source="TradingView",
        reliability_tier="medium",
        markets=["A", "HK", "US"],
        item_types=["market_news", "news"],
        freshness_minutes=30,
        env_flag="MARKET_INTEL_ENABLE_TRADINGVIEW",
    ),
    SourceConfig(
        provider="eastmoney",
        source="东方财富",
        reliability_tier="high",
        markets=["A", "HK", "US"],
        item_types=["financial", "announcement", "research_report"],
        freshness_minutes=720,
        env_flag="MARKET_INTEL_ENABLE_EASTMONEY",
    ),
    SourceConfig(
        provider="global_index",
        source="东方财富指数",
        reliability_tier="medium",
        markets=["A", "HK", "US", "global"],
        item_types=["index_snapshot"],
        freshness_minutes=15,
        env_flag="MARKET_INTEL_ENABLE_GLOBAL_INDEX",
    ),
    SourceConfig(
        provider="iwencai",
        source="问财",
        reliability_tier="medium",
        markets=["A"],
        item_types=["news", "research_report", "search_document"],
        freshness_minutes=60,
        requires_auth=True,
        default_enabled=False,
        env_flag="MARKET_INTEL_ENABLE_IWENCAI",
        disabled_reason="request contract not verified",
    ),
    SourceConfig(
        provider="eastmoney_search",
        source="东方财富妙想",
        reliability_tier="medium",
        markets=["A", "HK", "US"],
        item_types=["news", "announcement", "research_report", "search_document"],
        freshness_minutes=60,
        requires_auth=True,
        default_enabled=False,
        env_flag="MARKET_INTEL_ENABLE_EASTMONEY_SEARCH",
        disabled_reason="request contract not verified",
    ),
    SourceConfig(
        provider="xueqiu",
        source="雪球",
        reliability_tier="low",
        markets=["A", "HK", "US"],
        item_types=["hot_stock", "hot_event", "news"],
        freshness_minutes=30,
        requires_auth=True,
        requires_browser=True,
        default_enabled=False,
        automated_safe=False,
        env_flag="MARKET_INTEL_ENABLE_XUEQIU",
        disabled_reason="requires browser/cookie support",
    ),
]


def list_source_configs() -> List[SourceConfig]:
    return list(SOURCE_CONFIGS)


def source_status_payload() -> List[Dict[str, object]]:
    return [item.to_dict() for item in SOURCE_CONFIGS]


def enabled_provider_order() -> List[str]:
    configured = os.getenv("MARKET_INTEL_PROVIDER_ORDER", "").strip()
    names = configured.split(",") if configured else [
        "cailianpress",
        "sina",
        "tradingview",
        "eastmoney",
        "global_index",
    ]
    return _dedupe_provider_names(names)


def config_by_provider() -> Dict[str, SourceConfig]:
    rows: Dict[str, SourceConfig] = {}
    for config in SOURCE_CONFIGS:
        rows[config.provider] = config
        for alias in config.aliases:
            rows[alias] = config
    return rows


def _dedupe_provider_names(raw_names) -> List[str]:
    configs = config_by_provider()
    names: List[str] = []
    seen = set()
    for raw_name in raw_names:
        key = str(raw_name).strip().lower()
        config = configs.get(key)
        if config is None or config.provider in seen:
            continue
        names.append(config.provider)
        seen.add(config.provider)
    return names


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}
```

- [ ] **Step 4: Wire factory through the registry**

Replace `stock_screener/market_intel/providers/factory.py` with:

```python
from __future__ import annotations

import os
from typing import List

from market_intel.providers.base import MarketIntelProvider
from market_intel.providers.cailianpress import CailianpressIntelProvider
from market_intel.providers.eastmoney import EastmoneyMarketIntelProvider
from market_intel.providers.global_index import GlobalIndexProvider
from market_intel.providers.sina import SinaNewsIntelProvider
from market_intel.providers.source_registry import config_by_provider, enabled_provider_order
from market_intel.providers.tradingview import TradingViewNewsIntelProvider


def build_market_intel_providers(enable_live: bool | None = None) -> List[MarketIntelProvider]:
    if enable_live is None:
        enable_live = _env_flag("MARKET_INTEL_ENABLE_LIVE_PROVIDERS")
    if not enable_live:
        return []

    timeout_sec = _env_float("MARKET_INTEL_PROVIDER_TIMEOUT_SEC", 5.0)
    detail_limit = _env_int("MARKET_INTEL_TRADINGVIEW_DETAIL_LIMIT", 5)
    configs = config_by_provider()
    providers: List[MarketIntelProvider] = []
    for provider_name in enabled_provider_order():
        config = configs.get(provider_name)
        if config is None or not config.enabled():
            continue
        provider = _build_provider(provider_name, timeout_sec=timeout_sec, detail_limit=detail_limit)
        if provider is not None and provider.is_available:
            providers.append(provider)
    return providers


def _build_provider(provider_name: str, *, timeout_sec: float, detail_limit: int):
    if provider_name == "cailianpress":
        return CailianpressIntelProvider(timeout_sec=timeout_sec)
    if provider_name == "sina":
        return SinaNewsIntelProvider(timeout_sec=timeout_sec)
    if provider_name == "tradingview":
        return TradingViewNewsIntelProvider(timeout_sec=timeout_sec, detail_limit=detail_limit)
    if provider_name == "eastmoney":
        return EastmoneyMarketIntelProvider(timeout_sec=timeout_sec)
    if provider_name == "global_index":
        return GlobalIndexProvider(timeout_sec=timeout_sec)
    return None


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default
```

- [ ] **Step 5: Run tests to verify current missing provider failures**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_market_intel_providers.MarketIntelSourceRegistryTests -v
```

Expected: FAIL with missing `market_intel.providers.cailianpress`, `sina`, or `tradingview`.

- [ ] **Step 6: Commit registry and factory scaffold after Task 2 and Task 3 make tests pass**

Do not commit at the end of Task 1 if imports still fail. Commit these files together after Task 3:

```bash
git add stock_screener/market_intel/providers/source_registry.py stock_screener/market_intel/providers/factory.py stock_screener/tests/test_market_intel_providers.py
git commit -m "feat: add market intel source registry"
```

## Task 2: Cailianpress Provider

**Files:**
- Create: `stock_screener/market_intel/providers/cailianpress.py`
- Modify: `stock_screener/market_intel/providers/news.py`
- Modify: `stock_screener/tests/test_market_intel_providers.py`

- [ ] **Step 1: Write failing Cailianpress tests**

Append to `MarketIntelProviderTests` in `stock_screener/tests/test_market_intel_providers.py`:

```python
    def test_cailianpress_fetch_market_accepts_nested_roll_data(self):
        from market_intel.providers.cailianpress import CailianpressIntelProvider

        session = FakeSession([
            {
                "data": {
                    "roll_data": [
                        {
                            "title": "政策利好落地",
                            "content": "科技板块走强",
                            "ctime": 1779685200000,
                            "id": "cls-nested-1",
                        }
                    ]
                }
            }
        ])
        provider = CailianpressIntelProvider(session=session)

        items = provider.fetch_market("A")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].provider, "cailianpress")
        self.assertEqual(items[0].source, "财联社")
        self.assertEqual(items[0].item_type, "market_news")
        self.assertEqual(items[0].title, "政策利好落地")

    def test_cailianpress_fetch_market_accepts_top_level_roll_data(self):
        from market_intel.providers.cailianpress import CailianpressIntelProvider

        session = FakeSession([
            {
                "roll_data": [
                    {
                        "title": "市场快讯",
                        "content": "指数集体走强",
                        "ctime": 1779685200000,
                        "id": "cls-top-1",
                    }
                ]
            }
        ])
        provider = CailianpressIntelProvider(session=session)

        items = provider.fetch_market("A")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].dedupe_key, "cailianpress:market_news:cls-top-1")

    def test_cailianpress_html_parser_extracts_telegraph_items(self):
        from market_intel.providers.cailianpress import parse_cailianpress_html

        html = """
        <html><body>
          <div class="telegraph-content-box">
            <span class="telegraph-time">09:31</span>
            <div class="telegraph-content">半导体板块快速拉升</div>
          </div>
        </body></html>
        """

        rows = parse_cailianpress_html(html)

        self.assertEqual(rows, [{"title": "半导体板块快速拉升", "content": "半导体板块快速拉升", "time": "09:31"}])

    def test_legacy_news_provider_alias_still_works(self):
        from market_intel.providers.news import NewsIntelProvider

        self.assertEqual(NewsIntelProvider.provider_name, "cailianpress")
```

- [ ] **Step 2: Run Cailianpress tests to verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_market_intel_providers.MarketIntelProviderTests -v
```

Expected: FAIL with missing `CailianpressIntelProvider` or parser helpers.

- [ ] **Step 3: Implement Cailianpress provider**

Create `stock_screener/market_intel/providers/cailianpress.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Dict, Iterable, List

from market_intel.models import IntelItem
from market_intel.providers.base import MarketIntelProvider, ttl_for_item_type

try:
    import requests
except ImportError:
    requests = None


class CailianpressIntelProvider(MarketIntelProvider):
    provider_name = "cailianpress"
    name = "cailianpress"

    def __init__(self, session: Any = None, timeout_sec: float = 5.0):
        self.session = session
        if self.session is None and requests is not None:
            self.session = requests.Session()
        self.timeout_sec = timeout_sec

    @property
    def is_available(self) -> bool:
        return self.session is not None

    def fetch_stock(self, market: str, code: str) -> List[IntelItem]:
        return []

    def fetch_market(self, market: str) -> List[IntelItem]:
        if not self.is_available:
            return []
        response = self.session.get(
            "https://www.cls.cn/nodeapi/telegraphList",
            params={"app": "CailianpressWeb"},
            headers={"Referer": "https://www.cls.cn/"},
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        payload = response.json()
        rows = _extract_roll_data(payload)
        return [self._item(market, row) for row in rows if _title(row)]

    def _item(self, market: str, row: Dict[str, Any]) -> IntelItem:
        fetched_at = datetime.now(timezone.utc)
        title = _title(row)
        published_at = _unix_datetime(row.get("ctime") or row.get("time"))
        source_id = row.get("id") or row.get("telegraph_id") or published_at or title
        return IntelItem(
            scope_type="market",
            market=market,
            code="",
            source="财联社",
            provider=self.provider_name,
            item_type="market_news",
            title=title,
            summary=str(row.get("content") or row.get("brief") or "").strip(),
            published_at=published_at,
            raw_json=dict(row),
            fetched_at=fetched_at,
            expires_at=fetched_at + ttl_for_item_type("market_news"),
            dedupe_key=f"{self.provider_name}:market_news:{source_id}",
        )


def _extract_roll_data(payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("roll_data")
    if isinstance(rows, list):
        return _dict_rows(rows)
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("roll_data"), list):
        return _dict_rows(data.get("roll_data"))
    return []


def parse_cailianpress_html(html: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for block in re.findall(r'<div[^>]*class="[^"]*telegraph-content-box[^"]*"[^>]*>(.*?)</div>\s*</div>', html, flags=re.S):
        time_text = _strip_html(_first_match(block, r'class="[^"]*telegraph-time[^"]*"[^>]*>(.*?)<'))
        content = _strip_html(_first_match(block, r'class="[^"]*telegraph-content[^"]*"[^>]*>(.*?)</div>'))
        if content:
            rows.append({"title": content, "content": content, "time": time_text})
    if rows:
        return rows
    for content in re.findall(r'class="[^"]*telegraph-content[^"]*"[^>]*>(.*?)</div>', html, flags=re.S):
        text = _strip_html(content)
        if text:
            rows.append({"title": text, "content": text, "time": ""})
    return rows


def _dict_rows(rows: Any) -> List[Dict[str, Any]]:
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _title(row: Dict[str, Any]) -> str:
    return str(row.get("title") or row.get("content") or "").strip()


def _unix_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp > 10_000_000_000:
        timestamp = timestamp / 1000
    return datetime.fromtimestamp(timestamp, timezone.utc)


def _first_match(text: str, pattern: str) -> str:
    match = re.search(pattern, text, flags=re.S)
    return match.group(1) if match else ""


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text or "")).strip()
```

- [ ] **Step 4: Replace old news provider with compatibility alias**

Replace `stock_screener/market_intel/providers/news.py` with:

```python
from __future__ import annotations

from market_intel.providers.cailianpress import CailianpressIntelProvider


class NewsIntelProvider(CailianpressIntelProvider):
    """Backward-compatible alias for the original generic news provider."""
```

- [ ] **Step 5: Run provider tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_market_intel_providers.MarketIntelProviderTests -v
```

Expected: PASS for existing Eastmoney/global-index tests and new Cailianpress tests.

## Task 3: Sina and TradingView Providers

**Files:**
- Create: `stock_screener/market_intel/providers/sina.py`
- Create: `stock_screener/market_intel/providers/tradingview.py`
- Modify: `stock_screener/tests/test_market_intel_providers.py`

- [ ] **Step 1: Write failing Sina and TradingView tests**

Append to `MarketIntelProviderTests` in `stock_screener/tests/test_market_intel_providers.py`:

```python
    def test_sina_jsonp_parser_returns_market_news_rows(self):
        from market_intel.providers.sina import parse_sina_live_feed

        text = 'callback({"result":{"data":{"feed":{"list":[{"title":"美联储释放信号","content":"全球市场波动","created_at":"2026-05-25 09:32:00","url":"https://finance.sina.com.cn/news"}]}}}})'

        rows = parse_sina_live_feed(text)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "美联储释放信号")
        self.assertEqual(rows[0]["content"], "全球市场波动")

    def test_sina_provider_fetches_market_news(self):
        from market_intel.providers.sina import SinaNewsIntelProvider

        session = FakeSession([
            'callback({"result":{"data":{"feed":{"list":[{"title":"盘中快讯","content":"港股科技走强","created_at":"2026-05-25 09:32:00"}]}}}})'
        ])
        provider = SinaNewsIntelProvider(session=session)

        items = provider.fetch_market("HK")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].provider, "sina")
        self.assertEqual(items[0].source, "新浪财经")
        self.assertEqual(items[0].item_type, "market_news")

    def test_tradingview_provider_fetches_list_and_bounded_details(self):
        from market_intel.providers.tradingview import TradingViewNewsIntelProvider

        session = FakeSession([
            {
                "items": [
                    {"id": "tv-1", "title": "AI chip stocks rise", "published": 1779685200},
                    {"id": "tv-2", "title": "Oil prices fall", "published": 1779685300},
                ]
            },
            {"story": {"body": "Detailed AI chip context", "link": "https://www.tradingview.com/news/tv-1"}},
        ])
        provider = TradingViewNewsIntelProvider(session=session, detail_limit=1)

        items = provider.fetch_market("US")

        self.assertEqual(len(items), 2)
        self.assertEqual(session.calls[0][0], "https://news-mediator.tradingview.com/news-flow/v2/news")
        self.assertEqual(session.calls[1][0], "https://news-headlines.tradingview.com/v3/story")
        self.assertEqual(items[0].provider, "tradingview")
        self.assertIn("Detailed AI chip context", items[0].summary)
```

The existing `FakeSession.get` returns `FakeResponse`; update `FakeResponse` to support text payloads:

```python
class FakeResponse:
    def __init__(self, payload, status_error=None):
        self.payload = payload
        self.status_error = status_error
        self.text = payload if isinstance(payload, str) else ""
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_market_intel_providers.MarketIntelProviderTests -v
```

Expected: FAIL with missing `sina.py` or `tradingview.py`.

- [ ] **Step 3: Implement Sina provider**

Create `stock_screener/market_intel/providers/sina.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import time
from typing import Any, Dict, List

from market_intel.models import IntelItem
from market_intel.providers.base import MarketIntelProvider, ttl_for_item_type

try:
    import requests
except ImportError:
    requests = None


class SinaNewsIntelProvider(MarketIntelProvider):
    provider_name = "sina"
    name = "sina"

    def __init__(self, session: Any = None, timeout_sec: float = 5.0):
        self.session = session
        if self.session is None and requests is not None:
            self.session = requests.Session()
        self.timeout_sec = timeout_sec

    @property
    def is_available(self) -> bool:
        return self.session is not None

    def fetch_stock(self, market: str, code: str) -> List[IntelItem]:
        return []

    def fetch_market(self, market: str) -> List[IntelItem]:
        if not self.is_available:
            return []
        response = self.session.get(
            "https://zhibo.sina.com.cn/api/zhibo/feed",
            params={
                "callback": "callback",
                "page": 1,
                "page_size": 20,
                "zhibo_id": 152,
                "tag_id": 0,
                "dire": "f",
                "dpc": 1,
                "pagesize": 20,
                "type": 0,
                "_": int(time.time()),
            },
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        return [self._item(market, row) for row in parse_sina_live_feed(response.text)]

    def _item(self, market: str, row: Dict[str, Any]) -> IntelItem:
        fetched_at = datetime.now(timezone.utc)
        published_at = _parse_datetime(row.get("created_at") or row.get("time"))
        title = str(row.get("title") or row.get("content") or "").strip()
        return IntelItem(
            scope_type="market",
            market=market,
            code="",
            source="新浪财经",
            provider=self.provider_name,
            item_type="market_news",
            title=title,
            summary=str(row.get("content") or "").strip(),
            url=str(row.get("url") or "").strip(),
            published_at=published_at,
            raw_json=dict(row),
            fetched_at=fetched_at,
            expires_at=fetched_at + ttl_for_item_type("market_news"),
            dedupe_key=f"{self.provider_name}:market_news:{row.get('id') or row.get('url') or published_at or title}",
        )


def parse_sina_live_feed(text: str) -> List[Dict[str, Any]]:
    payload = _parse_jsonp(text)
    result = payload.get("result") if isinstance(payload, dict) else {}
    data = result.get("data") if isinstance(result, dict) else {}
    feed = data.get("feed") if isinstance(data, dict) else {}
    rows = feed.get("list") if isinstance(feed, dict) else []
    return [row for row in rows if isinstance(row, dict) and str(row.get("title") or row.get("content") or "").strip()]


def _parse_jsonp(text: str) -> Dict[str, Any]:
    match = re.match(r"^[^(]*\((.*)\)\s*;?\s*$", text or "", flags=re.S)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
```

- [ ] **Step 4: Implement TradingView provider**

Create `stock_screener/market_intel/providers/tradingview.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List
from urllib.parse import quote

from market_intel.models import IntelItem
from market_intel.providers.base import MarketIntelProvider, ttl_for_item_type

try:
    import requests
except ImportError:
    requests = None


class TradingViewNewsIntelProvider(MarketIntelProvider):
    provider_name = "tradingview"
    name = "tradingview"

    def __init__(self, session: Any = None, timeout_sec: float = 5.0, detail_limit: int = 5):
        self.session = session
        if self.session is None and requests is not None:
            self.session = requests.Session()
        self.timeout_sec = timeout_sec
        self.detail_limit = max(0, int(detail_limit))

    @property
    def is_available(self) -> bool:
        return self.session is not None

    def fetch_stock(self, market: str, code: str) -> List[IntelItem]:
        return []

    def fetch_market(self, market: str) -> List[IntelItem]:
        if not self.is_available:
            return []
        response = self.session.get(
            "https://news-mediator.tradingview.com/news-flow/v2/news",
            params={"filter": "lang:zh-Hans", "client": "screener", "streaming": "false"},
            headers={"Host": "news-mediator.tradingview.com"},
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        rows = parse_tradingview_news_list(response.json())
        details = self._fetch_details([row for row in rows if row.get("id")][:self.detail_limit])
        return [self._item(market, row, details.get(str(row.get("id") or ""))) for row in rows]

    def _fetch_details(self, rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        details: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            story_id = str(row.get("id") or "")
            if not story_id:
                continue
            response = self.session.get(
                "https://news-headlines.tradingview.com/v3/story",
                params={"id": story_id, "lang": "zh-Hans"},
                headers={"Host": "news-headlines.tradingview.com"},
                timeout=self.timeout_sec,
            )
            response.raise_for_status()
            payload = response.json()
            details[story_id] = payload if isinstance(payload, dict) else {}
        return details

    def _item(self, market: str, row: Dict[str, Any], detail: Dict[str, Any] | None) -> IntelItem:
        fetched_at = datetime.now(timezone.utc)
        story_id = str(row.get("id") or "")
        story = (detail or {}).get("story") if isinstance(detail, dict) else {}
        summary = str(
            row.get("summary")
            or row.get("description")
            or (story or {}).get("body")
            or ""
        ).strip()
        title = str(row.get("title") or (story or {}).get("title") or story_id).strip()
        url = str(row.get("url") or (story or {}).get("link") or "").strip()
        published_at = _parse_timestamp(row.get("published") or row.get("published_at"))
        return IntelItem(
            scope_type="market",
            market=market,
            code="",
            source="TradingView",
            provider=self.provider_name,
            item_type="market_news",
            title=title,
            summary=summary,
            url=url,
            published_at=published_at,
            raw_json={"row": dict(row), "detail": dict(detail or {})},
            fetched_at=fetched_at,
            expires_at=fetched_at + ttl_for_item_type("market_news"),
            dedupe_key=f"{self.provider_name}:market_news:{story_id or quote(title)}",
        )


def parse_tradingview_news_list(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("items", "data", "news"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp > 10_000_000_000:
        timestamp = timestamp / 1000
    return datetime.fromtimestamp(timestamp, timezone.utc)
```

- [ ] **Step 5: Run provider and registry tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_market_intel_providers -v
```

Expected: PASS.

- [ ] **Step 6: Commit source registry and stable HTTP providers**

Run:

```bash
git add stock_screener/market_intel/providers/source_registry.py \
  stock_screener/market_intel/providers/factory.py \
  stock_screener/market_intel/providers/cailianpress.py \
  stock_screener/market_intel/providers/news.py \
  stock_screener/market_intel/providers/sina.py \
  stock_screener/market_intel/providers/tradingview.py \
  stock_screener/tests/test_market_intel_providers.py
git commit -m "feat: expand market intel news providers"
```

## Task 4: Evidence Pack Dedupe and Ranking

**Files:**
- Modify: `stock_screener/market_intel/evidence.py`
- Modify: `stock_screener/tests/test_market_intel_evidence.py`

- [ ] **Step 1: Write failing evidence ranking tests**

Append to `MarketIntelEvidenceTests` in `stock_screener/tests/test_market_intel_evidence.py`:

```python
    def test_evidence_pack_dedupes_by_url_and_prefers_high_reliability_source(self):
        cailian = make_item(
            scope_type="market",
            code="",
            source="财联社",
            provider="cailianpress",
            title="AI算力板块走强",
            summary="财联社快讯",
            url="https://example.com/same-event",
            dedupe_key="cls-1",
        )
        search = SearchDocument(
            title="AI算力板块走强",
            url="https://example.com/same-event",
            content="搜索重复结果",
            query="AI 算力",
        )

        pack = EvidencePackBuilder().build(
            market="A",
            market_bundle=MarketIntelBundle(market="A", items=[cailian], freshness_status="fresh").to_dict(),
            search_documents=[search],
        )
        payload = pack.to_dict()

        self.assertEqual([item["provider"] for item in payload["structured_items"]], ["cailianpress"])
        self.assertEqual(payload["search_documents"], [])
        self.assertEqual(payload["structured_items"][0]["title"], "AI算力板块走强")

    def test_evidence_pack_ranks_manual_then_official_then_news_then_search(self):
        manual = make_item(source="manual", provider="manual", title="人工重点", dedupe_key="manual")
        announcement = make_item(source="东方财富", provider="eastmoney", item_type="announcement", title="公司公告", dedupe_key="ann")
        sina = make_item(source="新浪财经", provider="sina", item_type="market_news", title="新浪快讯", dedupe_key="sina")
        search = SearchDocument(title="搜索补充", url="https://example.com/search-only", content="搜索内容", query="query")

        pack = EvidencePackBuilder().build(
            market="A",
            stock_bundle=StockIntelBundle(market="A", code="600519", items=[sina, announcement], freshness_status="fresh").to_dict(),
            manual_items=[manual],
            search_documents=[search],
        )
        payload = pack.to_dict()

        self.assertEqual([item["title"] for item in payload["manual_items"]], ["人工重点"])
        self.assertEqual([item["title"] for item in payload["structured_items"]], ["公司公告", "新浪快讯"])
        self.assertEqual([item["title"] for item in payload["search_documents"]], ["搜索补充"])
```

- [ ] **Step 2: Run evidence tests to verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_market_intel_evidence.MarketIntelEvidenceTests -v
```

Expected: FAIL because duplicate search documents are still retained or ranking is unchanged.

- [ ] **Step 3: Add source-aware ranking helpers**

In `stock_screener/market_intel/evidence.py`, add these helper functions near `_dedupe_strings`:

```python
def _canonical_url(value: str) -> str:
    text = str(value or "").strip()
    if text.endswith("/"):
        text = text[:-1]
    return text


def _canonical_title(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _evidence_key(item: IntelItem) -> tuple:
    url = _canonical_url(item.url)
    if url:
        return ("url", url)
    title = _canonical_title(item.title)
    published = item.published_at.date().isoformat() if item.published_at else ""
    return ("title", title, published or item.source)


def _rank_item(item: IntelItem) -> tuple:
    provider_rank = {
        "manual": 0,
        "eastmoney": 1,
        "cailianpress": 2,
        "sina": 3,
        "tradingview": 4,
        "global_index": 5,
        "signal_analysis": 8,
    }.get(item.provider, 6)
    type_rank = {
        "announcement": 0,
        "financial": 1,
        "research_report": 2,
        "market_news": 3,
        "news": 4,
        "search_document": 8,
    }.get(item.item_type, 6)
    published = item.published_at or item.fetched_at
    timestamp = published.timestamp() if published else 0
    return (provider_rank, type_rank, -timestamp, item.title)


def _rank_and_dedupe_items(items: Iterable[IntelItem]) -> List[IntelItem]:
    rows = sorted(list(items), key=_rank_item)
    result: List[IntelItem] = []
    seen = set()
    for item in rows:
        key = _evidence_key(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
```

- [ ] **Step 4: Apply ranking inside `EvidencePackBuilder.build`**

In `EvidencePackBuilder.build`, replace:

```python
        return EvidencePack(
            market=market,
            code=code,
            structured_items=[*stock_items, *market_items],
            search_documents=search_intel_items,
            manual_items=manual_intel_items,
            stock_context={"items": [item.to_dict() for item in stock_items]},
            market_context={"items": [item.to_dict() for item in market_items]},
            source_status=merged_source_status,
            data_gaps=merged_data_gaps,
            citations=merged_citations,
        )
```

with:

```python
        manual_intel_items = _rank_and_dedupe_items(manual_intel_items)
        structured_items = _rank_and_dedupe_items([*stock_items, *market_items])
        structured_keys = {_evidence_key(item) for item in [*manual_intel_items, *structured_items]}
        search_intel_items = _rank_and_dedupe_items([
            item for item in search_intel_items
            if _evidence_key(item) not in structured_keys
        ])

        return EvidencePack(
            market=market,
            code=code,
            structured_items=structured_items,
            search_documents=search_intel_items,
            manual_items=manual_intel_items,
            stock_context={"items": [item.to_dict() for item in _rank_and_dedupe_items(stock_items)]},
            market_context={"items": [item.to_dict() for item in _rank_and_dedupe_items(market_items)]},
            source_status=merged_source_status,
            data_gaps=merged_data_gaps,
            citations=merged_citations,
        )
```

- [ ] **Step 5: Run evidence tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_market_intel_evidence -v
```

Expected: PASS.

- [ ] **Step 6: Commit evidence ranking**

Run:

```bash
git add stock_screener/market_intel/evidence.py stock_screener/tests/test_market_intel_evidence.py
git commit -m "feat: rank market intel evidence sources"
```

## Task 5: Source Metadata API and Frontend Display

**Files:**
- Modify: `stock_screener/web/market_intel.py`
- Modify: `stock_screener/tests/test_market_intel_api.py`
- Modify: `stock_screener/web_frontend/src/features/marketIntel/api.ts`
- Modify: `stock_screener/web_frontend/src/features/marketIntel/types.ts`
- Modify: `stock_screener/web_frontend/src/features/marketIntel/MarketIntelPage.tsx`
- Modify: `stock_screener/tests/test_market_intel_frontend.py`

- [ ] **Step 1: Write failing API test**

Append to `MarketIntelApiTest` in `stock_screener/tests/test_market_intel_api.py`:

```python
    def test_sources_endpoint_returns_registry_rows(self):
        response = self.client.get("/api/market-intel/sources")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("sources", payload)
        providers = {item["provider"]: item for item in payload["sources"]}
        self.assertEqual(providers["cailianpress"]["source"], "财联社")
        self.assertEqual(providers["xueqiu"]["status"], "disabled")
        self.assertTrue(providers["xueqiu"]["requires_browser"])
```

- [ ] **Step 2: Run API test to verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_market_intel_api.MarketIntelApiTest.test_sources_endpoint_returns_registry_rows -v
```

Expected: FAIL with 404 or missing route.

- [ ] **Step 3: Add source metadata route**

In `stock_screener/web/market_intel.py`, add this import:

```python
from market_intel.providers.source_registry import source_status_payload
```

Add this route before `/stocks/{market}/{code}`:

```python
@router.get("/sources")
def list_sources(user: CurrentUser = Depends(require_user)) -> Dict[str, Any]:
    _ = user
    return {"sources": source_status_payload()}
```

- [ ] **Step 4: Run API tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_market_intel_api -v
```

Expected: PASS.

- [ ] **Step 5: Write failing frontend contract test**

Append to `stock_screener/tests/test_market_intel_frontend.py`:

```python
    def test_market_intel_page_renders_source_registry_health(self):
        page = (ROOT / "web_frontend" / "src" / "features" / "marketIntel" / "MarketIntelPage.tsx").read_text(encoding="utf-8")
        api = (ROOT / "web_frontend" / "src" / "features" / "marketIntel" / "api.ts").read_text(encoding="utf-8")
        types = (ROOT / "web_frontend" / "src" / "features" / "marketIntel" / "types.ts").read_text(encoding="utf-8")

        self.assertIn("getMarketIntelSources", api)
        self.assertIn("/api/market-intel/sources", api)
        self.assertIn("MarketIntelSource", types)
        self.assertIn("sourceRegistry", page)
        self.assertIn("来源覆盖", page)
        self.assertIn("disabled_reason", page)
```

- [ ] **Step 6: Run frontend contract test to verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_market_intel_frontend -v
```

Expected: FAIL because frontend source registry code is missing.

- [ ] **Step 7: Add frontend types and API helper**

In `stock_screener/web_frontend/src/features/marketIntel/types.ts`, add:

```ts
export type MarketIntelSource = {
  provider: string
  source: string
  reliability_tier: string
  markets: string[]
  item_types: string[]
  freshness_minutes: number
  requires_auth: boolean
  requires_browser: boolean
  default_enabled: boolean
  automated_safe: boolean
  enabled: boolean
  status: string
  disabled_reason?: string
}

export type MarketIntelSourcesResponse = {
  sources: MarketIntelSource[]
}
```

In `stock_screener/web_frontend/src/features/marketIntel/api.ts`, update the import:

```ts
import type { EvidencePackPreview, IntelBundle, MarketCode, MarketIntelSourcesResponse, ProviderRunsResponse } from './types'
```

Add:

```ts
export function getMarketIntelSources() {
  return api<MarketIntelSourcesResponse>('/api/market-intel/sources')
}
```

- [ ] **Step 8: Render source registry in Market Intel page**

In `stock_screener/web_frontend/src/features/marketIntel/MarketIntelPage.tsx`, update imports:

```ts
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { getMarketDigest, getMarketIntelSources, getProviderRuns, getStockIntel, previewEvidencePack } from './api'
import type { EvidencePackPreview, IntelBundle, IntelItem, MarketCode, MarketIntelSource, ProviderRun, SourceStatus } from './types'
```

Extend `MarketIntelState`:

```ts
type MarketIntelState = {
  stock: IntelBundle | null
  market: IntelBundle | null
  runs: ProviderRun[]
  pack: EvidencePackPreview | null
  sourceRegistry: MarketIntelSource[]
}
```

Update initial state:

```ts
  const [data, setData] = useState<MarketIntelState>({
    stock: null,
    market: null,
    runs: [],
    pack: null,
    sourceRegistry: []
  })
```

Add this effect after `sourceStatuses`:

```ts
  useEffect(() => {
    getMarketIntelSources()
      .then(result => setData(current => ({ ...current, sourceRegistry: result.sources || [] })))
      .catch(() => setData(current => ({ ...current, sourceRegistry: [] })))
  }, [])
```

Update `setData` inside `load`:

```ts
      setData(current => ({
        ...current,
        stock,
        market: marketDigest,
        runs: providerRuns.runs || [],
        pack
      }))
```

Add a panel before `来源状态`:

```tsx
          <Panel title="来源覆盖">
            <SourceRegistryList rows={data.sourceRegistry} />
          </Panel>
```

Add this component near `SourceStatusList`:

```tsx
function SourceRegistryList({ rows }: { rows: MarketIntelSource[] }) {
  if (rows.length === 0) return <div className="empty">暂无来源配置</div>
  return (
    <div className="market-intel-source-list">
      {rows.map(row => (
        <article key={row.provider} className="market-intel-source">
          <div>
            <strong>{row.source || row.provider}</strong>
            <span>{row.provider} · {row.reliability_tier} · {row.item_types.join('/')}</span>
          </div>
          <span className={`status ${row.enabled ? 'success' : 'muted'}`}>{row.enabled ? '启用' : '停用'}</span>
          {!row.enabled && row.disabled_reason && <p>{row.disabled_reason}</p>}
        </article>
      ))}
    </div>
  )
}
```

- [ ] **Step 9: Run frontend tests and build**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_market_intel_frontend -v
npm --prefix stock_screener/web_frontend run build
```

Expected: unittest PASS and Vite build PASS. Existing Vite chunk-size warnings are acceptable.

- [ ] **Step 10: Commit API and frontend source health**

Run:

```bash
git add stock_screener/web/market_intel.py \
  stock_screener/tests/test_market_intel_api.py \
  stock_screener/web_frontend/src/features/marketIntel/api.ts \
  stock_screener/web_frontend/src/features/marketIntel/types.ts \
  stock_screener/web_frontend/src/features/marketIntel/MarketIntelPage.tsx \
  stock_screener/tests/test_market_intel_frontend.py
git commit -m "feat: expose market intel source health"
```

## Task 6: Env Docs, Regression, and Smoke

**Files:**
- Modify: `stock_screener/.env.example`
- Modify: `stock_screener/deploy/README.md`

- [ ] **Step 1: Write failing docs contract test**

Append to `stock_screener/tests/test_market_intel_providers.py`:

```python
class MarketIntelSourceDocsTests(unittest.TestCase):
    def test_env_example_documents_source_expansion_flags(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        text = (root / ".env.example").read_text(encoding="utf-8")

        self.assertIn("MARKET_INTEL_PROVIDER_ORDER=", text)
        self.assertIn("MARKET_INTEL_ENABLE_CAILIANPRESS=1", text)
        self.assertIn("MARKET_INTEL_ENABLE_SINA=1", text)
        self.assertIn("MARKET_INTEL_ENABLE_TRADINGVIEW=1", text)
        self.assertIn("MARKET_INTEL_ENABLE_XUEQIU=0", text)
        self.assertIn("MARKET_INTEL_BROWSER_PROVIDERS_ALLOWED=0", text)
```

- [ ] **Step 2: Run docs contract test to verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_market_intel_providers.MarketIntelSourceDocsTests -v
```

Expected: FAIL because `.env.example` does not document all flags yet.

- [ ] **Step 3: Update `.env.example`**

In `stock_screener/.env.example`, replace the Market Intel section with:

```bash
# Market Intel backend data layer.
# API/UI live providers are cache-first. Signal-analysis integration remains opt-in.
MARKET_INTEL_ENABLE_LIVE_PROVIDERS=1
MARKET_INTEL_PROVIDER_TIMEOUT_SEC=10
SIGNAL_ENABLE_MARKET_INTEL=0

# Market Intel source expansion.
# Stable HTTP providers are enabled by default; search-like and browser/cookie providers are opt-in.
MARKET_INTEL_PROVIDER_ORDER=cailianpress,sina,tradingview,eastmoney,global_index
MARKET_INTEL_ENABLE_CAILIANPRESS=1
MARKET_INTEL_ENABLE_SINA=1
MARKET_INTEL_ENABLE_TRADINGVIEW=1
MARKET_INTEL_ENABLE_EASTMONEY=1
MARKET_INTEL_ENABLE_GLOBAL_INDEX=1
MARKET_INTEL_ENABLE_IWENCAI=0
MARKET_INTEL_ENABLE_EASTMONEY_SEARCH=0
MARKET_INTEL_ENABLE_XUEQIU=0
MARKET_INTEL_TRADINGVIEW_DETAIL_LIMIT=5
MARKET_INTEL_SEARCH_BATCH_SIZE=10
MARKET_INTEL_BROWSER_PROVIDERS_ALLOWED=0
```

- [ ] **Step 4: Update deployment README**

In `stock_screener/deploy/README.md`, append this section near the existing Market Intel section:

```markdown
### Market Intel source expansion

Stable HTTP providers are controlled by `MARKET_INTEL_PROVIDER_ORDER` and the
per-provider `MARKET_INTEL_ENABLE_*` flags. The default order is:

`cailianpress,sina,tradingview,eastmoney,global_index`

`SIGNAL_ENABLE_MARKET_INTEL=0` keeps richer provider data out of automated
signal analysis by default. Turning on live Market Intel for the API/UI does not
increase screening search or LLM calls unless `SIGNAL_ENABLE_MARKET_INTEL=1` is
also set.

Search-like providers (`iwencai`, `eastmoney_search`) and browser/cookie
providers (`xueqiu`) stay disabled until their contracts and operating limits
are verified. `MARKET_INTEL_BROWSER_PROVIDERS_ALLOWED=0` prevents browser-backed
providers from running in normal screening paths.
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=stock_screener python3 -m unittest \
  stock_screener.tests.test_market_intel_providers \
  stock_screener.tests.test_market_intel_evidence \
  stock_screener.tests.test_market_intel_api \
  stock_screener.tests.test_market_intel_frontend \
  -v
```

Expected: PASS.

- [ ] **Step 6: Run build**

Run:

```bash
npm --prefix stock_screener/web_frontend run build
```

Expected: PASS. Existing chunk-size warnings are acceptable.

- [ ] **Step 7: Optional local smoke with existing dev server**

If the local backend and frontend are already running, open the Market Intel page and verify:

- `/api/market-intel/sources` returns Cailianpress, Sina, TradingView, Eastmoney, global index, Iwencai, Eastmoney Search, and Xueqiu rows.
- The Market Intel page shows `来源覆盖`.
- Disabled providers show a reason.
- Querying one stock still returns grouped intelligence or a non-blocking empty state.

No extra browser/cookie provider should run when `MARKET_INTEL_BROWSER_PROVIDERS_ALLOWED=0`.

- [ ] **Step 8: Commit docs and final verification**

Run:

```bash
git add stock_screener/.env.example stock_screener/deploy/README.md stock_screener/tests/test_market_intel_providers.py
git commit -m "docs: document market intel source flags"
```

## Final Verification

Run:

```bash
git status --short
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=stock_screener python3 -m unittest \
  stock_screener.tests.test_market_intel_providers \
  stock_screener.tests.test_market_intel_evidence \
  stock_screener.tests.test_market_intel_api \
  stock_screener.tests.test_market_intel_frontend \
  -v
npm --prefix stock_screener/web_frontend run build
```

Expected:

- `git status --short` shows no staged files and only unrelated pre-existing local files, if any.
- All listed Python tests pass.
- Vite build passes.
- The feature does not modify `stock_terminal` behavior.
- Rule-chain pass/fail behavior is unchanged.

## Spec Coverage Self-Review

- Stable HTTP source expansion: covered by Tasks 2 and 3.
- Source registry and provider ordering: covered by Task 1.
- Disabled Iwencai/Eastmoney Search/Xueqiu visibility: covered by Tasks 1 and 5.
- Evidence Pack ranking/dedupe: covered by Task 4.
- Provider health API/UI: covered by Task 5.
- Env and deployment docs: covered by Task 6.
- Cost and batch discipline: covered by registry defaults, disabled search-like providers, docs, and final verification.

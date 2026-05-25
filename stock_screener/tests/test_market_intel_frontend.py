#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SRC = ROOT / "web_frontend" / "src"
MARKET_INTEL_PAGE = FRONTEND_SRC / "features" / "marketIntel" / "MarketIntelPage.tsx"
MARKET_INTEL_API = FRONTEND_SRC / "features" / "marketIntel" / "api.ts"
MARKET_INTEL_TYPES = FRONTEND_SRC / "features" / "marketIntel" / "types.ts"


class MarketIntelFrontendTest(unittest.TestCase):
    def test_market_intel_page_exports_feature_view(self):
        source = MARKET_INTEL_PAGE.read_text(encoding="utf-8")

        self.assertIn("市场情报", source)
        self.assertIn("export function MarketIntelPage", source)
        self.assertIn("load(false)", source)

    def test_market_intel_page_has_operational_sections(self):
        source = MARKET_INTEL_PAGE.read_text(encoding="utf-8")

        self.assertIn("来源状态", source)
        self.assertIn("公告", source)
        self.assertIn("研报", source)
        self.assertIn("资金面", source)

    def test_market_intel_api_uses_v1_endpoints(self):
        source = MARKET_INTEL_API.read_text(encoding="utf-8")

        self.assertIn("/api/market-intel/stocks", source)
        self.assertIn("/api/market-intel/markets", source)
        self.assertIn("/api/market-intel/evidence-pack/preview", source)

    def test_market_intel_page_renders_source_registry_health(self):
        page = MARKET_INTEL_PAGE.read_text(encoding="utf-8")
        api = MARKET_INTEL_API.read_text(encoding="utf-8")
        types = MARKET_INTEL_TYPES.read_text(encoding="utf-8")

        self.assertIn("getMarketIntelSources", api)
        self.assertIn("/api/market-intel/sources", api)
        self.assertIn("MarketIntelSource", types)
        self.assertIn("sourceRegistry", page)
        self.assertIn("来源覆盖", page)
        self.assertIn("disabled_reason", page)


if __name__ == "__main__":
    unittest.main()

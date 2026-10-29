from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

from potential_analysis import macro_timeseries as macro
from potential_analysis.models import MacroSnapshot


class CalculationsTest(unittest.TestCase):
    def test_verified_china_sample_reproduces_published_changes(self):
        sample = json.loads((Path(__file__).parent / "fixtures" / "macro_cn_20261016.json").read_text())
        for name in ("cpi", "ppi"):
            row = sample["inflation"][name]
            history = macro.inflation_history(
                pd.Series([row["previous_yoy"], row["yoy"]], index=["2026-08", "2026-09"]),
                pd.Series([row["previous_mom"], row["mom"]], index=["2026-08", "2026-09"]))
            self.assertEqual(history[-1]["yoy"], row["yoy"])
            self.assertEqual(history[-1]["mom"], row["mom"])
        self.assertAlmostEqual((sample["bonds"]["10y"]-sample["bonds"]["2y"])*100,
                               sample["bonds"]["spread_bp"])
    def test_growth_uses_calendar_months_and_never_fills_gaps(self):
        series = pd.Series([121, 100, 110, 0], index=["2024-03", "2023-03", "2024-01", "2023-01"])
        self.assertAlmostEqual(macro.index_growth(series, 12)["2024-03"], 21)
        self.assertNotIn("2024-03", macro.index_growth(series, 1))
        self.assertNotIn("2024-01", macro.index_growth(series, 12))

    def test_inflation_sorts_dates_and_changes_are_percentage_points(self):
        history = macro.inflation_history(pd.Series([2.5, 2.8], index=["2024年03月份", "2024年02月份"]),
                                           pd.Series([0, -.1], index=["2024-03", "2024-02"]))
        self.assertEqual(history[-1]["period"], "2024-03")
        self.assertEqual(history[-1]["yoy_change_pp"], -.3)
        self.assertEqual(history[-1]["mom_change_pp"], .1)

    def test_history_is_24_calendar_months_not_24_observations(self):
        series = pd.Series(range(40), index=pd.period_range("2021-01", periods=40, freq="M").astype(str))
        self.assertEqual(len(macro.inflation_history(series, series)), 24)

    def test_no_previous_inflation_when_previous_calendar_month_missing(self):
        rows = macro.inflation_history(pd.Series([1, 2], index=["2024-01", "2024-03"]), pd.Series(dtype=float))
        self.assertIsNone(rows[-1]["yoy_change_pp"])

    def test_bond_change_skips_nulls_and_uses_window_predecessor(self):
        series = pd.Series([4.25, None, 4.20], index=["2024-01-08", "2024-01-07", "2024-01-05"])
        rows = macro.bond_history(series, "2024-01-08")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["change_bp"], 5)
        self.assertEqual(rows[0]["previous_date"], "2024-01-05")


class CacheTest(unittest.TestCase):
    def test_concurrent_requests_share_one_load_and_return_copies(self):
        cache, calls = macro.MacroCache(), []
        def load():
            calls.append(1)
            time.sleep(.02)
            return {"status": "ok", "rows": [1]}
        with ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(lambda _: cache.get("CN", load), range(8)))
        values[0]["rows"].append(2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(cache.get("CN", load)["rows"], [1])

    def test_partial_and_failure_expire_in_60_seconds(self):
        now = [0]
        cache = macro.MacroCache(clock=lambda: now[0])
        calls = []
        def load():
            calls.append(1)
            return {"status": "partial"}
        cache.get("CN", load)
        now[0] = 59
        cache.get("CN", load)
        self.assertEqual(len(calls), 1)
        now[0] = 61
        cache.get("CN", load)
        self.assertEqual(len(calls), 2)

    def test_success_expires_after_six_hours(self):
        now, calls = [0], []
        cache = macro.MacroCache(clock=lambda: now[0])
        def load():
            calls.append(1)
            return {"status": "ok"}
        cache.get("CN", load)
        now[0] = 21599
        cache.get("CN", load)
        self.assertEqual(len(calls), 1)
        now[0] = 21601
        cache.get("CN", load)
        self.assertEqual(len(calls), 2)

    def test_exceptions_are_cached_briefly_then_retried(self):
        now, calls = [0], []
        cache = macro.MacroCache(clock=lambda: now[0])
        def load():
            calls.append(1)
            raise RuntimeError("offline")
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                cache.get("key", load)
        self.assertEqual(len(calls), 1)
        now[0] = 61
        with self.assertRaises(RuntimeError):
            cache.get("key", load)
        self.assertEqual(len(calls), 2)


class ProviderTest(unittest.TestCase):
    def setUp(self):
        self.cache_patch = patch.object(macro, "_cache", macro.MacroCache())
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)

    def test_china_exact_bond_fields_and_same_date_spread(self):
        today = date.today().isoformat()
        frame = pd.DataFrame({"日期": [today], "中国国债收益率2年": [1.2],
                              "中国国债收益率10年": [1.6], "中国国债收益率10年-2年": [999]})
        with patch("akshare.bond_zh_us_rate", return_value=frame) as read:
            payload = macro.get_macro_timeseries("bond_yields", "CN")
            macro.get_macro_timeseries("bond_yields", "CN")
        self.assertEqual(read.call_count, 1)
        self.assertEqual(payload["indicators"]["10y"]["latest"]["value"], 1.6)
        self.assertEqual(payload["indicators"]["10y_2y_spread"]["latest"]["value"], 40)

    def test_us_partial_failure_keeps_other_tenor(self):
        def load(series, key):
            if series == "DGS2":
                raise RuntimeError("offline")
            return pd.Series([4.2], index=[pd.Timestamp.now().normalize()])
        with patch.object(macro, "fred_series", side_effect=load):
            payload = macro.get_macro_timeseries("bond_yields", "US")
        self.assertEqual(payload["status"], "partial")
        self.assertIsNone(payload["indicators"]["2y"]["latest"])
        self.assertEqual(payload["indicators"]["10y"]["latest"]["value"], 4.2)

    def test_spread_never_aligns_different_observation_dates(self):
        now = pd.Timestamp.now().normalize()
        with patch.object(macro, "fred_series", side_effect=[pd.Series([4.2], index=[now]),
                                                            pd.Series([4.5], index=[now-pd.Timedelta(days=1)])]):
            payload = macro.get_macro_timeseries("bond_yields", "US")
        self.assertEqual(payload["indicators"]["10y_2y_spread"]["history"], [])

    def test_nbs_total_row_only_and_index_base_conversion(self):
        frame = pd.DataFrame([[100.4, 99.8], [105, 105]],
            index=["工业生产者出厂价格指数 (上月=100) ", "生产资料工业生产者出厂价格指数(上月=100)"],
            columns=["2024年03月", "2024年02月"])
        with patch("akshare.macro_china_nbs_nation", return_value=frame):
            import akshare as ak
            result = macro._cn_ppi_mom(ak)
        self.assertAlmostEqual(result.iloc[0], .4)

    def test_missing_key_does_not_fetch_china_or_other_sources(self):
        with patch.dict("os.environ", {"FRED_API_KEY": ""}), patch("akshare.bond_zh_us_rate") as china:
            result = macro.get_macro_timeseries("inflation", "US")
        china.assert_not_called()
        self.assertEqual(result["status"], "error")

    def test_us_factory_preserves_fred_values_and_never_calls_china(self):
        from potential_analysis.providers import build_default_macro_provider
        snap = MacroSnapshot("US", ten_year_yield=4.2)
        with patch("potential_analysis.providers.ChinaMacroProvider.fetch") as china, \
             patch("potential_analysis.fred_provider.FredMacroProvider.fetch", return_value=snap), \
             patch("potential_analysis.providers.GlobalMacroProvider.fetch", return_value=MacroSnapshot("US", ten_year_yield=99)):
            result = build_default_macro_provider("US")
        china.assert_not_called()
        self.assertEqual(result.ten_year_yield, 4.2)

    def test_us_inflation_uses_four_series_and_reuses_cached_reads(self):
        client = unittest.mock.Mock()
        index = pd.date_range(end=pd.Timestamp.now().replace(day=1) - pd.offsets.MonthBegin(1), periods=38, freq="MS")
        client.get_series.return_value = pd.Series(range(100, 138), index=index)
        with patch.dict("os.environ", {"FRED_API_KEY": "test-key"}), \
             patch.object(macro, "_fred_client", return_value=client):
            result = macro.get_macro_timeseries("inflation", "US")
            macro.fred_series("CPIAUCSL")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["indicators"]["cpi"]["history"]), 24)
        self.assertEqual(client.get_series.call_count, 4)
        self.assertEqual({c.args[0] for c in client.get_series.call_args_list},
                         {"CPIAUCNS", "CPIAUCSL", "PPIFID", "PPIFIS"})

    def test_snapshot_contains_details_but_not_histories(self):
        now = pd.Timestamp.now().normalize()
        periods = pd.period_range(end=now.to_period("M") - 1, periods=38, freq="M").astype(str)
        def series(name, key):
            return pd.Series([4, 4.2], index=[now-pd.Timedelta(days=1), now]) if name.startswith("DGS") else pd.Series(range(100, 138), index=periods)
        snap = MacroSnapshot("US")
        with patch.object(macro, "fred_series", side_effect=series):
            macro.enrich_macro_snapshot(snap, "US")
        self.assertIsNotNone(snap.cpi_yoy)
        self.assertIsNotNone(snap.ppi_mom)
        self.assertEqual(snap.ten_year_yield, 4.2)
        self.assertNotIn("history", snap.macro_details["cpi"])
        self.assertIn("macro_details", snap.to_dict())


class ApiTest(unittest.TestCase):
    def setUp(self):
        from web.macro import router
        from web.auth import require_user
        self.app = FastAPI()
        self.app.include_router(router)
        self.app.dependency_overrides[require_user] = lambda: object()
        self.client = TestClient(self.app)

    def test_country_required_and_strict(self):
        for query in ("", "?country=HK", "?country=cn"):
            self.assertEqual(self.client.get("/api/macro/inflation" + query).status_code, 422)

    def test_queries_only_country_data_without_stock_services(self):
        with patch("web.macro.get_macro_timeseries", return_value={"status": "partial"}) as fetch:
            self.assertEqual(self.client.get("/api/macro/inflation?country=CN").json()["status"], "partial")
            self.client.get("/api/macro/bond-yields?country=US")
        self.assertEqual(fetch.call_args_list[0].args, ("inflation", "CN"))
        self.assertEqual(fetch.call_args_list[1].args, ("bond_yields", "US"))

    def test_login_required(self):
        from fastapi import HTTPException
        from web.auth import require_user
        def deny():
            raise HTTPException(status_code=401)
        self.app.dependency_overrides[require_user] = deny
        for path in ("inflation", "bond-yields"):
            self.assertEqual(self.client.get(f"/api/macro/{path}?country=CN").status_code, 401)

    def test_real_query_needs_neither_stock_database_nor_analysis_service(self):
        frame = pd.DataFrame({"日期": [date.today()], "中国国债收益率2年": [1.2], "中国国债收益率10年": [1.6]})
        with patch.object(macro, "_cache", macro.MacroCache()), \
             patch("akshare.bond_zh_us_rate", return_value=frame) as read, \
             patch("db.MarketDatabase", side_effect=AssertionError("stock DB accessed")), \
             patch("potential_analysis.service.EnterprisePotentialService", side_effect=AssertionError("analysis invoked")):
            for _ in range(3):
                result = self.client.get("/api/macro/bond-yields?country=CN")
                self.assertEqual(result.json()["status"], "ok")
            self.assertEqual(read.call_count, 1)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""
集成测试：港股 / 美股 / A 股各一只标的，经 run_screening_task 跑通
K 线 fetcher → 筛选器链（PriceFilter）→ 策略器链（EMA、RSI 超卖/超买、放量超前三日），并写入 screening_results。

需要：可用 MySQL（库名见环境变量）、外网（yfinance / akshare 等拉 K 线）。
无 MySQL 时整组跳过。
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import date

import pytest
from api.screen_service import run_screening_task
from db import MarketDatabase, MySqlConfig


def _mysql_config() -> MySqlConfig:
    return MySqlConfig(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", "123456"),
        database=os.getenv("MYSQL_DATABASE", "market_data"),
        connect_timeout=10,
    )


@pytest.fixture(scope="session")
def mysql_live():
    try:
        import pymysql

        c = _mysql_config()
        conn = pymysql.connect(
            host=c.host,
            port=c.port,
            user=c.user,
            password=c.password,
            database=c.database,
            connect_timeout=10,
        )
        conn.close()
    except Exception as e:  # noqa: BLE001 — 用于探测环境
        pytest.skip(f"MySQL 不可用，跳过集成测试: {e}")
    return _mysql_config()


# 内部 code 与 KlineFetcher / 项目约定一致
SCREEN_ONE_STOCK = [
    ("HK", "HK.00700", "腾讯控股"),
    ("US", "US.AAPL", "Apple"),
    ("A", "SH.600519", "贵州茅台"),
]


def _default_smoke_params() -> dict:
    return {
        "price_min": 0.01,
        "price_max": 1e12,
        "use_ema_breakout": True,
        # 与 screen_service 默认一致：放量策略器参与链路；涨跌幅带仍关以减轻断言噪音
        "use_volume_spike_vs_prior3": True,
        "use_daily_drop_band": False,
        "use_daily_rise_band": False,
    }


def _load_filter_details(row_filter_details):
    if row_filter_details is None:
        return []
    if isinstance(row_filter_details, list):
        return row_filter_details
    if isinstance(row_filter_details, dict):
        return [row_filter_details]
    if isinstance(row_filter_details, str):
        return json.loads(row_filter_details)
    return list(row_filter_details)


@pytest.mark.integration
@pytest.mark.parametrize("market,code,name", SCREEN_ONE_STOCK)
def test_run_screening_task_pipeline_one_stock(mysql_live, market, code, name):
    mysql_config = mysql_live
    task_id = str(uuid.uuid4())
    timeframe = "1d"
    params = _default_smoke_params()

    db = MarketDatabase(mysql_config)
    db.init_schema(timeframe)
    db.create_screening_task(
        task_id=task_id,
        market=market,
        timeframe=timeframe,
        total_count=1,
        params_json=params,
        check_date=date.today(),
    )
    db.close()

    run_screening_task(
        mysql_config=mysql_config,
        task_id=task_id,
        market=market,
        timeframe=timeframe,
        params=params,
        verbose=False,
        watchlist=[{"code": code, "name": name}],
        progress_log=False,
    )

    db2 = MarketDatabase(mysql_config)
    with db2.conn.cursor() as cur:
        cur.execute(
            "SELECT status FROM screening_tasks WHERE task_id=%s",
            (task_id,),
        )
        row = cur.fetchone()
    assert row is not None, "screening_tasks 中应有任务记录"
    assert row[0] == "completed", f"任务应成功完成，实际 status={row[0]!r}"

    with db2.conn.cursor() as cur:
        cur.execute(
            """
            SELECT is_passed, filter_details FROM screening_results
            WHERE task_id=%s AND market=%s AND code=%s AND check_date=%s
            """,
            (task_id, market, code, date.today()),
        )
        res = cur.fetchone()
    db2.close()

    assert res is not None, "screening_results 应有对应股票一行"
    details = _load_filter_details(res[1])
    names = {item.get("filter_name") for item in details if isinstance(item, dict)}
    assert "PriceFilter" in names, f"应有 PriceFilter 输出，实际 filter_name 集合: {names}"
    assert "EMABreakoutStrategizer" in names, f"应有 EMABreakoutStrategizer，实际: {names}"
    assert "RSIOversoldStrategizer" in names, f"应有 RSIOversoldStrategizer，实际: {names}"
    assert "RSIOverboughtStrategizer" in names, f"应有 RSIOverboughtStrategizer，实际: {names}"
    assert "TodayVolumeExceedsPrior3MaxStrategizer" in names, (
        f"应有 TodayVolumeExceedsPrior3MaxStrategizer，实际: {names}"
    )

    ema = next(x for x in details if x.get("filter_name") == "EMABreakoutStrategizer")
    ema_details = ema.get("details") or {}
    assert ema_details.get("kline_available") is not False, (
        "EMA 策略器应能读到 K 线（fetcher 生效）；若失败多为网络或数据源限流"
    )

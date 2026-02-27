#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
筛选和进度 API
"""

import json
import os
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from typing import List, Optional

from db import MarketDatabase, MySqlConfig
from market import normalize_market
from param_parser import parse_filter_params
from timeframe import parse_timeframe
from api.screen_service import create_and_start_screening_task

router = APIRouter()


# 请求模型
class ScreenRequest(BaseModel):
    market: str
    timeframe: str = "1d"
    use_ema_breakout: Optional[bool] = True  # 是否启用 EMA 突破策略（默认启用）
    ema_short: Optional[int] = 10  # EMA 短期周期
    ema_long: Optional[int] = 150  # EMA 长期周期
    market_cap_min: Optional[float] = None
    market_cap_max: Optional[float] = None
    avg_daily_volume_min: Optional[float] = None
    avg_daily_volume_max: Optional[float] = None
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    pe_min: Optional[float] = None
    pe_max: Optional[float] = None
    require_profitable: Optional[bool] = None
    watchlist: Optional[List[dict]] = None  # 自选股列表 [{code, name}, ...]，非空时仅筛选此列表


def get_mysql_config() -> MySqlConfig:
    """获取 MySQL 配置"""
    return MySqlConfig(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", "123456"),
        database=os.getenv("MYSQL_DATABASE", "market_data"),
    )


@router.post("/screen")
async def start_screening(request: ScreenRequest, background_tasks: BackgroundTasks):
    """
    启动筛选任务
    
    Args:
        request: 筛选请求参数
        background_tasks: FastAPI 后台任务
        
    Returns:
        {"task_id": "xxx"}
    """
    try:
        # 解析市场和 timeframe
        market = normalize_market(request.market)
        timeframe = parse_timeframe(request.timeframe)
        
        # 解析筛选参数（不再注入默认值，仅对用户填写的条件生效）
        params = request.dict()
        
        # EMA 突破策略默认启用
        if params.get("use_ema_breakout") is None:
            params["use_ema_breakout"] = True
        
        # 解析参数（支持字符串格式）
        parsed_params = parse_filter_params(params)
        
        # 获取 MySQL 配置
        mysql_config = get_mysql_config()
        
        # 自选股列表：非空且长度>0 时仅筛选该列表
        watchlist = getattr(request, "watchlist", None)
        if watchlist is not None and (not isinstance(watchlist, list) or len(watchlist) == 0):
            watchlist = None

        # 创建并启动筛选任务
        task_id = create_and_start_screening_task(
            mysql_config=mysql_config,
            market=market,
            timeframe=timeframe,
            params=parsed_params,
            watchlist=watchlist,
            background_runner=background_tasks.add_task,
        )
        
        return {"task_id": task_id}
    
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"启动筛选失败: {str(e)}")


@router.get("/progress/{task_id}")
async def get_progress(task_id: str):
    """
    获取筛选任务进度
    
    Args:
        task_id: 任务ID
        
    Returns:
        {
            "task_id": "xxx",
            "status": "running/completed/failed",
            "total_count": 100,
            "completed_count": 50,
            "current_stock_code": "HK.00700",
            "current_stock_name": "腾讯控股",
            "passed_stocks": [
                {"code": "HK.00700", "name": "腾讯控股"},
                {"code": "HK.00005", "name": "汇丰控股"}
            ]
        }
    """
    try:
        mysql_config = get_mysql_config()
        db = MarketDatabase(mysql_config)
        
        task = db.get_task_by_id(task_id)
        
        if not task:
            db.close()
            raise HTTPException(status_code=404, detail="任务不存在")
        
        # 按 task_id 查询已通过筛选的股票（避免同一天多任务结果混淆）
        sql = """
            SELECT code, name, filter_details
            FROM screening_results
            WHERE task_id=%s AND is_passed=1
            ORDER BY code
        """
        with db.conn.cursor() as cursor:
            cursor.execute(sql, (task["task_id"],))
            rows = cursor.fetchall() or []

        db.close()

        # 提取满足的策略
        strategy_name_map = {
            'EMABreakoutStrategizer': 'EMA突破',
            'RSIOversoldStrategizer': 'RSI超卖',
            'RSIOverboughtStrategizer': 'RSI超买',
        }

        passed_stocks = []
        for row in rows:
            code = row[0]
            name = row[1] or code
            filter_details_raw = row[2]

            # 解析 filter_details 提取满足的策略
            satisfied_strategies = []
            if filter_details_raw:
                try:
                    filter_details = json.loads(filter_details_raw) if isinstance(filter_details_raw, str) else filter_details_raw
                    if isinstance(filter_details, list):
                        for detail in filter_details:
                            if detail.get('result') == 'pass' and detail.get('filter_name') in strategy_name_map:
                                satisfied_strategies.append(strategy_name_map[detail['filter_name']])
                except Exception:
                    pass

            passed_stocks.append({
                "code": code,
                "name": name,
                "satisfied_strategies": satisfied_strategies
            })
        
        return {
            "task_id": task["task_id"],
            "status": task["status"],
            "total_count": task["total_count"],
            "completed_count": task["completed_count"],
            "current_stock_code": task["current_stock_code"],
            "current_stock_name": task["current_stock_name"],
            "market": task["market"],
            "timeframe": task["timeframe"],
            "passed_stocks": passed_stocks,
            "passed_count": len(passed_stocks),
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取进度失败: {str(e)}")


@router.get("/last-result")
async def get_last_result():
    """
    获取最近一次已完成的筛选任务摘要，用于页面刷新后恢复结果。
    无则返回 204 或 task_id 为 null。
    """
    try:
        mysql_config = get_mysql_config()
        db = MarketDatabase(mysql_config)
        task = db.get_latest_completed_task()
        db.close()
        if not task:
            return {"task_id": None}
        return {
            "task_id": task["task_id"],
            "market": task.get("market"),
            "timeframe": task.get("timeframe", "1d"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取上次结果失败: {str(e)}")


@router.get("/results/{task_id}")
async def get_results(
    task_id: str,
    passed_only: bool = True,
    sector: Optional[str] = None,
    industry: Optional[str] = None,
    market_cap_min: Optional[float] = None,
    market_cap_max: Optional[float] = None,
    pe_min: Optional[float] = None,
    pe_max: Optional[float] = None,
    close_price_min: Optional[float] = None,
    close_price_max: Optional[float] = None,
):
    """
    获取筛选结果，支持可选条件下钻过滤。
    
    Args:
        task_id: 任务ID
        passed_only: 是否只返回通过筛选的股票（默认 true）
        sector, industry, market_cap_min/max, pe_min/max, close_price_min/max: 可选，对结果集再过滤
    """
    try:
        mysql_config = get_mysql_config()
        db = MarketDatabase(mysql_config)
        
        # 获取任务信息
        task = db.get_task_by_id(task_id)
        if not task:
            db.close()
            raise HTTPException(status_code=404, detail="任务不存在")
        
        # 按 task_id 查询筛选结果，并应用可选过滤条件
        sql = """
            SELECT code, name, is_passed, filter_summary, filter_details, sector, industry,
                   market_cap, pe_ratio, close_price
            FROM screening_results
            WHERE task_id=%s
        """
        args = [task["task_id"]]
        if passed_only:
            sql += " AND is_passed=1"
        if sector is not None and sector.strip():
            sql += " AND sector=%s"
            args.append(sector.strip())
        if industry is not None and industry.strip():
            sql += " AND industry=%s"
            args.append(industry.strip())
        if market_cap_min is not None:
            sql += " AND market_cap>=%s"
            args.append(market_cap_min)
        if market_cap_max is not None:
            sql += " AND market_cap<=%s"
            args.append(market_cap_max)
        if pe_min is not None:
            sql += " AND pe_ratio>=%s"
            args.append(pe_min)
        if pe_max is not None:
            sql += " AND pe_ratio<=%s"
            args.append(pe_max)
        if close_price_min is not None:
            sql += " AND close_price>=%s"
            args.append(close_price_min)
        if close_price_max is not None:
            sql += " AND close_price<=%s"
            args.append(close_price_max)
        sql += " ORDER BY code"

        with db.conn.cursor() as cursor:
            cursor.execute(sql, tuple(args))
            rows = cursor.fetchall() or []
        
        db.close()
        
        results = []
        for row in rows:
            filter_details_raw = row[4]
            filter_details = None
            if filter_details_raw:
                try:
                    filter_details = json.loads(filter_details_raw) if isinstance(filter_details_raw, str) else filter_details_raw
                except Exception:
                    filter_details = None
            results.append({
                "code": row[0],
                "name": row[1],
                "is_passed": bool(row[2]),
                "filter_summary": row[3],
                "filter_details": filter_details,
                "sector": row[5],
                "industry": row[6],
                "market_cap": float(row[7]) if row[7] else None,
                "pe_ratio": float(row[8]) if row[8] else None,
                "close_price": float(row[9]) if row[9] else None,
            })
        
        return {
            "task_id": task_id,
            "results": results,
            "total": len(results),
            "market": task.get("market"),
            "timeframe": task.get("timeframe", "1d"),
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取结果失败: {str(e)}")

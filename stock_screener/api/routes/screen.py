#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
筛选和进度 API
"""

import os
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from typing import List, Optional

from db import MarketDatabase, MySqlConfig
from market import normalize_market
from param_parser import parse_filter_params, get_default_params
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
        
        # 解析筛选参数（应用默认值）
        params = request.dict()
        
        # 如果没有提供任何筛选参数，使用默认值
        defaults = get_default_params()
        for key in ["market_cap_min", "avg_daily_volume_min", "price_min", "price_max", "pe_min", "pe_max", "require_profitable"]:
            if params.get(key) is None and key in defaults:
                params[key] = defaults[key]
        
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
            SELECT code, name
            FROM screening_results
            WHERE task_id=%s AND is_passed=1
            ORDER BY code
        """
        with db.conn.cursor() as cursor:
            cursor.execute(sql, (task["task_id"],))
            rows = cursor.fetchall() or []
        
        db.close()
        
        passed_stocks = [
            {"code": row[0], "name": row[1] or row[0]}
            for row in rows
        ]
        
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


@router.get("/results/{task_id}")
async def get_results(task_id: str, passed_only: bool = True):
    """
    获取筛选结果
    
    Args:
        task_id: 任务ID
        passed_only: 是否只返回通过筛选的股票（默认 true）
        
    Returns:
        {
            "task_id": "xxx",
            "results": [...]
        }
    """
    try:
        mysql_config = get_mysql_config()
        db = MarketDatabase(mysql_config)
        
        # 获取任务信息
        task = db.get_task_by_id(task_id)
        if not task:
            db.close()
            raise HTTPException(status_code=404, detail="任务不存在")
        
        # 按 task_id 查询筛选结果（避免同一天多任务结果混淆）
        sql = """
            SELECT code, name, is_passed, filter_summary, sector, industry,
                   market_cap, pe_ratio, close_price
            FROM screening_results
            WHERE task_id=%s
        """
        if passed_only:
            sql += " AND is_passed=1"
        sql += " ORDER BY code"

        with db.conn.cursor() as cursor:
            cursor.execute(sql, (task["task_id"],))
            rows = cursor.fetchall() or []
        
        db.close()
        
        results = []
        for row in rows:
            results.append({
                "code": row[0],
                "name": row[1],
                "is_passed": bool(row[2]),
                "filter_summary": row[3],
                "sector": row[4],
                "industry": row[5],
                "market_cap": float(row[6]) if row[6] else None,
                "pe_ratio": float(row[7]) if row[7] else None,
                "close_price": float(row[8]) if row[8] else None,
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

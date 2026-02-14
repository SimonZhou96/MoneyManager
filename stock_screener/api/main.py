#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FastAPI 主应用
"""

import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routes import markets, timeframes, screen, watchlist, chart

# 创建 FastAPI 应用
app = FastAPI(
    title="选股器 API",
    description="股票筛选器 REST API",
    version="1.0.0",
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应该限制为具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(markets.router, prefix="/api", tags=["markets"])
app.include_router(timeframes.router, prefix="/api", tags=["timeframes"])
app.include_router(screen.router, prefix="/api", tags=["screen"])
app.include_router(watchlist.router, prefix="/api", tags=["watchlist"])
app.include_router(chart.router, prefix="/api", tags=["chart"])

# 挂载静态文件（前端）
frontend_dir = Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="static")


@app.get("/api/health")
async def health_check():
    """健康检查"""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    
    # 从环境变量读取配置
    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("API_PORT", "8000"))
    
    uvicorn.run(app, host=host, port=port)

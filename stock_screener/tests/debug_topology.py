#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PyCharm 可调试的产业拓扑 API 测试用例。

Usage（在 PyCharm 中右键 Run/Debug 本文件即可）:
    python tests/debug_topology.py

或者只跑部分步骤:
    TOPO_STEPS=graph python tests/debug_topology.py          # 仅 /graph
    TOPO_STEPS=search_enrich python tests/debug_topology.py  # 仅 /search-enrich
    TOPO_STEPS=all python tests/debug_topology.py            # 全部（默认）

Pre-requisites:
    - .env 文件中 MYSQL_* 配置正确，DB 可连接
    - LLM_API_KEY / LLM_BASE_URL 等环境变量已配置（走真实 LLM）
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# ---------- 确保 stock_screener 在 sys.path ----------
_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
sys.path.insert(0, str(_PROJECT))

# ---------- 加载 .env ----------
from web.config import load_dotenv, mysql_config_from_env

load_dotenv()


def _pprint(data: Any, title: str = "") -> None:
    """Pretty-print JSON，方便在 PyCharm Debugger 的 Console 中查看。"""
    if title:
        print(f"\n{'='*80}")
        print(f"  {title}")
        print(f"{'='*80}")
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _summarize_graph(data: Dict[str, Any]) -> Dict[str, Any]:
    """返回 graph 结果的简要摘要，避免终端被 JSON 淹没。"""
    stats = data.get("stats", {})
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    center = data.get("center", {})

    return {
        "center": {
            "id": center.get("id"),
            "name": center.get("name"),
            "sector": center.get("sector"),
            "market_cap_str": center.get("market_cap_str"),
        },
        "stats": stats,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": [
            {
                "id": n.get("id"),
                "name": n.get("name"),
                "sector": n.get("sector"),
                "zone": n.get("zone"),
                "depth": n.get("depth"),
                "data_stage": n.get("data_stage"),
            }
            for n in nodes
        ],
        "edges": [
            {
                "source": e.get("source"),
                "target": e.get("target"),
                "label": e.get("label"),
                "direction": e.get("direction"),
            }
            for e in edges
        ],
    }


def _summarize_enrich(data: Dict[str, Any]) -> Dict[str, Any]:
    """返回 search-enrich 结果的简要摘要。"""
    items = data.get("items", [])
    return {
        "item_count": len(items),
        "warnings": data.get("warnings", []),
        "items": [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "sector": item.get("sector"),
                "market_cap_str": item.get("market_cap_str"),
                "field_sources": item.get("field_sources"),
                "data_gaps": item.get("data_gaps"),
            }
            for item in items
        ],
    }


def _load_llm_env_info() -> Dict[str, str]:
    """收集当前 LLM 相关环境变量（隐藏敏感值）。"""
    keys = [
        "LLM_PROVIDER", "LLM_API_BASE", "LLM_MODEL",
        "DEEPSEEK_API_KEY", "DEEPSEEK_API_BASE", "DEEPSEEK_LLM_MODEL",
        "CODEX_API_KEY", "CODEX_API_BASE", "CODEX_LLM_MODEL",
        "TAVILY_API_KEY", "BING_BAIDU_BROWSER_HEADLESS",
        "TOPOLOGY_BING_BAIDU_TIMEOUT_SEC",
        "LLM_PROVIDER_ORDER",
    ]
    info = {}
    for k in keys:
        v = os.getenv(k, "")
        if v and "KEY" in k.upper():
            info[k] = f"{v[:8]}...（已隐藏）" if len(v) > 12 else "***"
        else:
            info[k] = v or "（未设置）"
    return info


def _diagnose_api_reachability(urls: List[str], timeout_sec: float = 5.0) -> Dict[str, Any]:
    """快速诊断 LLM API 可达性，每个 URL 只做 TCP 连接，不发送 HTTP 请求。"""
    import socket
    from urllib.parse import urlparse

    results = {}
    for url in urls:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if not host:
            results[url] = {"error": "无法解析 hostname"}
            continue

        t0 = time.perf_counter()
        try:
            sock = socket.create_connection((host, port), timeout=timeout_sec)
            sock.close()
            elapsed = time.perf_counter() - t0
            results[url] = {"reachable": True, "latency_ms": round(elapsed * 1000)}
        except socket.timeout:
            elapsed = time.perf_counter() - t0
            results[url] = {"reachable": False, "error": f"TCP 连接超时（{elapsed:.1f}s）"}
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            results[url] = {"reachable": False, "error": f"{type(exc).__name__}: {exc}"}

    return results


# ============================================================================
# 主流程
# ============================================================================
def main():
    from db import MarketDatabase
    from industry_topology.service import TopologyService

    steps = os.getenv("TOPO_STEPS", "all").strip().lower()

    # ---- Step 0: 环境检查 ----
    print("\n🔧 LLM / Search 环境变量：")
    for k, v in _load_llm_env_info().items():
        print(f"   {k} = {v}")

    # ---- Step 0.5: API 可达性快速诊断 ----
    api_urls = []
    deepseek_base = os.getenv("DEEPSEEK_API_BASE", "").strip()
    if deepseek_base:
        api_urls.append(deepseek_base)
    llm_base = os.getenv("LLM_API_BASE", "").strip()
    if llm_base and llm_base != deepseek_base:
        api_urls.append(llm_base)
    codex_base = os.getenv("CODEX_API_BASE", "").strip()
    if codex_base and codex_base not in (deepseek_base, llm_base):
        api_urls.append(codex_base)

    if api_urls:
        print("\n🌐 API 可达性诊断（TCP connect, 5s 超时）：")
        diag = _diagnose_api_reachability(api_urls)
        for url, status in diag.items():
            if status.get("reachable"):
                print(f"   ✅ {url} — {status['latency_ms']}ms")
            else:
                print(f"   ❌ {url} — {status['error']}")

    # ---- Step 1: 连接 DB ----
    print("\n📡 连接 MySQL ...")
    cfg = mysql_config_from_env()
    print(f"   host={cfg.host}, port={cfg.port}, db={cfg.database}, user={cfg.user}")
    db = MarketDatabase(cfg)

    try:
        # ---- Step 2: 构造 TopologyService ----
        print("\n🏗️  构造 TopologyService（含真实 LLM + Search Provider）...")
        svc = TopologyService(db)
        p_name, m_name = svc._provider_meta()
        print(f"   LLM provider = {p_name}, model = {m_name or '(default)'}")
        print(f"   Search provider = {getattr(svc.search_provider, 'name', 'none')}")
        print(f"   Engine = {'✅' if svc.engine else '❌ None'}")
        print(f"   Enricher = {'✅' if svc.enricher else '❌ None'}")

        # ---- Step 3: 调用 /graph ----
        if steps in ("all", "graph"):
            code = "US.NVDA"
            market = "US"
            depth = 3

            print(f"\n🚀 调用 build_graph(code={code}, market={market}, depth={depth}, quote_mode=llm_initial) ...")
            t0 = time.perf_counter()
            graph_data = svc.build_graph(code, market, depth, quote_mode="llm_initial")
            elapsed = time.perf_counter() - t0
            print(f"⏱️  耗时 {elapsed:.1f}s")

            # 完整结果（可在 PyCharm Variables 面板中展开查看）
            _pprint(graph_data, title="/api/topology/graph 完整结果（US.NVDA depth=3）")

            # 摘要
            _pprint(_summarize_graph(graph_data), title="/graph 摘要")

            # 单独打印 stats 便于查看
            stats = graph_data.get("stats", {})
            print(f"\n📊 核心指标：")
            print(f"   llm_calls       = {stats.get('llm_calls')}")
            print(f"   cached_nodes    = {stats.get('cached_nodes')}")
            print(f"   stale_nodes     = {stats.get('stale_nodes')}")
            print(f"   relation_status = {stats.get('relation_status')}")
            print(f"   data_stage      = {stats.get('data_stage')}")
            print(f"   depth           = {stats.get('depth')}")
            if stats.get("error"):
                print(f"   ❌ error         = {stats['error']}")
            if stats.get("warnings"):
                print(f"   ⚠️  warnings      = {stats['warnings']}")

            # 保存 graph_data 到局部变量，方便在 PyCharm Debugger 中直接查看
            _graph_result = graph_data  # noqa: F841 — 设断点在这行后面即可查看

        # ---- Step 4: 调用 /search-enrich ----
        if steps in ("all", "search_enrich"):
            print(f"\n🔍 调用 search_enrich(center=US.NVDA, symbols=...) ...")

            # 构造 center（与 web/topology.py 中 SearchEnrichRequest 一致）
            center = {
                "market": "US",
                "code": "US.NVDA",
                "name": "NVIDIA",
                "sector": "半导体",
                "industry": "人工智能芯片",
            }
            # 使用一些已知的 NVDA 供应链/竞品 symbol
            symbols = [
                "US.TSM",   # 台积电 — 代工
                "US.AMD",   # AMD — 竞品
                "US.INTC",  # 英特尔 — 竞品
                "US.AVGO",  # 博通 — 竞品/客户
                "US.QCOM",  # 高通 — 相关
                "US.MRVL",  # Marvell — 相关
                "US.ASML",  # ASML — 设备供应商
                "US.AMAT",  # 应用材料 — 设备供应商
            ]

            t0 = time.perf_counter()
            enrich_data = svc.search_enrich(center=center, symbols=symbols)
            elapsed = time.perf_counter() - t0
            print(f"⏱️  耗时 {elapsed:.1f}s")

            _pprint(enrich_data, title="/api/topology/graph/search-enrich 完整结果")

            _pprint(_summarize_enrich(enrich_data), title="/search-enrich 摘要")

            _enrich_result = enrich_data  # noqa: F841 — 设断点在这行后面即可查看

        print("\n✅ 调试脚本执行完毕。在 PyCharm 中设断点后重新 Run/Debug 即可单步跟踪。")

    finally:
        db.close()
        print("🔒 DB 连接已关闭。")


if __name__ == "__main__":
    main()

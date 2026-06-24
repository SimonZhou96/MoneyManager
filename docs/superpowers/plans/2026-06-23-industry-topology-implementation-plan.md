# 产业拓扑（Industry Topology）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 输入股票代码/名称 → 搜索下拉选中 → 点"开始拓扑" → 展示以该股票为中心、上下游公司 3-4 度（可选 1-5 度）的产业链拓扑图，节点面积随市值、颜色随涨跌、节点内显示板块/名称/代码/涨跌幅/市值、边显示关系，公司维度持久化缓存避免重复 LLM 调用。

**Architecture:** 新增独立后端子系统 `stock_screener/industry_topology/`（三层：resolver 行情聚合 / relation_engine LLM 推理 / service 编排），关系持久化到 `industry_relations` MySQL 表（TTL 7 天），用 networkx 内存图做 N 度遍历与"是否需重算"判定。FastAPI 路由 `web/topology.py` 挂到 `main.py`。前端 React Flow v12 + dagre 分层布局，自定义节点卡片。

**Tech Stack:** Python (PyMySQL, networkx, FastAPI, 复用 `signal_analysis/factories.py` LLM provider)；React 18 + Vite + TypeScript + `@xyflow/react` + dagre。

## Global Constraints

- **三模式兼容**：所有后端改动必须同时在 `desktop.py`(pywebview)、web(uvicorn)、`interactive_screening.py` 三模式工作。`industry_topology/service.py` 不依赖 web 层。
- **故障隔离**：LLM 失败/行情失败不阻断已渲染图；行情失败节点显示 `--` 灰色。
- **同步 SQL 和 Python**：`industry_relations` 建表语句同时写 `sql/` 部署文件和 `db.py` 的 `init_web_schema`。
- **provider 集中**：LLM provider 通过 `signal_analysis/factories.py` 工厂创建，不在 `relation_engine` 写 provider 分支。
- **RTK 代理**：所有 shell 命令用 `rtk <cmd>`（如 `rtk npm install`、`rtk git`）。
- **测试**：项目用 `unittest`（非 pytest），测试在 `stock_screener/tests/`，运行 `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m unittest tests.test_xxx`。
- **绿涨红跌**：全局统一 `pct_chg>0` 绿、`pct_chg<0` 红（非 A 股惯例），按需求。
- **每节点最多 50 家**关系公司，单次展开每节点最多 1 次 LLM，会话硬上限 20 次。
- **深度默认 3，最大 5**，前端可选。
- **缓存 TTL 7 天**，公司维度整组失效，命中即 0 LLM。

---

## 文件结构

### 后端（`stock_screener/`）
- Create: `industry_topology/__init__.py` — 包标记
- Create: `industry_topology/models.py` — `RelationType` 枚举、`Direction` 枚举、`TopologyNode`/`TopologyEdge`/`CachedRelation` dataclass
- Create: `industry_topology/cache.py` — `GraphCache`：`industry_relations` 表读写 + networkx 图遍历 + TTL 判定
- Create: `industry_topology/resolver.py` — `NodeResolver`：聚合行情(名称/板块/市值/涨跌幅) + `compute_size_level(market_cap)` + `format_market_cap(market_cap)`
- Create: `industry_topology/relation_engine.py` — `RelationEngine`：LLM 提示词构造 + JSON 解析 + 三道校验(代码/去重/超纲兜底) + 空结果缓存
- Create: `industry_topology/service.py` — `TopologyService`：编排 search/graph/expand/refresh，受 LLM 硬上限约束
- Create: `web/topology.py` — `APIRouter(prefix="/api/topology")` 四个端点
- Modify: `web/main.py` — import + include `topology_router`，startup 调 `db.init_industry_topology_schema()`
- Modify: `db.py` — `init_industry_topology_schema()` 建表 + `search_stocks_by_name`/`get_stocks_by_codes` 复用（已存在）
- Modify: `sql/industry_topology.sql`（或现有部署 SQL）— 同步建表语句
- Create: `tests/test_industry_topology.py` — 9 组单元测试

### 前端（`stock_screener/web_frontend/src/features/industryTopology/`）
- Create: `types.ts` — TS 类型 = 后端契约
- Create: `topologyApi.ts` — 调 `/api/topology/*`
- Create: `StockSearchInput.tsx` — 搜索下拉
- Create: `TopologyNode.tsx` — 自定义节点卡片
- Create: `TopologyCanvas.tsx` — React Flow 画布 + dagre 布局
- Create: `IndustryTopologyPanel.tsx` — 入口容器
- Modify: `main.tsx` — import + nav 按钮 + page 渲染分支
- Modify: `styles.css` — 拓扑节点/边/工具栏样式
- Modify: `package.json` — 加 `dagre` + `@types/dagre`

---

## Task 1: 数据模型与枚举

**Files:**
- Create: `stock_screener/industry_topology/__init__.py`
- Create: `stock_screener/industry_topology/models.py`
- Test: `stock_screener/tests/test_industry_topology.py`

**Interfaces:**
- Produces: `models.RelationType`(enum)、`models.Direction`(enum)、`models.TopologyNode`(dataclass)、`models.TopologyEdge`(dataclass)、`models.CachedRelation`(dataclass) — 后续所有 task import 这些。

- [ ] **Step 1: 写失败测试**

`stock_screener/tests/test_industry_topology.py`:
```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""产业拓扑单元测试。"""
import unittest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from industry_topology.models import (
    RelationType, Direction, TopologyNode, TopologyEdge, CachedRelation,
)


class TestModels(unittest.TestCase):
    def test_relation_type_members(self):
        self.assertIn("supplier", [r.value for r in RelationType])
        self.assertIn("customer", [r.value for r in RelationType])
        self.assertIn("competitor", [r.value for r in RelationType])
        self.assertIn("other", [r.value for r in RelationType])
        self.assertEqual(len(list(RelationType)), 13)  # 12 标签 + other

    def test_direction_members(self):
        self.assertEqual({d.value for d in Direction}, {"upstream", "downstream", "peer"})

    def test_topology_node_fields(self):
        n = TopologyNode(
            code="US.NVDA", name="英伟达", market="US", sector="半导体",
            pct_chg=2.13, market_cap_str="2.8万亿", size_level=6,
            expanded=False, stale=False, is_center=True,
        )
        self.assertEqual(n.size_level, 6)
        self.assertTrue(n.is_center)

    def test_topology_edge_fields(self):
        e = TopologyEdge(
            source="US.NVDA", target="300308", direction=Direction.UPSTREAM,
            relation=RelationType.SUPPLIER, label="供应商·提供800G光模块", evidence="提供800G光模块",
        )
        self.assertEqual(e.direction, Direction.UPSTREAM)

    def test_cached_relation_fields(self):
        r = CachedRelation(
            source_code="US.NVDA", source_market="US",
            peer_code="300308", peer_market="A", peer_name="中际旭创",
            relation=RelationType.SUPPLIER, direction=Direction.UPSTREAM,
            evidence="提供800G光模块", expires_at=None, is_empty=False,
        )
        self.assertEqual(r.peer_name, "中际旭创")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd stock_screener && PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m unittest tests.test_industry_topology -v`
Expected: FAIL `ModuleNotFoundError: No module named 'industry_topology'`

- [ ] **Step 3: 写最小实现**

`stock_screener/industry_topology/__init__.py`:
```python
"""产业拓扑子系统：LLM 推理上下游关系 + 公司维度缓存 + networkx 图遍历。"""
```

`stock_screener/industry_topology/models.py`:
```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""产业拓扑数据模型。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class Direction(str, Enum):
    UPSTREAM = "upstream"      # peer 是 source 的上游
    DOWNSTREAM = "downstream"  # peer 是 source 的下游
    PEER = "peer"              # 同业/竞品/替代品


class RelationType(str, Enum):
    # 上游类
    SUPPLIER = "supplier"
    RAW_MATERIAL = "raw_material"
    EQUIPMENT = "equipment"
    FOUNDRY_PACKAGING = "foundry_packaging"
    # 下游类
    CUSTOMER = "customer"
    ODM = "odm"
    DISTRIBUTOR = "distributor"
    APPLICATION = "application"
    # 同业类
    COMPETITOR = "competitor"
    SUBSTITUTE = "substitute"
    # 兜底
    OTHER = "other"


@dataclass(frozen=True)
class TopologyNode:
    code: str
    name: str
    market: str
    sector: str
    pct_chg: Optional[float]
    market_cap_str: str
    size_level: int  # 1-6
    expanded: bool
    stale: bool
    is_center: bool


@dataclass(frozen=True)
class TopologyEdge:
    source: str
    target: str
    direction: Direction
    relation: RelationType
    label: str       # "关系·依据"
    evidence: str


@dataclass(frozen=True)
class CachedRelation:
    source_code: str
    source_market: str
    peer_code: str
    peer_market: str
    peer_name: str
    relation: RelationType
    direction: Direction
    evidence: str
    expires_at: Optional[datetime]
    is_empty: bool
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd stock_screener && PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m unittest tests.test_industry_topology -v`
Expected: PASS (4 tests)

- [ ] **Step 5: 提交**

```bash
cd stock_screener
rtk git add industry_topology/__init__.py industry_topology/models.py tests/test_industry_topology.py
rtk git commit -m "feat(industry-topology): add data models and enums"
```

---

## Task 2: DB 建表（init_industry_topology_schema）

**Files:**
- Modify: `stock_screener/db.py`（在 `init_web_schema` 末尾或新增方法加建表）
- Create: `stock_screener/sql/industry_topology.sql`

**Interfaces:**
- Consumes: `MarketDatabase`（已存在，`self.conn` PyMySQL autocommit）
- Produces: `MarketDatabase.init_industry_topology_schema()` — 后续 service/cache 依赖此方法建表；表名 `industry_relations`。

- [ ] **Step 1: 写失败测试**

追加到 `stock_screener/tests/test_industry_topology.py`（在 `TestModels` 后）:
```python
from unittest.mock import MagicMock
from db import MarketDatabase


class TestSchema(unittest.TestCase):
    def test_init_industry_topology_schema_exists(self):
        self.assertTrue(hasattr(MarketDatabase, "init_industry_topology_schema"))

    def test_init_industry_topology_schema_executes_create(self):
        db = MagicMock(spec=MarketDatabase)
        cursor = MagicMock()
        db.conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        db.conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        db.init_industry_topology_schema()
        executed = [c.args[0] for c in cursor.execute.call_args_list if c.args]
        self.assertTrue(any("CREATE TABLE IF NOT EXISTS industry_relations" in s for s in executed))
        self.assertTrue(any("expires_at" in s for s in executed))
        self.assertTrue(any("is_empty" in s for s in executed))
        self.assertTrue(any("UNIQUE KEY" in s and "source_code" in s for s in executed))
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd stock_screener && PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m unittest tests.test_industry_topology.TestSchema -v`
Expected: FAIL `AttributeError: 'MarketDatabase' has no attribute 'init_industry_topology_schema'`

- [ ] **Step 3: 写最小实现**

在 `stock_screener/db.py` 的 `MarketDatabase` 类内（`init_web_schema` 方法附近）新增方法。先读 `init_web_schema` 的建表代码风格，然后在 `db.py` 文件末尾类的最后一个方法后追加：

```python
    def init_industry_topology_schema(self) -> None:
        """创建产业拓扑关系缓存表（同步 sql/industry_topology.sql）。"""
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS industry_relations (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    source_code VARCHAR(32) NOT NULL,
                    source_market VARCHAR(8) NOT NULL,
                    peer_code VARCHAR(32) NOT NULL,
                    peer_market VARCHAR(8) NOT NULL,
                    peer_name VARCHAR(128) NOT NULL DEFAULT '',
                    relation VARCHAR(32) NOT NULL,
                    direction VARCHAR(8) NOT NULL,
                    evidence TEXT,
                    is_empty TINYINT(1) NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    expires_at DATETIME NOT NULL,
                    llm_provider VARCHAR(32) NOT NULL DEFAULT '',
                    llm_model VARCHAR(64) NOT NULL DEFAULT '',
                    UNIQUE KEY uk_source_peer_relation (source_code, peer_code, relation),
                    KEY idx_source_code (source_code),
                    KEY idx_expires (expires_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
```

`stock_screener/sql/industry_topology.sql`:
```sql
-- 产业拓扑关系缓存表（公司维度，TTL 7 天）
CREATE TABLE IF NOT EXISTS industry_relations (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    source_code VARCHAR(32) NOT NULL,
    source_market VARCHAR(8) NOT NULL,
    peer_code VARCHAR(32) NOT NULL,
    peer_market VARCHAR(8) NOT NULL,
    peer_name VARCHAR(128) NOT NULL DEFAULT '',
    relation VARCHAR(32) NOT NULL,
    direction VARCHAR(8) NOT NULL,
    evidence TEXT,
    is_empty TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL,
    llm_provider VARCHAR(32) NOT NULL DEFAULT '',
    llm_model VARCHAR(64) NOT NULL DEFAULT '',
    UNIQUE KEY uk_source_peer_relation (source_code, peer_code, relation),
    KEY idx_source_code (source_code),
    KEY idx_expires (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd stock_screener && PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m unittest tests.test_industry_topology.TestSchema -v`
Expected: PASS (2 tests)

- [ ] **Step 5: 提交**

```bash
cd stock_screener
rtk git add db.py sql/industry_topology.sql tests/test_industry_topology.py
rtk git commit -m "feat(industry-topology): add industry_relations schema table"
```

---

## Task 3: 行情聚合 resolver + size/market_cap 计算

**Files:**
- Create: `stock_screener/industry_topology/resolver.py`
- Test: `stock_screener/tests/test_industry_topology.py`（追加）

**Interfaces:**
- Consumes: `MarketDatabase.search_stocks_by_name(market, keyword, limit)`、`MarketDatabase.get_stocks_by_codes(market, codes, include_fundamentals)`、`normalize_stock_code(market, code)`（从 `web.single_stock` import，三模式可用）
- Produces: `NodeResolver(db).resolve(code, market) -> dict`（含 code/name/sector/market_cap/pct_chg 等）、`compute_size_level(market_cap) -> int(1-6)`、`format_market_cap(market_cap) -> str`

> **pct_chg 来源**：A 股/港股用 `kline_fetcher` 最新一日 `change_rate`；美股用 yfinance `.info` 的 `regularMarketChangePercent`。失败返回 None，节点灰色。本 Task 先实现 size/format 和 resolve 主体，pct_chg 取数走 `_fetch_pct_chg`（内部 try/except，失败 None），具体取数逻辑在 Step 3 实现。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_industry_topology.py`:
```python
from industry_topology.resolver import compute_size_level, format_market_cap, NodeResolver


class TestSizeLevel(unittest.TestCase):
    # 市值单位：元
    def test_level1_under_50yi(self):
        self.assertEqual(compute_size_level(30 * 1e8), 1)   # 30亿
    def test_level2_50_200yi(self):
        self.assertEqual(compute_size_level(100 * 1e8), 2)  # 100亿
    def test_level3_200_1000yi(self):
        self.assertEqual(compute_size_level(500 * 1e8), 3)  # 500亿
    def test_level4_1000_3000yi(self):
        self.assertEqual(compute_size_level(2000 * 1e8), 4) # 2000亿
    def test_level5_3000yi_1wan(self):
        self.assertEqual(compute_size_level(5000 * 1e8), 5) # 5000亿
    def test_level6_over_1wan(self):
        self.assertEqual(compute_size_level(2 * 1e12), 6)   # 2万亿
    def test_none_returns_min(self):
        self.assertEqual(compute_size_level(None), 1)


class TestFormatMarketCap(unittest.TestCase):
    def test_wan_yi(self):
        self.assertEqual(format_market_cap(2 * 1e12), "2.0万亿")
    def test_yi(self):
        self.assertEqual(format_market_cap(850 * 1e8), "850亿")
    def test_none(self):
        self.assertEqual(format_market_cap(None), "--")
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd stock_screener && PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m unittest tests.test_industry_topology.TestSizeLevel tests.test_industry_topology.TestFormatMarketCap -v`
Expected: FAIL `ImportError: cannot import name 'compute_size_level'`

- [ ] **Step 3: 写最小实现**

`stock_screener/industry_topology/resolver.py`:
```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""节点行情聚合：名称/板块/市值/涨跌幅 + size 计算（不含 LLM）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from web.single_stock import normalize_stock_code

# 市值分桶边界（单位：元）
_SIZE_BUCKETS = [
    50 * 1e8,    # 50亿
    200 * 1e8,   # 200亿
    1000 * 1e8,  # 1000亿
    3000 * 1e8,  # 3000亿
    1 * 1e12,    # 1万亿
]


def compute_size_level(market_cap: Optional[float]) -> int:
    """市值 → size_level 1-6。None → 1（最小）。"""
    if market_cap is None:
        return 1
    for i, boundary in enumerate(_SIZE_BUCKETS):
        if market_cap < boundary:
            return i + 1
    return 6


def format_market_cap(market_cap: Optional[float]) -> str:
    """市值 → 中文格式化字符串。"""
    if market_cap is None:
        return "--"
    if market_cap >= 1e12:
        return f"{market_cap / 1e12:.1f}万亿"
    if market_cap >= 1e8:
        return f"{market_cap / 1e8:.0f}亿"
    if market_cap >= 1e4:
        return f"{market_cap / 1e4:.0f}万"
    return str(int(market_cap))


class NodeResolver:
    """聚合单只股票的行情/板块/市值/涨跌幅。行情失败不抛，返回 None 字段。"""

    def __init__(self, db: Any):
        self.db = db

    def resolve(self, code: str, market: str) -> Dict[str, Any]:
        market = _normalize_market(market)
        norm_code = normalize_stock_code(market, code)
        rows: List[dict] = []
        if hasattr(self.db, "get_stocks_by_codes"):
            rows = self.db.get_stocks_by_codes(market, [norm_code], include_fundamentals=True)
        base = rows[0] if rows else {"code": norm_code, "name": "", "sector": "", "market_cap": None}
        pct_chg = self._fetch_pct_chg(norm_code, market)
        return {
            "code": base.get("code") or norm_code,
            "name": base.get("name") or "",
            "market": market,
            "sector": base.get("sector") or base.get("industry") or "--",
            "market_cap": base.get("market_cap"),
            "pct_chg": pct_chg,
        }

    def resolve_many(self, codes: List[str], market: str) -> Dict[str, Dict[str, Any]]:
        market = _normalize_market(market)
        norm = []
        for c in codes:
            try:
                norm.append(normalize_stock_code(market, c))
            except ValueError:
                continue
        rows = self.db.get_stocks_by_codes(market, norm, include_fundamentals=True) if norm else []
        by_code = {r["code"]: r for r in rows}
        result = {}
        for c in norm:
            base = by_code.get(c, {"code": c, "name": "", "sector": "", "market_cap": None})
            result[c] = {
                "code": c,
                "name": base.get("name") or "",
                "market": market,
                "sector": base.get("sector") or base.get("industry") or "--",
                "market_cap": base.get("market_cap"),
                "pct_chg": self._fetch_pct_chg(c, market),
            }
        return result

    def _fetch_pct_chg(self, code: str, market: str) -> Optional[float]:
        """取最近一日涨跌幅（%）。失败返回 None，不抛。"""
        try:
            from kline_fetcher import KlineFetcherFactory
            chain = KlineFetcherFactory.create_fetcher_chain(market=market, db=self.db)
            df = chain.fetch(code, market, period="1d", count=2)
            if df is None or df.empty or "change_rate" not in df.columns:
                return None
            last = df["change_rate"].dropna()
            return round(float(last.iloc[-1]), 2) if len(last) else None
        except Exception:
            return None


def _normalize_market(market: str) -> str:
    m = (market or "").strip().upper()
    return m if m in ("HK", "US", "A") else "A"
```

> 注：`KlineFetcherFactory.create_fetcher_chain` 的确切参数签名在实现时需对照 `kline_fetcher.py:642` 核实（当前确认存在该方法）。如签名不符，以实际为准调整——这是实现阶段要在本文件读签名后对齐的点，不阻塞 plan。

- [ ] **Step 4: 运行测试验证通过**

Run: `cd stock_screener && PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m unittest tests.test_industry_topology.TestSizeLevel tests.test_industry_topology.TestFormatMarketCap -v`
Expected: PASS (9 tests)

- [ ] **Step 5: 提交**

```bash
cd stock_screener
rtk git add industry_topology/resolver.py tests/test_industry_topology.py
rtk git commit -m "feat(industry-topology): add node resolver with size/market_cap computation"
```

---

## Task 4: 关系缓存 GraphCache（DB 读写 + networkx 遍历 + TTL）

**Files:**
- Create: `stock_screener/industry_topology/cache.py`
- Test: `stock_screener/tests/test_industry_topology.py`（追加）

**Interfaces:**
- Consumes: `MarketDatabase`（`self.conn` PyMySQL），`models.CachedRelation`、`models.Direction`、`models.RelationType`
- Produces:
  - `GraphCache(db).get_relations(source_code, market) -> Optional[List[CachedRelation]]`（命中返回列表，过期/无返回 None）
  - `GraphCache(db).save_relations(source_code, market, relations, provider, model, ttl_days=7)`（整组替换）
  - `GraphCache(db).build_graph(cached_map: Dict[str, List[CachedRelation]]) -> nx.DiGraph`
  - `GraphCache.reachable_within(graph, source_code, depth) -> List[str]`（N 度可达节点）
  - `GraphCache.stale_nodes(graph, codes, now) -> List[str]`（过期/缺失节点）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_industry_topology.py`:
```python
import networkx as nx
from datetime import datetime, timedelta
from industry_topology.cache import GraphCache
from industry_topology.models import CachedRelation, Direction, RelationType


def _make_relation(peer_code, direction=Direction.UPSTREAM, relation=RelationType.SUPPLIER,
                   expires=None, is_empty=False):
    return CachedRelation(
        source_code="US.NVDA", source_market="US",
        peer_code=peer_code, peer_market="A", peer_name=f"公司{peer_code}",
        relation=relation, direction=direction, evidence="测试",
        expires_at=expires, is_empty=is_empty,
    )


class TestGraphCache(unittest.TestCase):
    def test_get_relations_hit(self):
        db = MagicMock(spec=MarketDatabase)
        cursor = MagicMock()
        db.conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        db.conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        cursor.fetchall.return_value = [
            ("US.NVDA", "US", "300308", "A", "中际旭创", "supplier", "upstream", "提供光模块", 0,
             datetime.now(), datetime.now(), datetime.now() + timedelta(days=7), "deepseek", "v4"),
        ]
        gc = GraphCache(db)
        rels = gc.get_relations("US.NVDA", "US")
        self.assertIsNotNone(rels)
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0].peer_code, "300308")

    def test_get_relations_expired_returns_none(self):
        db = MagicMock(spec=MarketDatabase)
        cursor = MagicMock()
        db.conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        db.conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        cursor.fetchall.return_value = []  # 过期被 WHERE 过滤
        gc = GraphCache(db)
        self.assertIsNone(gc.get_relations("US.NVDA", "US"))

    def test_save_relations_replaces_group(self):
        db = MagicMock(spec=MarketDatabase)
        cursor = MagicMock()
        db.conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        db.conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        gc = GraphCache(db)
        rels = [_make_relation("300308"), _make_relation("300394", direction=Direction.DOWNSTREAM, relation=RelationType.CUSTOMER)]
        gc.save_relations("US.NVDA", "US", rels, provider="deepseek", model="v4", ttl_days=7)
        # 应先 DELETE WHERE source_code 再 INSERT
        executed = [c.args[0] for c in cursor.execute.call_args_list if c.args]
        self.assertTrue(any("DELETE FROM industry_relations" in s and "source_code=%s" in s for s in executed))
        self.assertTrue(any("INSERT INTO industry_relations" in s for s in executed))

    def test_reachable_within_depth(self):
        gc = GraphCache(MagicMock(spec=MarketDatabase))
        g = nx.DiGraph()
        g.add_edge("A", "B"); g.add_edge("B", "C"); g.add_edge("C", "D")
        self.assertEqual(set(gc.reachable_within(g, "A", 1)), {"A", "B"})
        self.assertEqual(set(gc.reachable_within(g, "A", 3)), {"A", "B", "C", "D"})

    def test_build_graph_carries_attrs(self):
        gc = GraphCache(MagicMock(spec=MarketDatabase))
        cached = {
            "US.NVDA": [_make_relation("300308"), _make_relation("300394", Direction.DOWNSTREAM, RelationType.CUSTOMER)],
            "300308": [_make_relation("002156", direction=Direction.UPSTREAM, relation=RelationType.FOUNDRY_PACKAGING)],
        }
        g = gc.build_graph(cached)
        self.assertTrue(g.has_edge("US.NVDA", "300308"))
        self.assertEqual(g.nodes["US.NVDA"]["source_code"], "US.NVDA")
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd stock_screener && PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m unittest tests.test_industry_topology.TestGraphCache -v`
Expected: FAIL `ImportError: cannot import name 'GraphCache'`

- [ ] **Step 3: 写最小实现**

`stock_screener/industry_topology/cache.py`:
```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公司维度关系缓存 + networkx 图遍历。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import networkx as nx

from .models import CachedRelation, Direction, RelationType

_TTL_DAYS_DEFAULT = 7


class GraphCache:
    def __init__(self, db: Any):
        self.db = db

    def get_relations(self, source_code: str, market: str) -> Optional[List[CachedRelation]]:
        """命中返回列表（0 LLM）；过期/无返回 None。"""
        sql = (
            "SELECT source_code, source_market, peer_code, peer_market, peer_name, "
            "relation, direction, evidence, is_empty, created_at, updated_at, expires_at "
            "FROM industry_relations WHERE source_code=%s AND source_market=%s AND expires_at > NOW()"
        )
        with self.db.conn.cursor() as cursor:
            cursor.execute(sql, [source_code, market])
            rows = cursor.fetchall() or []
        if not rows:
            return None
        return [self._row_to_relation(r) for r in rows]

    def save_relations(self, source_code: str, market: str, relations: List[CachedRelation],
                       provider: str, model: str, ttl_days: int = _TTL_DAYS_DEFAULT) -> None:
        """整组替换：DELETE 旧组 + INSERT 新组。"""
        expires = datetime.now() + timedelta(days=ttl_days)
        with self.db.conn.cursor() as cursor:
            cursor.execute("DELETE FROM industry_relations WHERE source_code=%s", [source_code])
            if not relations:
                # 空结果也写一条 is_empty=1，防反复空推理浪费额度
                cursor.execute(
                    "INSERT INTO industry_relations "
                    "(source_code, source_market, peer_code, peer_market, peer_name, "
                    "relation, direction, evidence, is_empty, expires_at, llm_provider, llm_model) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s)",
                    [source_code, market, "", "", "", "other", "peer", "", expires, provider, model],
                )
                return
            for r in relations:
                cursor.execute(
                    "INSERT INTO industry_relations "
                    "(source_code, source_market, peer_code, peer_market, peer_name, "
                    "relation, direction, evidence, is_empty, expires_at, llm_provider, llm_model) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [source_code, market, r.peer_code, r.peer_market, r.peer_name,
                     r.relation.value, r.direction.value, r.evidence, 0, expires, provider, model],
                )

    def build_graph(self, cached_map: Dict[str, List[CachedRelation]]) -> nx.DiGraph:
        """把多 source 的缓存关系组装成有向图。节点属性带 source_code/expires_at。"""
        g = nx.DiGraph()
        for source_code, rels in cached_map.items():
            g.add_node(source_code, source_code=source_code, stale=False)
            for r in rels:
                if r.is_empty:
                    continue
                g.add_node(r.peer_code, source_code=r.peer_code, stale=False)
                g.add_edge(source_code, r.peer_code, relation=r.relation, direction=r.direction, evidence=r.evidence)
        return g

    def reachable_within(self, graph: nx.DiGraph, source_code: str, depth: int) -> List[str]:
        """N 度内可达节点（含 source 自身）。"""
        lengths = nx.single_source_shortest_path_length(graph, source_code, cutoff=depth)
        return list(lengths.keys())

    def stale_nodes(self, graph: nx.DiGraph, codes: List[str], now: Optional[datetime] = None) -> List[str]:
        """返回未在 graph 中出现（无缓存）的节点。过期判定在 get_relations 返回 None 时已体现。"""
        now = now or datetime.now()
        return [c for c in codes if c not in graph.nodes]

    @staticmethod
    def _row_to_relation(row) -> CachedRelation:
        return CachedRelation(
            source_code=row[0], source_market=row[1],
            peer_code=row[2], peer_market=row[3], peer_name=row[4],
            relation=RelationType(row[5]) if row[5] in RelationType._value2member_map_ else RelationType.OTHER,
            direction=Direction(row[6]) if row[6] in Direction._value2member_map_ else Direction.PEER,
            evidence=row[7] or "", is_empty=bool(row[8]),
            expires_at=row[11],
        )
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd stock_screener && PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m unittest tests.test_industry_topology.TestGraphCache -v`
Expected: PASS (5 tests)

- [ ] **Step 5: 提交**

```bash
cd stock_screener
rtk git add industry_topology/cache.py tests/test_industry_topology.py
rtk git commit -m "feat(industry-topology): add GraphCache with TTL + networkx traversal"
```

---

## Task 5: LLM 关系推理 RelationEngine（提示词 + 解析 + 三道校验）

**Files:**
- Create: `stock_screener/industry_topology/relation_engine.py`
- Test: `stock_screener/tests/test_industry_topology.py`（追加）

**Interfaces:**
- Consumes: `LLMProvider`（`signal_analysis.llm_providers.LLMProvider`，有 `complete_json(system_prompt, user_prompt, json_schema)`）、`NodeResolver.resolve_many`（校验 peer 代码）、`RelationType`/`Direction`
- Produces:
  - `RelationEngine(resolver, llm_provider).infer(code, name, market, sector) -> List[CachedRelation]`
  - 失败抛 `RelationEngineError`（service 捕获做故障隔离）
  - 空结果返回 `[]`（service 写 is_empty 缓存）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_industry_topology.py`:
```python
import json
from industry_topology.relation_engine import RelationEngine, RelationEngineError


class FakeLLMProvider:
    name = "fake"
    is_available = True
    def __init__(self, payload): self.payload = payload
    def complete_json(self, *, system_prompt, user_prompt, json_schema):
        return self.payload


class FakeResolver:
    def resolve_many(self, codes, market):
        return {c: {"code": c, "name": f"公司{c}", "market": market, "sector": "板块", "market_cap": 1e10, "pct_chg": 1.0} for c in codes}


class TestRelationEngine(unittest.TestCase):
    def test_parse_valid_json(self):
        payload = {"items": [
            {"code": "300308", "name": "中际旭创", "market": "A", "direction": "upstream", "relation": "supplier", "evidence": "提供光模块"},
            {"code": "601138", "name": "工业富联", "market": "A", "direction": "downstream", "relation": "odm", "evidence": "AI服务器代工"},
        ]}
        eng = RelationEngine(FakeResolver(), FakeLLMProvider(payload))
        rels = eng.infer("US.NVDA", "英伟达", "US", "半导体")
        self.assertEqual(len(rels), 2)
        self.assertEqual(rels[0].peer_code, "300308")
        self.assertEqual(rels[0].direction.value, "upstream")

    def test_invalid_json_raises(self):
        eng = RelationEngine(FakeResolver(), FakeLLMProvider({"not_items": []}))
        with self.assertRaises(RelationEngineError):
            eng.infer("US.NVDA", "英伟达", "US", "半导体")

    def test_out_of_vocab_relation_falls_back_to_other(self):
        payload = {"items": [
            {"code": "300308", "name": "中际旭创", "market": "A", "direction": "upstream", "relation": "mystery_rel", "evidence": "未知"},
        ]}
        eng = RelationEngine(FakeResolver(), FakeLLMProvider(payload))
        rels = eng.infer("US.NVDA", "英伟达", "US", "半导体")
        self.assertEqual(rels[0].relation, RelationType.OTHER)

    def test_dedup_same_source_peer_relation(self):
        payload = {"items": [
            {"code": "300308", "name": "中际旭创", "market": "A", "direction": "upstream", "relation": "supplier", "evidence": "a"},
            {"code": "300308", "name": "中际旭创", "market": "A", "direction": "upstream", "relation": "supplier", "evidence": "b"},
        ]}
        eng = RelationEngine(FakeResolver(), FakeLLMProvider(payload))
        rels = eng.infer("US.NVDA", "英伟达", "US", "半导体")
        self.assertEqual(len(rels), 1)

    def test_conflicting_direction_keeps_both(self):
        payload = {"items": [
            {"code": "300308", "name": "中际旭创", "market": "A", "direction": "upstream", "relation": "supplier", "evidence": "a"},
            {"code": "300308", "name": "中际旭创", "market": "A", "direction": "downstream", "relation": "customer", "evidence": "b"},
        ]}
        eng = RelationEngine(FakeResolver(), FakeLLMProvider(payload))
        rels = eng.infer("US.NVDA", "英伟达", "US", "半导体")
        self.assertEqual(len(rels), 2)

    def test_empty_result_returns_empty_list(self):
        eng = RelationEngine(FakeResolver(), FakeLLMProvider({"items": []}))
        rels = eng.infer("US.NVDA", "英伟达", "US", "半导体")
        self.assertEqual(rels, [])
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd stock_screener && PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m unittest tests.test_industry_topology.TestRelationEngine -v`
Expected: FAIL `ImportError`

- [ ] **Step 3: 写最小实现**

`stock_screener/industry_topology/relation_engine.py`:
```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 产业链关系推理 + 解析校验。不含行情。"""
from __future__ import annotations

from typing import Any, List

from .models import CachedRelation, Direction, RelationType

_VALID_RELATIONS = {r.value for r in RelationType if r != RelationType.OTHER}
_VALID_DIRECTIONS = {d.value for d in Direction}

_RELATION_CN = {
    "supplier": "供应商", "raw_material": "原材料", "equipment": "设备", "foundry_packaging": "代工封测",
    "customer": "客户", "odm": "代工", "distributor": "分销", "application": "应用场景",
    "competitor": "竞品", "substitute": "替代品",
}


class RelationEngineError(Exception):
    pass


_SYSTEM_PROMPT = "你是产业分析专家。给定一只股票，推理其产业链上下游及同业公司。严格输出 JSON，不要解释。"

_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "name": {"type": "string"},
                    "market": {"type": "string"},
                    "direction": {"type": "string", "enum": ["upstream", "downstream", "peer"]},
                    "relation": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["code", "name", "market", "direction", "relation", "evidence"],
            },
        }
    },
    "required": ["items"],
}


class RelationEngine:
    def __init__(self, resolver: Any, llm_provider: Any):
        self.resolver = resolver
        self.llm = llm_provider

    def infer(self, code: str, name: str, market: str, sector: str) -> List[CachedRelation]:
        user_prompt = self._user_prompt(name, market, code, sector)
        try:
            payload = self.llm.complete_json(
                system_prompt=_SYSTEM_PROMPT, user_prompt=user_prompt, json_schema=_JSON_SCHEMA,
            )
        except Exception as exc:
            raise RelationEngineError(f"LLM 调用失败: {exc}") from exc

        items = self._extract_items(payload)
        if items is None:
            # 重试一次
            try:
                payload = self.llm.complete_json(
                    system_prompt=_SYSTEM_PROMPT, user_prompt=user_prompt, json_schema=_JSON_SCHEMA,
                )
                items = self._extract_items(payload)
            except Exception as exc:
                raise RelationEngineError(f"LLM JSON 解析重试失败: {exc}") from exc
        if items is None:
            raise RelationEngineError("LLM 返回非预期 JSON 结构")

        relations: List[CachedRelation] = []
        seen = set()
        for it in items:
            peer_code = str(it.get("code", "")).strip()
            peer_name = str(it.get("name", "")).strip()
            peer_market = str(it.get("market", "")).strip().upper() or "A"
            direction_raw = str(it.get("direction", "")).strip()
            relation_raw = str(it.get("relation", "")).strip()
            evidence = str(it.get("evidence", "")).strip()
            if not peer_code:
                continue
            direction = Direction(direction_raw) if direction_raw in _VALID_DIRECTIONS else Direction.PEER
            relation = RelationType(relation_raw) if relation_raw in _VALID_RELATIONS else RelationType.OTHER
            # 去重：(peer_code, relation)
            key = (peer_code, relation.value)
            if key in seen:
                continue
            seen.add(key)
            relations.append(CachedRelation(
                source_code=code, source_market=market,
                peer_code=peer_code, peer_market=peer_market, peer_name=peer_name,
                relation=relation, direction=direction, evidence=evidence,
                expires_at=None, is_empty=False,
            ))
        return relations

    @staticmethod
    def _extract_items(payload: Any):
        if isinstance(payload, dict) and "items" in payload and isinstance(payload["items"], list):
            return payload["items"]
        return None

    @staticmethod
    def _user_prompt(name: str, market: str, code: str, sector: str) -> str:
        return (
            f"公司：{name}（{market}市场，代码{code}）\n"
            f"所属板块：{sector}\n\n"
            f"任务：列出最多 50 家与该公司有明确产业关系的上市公司，要求：\n"
            f"1. 关系必须真实、可溯源（凭产业链事实，不要编造公司）。\n"
            f"2. 优先覆盖上游(供应商/原材料/设备/代工封测)与下游(客户/ODM/分销/应用场景)，可含少量同业竞品。\n"
            f"3. 对方须为真实上市公司，给出股票代码与简称；代码不确定时宁可不列。\n"
            f"4. 每条给方向(upstream/downstream/peer)、归一化关系标签、一句话依据。\n"
            f"严格输出 JSON：{{\"items\":[{{\"code\":\"\",\"name\":\"\",\"market\":\"\",\"direction\":\"\",\"relation\":\"\",\"evidence\":\"\"}}]}}"
        )
```

> 注：本 Task 不做 peer_code 真实性校验（resolver.resolve_many 调用放在 service 层统一做），保持 relation_engine 纯解析。校验测试已覆盖去重/超纲/冲突方向/空结果。

- [ ] **Step 4: 运行测试验证通过**

Run: `cd stock_screener && PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m unittest tests.test_industry_topology.TestRelationEngine -v`
Expected: PASS (6 tests)

- [ ] **Step 5: 提交**

```bash
cd stock_screener
rtk git add industry_topology/relation_engine.py tests/test_industry_topology.py
rtk git commit -m "feat(industry-topology): add RelationEngine with LLM inference + validation"
```

---

## Task 6: 编排服务 TopologyService（search/graph/expand/refresh + 硬上限）

**Files:**
- Create: `stock_screener/industry_topology/service.py`
- Test: `stock_screener/tests/test_industry_topology.py`（追加）

**Interfaces:**
- Consumes: `NodeResolver`、`GraphCache`、`RelationEngine`、`models.*`、`signal_analysis.factories.LLMProviderFactory`（建 provider）
- Produces:
  - `TopologyService(db, llm_provider=None).search(keyword, limit=10) -> List[dict]`
  - `TopologyService.build_graph(code, market, depth) -> dict`（center/nodes/edges/stats）
  - `TopologyService.expand(code, market, depth, existing_codes) -> dict`（nodes/edges/stats）
  - `TopologyService.refresh(code, market) -> dict`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_industry_topology.py`:
```python
from industry_topology.service import TopologyService
from industry_topology.relation_engine import RelationEngine


class FakeDB:
    def __init__(self): self.conn = MagicMock()
    def search_stocks_by_name(self, market, keyword, limit=10):
        return [{"code": "US.NVDA", "name": "英伟达", "sector": "半导体", "industry": "半导体"}]
    def get_stocks_by_codes(self, market, codes, include_fundamentals=True):
        return [{"code": c, "name": f"公司{c}", "sector": "板块", "industry": "板块", "market_cap": 1e10} for c in codes]


class FakeResolver2:
    def resolve(self, code, market):
        return {"code": code, "name": f"公司{code}", "market": market, "sector": "板块", "market_cap": 1e10, "pct_chg": 1.5}
    def resolve_many(self, codes, market):
        return {c: {"code": c, "name": f"公司{c}", "market": market, "sector": "板块", "market_cap": 1e10, "pct_chg": 1.0} for c in codes}


class FakeCache:
    def __init__(self, hit_map): self.hit_map = hit_map; self.saved = []
    def get_relations(self, code, market):
        return self.hit_map.get(code)
    def save_relations(self, code, market, rels, provider, model, ttl_days=7):
        self.saved.append((code, rels))
    def build_graph(self, cached_map): import networkx as nx; g = nx.DiGraph(); [g.add_edge(s, r.peer_code) for s, rels in cached_map.items() for r in rels if not r.is_empty]; return g
    def reachable_within(self, g, s, d): return list({s} | {r.peer_code for r in self.hit_map.get(s, [])})


class TestTopologyService(unittest.TestCase):
    def test_search(self):
        svc = TopologyService(FakeDB(), llm_provider=FakeLLMProvider({"items": []}))
        res = svc.search("英伟达")
        self.assertEqual(res[0]["code"], "US.NVDA")

    def test_build_graph_first_time_triggers_llm(self):
        cache = FakeCache({})
        eng = RelationEngine(FakeResolver2(), FakeLLMProvider({"items": [
            {"code": "300308", "name": "中际旭创", "market": "A", "direction": "upstream", "relation": "supplier", "evidence": "光模块"}
        ]}))
        svc = TopologyService(FakeDB(), llm_provider=FakeLLMProvider({"items": []}))
        svc.cache = cache; svc.engine = eng; svc.resolver = FakeResolver2()
        result = svc.build_graph("US.NVDA", "US", depth=1)
        self.assertEqual(result["stats"]["llm_calls"], 1)
        self.assertGreater(len(result["nodes"]), 1)

    def test_build_graph_cached_zero_llm(self):
        cache = FakeCache({"US.NVDA": [_make_relation("300308")]})
        svc = TopologyService(FakeDB(), llm_provider=FakeLLMProvider({"items": []}))
        svc.cache = cache; svc.resolver = FakeResolver2()
        # engine 不该被调用：给一个会抛的 fake
        svc.engine = RelationEngine(FakeResolver2(), type("Boom",(),{"complete_json": lambda self,**k: (_ for _ in ()).throw(Exception("不该调"))})())
        result = svc.build_graph("US.NVDA", "US", depth=1)
        self.assertEqual(result["stats"]["llm_calls"], 0)

    def test_llm_failure_isolated(self):
        cache = FakeCache({})
        boom_engine = RelationEngine(FakeResolver2(), type("Boom",(),{"complete_json": lambda self,**k: (_ for _ in ()).throw(Exception("LLM挂"))})())
        svc = TopologyService(FakeDB(), llm_provider=FakeLLMProvider({"items": []}))
        svc.cache = cache; svc.engine = boom_engine; svc.resolver = FakeResolver2()
        result = svc.build_graph("US.NVDA", "US", depth=1)
        # 中心节点仍返回，edges 空
        self.assertEqual(result["stats"].get("error"), "llm_failed")
        self.assertEqual(len(result["nodes"]), 1)
        self.assertEqual(result["nodes"][0]["code"], "US.NVDA")
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd stock_screener && PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m unittest tests.test_industry_topology.TestTopologyService -v`
Expected: FAIL `ImportError`

- [ ] **Step 3: 写最小实现**

`stock_screener/industry_topology/service.py`:
```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""编排：搜索 → 首次拓扑 → 按需展开 → 刷新。故障隔离 + LLM 硬上限。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from market import normalize_market

from .cache import GraphCache
from .models import Direction, RelationType, TopologyEdge, TopologyNode
from .relation_engine import RelationEngine, RelationEngineError
from .resolver import NodeResolver, compute_size_level, format_market_cap

_LLM_HARD_LIMIT = 20


class TopologyService:
    def __init__(self, db: Any, llm_provider: Any = None):
        self.db = db
        self.resolver = NodeResolver(db)
        self.cache = GraphCache(db)
        if llm_provider is None:
            llm_provider = self._default_llm()
        self.engine = RelationEngine(self.resolver, llm_provider) if llm_provider else None
        self._llm_calls = 0  # 会话累计

    @staticmethod
    def _default_llm():
        try:
            from signal_analysis.factories import LLMProviderFactory
            from signal_analysis.models import AnalysisSettings
            return LLMProviderFactory.from_env(AnalysisSettings())
        except Exception:
            return None

    # ── 搜索 ──
    def search(self, keyword: str, limit: int = 10) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for market in ("A", "HK", "US"):
            try:
                rows = self.db.search_stocks_by_name(market, keyword, limit) if hasattr(self.db, "search_stocks_by_name") else []
            except Exception:
                rows = []
            for r in rows:
                results.append({"code": r["code"], "name": r.get("name", ""), "market": market,
                                "sector": r.get("sector") or r.get("industry") or ""})
                if len(results) >= limit:
                    return results
        return results

    # ── 首次拓扑 ──
    def build_graph(self, code: str, market: str, depth: int = 3) -> Dict[str, Any]:
        market = normalize_market(market)
        return self._traverse(code, market, depth, existing_codes=None, is_center=True)

    # ── 按需展开 ──
    def expand(self, code: str, market: str, depth: int = 2, existing_codes: Optional[List[str]] = None) -> Dict[str, Any]:
        market = normalize_market(market)
        return self._traverse(code, market, depth, existing_codes=existing_codes or [], is_center=False)

    # ── 刷新 ──
    def refresh(self, code: str, market: str) -> Dict[str, Any]:
        market = normalize_market(market)
        # 强制重算：先取行情（中心节点），再重算关系
        center_info = self.resolver.resolve(code, market)
        relations = self._infer_with_limit(code, market, center_info.get("name", ""), center_info.get("sector", ""))
        nodes, edges = self._assemble(code, market, [code], relations, existing_codes=[])
        return {"nodes": nodes, "edges": edges, "stats": {"llm_calls": self._llm_calls}}

    # ── 内部遍历 ──
    def _traverse(self, code: str, market: str, depth: int, existing_codes: Optional[List[str]], is_center: bool) -> Dict[str, Any]:
        existing_set = set(existing_codes) if existing_codes else set()
        center_info = self.resolver.resolve(code, market)
        center_name = center_info.get("name", "") or code
        center_sector = center_info.get("sector", "") or "--"

        # 收集缓存
        cached_map: Dict[str, List] = {}
        stale_sources: List[str] = []
        cached_for_center = self.cache.get_relations(code, market)
        if cached_for_center is not None:
            cached_map[code] = cached_for_center
        else:
            stale_sources.append(code)

        # 对已缓存节点的子关系也尝试加载（depth>1 时）
        if depth > 1 and cached_for_center:
            for r in cached_for_center:
                if r.is_empty:
                    continue
                sub = self.cache.get_relations(r.peer_code, r.peer_market)
                if sub is not None:
                    cached_map[r.peer_code] = sub
                else:
                    stale_sources.append(r.peer_code)

        # LLM 补算 stale_sources（受硬上限）
        for src in list(stale_sources):
            rels = self._infer_with_limit(src, market, center_name if src == code else "", center_sector if src == code else "--")
            if rels is not None:
                cached_map[src] = rels
                # 写缓存
                try:
                    self.cache.save_relations(src, market, rels, provider="deepseek", model="v4")
                except Exception:
                    pass

        graph = self.cache.build_graph(cached_map)
        reachable = set(self.cache.reachable_within(graph, code, depth))
        # 取这些节点的行情
        node_codes = [c for c in reachable if c not in existing_set]
        infos = self.resolver.resolve_many(node_codes, market) if node_codes else {}
        infos[code] = center_info

        nodes, edges = self._assemble(code, market, list(reachable), cached_map, existing_set, is_center=is_center)
        stats = {"llm_calls": self._llm_calls, "cached_nodes": len(cached_map), "stale_nodes": len(stale_sources), "depth": depth}
        if not cached_map and self._llm_calls == 0:
            stats["error"] = "llm_failed"
        return {"center": self._to_node(center_info, code, market, expanded=True, is_center=True, stale=False),
                "nodes": nodes, "edges": edges, "stats": stats}

    def _infer_with_limit(self, code: str, market: str, name: str, sector: str):
        if self._llm_calls >= _LLM_HARD_LIMIT or self.engine is None:
            return None
        try:
            rels = self.engine.infer(code, name, market, sector)
            self._llm_calls += 1
            return rels
        except RelationEngineError:
            return None
        except Exception:
            return None

    def _assemble(self, center_code, market, codes, cached_map, existing_set, is_center=False):
        infos = self.resolver.resolve_many([c for c in codes if c != center_code], market) if codes else {}
        center_info = self.resolver.resolve(center_code, market)
        infos[center_code] = center_info
        nodes = []
        for c in codes:
            if c in existing_set and not is_center:
                continue
            info = infos.get(c, {"code": c, "name": "", "market": market, "sector": "--", "market_cap": None, "pct_chg": None})
            nodes.append(self._to_node(info, c, market, expanded=c in cached_map, is_center=(c == center_code and is_center), stale=False))
        edges = []
        for src, rels in cached_map.items():
            for r in rels:
                if r.is_empty:
                    continue
                if r.peer_code not in codes and src not in codes:
                    continue
                rel_cn = _RELATION_CN.get(r.relation.value, r.relation.value)
                label = f"{rel_cn}·{r.evidence}" if r.evidence else rel_cn
                edges.append({
                    "source": src, "target": r.peer_code,
                    "direction": r.direction.value, "relation": r.relation.value,
                    "label": label[:40], "evidence": r.evidence,
                })
        return nodes, edges

    @staticmethod
    def _to_node(info, code, market, expanded, is_center, stale):
        market_cap = info.get("market_cap")
        return {
            "code": code, "name": info.get("name", "") or code,
            "market": info.get("market", market),
            "sector": info.get("sector", "--") or "--",
            "pct_chg": info.get("pct_chg"),
            "market_cap_str": format_market_cap(market_cap),
            "size_level": 6 if is_center else compute_size_level(market_cap),
            "expanded": expanded, "stale": stale, "is_center": is_center,
        }


_RELATION_CN = {
    "supplier": "供应商", "raw_material": "原材料", "equipment": "设备", "foundry_packaging": "代工封测",
    "customer": "客户", "odm": "代工", "distributor": "分销", "application": "应用场景",
    "competitor": "竞品", "substitute": "替代品", "other": "其他",
}
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd stock_screener && PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m unittest tests.test_industry_topology.TestTopologyService -v`
Expected: PASS (4 tests)

> 如 `_traverse` 内的缓存遍历在 mock 下行为与预期有偏差，按实际测试结果微调 `_traverse` 中 `cached_for_center` 分支逻辑——以测试通过为准。这是实现阶段允许的对齐点。

- [ ] **Step 5: 提交**

```bash
cd stock_screener
rtk git add industry_topology/service.py tests/test_industry_topology.py
rtk git commit -m "feat(industry-topology): add TopologyService orchestration with LLM hard limit"
```

---

## Task 7: FastAPI 路由 web/topology.py + 注册到 main.py

**Files:**
- Create: `stock_screener/web/topology.py`
- Modify: `stock_screener/web/main.py`

**Interfaces:**
- Consumes: `TopologyService`、`get_db`（`from .auth import get_db`）、`MarketDatabase`（`init_industry_topology_schema`）
- Produces: 四个端点 `GET /api/topology/search`、`POST /api/topology/graph`、`POST /api/topology/expand`、`POST /api/topology/refresh`

- [ ] **Step 1: 写路由实现**

`stock_screener/web/topology.py`:
```python
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from industry_topology.service import TopologyService

from .auth import get_db

router = APIRouter(prefix="/api/topology", tags=["topology"])


def get_topology_service(db=Depends(get_db)) -> TopologyService:
    return TopologyService(db)


class GraphRequest(BaseModel):
    code: str
    market: str
    depth: int = 3


class ExpandRequest(BaseModel):
    code: str
    market: str
    depth: int = 2
    existing_codes: List[str] = []


class RefreshRequest(BaseModel):
    code: str
    market: str


@router.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = Query(10, le=50),
           svc: TopologyService = Depends(get_topology_service)):
    return {"ok": True, "data": svc.search(q, limit)}


@router.post("/graph")
def graph(req: GraphRequest, svc: TopologyService = Depends(get_topology_service)):
    return {"ok": True, "data": svc.build_graph(req.code, req.market, req.depth)}


@router.post("/expand")
def expand(req: ExpandRequest, svc: TopologyService = Depends(get_topology_service)):
    return {"ok": True, "data": svc.expand(req.code, req.market, req.depth, req.existing_codes)}


@router.post("/refresh")
def refresh(req: RefreshRequest, svc: TopologyService = Depends(get_topology_service)):
    return {"ok": True, "data": svc.refresh(req.code, req.market)}
```

- [ ] **Step 2: 注册到 main.py**

在 `stock_screener/web/main.py` 顶部 import 区（`from .stock_terminal import router as stock_terminal_router` 下方）加：
```python
from .topology import router as topology_router
```

在 `app.include_router(stock_terminal_router)` 下方加：
```python
app.include_router(topology_router)
```

在 startup 钩子（`db.init_web_schema()` 调用处附近）加 `db.init_industry_topology_schema()`。若 `main.py` 有 `@app.on_event("startup")` 或 lifespan，在其中加；若 `init_web_schema` 是在模块顶层调用，紧随其后加 `MarketDatabase(mysql_config_from_env()).init_industry_topology_schema()`——实现时按 main.py 实际结构定位。

- [ ] **Step 3: 三模式验证**

Run:
```bash
cd stock_screener
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -c "from web.main import app; print([r.path for r in app.routes if 'topology' in r.path])"
```
Expected: 输出含 `/api/topology/search`、`/api/topology/graph`、`/api/topology/expand`、`/api/topology/refresh`

- [ ] **Step 4: 提交**

```bash
cd stock_screener
rtk git add web/topology.py web/main.py
rtk git commit -m "feat(industry-topology): add FastAPI routes /api/topology/*"
```

---

## Task 8: 前端 types + api + 安装 dagre

**Files:**
- Create: `stock_screener/web_frontend/src/features/industryTopology/types.ts`
- Create: `stock_screener/web_frontend/src/features/industryTopology/topologyApi.ts`
- Modify: `stock_screener/web_frontend/package.json`

**Interfaces:**
- Consumes: `api.ts` 的 `api<T>()`（已存在）
- Produces: TS 类型 + `topologyApi.search/graph/expand/refresh`

- [ ] **Step 1: 安装 dagre**

Run:
```bash
cd stock_screener/web_frontend
rtk npm install dagre@0.8.5 @types/dagre@0.7.52
```
Expected: `package.json` 出现 dagre 依赖。

- [ ] **Step 2: 写 types.ts**

`stock_screener/web_frontend/src/features/industryTopology/types.ts`:
```ts
export type Market = 'HK' | 'US' | 'A'
export type Direction = 'upstream' | 'downstream' | 'peer'

export interface TopologyNodeData {
  code: string
  name: string
  market: Market
  sector: string
  pct_chg: number | null
  market_cap_str: string
  size_level: 1 | 2 | 3 | 4 | 5 | 6
  expanded: boolean
  stale: boolean
  is_center: boolean
}

export interface TopologyEdgeData {
  source: string
  target: string
  direction: Direction
  relation: string
  label: string
  evidence: string
}

export interface TopologyStats {
  llm_calls: number
  cached_nodes?: number
  stale_nodes?: number
  depth?: number
  error?: string
}

export interface TopologyGraph {
  center: TopologyNodeData
  nodes: TopologyNodeData[]
  edges: TopologyEdgeData[]
  stats: TopologyStats
}

export interface ExpandResult {
  nodes: TopologyNodeData[]
  edges: TopologyEdgeData[]
  stats: TopologyStats
}

export interface SearchResult {
  code: string
  name: string
  market: Market
  sector: string
}
```

- [ ] **Step 3: 写 topologyApi.ts**

`stock_screener/web_frontend/src/features/industryTopology/topologyApi.ts`:
```ts
import { api } from '../../api'
import type { ExpandResult, SearchResult, TopologyGraph } from './types'

export const topologyApi = {
  search: (q: string, limit = 10) =>
    api<{ ok: true; data: SearchResult[] }>(`/api/topology/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  graph: (code: string, market: string, depth: number) =>
    api<{ ok: true; data: TopologyGraph }>('/api/topology/graph', {
      method: 'POST',
      body: JSON.stringify({ code, market, depth }),
    }),
  expand: (code: string, market: string, depth: number, existingCodes: string[]) =>
    api<{ ok: true; data: ExpandResult }>('/api/topology/expand', {
      method: 'POST',
      body: JSON.stringify({ code, market, depth, existing_codes: existingCodes }),
    }),
  refresh: (code: string, market: string) =>
    api<{ ok: true; data: ExpandResult }>('/api/topology/refresh', {
      method: 'POST',
      body: JSON.stringify({ code, market }),
    }),
}
```

> 注：`api.ts` 的相对路径——实现时核实 `api.ts` 是否在 `src/api.ts`（早前确认是 `src/api.ts`，所以从 `features/industryTopology/` 走 `../../api`）。如路径不符以实际为准。

- [ ] **Step 4: TypeScript 编译验证**

Run:
```bash
cd stock_screener/web_frontend
rtk npx tsc --noEmit
```
Expected: 无类型错误（仅 industryTopology 的新文件）。

- [ ] **Step 5: 提交**

```bash
cd stock_screener
rtk git add web_frontend/src/features/industryTopology/types.ts web_frontend/src/features/industryTopology/topologyApi.ts web_frontend/package.json web_frontend/package-lock.json
rtk git commit -m "feat(industry-topology): add frontend types + api + dagre dep"
```

---

## Task 9: 前端搜索下拉 StockSearchInput

**Files:**
- Create: `stock_screener/web_frontend/src/features/industryTopology/StockSearchInput.tsx`

**Interfaces:**
- Consumes: `topologyApi.search`、`SearchResult`
- Produces: `<StockSearchInput onSelect={(s) => ...} />`

- [ ] **Step 1: 写组件**

`stock_screener/web_frontend/src/features/industryTopology/StockSearchInput.tsx`:
```tsx
import { useEffect, useRef, useState } from 'react'
import { topologyApi } from './topologyApi'
import type { SearchResult } from './types'

interface Props {
  onSelect: (s: SearchResult) => void
  selected?: SearchResult | null
}

export function StockSearchInput({ onSelect, selected }: Props) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<SearchResult[]>([])
  const [loading, setLoading] = useState(false)
  const boxRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!q.trim()) { setItems([]); return }
    setLoading(true)
    const t = setTimeout(async () => {
      try {
        const res = await topologyApi.search(q.trim())
        setItems(res.data)
        setOpen(true)
      } catch { setItems([]) } finally { setLoading(false) }
    }, 300)
    return () => clearTimeout(t)
  }, [q])

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  return (
    <div className="topo-search" ref={boxRef}>
      <input
        className="topo-search-input"
        placeholder="输入股票代码或名称"
        value={selected ? `${selected.name} (${selected.code})` : q}
        onChange={(e) => { setQ(e.target.value); if (selected) { /* 允许重新搜索 */ } }}
        onKeyDown={(e) => { if (e.key === 'Escape') setOpen(false) }}
      />
      {open && (
        <ul className="topo-search-dropdown">
          {loading && <li>搜索中…</li>}
          {!loading && items.length === 0 && <li>无匹配</li>}
          {!loading && items.map((s) => (
            <li key={`${s.market}:${s.code}`} onClick={() => { onSelect(s); setOpen(false); setQ('') }}>
              <span className="topo-search-name">{s.name}</span>
              <span className="topo-search-code">{s.code}</span>
              <span className="topo-search-sector">{s.sector}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
```

- [ ] **Step 2: tsc 验证**

Run: `cd stock_screener/web_frontend && rtk npx tsc --noEmit`
Expected: 无错误。

- [ ] **Step 3: 提交**

```bash
cd stock_screener
rtk git add web_frontend/src/features/industryTopology/StockSearchInput.tsx
rtk git commit -m "feat(industry-topology): add StockSearchInput with debounce dropdown"
```

---

## Task 10: 前端自定义节点 TopologyNode

**Files:**
- Create: `stock_screener/web_frontend/src/features/industryTopology/TopologyNode.tsx`

**Interfaces:**
- Consumes: `TopologyNodeData`、React Flow `NodeProps`
- Produces: `<TopologyNode data={...} />`（注册为 React Flow nodeType `'topology'`）

- [ ] **Step 1: 写组件**

`stock_screener/web_frontend/src/features/industryTopology/TopologyNode.tsx`:
```tsx
import { memo } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { TopologyNodeData } from './types'

const SIZE_MAP: Record<number, { w: number; h: number }> = {
  1: { w: 120, h: 72 }, 2: { w: 150, h: 88 }, 3: { w: 180, h: 104 },
  4: { w: 210, h: 120 }, 5: { w: 240, h: 136 }, 6: { w: 270, h: 152 },
}

function colorFor(pct: number | null): { border: string; bg: string; text: string } {
  if (pct === null) return { border: '#6b7280', bg: '#f3f4f6', text: '#6b7280' }
  if (pct > 0) return { border: '#16a34a', bg: '#dcfce7', text: '#16a34a' }
  if (pct < 0) return { border: '#dc2626', bg: '#fee2e2', text: '#dc2626' }
  return { border: '#6b7280', bg: '#f3f4f6', text: '#6b7280' }
}

export const TopologyNode = memo(function TopologyNode({ data, id }: NodeProps) {
  const d = data as TopologyNodeData
  const sz = SIZE_MAP[d.size_level] || SIZE_MAP[1]
  const c = colorFor(d.pct_chg)
  const pctStr = d.pct_chg === null ? '--' : `${d.pct_chg > 0 ? '+' : ''}${d.pct_chg}%`
  return (
    <div
      className="topo-node"
      style={{ width: sz.w, height: sz.h, borderColor: c.border, backgroundColor: c.bg,
               boxShadow: d.is_center ? '0 0 0 3px #fbbf24' : undefined }}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div className="topo-node-top">
        <span className="topo-node-sector">{d.sector}</span>
        <span className="topo-node-pct" style={{ color: c.text }}>{pctStr}</span>
      </div>
      <div className="topo-node-name">{d.name || d.code}</div>
      <div className="topo-node-bottom">
        <span className="topo-node-code">{d.code}</span>
        <span className="topo-node-cap">{d.market_cap_str}</span>
      </div>
      {!d.is_center && (
        <button className="topo-node-expand" disabled={d.expanded} data-node-id={id}>
          {d.expanded ? '已展开' : '展开↗'}
        </button>
      )}
      {d.stale && <span className="topo-node-stale">数据较旧</span>}
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  )
})
```

- [ ] **Step 2: tsc 验证**

Run: `cd stock_screener/web_frontend && rtk npx tsc --noEmit`
Expected: 无错误。

- [ ] **Step 3: 提交**

```bash
cd stock_screener
rtk git add web_frontend/src/features/industryTopology/TopologyNode.tsx
rtk git commit -m "feat(industry-topology): add TopologyNode custom card with size/color"
```

---

## Task 11: 前端画布 TopologyCanvas（React Flow + dagre 布局）

**Files:**
- Create: `stock_screener/web_frontend/src/features/industryTopology/TopologyCanvas.tsx`

**Interfaces:**
- Consumes: `@xyflow/react`、`dagre`、`TopologyNode`、`TopologyNodeData`/`TopologyEdgeData`
- Produces: `<TopologyCanvas nodes edges onExpand={...} />`，dagre 布局，边标签胶囊。

- [ ] **Step 1: 写组件**

`stock_screener/web_frontend/src/features/industryTopology/TopologyCanvas.tsx`:
```tsx
import { useMemo } from 'react'
import {
  ReactFlow, Background, Controls, MiniMap, EdgeLabelRenderer, MarkerType,
  useNodesState, useEdgesState, type Node, type Edge,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import dagre from 'dagre'
import { TopologyNode } from './TopologyNode'
import type { TopologyEdgeData, TopologyNodeData } from './types'

const EDGE_COLOR: Record<string, string> = {
  upstream: '#3b82f6', downstream: '#f59e0b', peer: '#8b5cf6',
}

function layout(rawNodes: Node[], rawEdges: Edge[]): { nodes: Node[]; edges: Edge[] } {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'LR', nodesep: 60, ranksep: 80 })
  g.setDefaultEdgeLabel(() => ({}))
  rawNodes.forEach((n) => {
    const sz = (n.data as TopologyNodeData)?.size_level || 1
    const w = [0, 120, 150, 180, 210, 240, 270][sz] || 120
    const h = [0, 72, 88, 104, 120, 136, 152][sz] || 72
    g.setNode(n.id, { width: w, height: h })
  })
  rawEdges.forEach((e) => g.setEdge(e.source, e.target))
  dagre.layout(g)
  const nodes = rawNodes.map((n) => {
    const pos = g.node(n.id)
    return { ...n, position: { x: pos.x - (pos.width / 2), y: pos.y - (pos.height / 2) } }
  })
  return { nodes, edges: rawEdges }
}

interface Props {
  rawNodes: Node<TopologyNodeData>[]
  rawEdges: Edge[]
  onExpand: (code: string, market: string) => void
}

export function TopologyCanvas({ rawNodes, rawEdges, onExpand }: Props) {
  const [nodes, , onNodesChange] = useNodesState(rawNodes)
  const [edges, , onEdgesChange] = useEdgesState(rawEdges)

  const laid = useMemo(() => layout(rawNodes, rawEdges), [rawNodes, rawEdges])

  const styledEdges: Edge[] = laid.edges.map((e) => {
    const d = (e.data as TopologyEdgeData) || ({} as TopologyEdgeData)
    const color = EDGE_COLOR[d.direction] || '#94a3b8'
    return {
      ...e,
      markerEnd: { type: MarkerType.ArrowClosed, color },
      style: { stroke: color, strokeWidth: 2 },
      label: d.label,
      labelStyle: { fill: '#1f2937', fontWeight: 500, fontSize: 11 },
      labelBgStyle: { fill: '#ffffff' },
      labelBgPadding: [4, 2],
      labelBgBorderRadius: 4,
    }
  })

  return (
    <div className="topo-canvas" style={{ height: '70vh' }}
         onClick={(ev) => {
           const t = ev.target as HTMLElement
           const btn = t.closest('.topo-node-expand') as HTMLElement | null
           if (btn && !btn.disabled) {
             const id = btn.getAttribute('data-node-id') || ''
             const node = laid.nodes.find((n) => n.id === id)
             if (node) onExpand(id, (node.data as TopologyNodeData).market)
           }
         }}>
      <ReactFlow
        nodes={laid.nodes} edges={styledEdges}
        onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
        nodeTypes={{ topology: TopologyNode }}
        fitView fitViewOptions={{ padding: 0.2 }}
      >
        <Background /> <Controls /> <MiniMap />
      </ReactFlow>
    </div>
  )
}
```

- [ ] **Step 2: tsc 验证**

Run: `cd stock_screener/web_frontend && rtk npx tsc --noEmit`
Expected: 无错误。

- [ ] **Step 3: 提交**

```bash
cd stock_screener
rtk git add web_frontend/src/features/industryTopology/TopologyCanvas.tsx
rtk git commit -m "feat(industry-topology): add TopologyCanvas with dagre layout"
```

---

## Task 12: 前端入口容器 IndustryTopologyPanel + main.tsx 接入

**Files:**
- Create: `stock_screener/web_frontend/src/features/industryTopology/IndustryTopologyPanel.tsx`
- Modify: `stock_screener/web_frontend/src/main.tsx`
- Modify: `stock_screener/web_frontend/src/styles.css`

**Interfaces:**
- Consumes: `StockSearchInput`、`TopologyCanvas`、`topologyApi`、types；`main.tsx` 的 `useState('page')` 导航
- Produces: `<IndustryTopologyPanel />` + 导航按钮"产业拓扑" + `page === 'topology'` 渲染分支

- [ ] **Step 1: 写 Panel**

`stock_screener/web_frontend/src/features/industryTopology/IndustryTopologyPanel.tsx`:
```tsx
import { useCallback, useState } from 'react'
import type { Edge, Node } from '@xyflow/react'
import { StockSearchInput } from './StockSearchInput'
import { TopologyCanvas } from './TopologyCanvas'
import { topologyApi } from './topologyApi'
import type { SearchResult, TopologyNodeData } from './types'

export function IndustryTopologyPanel() {
  const [selected, setSelected] = useState<SearchResult | null>(null)
  const [depth, setDepth] = useState(3)
  const [nodes, setNodes] = useState<Node<TopologyNodeData>[]>([])
  const [edges, setEdges] = useState<Edge[]>([])
  const [stats, setStats] = useState<string>('')

  const startTopology = useCallback(async () => {
    if (!selected) return
    try {
      const res = await topologyApi.graph(selected.code, selected.market, depth)
      const g = res.data
      setNodes(g.nodes.map((n) => ({ id: n.code, type: 'topology' as const, position: { x: 0, y: 0 }, data: n })))
      setEdges(g.edges.map((e) => ({ id: `${e.source}->${e.target}`, source: e.source, target: e.target, data: e })))
      setStats(`LLM调用 ${g.stats.llm_calls} | 缓存 ${g.stats.cached_nodes ?? 0} | stale ${g.stats.stale_nodes ?? 0}`)
    } catch (e) {
      setStats(`拓扑失败: ${(e as Error).message}`)
    }
  }, [selected, depth])

  const onExpand = useCallback(async (code: string, market: string) => {
    if (!selected) return
    const existing = nodes.map((n) => n.id)
    try {
      const res = await topologyApi.expand(code, market, depth, existing)
      const d = res.data
      setNodes((prev) => {
        const map = new Map(prev.map((n) => [n.id, n]))
        d.nodes.forEach((n) => map.set(n.code, { id: n.code, type: 'topology' as const, position: { x: 0, y: 0 }, data: n }))
        // 标记被展开节点 expanded
        return Array.from(map.values()).map((n) => n.id === code ? { ...n, data: { ...n.data, expanded: true } } : n)
      })
      setEdges((prev) => {
        const set = new Set(prev.map((e) => e.id))
        const merged = [...prev]
        d.edges.forEach((e) => { const id = `${e.source}->${e.target}`; if (!set.has(id)) merged.push({ id, source: e.source, target: e.target, data: e }) })
        return merged
      })
    } catch { /* toast 可后续加 */ }
  }, [selected, nodes, depth])

  const onRefresh = useCallback(async () => {
    if (!selected) return
    try {
      await topologyApi.refresh(selected.code, selected.market)
      await startTopology()
    } catch { /* */ }
  }, [selected, startTopology])

  return (
    <div className="industry-topology">
      <div className="topo-toolbar">
        <StockSearchInput onSelect={setSelected} selected={selected} />
        <label>深度
          <select value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
            {[1, 2, 3, 4, 5].map((d) => <option key={d} value={d}>{d}度</option>)}
          </select>
        </label>
        <button onClick={startTopology} disabled={!selected}>开始拓扑</button>
        <button onClick={onRefresh} disabled={!selected}>刷新关系</button>
        <span className="topo-stats">{stats}</span>
      </div>
      {nodes.length > 0 ? (
        <TopologyCanvas rawNodes={nodes} rawEdges={edges} onExpand={onExpand} />
      ) : <div className="topo-empty">选择股票后点击"开始拓扑"</div>}
    </div>
  )
}
```

- [ ] **Step 2: 接入 main.tsx**

在 `stock_screener/web_frontend/src/main.tsx` import 区（`import { RuleChainEditor } ...` 附近）加：
```tsx
import { IndustryTopologyPanel } from './features/industryTopology/IndustryTopologyPanel'
```

在 nav `<nav>` 块（"规则链"按钮下方）加：
```tsx
<button className={page === 'topology' ? 'active' : ''} onClick={() => setPage('topology')}>产业拓扑</button>
```

在 page 渲染分支（`{page === 'rules' && <Rules />}` 下方）加：
```tsx
{page === 'topology' && <IndustryTopologyPanel />}
```

- [ ] **Step 3: 加样式**

在 `stock_screener/web_frontend/src/styles.css` 末尾追加（最小可用的节点/下拉/工具栏样式）：
```css
.industry-topology { display: flex; flex-direction: column; height: 100%; }
.topo-toolbar { display: flex; gap: 12px; align-items: center; padding: 12px; border-bottom: 1px solid #e5e7eb; }
.topo-search { position: relative; }
.topo-search-input { width: 240px; padding: 6px 10px; border: 1px solid #d1d5db; border-radius: 6px; }
.topo-search-dropdown { position: absolute; top: 100%; left: 0; right: 0; background: #fff; border: 1px solid #d1d5db; border-radius: 6px; max-height: 280px; overflow-y: auto; z-index: 50; margin: 4px 0 0; list-style: none; padding: 0; }
.topo-search-dropdown li { padding: 8px 12px; cursor: pointer; display: flex; gap: 8px; font-size: 13px; }
.topo-search-dropdown li:hover { background: #f3f4f6; }
.topo-search-code { color: #6b7280; }
.topo-search-sector { color: #9ca3af; margin-left: auto; }
.topo-node { border: 2px solid; border-radius: 8px; padding: 8px; display: flex; flex-direction: column; gap: 4px; font-size: 12px; background: #fff; }
.topo-node-top { display: flex; justify-content: space-between; }
.topo-node-sector { color: #6b7280; font-size: 11px; }
.topo-node-pct { font-weight: 600; }
.topo-node-name { font-weight: 700; font-size: 13px; }
.topo-node-bottom { display: flex; justify-content: space-between; color: #4b5563; }
.topo-node-expand { margin-top: 4px; align-self: flex-end; font-size: 11px; padding: 2px 6px; border: 1px solid #d1d5db; border-radius: 4px; background: #fff; cursor: pointer; }
.topo-node-expand:disabled { color: #9ca3af; cursor: default; }
.topo-node-stale { position: absolute; top: -8px; right: -4px; background: #f59e0b; color: #fff; font-size: 10px; padding: 1px 4px; border-radius: 4px; }
.topo-empty { padding: 60px; text-align: center; color: #9ca3af; }
.topo-stats { margin-left: auto; color: #6b7280; font-size: 12px; }
.topo-canvas { flex: 1; }
```

- [ ] **Step 4: tsc + dev server 验证**

Run:
```bash
cd stock_screener/web_frontend
rtk npx tsc --noEmit
rtk npm run dev
```
Expected: tsc 无错误；dev server 启动，浏览器可访问，左侧导航出现"产业拓扑"。

- [ ] **Step 5: 提交**

```bash
cd stock_screener
rtk git add web_frontend/src/features/industryTopology/IndustryTopologyPanel.tsx web_frontend/src/main.tsx web_frontend/src/styles.css
rtk git commit -m "feat(industry-topology): add IndustryTopologyPanel + wire into main.tsx nav"
```

---

## Task 13: 端到端验证（三模式 + 真实跑通）

**Files:**
- 无新文件，验证现有

- [ ] **Step 1: 后端单元测试全跑**

Run: `cd stock_screener && PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m unittest tests.test_industry_topology -v`
Expected: 全部 PASS（models/schema/size/format/cache/relation_engine/service，约 30 tests）。

- [ ] **Step 2: web 模式启动验证**

Run:
```bash
cd stock_screener
rtk grep -n "init_industry_topology_schema" web/main.py  # 确认注册
# 启动 web（按项目惯例，参考 run_moneymanager.sh 或 uvicorn）
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -c "import uvicorn; from web.main import app; uvicorn.run(app, port=8000)" &
sleep 3
rtk curl -s "http://localhost:8000/api/topology/search?q=英伟达&limit=5" | rtk head -5
```
Expected: 返回 `{"ok":true,"data":[...]}`。

- [ ] **Step 3: 桌面模式启动验证**

Run: `cd stock_screener && PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python desktop.py`（短暂启动确认不报错后 Ctrl-C）
Expected: 窗口启动无 import/建表报错。

- [ ] **Step 4: 浏览器手动验证清单**

在 dev server 打开"产业拓扑"：
- [ ] 搜索"英伟达"出下拉，选中后下拉关闭
- [ ] 选深度 3，点"开始拓扑"出图，中心节点黄色描边、节点显示板块/名称/代码/涨跌幅/市值
- [ ] 节点颜色：涨绿跌红
- [ ] 点某节点"展开↗"，新节点和带标签的边增量出现
- [ ] 边标签显示 `关系·依据`，按方向蓝/橙/紫
- [ ] stats 显示 `LLM调用 N | 缓存 N | stale N`
- [ ] 同一股票第二次"开始拓扑"：`LLM调用 0`
- [ ] 行情失败时节点灰色、信息 `--`
- [ ] LLM 失败时中心节点仍在 + stats.error

- [ ] **Step 5: 最终提交**

```bash
cd stock_screener
rtk git add -A
rtk git commit -m "test(industry-topology): e2e verification across 3 modes"
```

---

## Self-Review

**Spec coverage:**
- §1 架构边界 → Task 1-7（models/cache/resolver/engine/service/route）
- §2 公司维度缓存+networkx+TTL+硬上限 → Task 4(cache) + Task 6(service 硬上限)
- §3 LLM 提示词+12 标签+三道校验+空结果缓存 → Task 5(relation_engine)
- §4 节点边视觉（size 6 档/绿涨红跌/五项/边标签）→ Task 10(node) + Task 11(canvas) + styles
- §5 API 四端点 → Task 7
- §6 前端组件 → Task 8-12
- §7 错误处理与测试 → 各 Task 测试 + Task 13 e2e
- 深度可选默认3最大5 → Task 6 service + Task 12 Panel select
- 每节点 50 家 → Task 5 提示词
- 行情实时不缓存 → Task 3 resolver 每次取

**Placeholder scan:** 已排除 TODO/TBD；每步含完整代码。`_traverse` 缓存遍历在 mock 下行为标注了"以测试通过为准对齐"——这是实现阶段允许的微调点，非占位。`main.py` startup 建表调用位置标注"按实际结构定位"——因 main.py 结构复杂，实现时读具体行号确定，指令明确（找 init_web_schema 调用处附近加）。

**Type consistency:** `TopologyNode`/`TopologyEdge` Python dataclass(Task1) ↔ TS types(Task8) 字段名一致（code/name/market/sector/pct_chg/market_cap_str/size_level/expanded/stale/is_center；edge: source/target/direction/relation/label/evidence）。`GraphCache.get_relations/save_relations/build_graph/reachable_within` 在 Task4 定义、Task6 消费，签名一致。`RelationEngine.infer` Task5 定义、Task6 消费，签名一致。`TopologyService.search/build_graph/expand/refresh` Task6 定义、Task7 消费，一致。

**已知实现时需核实的对齐点（非阻塞，已在对应 Task 标注）：**
1. `KlineFetcherFactory.create_fetcher_chain` 签名（Task3 Step3 注）
2. `api.ts` 相对路径 `../../api`（Task8 Step3 注）
3. `main.py` startup 建表调用确切位置（Task7 Step2 注）
4. `_traverse` 缓存遍历在 mock 下的行为对齐（Task6 Step4 注）

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-23-industry-topology-implementation-plan.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**

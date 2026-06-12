# 热点板块热力图重设计

**日期**: 2026-06-12
**状态**: 已批准

## 目标

替换当前基于 LLM 的热点板块发现逻辑，改为 DB 聚合驱动 + 前端 ECharts treemap 渲染。解决三个问题：
1. 所有板块热度 100%（LLM 固定 score=1.0）
2. 成分股数全为 0（LLM 中文名 vs DB 英文名不匹配）
3. 前端 treemap 显示 `name|undefined`

## 架构

```
DB (stock_sector_memberships + stock_pool)
  ├─ HK: FutuOpenD → English sector names → 中文映射
  ├─ US: FutuOpenD → English sector names → 中文映射
  └─ A:  DB聚合 + Akshare 补充 → Chinese names
       ↓
  GET /api/sectors/hot  (DB聚合 + 5min 内存缓存)
       ↓
  ECharts Treemap (自绘，可点击 → 加载成分股)
```

## 热度计算公式

三个维度加权合成 0-100 分：

```
heat_score = 归一化涨跌分 × 0.5 + 归一化活跃度 × 0.3 + 上涨广度分 × 0.2
```

| 维度 | 权重 | 数据来源 | 说明 |
|------|------|---------|------|
| 涨跌强度 | 50% | avg(stock_pool.change_percent) | 板块平均涨跌幅，按市场最大涨跌幅归一化 |
| 交易活跃度 | 30% | avg(turnover / market_cap) | 近似换手率，按市场最大值归一化 |
| 上涨广度 | 20% | up_count / total_count × 100 | 板块内上涨股占比，天然 0-100 |

## 数据源

### 后端 `/api/sectors/hot` 重写

文件: `web/sectors.py`

```python
# 删除 _aggregate_hot_sectors() 中的 WebSearchHotSectorProvider 调用
# 新增: 从 DB 直接查询板块聚合数据

def _aggregate_hot_sectors(market: str, limit: int = 15):
    """DB驱动的热点板块聚合（零外部API依赖）"""
    db = MarketDatabase(mysql_config_from_env())
    try:
        sectors = _query_sector_heat_from_db(db, market, limit)
    finally:
        db.close()
    
    # A股补充Akshare数据
    if market == "A":
        _merge_akshare_sectors(sectors, market, limit)
    
    # 英文名→中文名映射
    _translate_sector_names(sectors)
    
    return sectors[:limit]
```

### 缓存

```python
_cache: Dict[str, tuple[List[dict], float]] = {}
CACHE_TTL = 300  # 5分钟

def _cached_get(market: str, limit: int) -> List[dict]:
    key = f"{market}:{limit}"
    now = time.time()
    if key in _cache:
        data, ts = _cache[key]
        if now - ts < CACHE_TTL:
            return data
    data = _aggregate_hot_sectors(market, limit)
    _cache[key] = (data, now)
    return data
```

## 英文→中文板块名映射

文件: `web/sectors.py` 新增静态字典

覆盖三市场 DB 中 Top 50 板块名的中文转译。未命中 → 保留英文原名。

```python
SECTOR_CN_MAP = {
    # HK
    "Biotechnology": "生物科技",
    "Real Estate Developers": "房地产开发商",
    "Pharmaceuticals": "制药",
    "Software - Application": "软件应用",
    ...
    # US
    "Semiconductors": "半导体",
    "Aerospace & Defense": "航空航天与国防",
    ...
}
```

## 前端修复

### `SectorTreemap.tsx`

**修复 1**: `name|undefined` — 关闭 `upperLabel` 或添加 formatter

```typescript
upperLabel: {
  show: false,  // ← 改为 false
}
```

**修复 2**: 热度色阶与涨跌一致

已在 `colorMappingBy: 'value'` 中处理，确认色阶映射 `visualMin`/`visualMax` 基于 `change_pct` 范围。

## 改动文件清单

| 文件 | 改动 | 行数估计 |
|------|------|---------|
| `web/sectors.py` | 重写 `_aggregate_hot_sectors()`，新增缓存、映射表 | ~120行 |
| `SectorTreemap.tsx` | `upperLabel.show = false` | 1行 |

## 不涉及

- 不新增外部 API 依赖
- 不修改 DB schema
- 不删除 `WebSearchHotSectorProvider`（保留给后续可选调用）
- 不修改 `/api/sectors/{name}/stocks` 接口

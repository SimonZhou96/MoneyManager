# 产业拓扑（Industry Topology）设计

**日期**: 2026-06-23
**状态**: 设计已确认，待实现计划

## 目标

输入股票代码或名称 → 搜索下拉选中 → 点"开始拓扑" → 展示以该股票为中心、上下游公司 3-4 度（可选 1-5 度）的产业链拓扑图。

- 节点面积（size）由后端按市值分档控制，size 越大节点越大。
- 节点颜色：绿色=涨、红色=跌（当天或最近一个交易日）。
- 节点内显示：板块、公司名称、股票代码、涨跌幅、市值。
- 边上显示两节点之间的关系。
- 公司维度持久化缓存，避免重复请求 LLM 浪费额度。

## 关键决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 上下游关系数据源 | LLM 推理生成 | 公司级精确、关系语义丰富 |
| 遍历策略 | 按需展开 + 深度可选（默认3，最大5） | 兼顾速度与深度 |
| 图渲染 | React Flow 自定义节点（`@xyflow/react`） | 节点内信息密度高，HTML 卡片最干净 |
| 持久化与遍历 | SQL 表 + networkx 内存图 | 零新依赖、三模式兼容、桌面友好 |
| 布局 | dagre 分层 | 体现产业链层级，上下游分两侧 |
| LLM provider | 复用 `signal_analysis/factories.py` 工厂 | 遵循项目"不在 chain 写 provider 分支"约定 |

## §1 架构与组件边界

新增独立子系统 `industry_topology/`，遵循项目"三模式兼容 + 故障隔离"约定。

```
stock_screener/
  industry_topology/
    __init__.py
    models.py          # 数据模型: TopologyNode, TopologyEdge, RelationType 枚举
    service.py         # 编排: 搜索 → 首次拓扑 → 按需展开; 聚合快照+关系
    relation_engine.py # LLM 调用 + 结果解析 + 关系归一化
    cache.py           # GraphCache: 关系缓存读写 + networkx 图遍历
    resolver.py        # 节点行情/板块/市值快照聚合 + compute_size_level
  web/
    topology.py        # APIRouter(prefix="/api/topology")  ← 新增, 在 main.py include
  web_frontend/src/features/
    industryTopology/
      IndustryTopologyPanel.tsx
      StockSearchInput.tsx
      TopologyCanvas.tsx
      TopologyNode.tsx
      topologyApi.ts
      types.ts
```

**三层职责**:

- `resolver.py` — 纯数据聚合：给定 code → 返回 `{code, name, market, sector, market_cap, pct_chg, price}`。复用已有 `stock_name_resolver`、`sectors`、eastmoney snapshot。**不含 LLM**。
- `relation_engine.py` — 纯 LLM 推理：给定一只股票 → 返回 `[{peer_code, peer_name, relation, direction}]`。**不碰行情**。结果落 `industry_relations` 缓存表。
- `service.py` — 编排：把 resolver 的行情和 relation_engine 的关系拼成 `TopologyNode[]` + `TopologyEdge[]`。行情每次实时取，关系走缓存。

**故障隔离**: LLM 失败 → 该节点不展开，画布保留已有节点 + 提示。行情取数失败 → 节点仍渲染，涨跌幅/市值显示 `--`，颜色灰色。任何子故障不阻断中心节点和已有关系展示。

**三模式兼容**: `service.py` 不依赖 web 层；`desktop.py`/`interactive_screening.py` 都能直接 import 调用。Web 路由是薄封装。

## §2 公司维度持久化缓存

**核心原则**: LLM 关系推理结果按公司维度持久化到 DB，任何公司在 TTL 内只需推理一次。展开 A 的上下游、再展开 A 的某个上游 B，若 B 之前被推理过，直接命中缓存，零 LLM 调用。

### 持久化表（`sql/` 部署文件 + Python 建表双写，遵循"同步 SQL 和 Python"约定）

**表 1: `industry_relations`（公司关系缓存，主表）**

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | BIGINT PK | 自增 |
| `source_code` | VARCHAR(32) | 关系起点公司代码（归一化，如 `US.AAPL`）|
| `source_market` | VARCHAR(8) | `HK`/`US`/`A` |
| `peer_code` | VARCHAR(32) | 关系对方公司代码 |
| `peer_market` | VARCHAR(8) | |
| `peer_name` | VARCHAR(128) | LLM 返回的对方名称（冗余，便于未命中行情时仍能展示）|
| `relation` | VARCHAR(32) | 归一化关系标签（见 §3 词表）|
| `direction` | VARCHAR(8) | `upstream`/`downstream`/`peer` |
| `evidence` | TEXT | LLM 给出的简短依据 |
| `created_at` | DATETIME | 首次推理时间 |
| `updated_at` | DATETIME | 最近刷新时间 |
| `expires_at` | DATETIME | 失效时间 = `updated_at + TTL` |
| `llm_provider` | VARCHAR(32) | 用的哪个 provider |
| `llm_model` | VARCHAR(64) | 模型名 |
| `is_empty` | BOOLEAN | 是否空结果（防反复空推理）|

- **唯一索引**: `(source_code, peer_code, relation)` — 同一对公司同一种关系只存一条。
- **查询索引**: `(source_code)` — 展开某节点时一次性取出它全部关系。
- **TTL**: 默认 **7 天**。读时判断 `expires_at > now()`，过期则该 source 整组失效，下次展开重新推理。
- **公司维度失效**: 缓存以 `source_code` 为粒度，整组命中或整组重算，避免半新半旧。

**表 2: `industry_relation_refresh_log`（可选，运维用）**

记录何时、为何触发重算（TTL 过期 / 手动刷新 / 用户反馈错误），用于后续优化提示词。先建表不强制写。

### networkx 图遍历层（`cache.py` 的 `GraphCache`）

- 展开某节点时，从表载入该 source 的全部关系，构造 `networkx.DiGraph` 子图。节点属性携带 `{code, name, market, expires_at, stale}`，边属性携带 `{relation, direction, evidence}`。
- N 度遍历：`nx.single_source_shortest_path_length(graph, source, cutoff=N)` 一行拿到 N 度内全部可达节点。
- **"是否需要 LLM 重算"判定**：遍历到某节点时检查 `expires_at`：
  - `expires_at > now()` → 命中，直接用缓存的边，**0 LLM**。
  - `expires_at <= now()` 或节点不在表里 → 标记 `stale=True`，收集到待重算集合。
- service 拿到待重算集合后，按 LLM 次数规则触发推理，结果回写表 + 更新内存图节点 `expires_at`。
- 按需展开时前端传"展开节点 X 到 N 度"，后端在 networkx 图上先看 X 的 N 度邻域里哪些已命中缓存——已命中直接返回，只有 `stale` 或缺失的才触发 LLM。同一批公司反复展开，第二次起全图命中缓存，零 LLM 调用。

### 缓存读写流程

```
get_relations(source_code, market) -> List[CachedRelation] | None
  1. SELECT * FROM industry_relations WHERE source_code=? AND expires_at > now()
  2. 命中且行数>0 -> 返回（0 LLM 调用）
  3. 未命中或过期 -> 返回 None，由 service 决定是否触发 LLM

save_relations(source_code, market, relations, provider, model)
  1. 事务: DELETE WHERE source_code=?  -- 整组替换，避免残留过期关系
  2. INSERT 新的一组，expires_at = now() + 7d
  3. commit
```

### LLM 调用次数上限（防失控）

- 单次"开始拓扑"或单次"展开节点"：**最多 1 次 LLM 调用/节点**（仅对该节点推理）。
- 该节点若命中缓存：**0 次**。
- 全图累计 LLM 调用 = 未命中节点数 × 1。用户反复展开同一批公司，第二次起全部命中缓存。
- 设全局硬上限：单次会话内若未命中累计触发 ≥ **20 次** LLM，后续展开强制走缓存（即便过期也先用旧数据 + 标记"数据较旧"），保护额度。可配置。

### 深度可选（默认 3，最大 5）

- **缓存命中范围（0 LLM）**：选定深度内、`expires_at > now()` 的节点全部走缓存，networkx 一次遍历返回。
- **LLM 补算范围**：选定深度内但 `stale` 或表中没有的节点，触发 LLM 推理补全。选 5 度、当前缓存只覆盖 3 度时，第 4-5 度的新节点由 LLM 找出来。
- **交互**：前端选深度 → 点"开始拓扑"或"展开节点" → 后端按选定深度在 networkx 图上遍历，区分命中/待补算 → 命中的立即返回，待补算的 LLM 推理后增量返回。
- 默认 3 度大多数走缓存秒出；想看 5 度时多出的 2 度才花 LLM 额度，算完进缓存，下次再选 5 度免费。

### 手动刷新

节点卡片提供"刷新关系"按钮 → 强制 `save_relations` 前先重算 → 覆盖该 source 整组。

### 行情不缓存

涨跌幅/市值/价是当天实时数据，每次取实时（复用现有 eastmoney snapshot），不进 `industry_relations`。只有 LLM 推理出的"关系"进缓存。

## §3 LLM 提示词与关系词表

### Provider

复用 `signal_analysis/factories.py` 的 provider 工厂。优先级：DeepSeek → Codex → OpenAICompatible。temperature 用 0.2（要稳定可复现的关系）。

### 关系方向枚举（`direction`）

- `upstream` — 该 peer 是 source 的上游（供应商/原材料/设备）
- `downstream` — 该 peer 是 source 的下游（客户/应用端/终端）
- `peer` — 同业竞争/替代品

### 关系词表（`relation`，归一化标签，有限集合）

上游类：`supplier`(供应商) · `raw_material`(原材料) · `equipment`(设备) · `foundry_packaging`(代工封测)
下游类：`customer`(客户) · `odm`(代工) · `distributor`(分销) · `application`(应用场景)
同业类：`competitor`(竞品) · `substitute`(替代品)

LLM 输出必须落到这 12 个标签之一；解析时若输出超纲，记 `relation=other` 并保留原文到 `evidence`，不丢节点。

### 提示词骨架（`relation_engine.py`）

```
你是产业分析专家。给定一只股票，推理其产业链上下游及同业公司。

输入：
- 公司：{name}（{market}市场，代码{code}）
- 所属板块：{sector}

任务：列出最多 50 家与该公司有明确产业关系的上市公司，要求：
1. 关系必须真实、可溯源（凭你知道的产业链事实，不要编造公司）。
2. 优先覆盖上游（供应商/原材料/设备/代工封测）与下游（客户/ODM/分销/应用场景），可含少量同业竞品。
3. 对方须为真实上市公司，给出其股票代码与简称；代码不确定时宁可不列。
4. 每条给出方向(upstream/downstream/peer)、归一化关系标签(从词表选)、一句话依据。

严格输出 JSON，不要解释：
{
  "items": [
    {"code":"300308","name":"中际旭创","market":"A",
     "direction":"upstream","relation":"supplier",
     "evidence":"为该公司提供800G光模块"}
  ]
}
```

market 字段帮 resolver 反查行情——LLM 不保证代码准确，resolver 用 `stock_name_resolver` + 名称模糊匹配兜底校验，校验失败的关系直接丢弃（宁缺毋滥）。

### 解析与校验（防幻觉三道关）

1. **JSON 解析失败** → 重试 1 次，仍失败则该节点展开失败，不写缓存（下次还会重试）。
2. **代码校验**：每条 `peer_code` 经 `normalize_stock_code` + `stock_name_resolver` 校验是真实在市股票；校验失败的条目丢弃，其余保留。
3. **去重**：同一 `(source, peer, relation)` 只留一条；`direction` 冲突时（既是上游又是下游）保留两条边。

### 兜底：LLM 返回空

若返回 `items` 为空或全部校验失败 → 仍写缓存（`is_empty=true`，`expires_at` 正常），避免同一公司反复触发空推理浪费额度。前端该节点显示"无已知产业链关系"。

## §4 节点与边视觉规格

### 节点（自定义 React Flow 节点 `TopologyNode.tsx`）

**面积（size）由后端控制**：后端按市值分桶返回 `size_level`（1-6），前端映射到节点宽高。计算逻辑在 `resolver.py`。

市值分桶（后端 `compute_size_level(market_cap)`，单位人民币元）：

| size_level | 市值区间 | 节点尺寸 |
|---|---|---|
| 1 | < 50亿 | 120×72 |
| 2 | 50–200亿 | 150×88 |
| 3 | 200–1000亿 | 180×104 |
| 4 | 1000–3000亿 | 210×120 |
| 5 | 3000亿–1万亿 | 240×136 |
| 6 | > 1万亿 | 270×152 |

中心节点强制 `size_level=6` 并加描边强调。

**颜色（涨跌）**：后端返回 `pct_chg`，前端按符号定色：
- `pct_chg > 0` → 绿色系（`#16a34a` 边框 + 浅绿底 `#dcfce7`）
- `pct_chg < 0` → 红色系（`#dc2626` 边框 + 浅红底 `#fee2e2`）
- `pct_chg == 0` 或取数失败 → 灰色（`#6b7280` 边框 + `#f3f4f6` 底）

全局统一绿涨红跌（按需求，非 A 股惯例）。

**节点内信息**（固定排版，五项）：
```
┌─────────────────────────┐
│ [板块]            涨跌幅 │  ← 顶行：板块标签(左) + 涨跌幅%(右,带±号)
│  公司名称（简称）        │  ← 中行：公司名，加粗
│  代码 · 市值             │  ← 底行：代码(左) + 市值(右,如"1.2万亿")
│              [展开↗]     │  →点击展开该节点上下游
└─────────────────────────┘
```
- 涨跌幅：`+3.25%` / `-1.40%`，颜色随涨跌。
- 市值：后端返回格式化字符串（`1.2万亿`/`850亿`/`32亿`），取数失败显示 `--`。
- 板块：取数失败显示 `--`。
- `[展开]` 按钮：只在节点非中心、且未被展开过时显示；已展开的节点显示 `[已展开]` 置灰。

### 边（关系连线）

- **样式**：方向箭头（source → peer），上游指向中心、中心指向下游。
- **边标签**：显示关系，格式 `关系·依据`，如 `供应商·提供800G光模块`。依据过长截断，hover 显示完整。
- **颜色按方向**：upstream 边 `#3b82f6`（蓝）、downstream 边 `#f59e0b`（橙）、peer 边 `#8b5cf6`（紫）。边标签用白底胶囊避免遮挡。
- **方向布局**：用 `dagre` 分层，中心节点居中，upstream 在左、downstream 在右、peer 在上下。

### 画布（`TopologyCanvas.tsx`）

- React Flow + `dagre` 自动布局，Background/MiniMap/Controls 开箱即用。
- 顶部工具栏：深度选择器（1-5，默认3）+ "开始拓扑"按钮 + "刷新全部关系"。
- 节点展开时平滑插入新节点和边，自动 fitView。

## §5 API 契约

新增 `web/topology.py`，`APIRouter(prefix="/api/topology", tags=["topology"])`，在 `web/main.py` 注册。遵循项目 `{ok, data}` 风格。

### 1. 搜索股票 `GET /api/topology/search`

```
GET /api/topology/search?q=英伟达&limit=10
```
- 复用 `stock_name_resolver` + `stocks`/`stock_pools` 表，名称或代码模糊匹配，跨 HK/US/A。
- 返回：
```json
{"ok": true, "data": [
  {"code": "US.NVDA", "name": "英伟达", "market": "US", "sector": "半导体"}
]}
```

### 2. 首次拓扑 `POST /api/topology/graph`

```
POST /api/topology/graph
{"code": "US.NVDA", "market": "US", "depth": 3}
```
- service 在 networkx 上按 depth 遍历；命中缓存的关系直接返回，`stale`/缺失的节点触发 LLM 补算（受硬上限约束）。
- 返回：
```json
{"ok": true, "data": {
  "center": {"code":"US.NVDA","name":"英伟达","market":"US","sector":"半导体",
             "pct_chg":2.13,"market_cap_str":"2.8万亿","size_level":6,
             "expanded":true},
  "nodes": [ /* 含中心节点,每个含上面字段 + stale:bool */ ],
  "edges": [
    {"source":"US.NVDA","target":"300308","direction":"upstream",
     "relation":"supplier","label":"供应商·提供800G光模块","evidence":"..."}
  ],
  "stats": {"llm_calls":2,"cached_nodes":7,"stale_nodes":2,"depth":3}
}}
```

### 3. 展开节点 `POST /api/topology/expand`

```
POST /api/topology/expand
{"code": "300308", "market": "A", "depth": 2,
 "existing_codes": ["US.NVDA","300308","300394"]}
```
- `existing_codes` 让后端去重，不重复返回已有节点。
- 后端对该节点 LLM 推理（命中缓存则 0 调用），返回新增的 nodes/edges。
- 返回：
```json
{"ok": true, "data": {
  "nodes": [ /* 新增节点 */ ],
  "edges": [ /* 新增边,source=被展开节点 */ ],
  "stats": {"llm_calls":1,"cached":true}
}}
```

### 4. 刷新关系 `POST /api/topology/refresh`

```
POST /api/topology/refresh
{"code": "US.NVDA", "market": "US"}
```
- `cache.save_relations` 前先 DELETE 整组 + 重新 LLM 推理。
- 返回该 source 的新一组 nodes/edges（中心节点行情实时）。

### 错误约定（遵循项目故障隔离）

- LLM 失败：`200 ok:true` + `data.nodes/edges` 为空或部分 + `stats.error="llm_failed"`，前端 toast 提示，不阻断已渲染图。
- 行情取数失败：节点照常返回，`pct_chg=null`、`market_cap_str="--"`，前端灰色渲染。
- 搜索无结果：`200 ok:true data:[]`。

## §6 前端组件结构

### 目录

```
web_frontend/src/features/industryTopology/
  IndustryTopologyPanel.tsx   # 入口容器: 搜索 + 工具栏 + 画布
  StockSearchInput.tsx        # 搜索下拉
  TopologyCanvas.tsx          # React Flow 画布 + dagre 布局
  TopologyNode.tsx            # 自定义节点卡片
  topologyApi.ts              # 调 /api/topology/*
  types.ts                    # TS 类型 = 后端契约
```

### `types.ts`

```ts
export interface TopologyNodeData {
  code: string; name: string; market: 'HK'|'US'|'A';
  sector: string; pct_chg: number | null;
  market_cap_str: string; size_level: 1|2|3|4|5|6;
  expanded: boolean; stale: boolean; isCenter: boolean;
}
export interface TopologyEdgeData {
  source: string; target: string;
  direction: 'upstream'|'downstream'|'peer';
  relation: string; label: string; evidence: string;
}
export interface TopologyGraph {
  center: TopologyNodeData;
  nodes: TopologyNodeData[];
  edges: TopologyEdgeData[];
  stats: { llm_calls: number; cached_nodes: number; stale_nodes: number; depth: number };
}
```

### `IndustryTopologyPanel.tsx`（状态容器）

持有整图状态 `nodes: Node[]`、`edges: Edge[]`，编排交互：
- **搜索**：`StockSearchInput` 选中股票 → `setSelected({code,market,name})`，下拉关闭。
- **工具栏**：深度选择器（1-5，默认3）+ "开始拓扑"按钮 + "刷新全部关系"。
- **开始拓扑**：调 `topologyApi.graph(selected, depth)` → 构造 React Flow `Node[]`/`Edge[]`（节点 type=`'topology'`，位置先占位，交给 dagre 算）→ 喂给 `TopologyCanvas`。
- **展开节点**：点 `[展开]` → 调 `expand(code, depth, existingCodes)` → 合并新 nodes/edges（去重 by code）→ 重新布局。
- **刷新关系**：调 `refresh(code)` → 替换该 source 出发的边 + 更新节点 `expanded`。
- **stats 显示**：工具栏角落显示本次 `llm_calls / cached_nodes / stale_nodes`，让额度消耗可见。

### `StockSearchInput.tsx`

- 受控 input + debounce 300ms → `topologyApi.search(q)`。
- 下拉列表渲染结果，点击某项 → `onSelect(stock)` + 关闭下拉 + 清空 input 显示选中名。
- ESC/点外部关闭，空 query 不请求。

### `TopologyCanvas.tsx`

- `<ReactFlow nodeTypes={{topology: TopologyNode}} ...>` + Background/MiniMap/Controls。
- dagre 布局函数 `layout(nodes, edges)`：按 direction 定方向（upstream 左、downstream 右、peer 上下），中心节点固定中心，输出 `{x,y}` 回填节点 position。
- 节点/边变更时重算布局 + `fitView({padding:0.2})`。
- 边 label 用 React Flow 的 `EdgeLabelRenderer` 渲染胶囊标签（白底 + 关系·依据）。

### `TopologyNode.tsx`

- 读 `data`（TopologyNodeData），按 §4 规格渲染卡片：size 由 `size_level` 映射宽高（查表），颜色由 `pct_chg` 符号定，五项信息固定排版。
- `[展开]` 按钮：`data.expanded` 为 true 时置灰显示"已展开"，否则点击调 `onExpand(code)`（通过 `NodeProps` context 传回调）。
- `data.stale` 为 true 时节点角标显示"数据较旧"。

### 复用与接入

- `api.ts` 的 `api<T>()` fetch 封装直接用，`topologyApi.ts` 薄包装。
- 入口：在现有 feature 导航里加"产业拓扑"入口（具体位置实现阶段看 `main.tsx` 现有结构定）。

## §7 错误处理与测试

### 错误处理（贯穿各层，遵循项目"故障隔离"）

| 故障点 | 行为 | 用户可见 |
|---|---|---|
| 搜索无结果 | `data:[]` | 下拉显示"无匹配" |
| 搜索接口异常 | `ok:false` | 下拉显示"搜索失败，请重试" |
| 首次拓扑 LLM 失败 | 仍返回中心节点（行情实时）+ 空 edges + `stats.error="llm_failed"` | 画布只有中心节点 + toast"产业链推理失败，可点节点手动展开" |
| 首次拓扑部分 LLM 失败 | 命中缓存的节点照常返回，失败节点跳过 | 已有图正常，缺失分支不渲染 |
| 展开节点 LLM 失败 | 该节点不展开，`expanded` 保持 false | toast"展开失败"，节点可重试 |
| 行情取数失败 | 节点照常返回，`pct_chg=null`/`market_cap_str="--"` | 节点灰色，信息显示 `--` |
| LLM 返回非 JSON | 重试 1 次，仍失败按 LLM 失败处理 | 同 LLM 失败 |
| LLM 返回幻觉代码 | resolver 校验丢弃，其余保留 | 静默丢弃，宁缺毋滥 |
| LLM 返回空 | 写缓存（防反复空推理），节点标"无已知产业链关系" | 节点角标提示 |
| DB 写缓存失败 | 关系照常返回前端，仅不落库 | 无感，下次重算 |
| 达 LLM 硬上限(20) | 强制走旧缓存 + 标 `stale` | 节点"数据较旧"角标 + toast"已达推理上限，部分数据为缓存" |
| 三模式兼容 | service 不依赖 web；desktop.py / interactive 可直接 import | — |

### 后端单元测试（`tests/test_industry_topology.py`，unittest）

1. `compute_size_level` — 市值分桶 6 档边界正确（50亿/200亿/1000亿/3000亿/1万亿临界值）。
2. `GraphCache.get_relations` — 命中（未过期）返回数据、0 LLM；过期返回 None。
3. `GraphCache.save_relations` — 整组替换（旧关系被 DELETE）、`expires_at` 正确。
4. `relation_engine` 解析 — 合法 JSON 解析成功；非法 JSON 重试 1 次仍失败抛错；超纲 relation 落 `other`。
5. `relation_engine` 校验 — 幻觉代码被 resolver 丢弃；同一 `(source,peer,relation)` 去重。
6. 空结果 — 返回空 items 时仍写缓存，标记 `is_empty`。
7. `service.build_graph` — 给定命中缓存 + 部分 stale，返回的 nodes/edges 正确，`stats.llm_calls` 只计 stale 部分。
8. LLM 硬上限 — mock 20 次后强制走缓存，不再调 LLM。
9. N 度遍历 — networkx 上 3 度/5 度可达节点集合正确。

**LLM mock**：测试不调真实 LLM，`relation_engine` 注入 fake provider 返回固定 JSON。

### 前端手动验证

- 搜索下拉选中后关闭、深度切换、开始拓扑出图、点节点展开增量渲染、刷新关系、行情失败灰色、LLM 失败 toast、额度 stats 显示。
- 三模式：`python3 desktop.py` 启动 + `curl localhost:8000/api/topology/search` 返回数据 + interactive shell import service 可调。

### 验收标准

- 选 3 度开始拓扑：缓存命中部分秒级返回，stale 部分触发 LLM，`stats.llm_calls` 与实际一致。
- 同一股票第二次开始拓扑：`llm_calls=0`，全图秒级返回。
- 选 5 度：多出的 2 度由 LLM 补算并进缓存，下次 5 度 `llm_calls=0`。
- 节点 size 随市值分档、颜色随涨跌（绿涨红跌）、五项信息齐全、边标签显示 `关系·依据`。
- LLM 失败/行情失败均不阻断已渲染图。

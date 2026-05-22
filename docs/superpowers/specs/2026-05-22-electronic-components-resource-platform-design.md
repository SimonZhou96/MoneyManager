# 电子元器件快速资源整合平台设计

## 背景

目标是做一个电子元器件资源整合平台：用户输入一个电子元器件型号后，平台自动从公开可访问的供应商和原厂页面中寻找匹配或相关的电子元器件，返回价格、库存数量、阶梯价、交期、供应/发货位置和原始商品地址。

用户明确要求第一版以爬虫为主，不依赖逐个申请官方 API 权限。设计因此采用“按需查询爬虫 + 结构化归一化 + 短缓存 + 风险隔离”的方式，而不是全站扫库或 API 优先接入。

## 目标

- 支持输入单个型号进行实时聚合查询。
- 第一版接入四个数据源：Texas Instruments、LCSC、DigiKey、Mouser。
- 对 Texas Instruments 做一等原厂直销数据源处理，而不是只通过分销商间接覆盖。
- 返回精确匹配、疑似匹配和相关/替代料结果。
- 展示库存、价格、阶梯价、MOQ/SPQ、交期、供应商地址、公开发货地区、原始商品链接和数据更新时间。
- 保存每次查询快照，便于后续对比价格和库存变化。
- 支持结果导出 CSV。
- 单个数据源失败时不影响其他数据源返回。

## 非目标

- 不做下单、支付、合同价、采购审批或 ERP 集成。
- 不承诺覆盖“市面上所有”库存，只承诺覆盖已接入公开数据源。
- 不绕过登录、验证码、付费墙或权限控制。
- 不做全站库存爬取或定时扫库。
- 不承诺拿到具体物理仓库地址；如果页面没有公开，只展示供应商地址、站点地区、发货地区或“未公开”。
- 不在第一版做 BOM 批量询价。
- 不在第一版接入非授权现货商、论坛、二手交易和灰色渠道。

## 数据源范围

### Texas Instruments

TI 是原厂直销源，优先级最高。TI 结果需要展示原厂通用型号和可订购物料号之间的关系。

核心字段：

- `manufacturer`: `Texas Instruments`
- `generic_part_number`: GPN，例如 `AFE7799`
- `orderable_part_number`: OPN，例如具体可订购物料号
- `package`: 封装
- `lifecycle_status`: 生命周期或采购状态
- `stock_qty`: 官方页面公开库存
- `price_breaks`: 阶梯价
- `buy_url`: TI 官方购买链接
- `source_region`: TI 页面地区或站点
- `ship_from_region`: 页面公开则展示，未公开则为空
- `location_note`: TI 官方库存，具体仓库地址未公开时明确标注

### LCSC

LCSC 用于覆盖中文和亚洲供应链，尤其适合小批量采购和国产替代料补充。

核心字段：

- LCSC 编号
- MPN
- 制造商
- 库存数量
- 阶梯价
- MOQ/SPQ
- 封装
- 商品详情链接
- 供应商或站点地址信息

### DigiKey

DigiKey 用于覆盖全球授权分销渠道，结果可信度高。

核心字段：

- DigiKey part number
- Manufacturer part number
- Manufacturer
- 库存数量
- 价格阶梯
- MOQ
- 标准交期或可采购状态
- 商品详情链接
- 区域站点信息

### Mouser

Mouser 用于补充全球授权分销库存和价格阶梯。

核心字段：

- Mouser part number
- Manufacturer part number
- Manufacturer
- 库存数量
- 价格阶梯
- MOQ
- 交期或预计到货信息
- 商品详情链接
- 区域站点信息

## 总体架构

系统分为前端、查询 API、爬虫 worker、解析归一化、匹配引擎、存储和缓存。

```text
用户输入型号
  -> 前端提交查询请求
  -> 后端创建查询任务
  -> 并发调度 TI/LCSC/DigiKey/Mouser 爬虫
  -> 每个爬虫抓取公开搜索页和必要详情页
  -> 解析器提取结构化字段
  -> 归一化层统一型号、价格、币种、库存、地址字段
  -> 匹配引擎计算匹配类型和置信度
  -> 保存查询快照
  -> 前端展示聚合结果
```

## 后端模块

### Query API

提供前端入口：

- `POST /api/components/search`
- `GET /api/components/search-tasks/{task_id}`
- `GET /api/components/search-tasks/{task_id}/results`
- `GET /api/components/search-tasks/{task_id}/export.csv`

请求字段：

```json
{
  "part_number": "TPS5430",
  "quantity": 100,
  "currency": "USD",
  "region": "US"
}
```

### Crawler Adapter

每个站点一个 adapter，避免页面结构变化影响其他站点。

接口形态：

```text
search(query) -> source_results[]
fetch_detail(source_result) -> enriched_result
parse(raw_page) -> parsed_result
```

adapter 需要内置：

- 搜索 URL 生成
- 请求头和地区参数
- 静态页面解析或 Playwright 渲染策略
- 限速配置
- 字段解析规则
- 异常降级策略

### Normalizer

将不同站点字段归一成统一结果。

统一字段：

```text
source_name
source_type
channel_type
manufacturer
generic_part_number
orderable_part_number
source_part_number
normalized_mpn
description
package
stock_qty
price_breaks
currency
moq
spq
lead_time
supplier_name
supplier_address
source_region
ship_from_region
stock_location_note
product_url
match_type
match_confidence
last_updated_at
raw_snapshot_id
```

### Match Engine

匹配类型分为：

- `exact`: MPN 和制造商都高度一致。
- `same_mpn`: MPN 一致但制造商缺失或不完全一致。
- `orderable_variant`: TI GPN/OPN 或其他订购物料号变体。
- `related`: 页面返回相关料或替代料。
- `low_confidence`: 型号相似但需要人工确认。

排序规则：

1. 原厂直销结果优先。
2. 授权分销商结果优先于聚合或未知渠道。
3. 精确匹配优先于疑似匹配。
4. 有库存优先。
5. 同等条件下按用户输入数量对应的有效单价排序。

## 存储设计

第一版使用 MySQL 或 Postgres 均可。如果复用 MoneyManager 现有平台能力，可以沿用 MySQL；如果独立成新服务，建议 Postgres。

核心表：

- `component_search_tasks`: 查询任务。
- `component_search_results`: 归一化结果。
- `component_raw_snapshots`: 原始页面摘要、URL、解析版本和抓取时间。
- `component_source_status`: 各数据源健康状态、错误和限速信息。

查询任务保存：

- 输入型号
- 数量
- 币种
- 地区
- 状态
- 创建时间
- 完成时间
- 每个源的成功/失败摘要

结果保存：

- 归一化字段
- 匹配类型
- 匹配置信度
- 原始链接
- 数据源
- 抓取时间

## 缓存和限速

- 同一型号、数量、地区、币种在短时间内命中缓存，默认 TTL 15 到 60 分钟。
- 每个数据源独立限速，避免一个站点慢或失败拖垮整体查询。
- 页面解析失败时记录 `parser_error`，但保留其他数据源结果。
- Playwright 只用于必须渲染的页面，静态页面优先使用普通 HTTP 请求。
- 不做后台全站扫描，只在用户查询时按需抓取。

## 前端信息架构

页面名称：元器件资源查询。

页面分为五块：

1. 查询表单：型号、数量、币种、采购地区。
2. 数据源状态：TI、LCSC、DigiKey、Mouser 的运行状态和耗时。
3. 聚合结果表：价格、库存、交期、渠道、地址和链接。
4. 匹配分组：精确匹配、订购变体、疑似匹配、相关料。
5. 查询历史：最近查询和导出入口。

结果表字段：

- 渠道类型
- 数据源
- 制造商
- 型号
- 可订购物料号
- 库存
- 用户数量对应价格
- 阶梯价
- MOQ/SPQ
- 交期
- 供应商地址
- 发货地区
- 地址说明
- 商品链接
- 更新时间
- 匹配置信度

UI 风格使用后台工具形态，信息密度高，适合采购和工程人员扫描，不做营销式首页。

## 地址字段定义

为了避免误导，地址相关字段必须拆开：

- `supplier_address`: 供应商公司地址或公开站点地址。
- `source_region`: 查询站点或区域，例如 US、CN、EU。
- `ship_from_region`: 页面公开的发货地区或仓库地区。
- `stock_location_note`: 对地址粒度的说明，例如“页面未公开具体仓库地址”。
- `product_url`: 原始商品页面。

前端展示名使用“供应/发货位置”，不使用“元器件当前物理地址”。

## 错误处理

- 型号为空：前端阻止提交。
- 所有数据源失败：展示失败摘要和重试按钮。
- 单个数据源失败：结果区展示该源失败原因，其他源照常展示。
- 页面结构变化：该 adapter 返回 `parse_failed`，并记录 parser 版本。
- 触发验证码或登录页：停止该源本次抓取，标记为 `blocked_or_login_required`，不尝试绕过。
- 库存或价格字段缺失：保留商品链接和可解析字段，缺失项展示为“未公开”。
- 价格币种不一致：保留原币种，换算价单独展示，并标注换算时间。

## 测试要求

- 对 Normalizer 做单元测试，覆盖 TI GPN/OPN、LCSC 编号、DigiKey part number、Mouser part number。
- 对 Match Engine 做单元测试，覆盖精确匹配、订购变体、疑似匹配和相关料。
- 对每个 adapter 保存 2 到 3 个 HTML fixture，验证解析规则稳定。
- 对数据源失败做集成测试，确保单源失败不影响整体查询。
- 对 CSV 导出做字段顺序测试。
- 对前端做基本构建和结果表字段契约测试。

## 第一阶段 MVP

第一阶段只做单料查询：

- 输入型号、数量、币种、地区。
- 并发查询 TI、LCSC、DigiKey、Mouser。
- 返回聚合结果表。
- 支持查询历史。
- 支持 CSV 导出。
- 展示每个数据源耗时和失败原因。

第一阶段完成标准：

- 输入一个 TI 型号时，TI 官方结果排在最前。
- 同一个型号能看到至少两个数据源的结果或明确失败原因。
- 结果能区分精确匹配、订购变体和疑似匹配。
- 每条结果都有来源、原始链接和更新时间。
- 如果具体仓库地址未公开，页面必须明确显示“未公开”，不能用供应商地址替代物理库存地址。

## 后续阶段

第二阶段：

- 增加 element14/Farnell/Newark、TME、Arrow 等站点。
- 增加 BOM 批量查询。
- 增加价格和库存历史趋势。
- 增加替代料推荐和生命周期风险。

第三阶段：

- 接入官方 API 作为可选增强通道。
- 增加企业账户、采购清单和询价单。
- 增加供应商可信度和授权渠道标记。

## 决策记录

- 第一版选择爬虫优先，不阻塞在 API 权限申请上。
- 第一版只做按需查询，不做全站扫库。
- Texas Instruments 作为原厂直销源优先展示。
- 第一版数据源锁定为 TI、LCSC、DigiKey、Mouser。
- 地址字段按公开信息拆分，不承诺具体物理仓库地址。
- 查询结果必须保留来源链接和抓取时间，便于人工核验。

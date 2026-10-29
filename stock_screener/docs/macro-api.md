# 中美宏观数据 API

登录后使用现有会话 cookie 查询：

```http
GET /api/macro/inflation?country=CN
GET /api/macro/inflation?country=US
GET /api/macro/bond-yields?country=CN
GET /api/macro/bond-yields?country=US
```

`country` 必填，仅允许 `CN/US`。接口不读取股票池、不接收股票代码、不触发筛选或个股分析。

## 配置和来源

安装项目依赖，AKShare 至少为 `1.19.1`，以使用国家统计局新接口。
美国数据需要设置 `FRED_API_KEY`；缺失时返回缺失说明，不用其他国家数据代替。

通胀来源：中国 CPI/PPI 同比与 CPI 环比取自 AKShare 东方财富；PPI 环比取国家统计局
“工业生产者出厂价格指数(上月=100)”减去 100。美国 CPI 使用 `CPIAUCNS/CPIAUCSL`，
最终需求 PPI 使用 `PPIFID/PPIFIS`，分别用非季调序列算同比、季调序列算环比。

国债来源：中国为 `bond_zh_us_rate` 的中国 2 年、10 年精确列；美国为 FRED `DGS2/DGS10`。

## 响应

顶层包含 `country`、`category`、`fetched_at`、`published_at`、`status`、`indicators`、`data_gaps`。
`fetched_at` 为缓存数据获取时间；源不提供发布时间时 `published_at=null`。
整体状态为 `ok/partial/error`，单指标状态为 `ok/missing`，缺失数值使用 `null`。

每项指标包含 `source`、`unit`、`change_unit`、`latest` 和按日期升序的 `history`。

- 通胀指标为 `cpi/ppi`，返回最近 24 个月月度历史。
  每行含 `period`、`yoy`、`mom`、`previous_yoy`、`previous_mom`、`yoy_change_pp`、`mom_change_pp`。
  增速单位为 %，变化单位为百分点，前值严格取上一日历月。
- 国债指标为 `2y/10y/10y_2y_spread`，返回最近 24 个月日度历史。
  每行含 `date`、`value`、`previous_date`、`previous_value`、`change_bp`。
  收益率单位为 %，变化单位为 bp；利差及其变化均为 bp，只使用同日两期限有效数据。
  前值为上一有效观测，不填充节假日。

按“类别+国家”进程内缓存：成功 6 小时，部分失败/失败 60 秒，同键并发合并。
多进程独立缓存。API 与原有 Provider 共用获取层，FRED 同一序列共享缓存。

## 请求预算

冷缓存：中国通胀 3 个逻辑接口、中国国债 1 个逻辑接口；美国通胀 4 个序列、美债 2 个序列。
AKShare 内部存在分页和统计局目录查询，逻辑接口数不等于 HTTP 数。
缓存命中不请求上游；请求数与股票数量无关，不增加 K 线、板块、搜索或 LLM 调用。

2026-10-16 使用 AKShare 1.19.1 的实测：中国通胀冷查询 9 次 HTTP（东方财富 2 次、
统计局 7 次），中国国债冷查询 3 次 HTTP；再次调用两类查询均为 0 次 HTTP。
分页/目录数量可能随上游变化。中国数据获得 CPI/PPI 各 24 个月、国债各 496 个日度观测。
最新 CPI/PPI 已对照统计局发布，精简样本保存在 `tests/fixtures/macro_cn_20261016.json`。
当前验证环境没有 FRED Key，美国分支完成官方序列口径核验和离线测试，未完成凭据下的真实 API 联调。

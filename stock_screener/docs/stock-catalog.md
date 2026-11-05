# 单表股票资料

`stock_pools` 是唯一的股票资料物理表。`stocks` 已退役。

- 池成员唯一键仍是 `(market, pool_type, code)`，保留指数、池内排名等字段。
- `stock_count`、`get_stocks`、`get_stocks_by_codes`、名称搜索和板块查询按
  `(market, code)` 聚合；名称/板块取非空值，基本面保留零值。
- `upsert_stocks` 保留兼容接口，更新该股票所有池记录的非空共享字段，不修改
  池类型、指数或排名。没有池成员记录时写入 `pool_type='catalog'`。
- `catalog` 仅保存个股资料，不属于默认筛选池，不能自动扩大筛选范围。
- `init_schema` 与 `init_stock_pool_schema` 会幂等补齐资料字段，不创建或删除
  `stocks`。只读搜索不会刷新行情、调用模型或写数据库。

## 已有数据库迁移

先停止 API、Worker 和其它股票资料写入进程，在 `stock_screener` 目录执行：

```bash
.venv/bin/python scripts/migrate_stock_catalog.py \
  --backup logs/backups/legacy-stocks.sql --drop-legacy
```

迁移会先独占创建 SQL 备份，再同步旧表所有非空资料字段，验证每只股票及字段，
成功提交后才删除旧表。重复执行时，旧表不存在则直接退出；已有备份不会覆盖。
不传 `--drop-legacy` 可以先迁移验证并保留旧表。随后启动新版 API 和 Worker。

需要回退旧代码时，先停止新版写入，再将备份导入原数据库恢复 `stocks`。
备份是迁移时快照，不包含迁移后发生的资料更新。

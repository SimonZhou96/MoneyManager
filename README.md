# MoneyManager

股票筛选与数据管理工具集。

## 子项目

### stock_screener — 股票筛选器

基于 EMA10 向上突破 EMA150 策略的多市场股票筛选系统，支持港股、美股、A 股。

**快速开始：**

```bash
cd stock_screener
pip install -r requirements.txt
# 配置 MySQL 后运行
python daily_job.py --markets HK --limit 10 --mysql-password your_password
```

**完整部署说明**：请参阅 [stock_screener/README.md](stock_screener/README.md)。

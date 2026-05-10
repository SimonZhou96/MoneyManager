-- 将已存在数据库中的左一战法默认窗口更新为 15 根 K 线。
-- 001_screening_rules.sql 使用 INSERT IGNORE，不会覆盖已存在的规则配置；
-- 部署已有数据库时需要执行本迁移脚本。

UPDATE screening_rule_metadata
SET
    params_json = JSON_SET(COALESCE(params_json, JSON_OBJECT()), '$.signal_window', 15),
    description = '当前周期15根K线内左一战法看涨/看跌信号'
WHERE market IN ('HK', 'US', 'A')
  AND rule_key = 'zuoyi_signal'
  AND implementation = 'ZuoYiStrategizer';

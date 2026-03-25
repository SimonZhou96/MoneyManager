#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试飞书 Webhook（从 stock_screener/.env 或环境变量读 FEISHU_WEBHOOK_URL）

用法（在仓库根目录）:
    python3 scripts/test_feishu_webhook.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_STOCK = Path(__file__).resolve().parents[1] / "stock_screener"
if str(_STOCK) not in sys.path:
    sys.path.insert(0, str(_STOCK))

import os  # noqa: E402

from feishu_notifier import send_feishu_text  # noqa: E402


def _load_dotenv():
    env_path = _STOCK / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main():
    _load_dotenv()
    webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    if not webhook_url:
        print("❌ 未配置 FEISHU_WEBHOOK_URL")
        return 1

    test_msg = "【选股器】飞书 Webhook 测试消息 - 配置正常 ✓"
    print("正在发送测试消息到飞书...")
    ok = send_feishu_text(webhook_url, test_msg)

    if ok:
        print("✅ 发送成功，请检查飞书群是否收到消息")
        return 0
    print("❌ 发送失败，请检查 Webhook URL 与网络")
    return 1


if __name__ == "__main__":
    sys.exit(main())

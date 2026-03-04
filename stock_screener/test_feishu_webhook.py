#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试飞书 Webhook 是否配置正确
用法: python3 test_feishu_webhook.py
支持从 .env 或环境变量读取 FEISHU_WEBHOOK_URL
"""

import os
from pathlib import Path

from feishu_notifier import send_feishu_text


def _load_dotenv():
    """简单加载同目录下的 .env"""
    env_path = Path(__file__).parent / ".env"
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
        print("   请设置环境变量: export FEISHU_WEBHOOK_URL='https://open.feishu.cn/open-apis/bot/v2/hook/xxx'")
        return 1

    test_msg = "【选股器】飞书 Webhook 测试消息 - 配置正常 ✓"
    print(f"正在发送测试消息到飞书...")
    ok = send_feishu_text(webhook_url, test_msg)

    if ok:
        print("✅ 发送成功，请检查飞书群是否收到消息")
        return 0
    else:
        print("❌ 发送失败，请检查 Webhook URL 是否正确、网络是否可达")
        return 1


if __name__ == "__main__":
    exit(main())

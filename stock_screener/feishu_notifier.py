#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞书消息通知

支持 Webhook 机器人发送文本消息。
飞书 Webhook 不支持直接发送文件，大内容以文本形式发送或分片。
"""

import json
import os
from typing import Optional

# requests 为常见依赖，若无则静默跳过
try:
    import requests
    _HAS_REQUESTS = True
except Exception:
    _HAS_REQUESTS = False


# 飞书单条消息文本约 20KB 限制，预留安全余量
FEISHU_TEXT_LIMIT = 15000


def send_feishu_text(webhook_url: str, text: str) -> bool:
    """
    通过飞书 Webhook 发送文本消息

    Args:
        webhook_url: 飞书机器人 Webhook URL
        text: 要发送的文本

    Returns:
        是否发送成功
    """
    if not _HAS_REQUESTS:
        return False
    if not webhook_url or not text:
        return False
    payload = {"msg_type": "text", "content": {"text": text[:FEISHU_TEXT_LIMIT]}}
    headers = {"Content-Type": "application/json; charset=utf-8"}
    try:
        r = requests.post(
            webhook_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            timeout=10,
        )
        return r.status_code == 200 and (r.json() or {}).get("code") == 0
    except Exception:
        return False


def send_screening_result(webhook_url: str, summary: str, csv_path: str) -> bool:
    """
    发送筛选结果到飞书：先发摘要，再尝试附带 CSV 内容（若不超限）

    Args:
        webhook_url: 飞书 Webhook URL
        summary: 摘要文本（如 "今日筛选: 港股 3 只, 美股 5 只..."）
        csv_path: CSV 文件路径

    Returns:
        是否至少摘要发送成功
    """
    ok = send_feishu_text(webhook_url, summary)
    if not ok:
        return False
    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            body = f.read()
        if body and len(body) <= FEISHU_TEXT_LIMIT:
            send_feishu_text(webhook_url, "--- CSV 内容 ---\n" + body)
        else:
            send_feishu_text(webhook_url, f"完整 CSV 已保存至: {csv_path}")
    except Exception:
        send_feishu_text(webhook_url, f"完整 CSV 已保存至: {csv_path}")
    return True

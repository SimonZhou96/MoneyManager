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
    发送筛选结果到飞书：先发摘要，再以文件形式发送 CSV（无论大小）

    优先使用飞书应用 API 发送文件；若未配置应用则回退为 Webhook 文本。
    """
    ok = send_feishu_text(webhook_url, summary)
    if not ok:
        return False

    abs_path = os.path.abspath(csv_path)
    if not os.path.exists(abs_path):
        send_feishu_text(webhook_url, f"CSV 文件不存在: {abs_path}")
        return True

    # 优先用应用 API 发送 CSV 文件（支持任意大小，≤30MB）
    try:
        from feishu_app_client import send_file_to_chat
        if send_file_to_chat(abs_path):
            return True
    except Exception:
        pass

    # 未配置应用或发送失败：回退为 Webhook 文本
    try:
        with open(abs_path, "r", encoding="utf-8-sig") as f:
            body = f.read()
        if not body:
            send_feishu_text(webhook_url, f"CSV 为空，完整文件: {abs_path}")
            return True
        content_limit = FEISHU_TEXT_LIMIT - 200
        if len(body) <= content_limit:
            send_feishu_text(webhook_url, "--- CSV 内容 ---\n" + body)
        else:
            lines = body.splitlines()
            header = lines[0] if lines else ""
            data_lines = lines[1:] if len(lines) > 1 else []
            prefix = "--- CSV 内容 ---\n"
            chunk = [header]
            for line in data_lines:
                trial = "\n".join(chunk + [line])
                if len(prefix) + len(trial) > content_limit and len(chunk) >= 1:
                    send_feishu_text(webhook_url, prefix + "\n".join(chunk))
                    chunk = [header, line]
                else:
                    chunk.append(line)
            if chunk:
                send_feishu_text(webhook_url, prefix + "\n".join(chunk))
            send_feishu_text(webhook_url, f"完整 CSV 已保存至: {abs_path}")
    except Exception as e:
        send_feishu_text(webhook_url, f"读取 CSV 失败: {e}\n完整 CSV 已保存至: {abs_path}")
    return True

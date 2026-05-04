#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞书消息通知

支持 Webhook 机器人发送文本消息。
飞书 Webhook 不支持直接发送文件，大内容以文本形式发送或分片。
"""

import json
import os
from typing import List, Sequence, Union

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


def _normalize_csv_paths(csv_paths: Union[str, Sequence[str]]) -> List[str]:
    if isinstance(csv_paths, str):
        return [csv_paths]
    return [p for p in csv_paths if p]


def send_screening_result(webhook_url: str, summary: str, csv_paths: Union[str, Sequence[str]]) -> bool:
    """
    发送筛选结果到飞书：先发摘要，再以文件形式发送 CSV。

    Webhook 不能直接发送文件，所以 CSV 只走飞书应用 API。
    每个文件都会在标准输出打印上传成功/失败，便于定时任务日志排查。
    """
    ok = send_feishu_text(webhook_url, summary)
    if not ok:
        print("[Feishu] 摘要发送失败")
        return False

    paths = _normalize_csv_paths(csv_paths)
    if not paths:
        print("[Feishu] 未提供 CSV 文件路径")
        return True

    try:
        from feishu_app_client import send_file_to_chat
    except Exception as e:
        print(f"[Feishu] 文件发送模块加载失败: {e}")
        send_feishu_text(webhook_url, f"CSV 文件发送失败: 文件发送模块加载失败。")
        return True

    all_ok = True
    for csv_path in paths:
        abs_path = os.path.abspath(csv_path)
        if not os.path.exists(abs_path):
            all_ok = False
            print(f"[Feishu] 文件上传失败: {abs_path} | 文件不存在")
            send_feishu_text(webhook_url, f"CSV 文件不存在: {abs_path}")
            continue

        try:
            file_ok = send_file_to_chat(abs_path)
        except Exception as e:
            file_ok = False
            print(f"[Feishu] 文件上传失败: {abs_path} | {e}")

        if file_ok:
            print(f"[Feishu] 文件上传成功: {abs_path}")
        else:
            all_ok = False
            print(f"[Feishu] 文件上传失败: {abs_path}")
            send_feishu_text(webhook_url, f"CSV 文件发送失败: {abs_path}")

    return all_ok

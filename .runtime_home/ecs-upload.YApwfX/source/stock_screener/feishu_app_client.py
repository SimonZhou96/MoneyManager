#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞书应用 API 客户端 - 支持发送文件到群聊

与 Webhook 不同，需使用 app_id + app_secret 认证，支持上传文件并发送。
限制：单文件 ≤30MB，不允许空文件，token 有效期 2 小时。
"""

import json
import os
import time
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from network_preflight import format_resolution_failures

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

# 飞书 IM 文件大小限制 30MB
FEISHU_FILE_SIZE_LIMIT = 30 * 1024 * 1024

# Token 缓存（提前 5 分钟过期）
_token_cache: Optional[Tuple[str, float]] = None
_TOKEN_EXPIRE_BUFFER = 300


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _dns_retry_attempts() -> int:
    return max(1, _env_int("FEISHU_DNS_RETRY_ATTEMPTS", _env_int("FEISHU_SEND_RETRY_ATTEMPTS", 3)))


def _dns_retry_delay_sec() -> float:
    return max(0.0, _env_float("FEISHU_DNS_RETRY_DELAY_SEC", _env_float("FEISHU_SEND_RETRY_DELAY_SEC", 1.0)))


def _feishu_resolution_failures(prefix: str, hosts: Iterable[str]) -> List[str]:
    return format_resolution_failures(
        prefix,
        hosts,
        attempts=_dns_retry_attempts(),
        retry_delay_sec=_dns_retry_delay_sec(),
    )


def _load_dotenv():
    """加载同目录 .env"""
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def get_tenant_access_token(app_id: str, app_secret: str) -> Optional[str]:
    """
    获取 tenant_access_token（带缓存）
    文档: https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token
    """
    global _token_cache
    now = time.time()
    if _token_cache and _token_cache[1] > now:
        return _token_cache[0]

    if not _HAS_REQUESTS:
        print("获取 token 失败: requests 不可用", file=__import__("sys").stderr)
        return None
    for message in _feishu_resolution_failures("[Feishu] token 获取预检失败", ["https://open.feishu.cn"]):
        print(message, file=__import__("sys").stderr)
        return None
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {"app_id": app_id, "app_secret": app_secret}
    try:
        r = requests.post(url, json=payload, timeout=10)
        data = r.json() or {}
        if r.status_code != 200:
            print(f"获取 token 失败: HTTP {r.status_code} | {str(r.text)[:300]}", file=__import__("sys").stderr)
            return None
        if data.get("code") != 0:
            print(f"获取 token 失败: {data}", file=__import__("sys").stderr)
            return None
        token = data.get("tenant_access_token")
        expire = data.get("expire", 7200)
        _token_cache = (token, now + expire - _TOKEN_EXPIRE_BUFFER)
        return token
    except Exception as e:
        print(f"获取 token 异常: {e}", file=__import__("sys").stderr)
        return None


def upload_file(token: str, file_path: str, file_name: Optional[str] = None) -> Optional[str]:
    """
    上传文件到飞书，获取 file_key
    文档: https://open.feishu.cn/document/server-docs/im-v1/file/create
    限制: ≤30MB，不允许空文件
    """
    if not _HAS_REQUESTS:
        print("上传文件失败: requests 不可用", file=__import__("sys").stderr)
        return None
    path = Path(file_path)
    if not path.exists():
        print(f"文件不存在: {file_path}", file=__import__("sys").stderr)
        return None
    size = path.stat().st_size
    if size == 0:
        print("不允许上传空文件", file=__import__("sys").stderr)
        return None
    if size > FEISHU_FILE_SIZE_LIMIT:
        print(f"文件超过 30MB 限制: {size / (1024*1024):.2f}MB", file=__import__("sys").stderr)
        return None

    name = file_name or path.name
    url = "https://open.feishu.cn/open-apis/im/v1/files"
    headers = {"Authorization": f"Bearer {token}"}
    for message in _feishu_resolution_failures("[Feishu] 文件上传预检失败", [url]):
        print(message, file=__import__("sys").stderr)
        return None
    try:
        with open(path, "rb") as f:
            files = {"file": (name, f, "text/csv" if name.lower().endswith(".csv") else "application/octet-stream")}
            data = {"file_type": "stream", "file_name": name}
            r = requests.post(url, headers=headers, data=data, files=files, timeout=60)
        resp = r.json() or {}
        if r.status_code != 200:
            print(f"上传文件失败: HTTP {r.status_code} | {str(r.text)[:300]}", file=__import__("sys").stderr)
            return None
        if resp.get("code") != 0:
            print(f"上传文件失败: {resp}", file=__import__("sys").stderr)
            return None
        return resp.get("data", {}).get("file_key")
    except Exception as e:
        print(f"上传文件异常: {e}", file=__import__("sys").stderr)
        return None


def send_file_message(token: str, chat_id: str, file_key: str) -> bool:
    """
    发送文件消息到群聊
    文档: https://open.feishu.cn/document/server-docs/im-v1/message/create
    """
    if not _HAS_REQUESTS:
        print("发送消息失败: requests 不可用", file=__import__("sys").stderr)
        return False
    url = "https://open.feishu.cn/open-apis/im/v1/messages"
    params = {"receive_id_type": "chat_id"}
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    content = json.dumps({"file_key": file_key}, ensure_ascii=False)
    payload = {
        "receive_id": chat_id,
        "msg_type": "file",
        "content": content,
    }
    for message in _feishu_resolution_failures("[Feishu] 文件消息发送预检失败", [url]):
        print(message, file=__import__("sys").stderr)
        return False
    try:
        r = requests.post(url, params=params, headers=headers, json=payload, timeout=10)
        data = r.json() or {}
        if r.status_code != 200:
            print(f"发送消息失败: HTTP {r.status_code} | {str(r.text)[:300]}", file=__import__("sys").stderr)
            return False
        if data.get("code") != 0:
            print(f"发送消息失败: {data}", file=__import__("sys").stderr)
            return False
        return True
    except Exception as e:
        print(f"发送消息异常: {e}", file=__import__("sys").stderr)
        return False


def send_file_to_chat(
    file_path: str,
    app_id: Optional[str] = None,
    app_secret: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> bool:
    """
    一站式：上传文件并发送到群聊

    Args:
        file_path: 文件路径
        app_id: 应用 ID（不传则从 FEISHU_APP_ID 读取）
        app_secret: 应用密钥（不传则从 FEISHU_APP_SECRET 读取）
        chat_id: 群聊 ID（不传则从 FEISHU_CHAT_ID 读取）

    Returns:
        是否成功
    """
    _load_dotenv()
    app_id = app_id or os.getenv("FEISHU_APP_ID", "").strip()
    app_secret = app_secret or os.getenv("FEISHU_APP_SECRET", "").strip()
    chat_id = chat_id or os.getenv("FEISHU_CHAT_ID", "").strip()

    if not app_id or not app_secret or not chat_id:
        print("缺少配置: 请设置 FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_CHAT_ID", file=__import__("sys").stderr)
        return False

    token = get_tenant_access_token(app_id, app_secret)
    if not token:
        return False

    file_key = upload_file(token, file_path)
    if not file_key:
        return False

    return send_file_message(token, chat_id, file_key)


if __name__ == "__main__":
    import sys
    _load_dotenv()
    app_id = os.getenv("FEISHU_APP_ID", "").strip()
    app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
    chat_id = os.getenv("FEISHU_CHAT_ID", "").strip()

    if not all([app_id, app_secret, chat_id]):
        print("请在 .env 中配置: FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_CHAT_ID")
        sys.exit(1)

    # 创建测试 CSV
    test_csv = Path(__file__).parent / "logs" / "feishu_file_test.csv"
    test_csv.parent.mkdir(parents=True, exist_ok=True)
    test_csv.write_text("股票代码,市场,名称\nHK.00700,港股,腾讯控股\nUS.AAPL,美股,苹果", encoding="utf-8-sig")
    print(f"测试文件: {test_csv}")

    if send_file_to_chat(str(test_csv)):
        print("✅ 文件发送成功，请检查飞书群")
        sys.exit(0)
    else:
        print("❌ 发送失败")
        sys.exit(1)

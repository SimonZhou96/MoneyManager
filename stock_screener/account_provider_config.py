"""Per-user external-provider settings and isolated provider configuration context."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional


@dataclass(frozen=True)
class ConfigField:
    key: str
    env_name: str
    label: str
    secret: bool = False
    input_type: str = "text"
    placeholder: str = ""
    options: tuple[tuple[str, str], ...] = ()

    def to_public_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "secret": self.secret,
            "input_type": self.input_type,
            "placeholder": self.placeholder,
            "options": [{"value": value, "label": label} for value, label in self.options],
        }


@dataclass(frozen=True)
class ProviderGroup:
    key: str
    label: str
    description: str
    fields: tuple[ConfigField, ...]
    testable: bool = False

    def to_public_dict(self, values: Dict[str, str]) -> dict:
        fields = []
        for field in self.fields:
            public = field.to_public_dict()
            public["value"] = "" if field.secret else values.get(field.env_name, "")
            public["is_set"] = bool(values.get(field.env_name)) if field.secret else False
            fields.append(public)
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "testable": self.testable,
            "fields": fields,
        }


def _field(key: str, env_name: str, label: str, **kwargs: Any) -> ConfigField:
    return ConfigField(key=key, env_name=env_name, label=label, **kwargs)


PROVIDER_GROUPS: tuple[ProviderGroup, ...] = (
    ProviderGroup(
        "llm_routing", "大模型路由", "设置账号使用的大模型顺序。每个账号只读取自己的模型密钥。", (
            _field("provider", "LLM_PROVIDER", "首选提供方", input_type="select", options=(
                ("openai_compatible", "OpenAI 兼容"), ("codex_responses", "Codex Responses"), ("deepseek", "DeepSeek"),
            )),
            _field("provider_order", "LLM_PROVIDER_ORDER", "回退顺序", placeholder="openai_compatible,codex_responses,deepseek"),
        ),
    ),
    ProviderGroup(
        "llm_openai", "OpenAI 兼容 / 阿里云百炼", "适用于兼容 /v1/chat/completions 的服务。", (
            _field("api_base", "LLM_API_BASE", "API 地址", placeholder="https://dashscope.aliyuncs.com/compatible-mode"),
            _field("api_key", "LLM_API_KEY", "API Key", secret=True, placeholder="留空表示保留已保存的 Key"),
            _field("model", "LLM_MODEL", "模型名称", placeholder="qwen-plus"),
        ), True,
    ),
    ProviderGroup(
        "llm_codex", "Codex Responses", "使用 /v1/responses 接口。", (
            _field("api_base", "CODEX_API_BASE", "API 地址", placeholder="https://api.openai.com"),
            _field("api_key", "CODEX_API_KEY", "API Key", secret=True, placeholder="留空表示保留已保存的 Key"),
            _field("model", "CODEX_LLM_MODEL", "模型名称", placeholder="gpt-5.2-codex"),
            _field("reasoning_effort", "CODEX_REASONING_EFFORT", "推理强度", input_type="select", options=(
                ("low", "low"), ("medium", "medium"), ("high", "high"),
            )),
        ), True,
    ),
    ProviderGroup(
        "llm_deepseek", "DeepSeek", "使用 DeepSeek Chat Completions 接口。", (
            _field("api_base", "DEEPSEEK_API_BASE", "API 地址", placeholder="https://api.deepseek.com"),
            _field("api_key", "DEEPSEEK_API_KEY", "API Key", secret=True, placeholder="留空表示保留已保存的 Key"),
            _field("model", "DEEPSEEK_LLM_MODEL", "模型名称", placeholder="deepseek-v4-flash"),
        ), True,
    ),
    ProviderGroup(
        "search_routing", "新闻搜索路由", "按顺序尝试已配置的搜索服务。", (
            _field("provider_order", "SIGNAL_SEARCH_PROVIDER_ORDER", "搜索顺序", placeholder="tavily,zhipuai,bing_baidu"),
        ),
    ),
    ProviderGroup(
        "search_tavily", "Tavily", "新闻和公司事件搜索。", (
            _field("api_key", "TAVILY_API_KEY", "API Key", secret=True, placeholder="留空表示保留已保存的 Key"),
            _field("endpoint", "TAVILY_API_ENDPOINT", "API 地址", placeholder="https://api.tavily.com/search"),
        ), True,
    ),
    ProviderGroup(
        "search_zhipu", "智谱 Web Search", "使用智谱 / BigModel Web Search。", (
            _field("api_key", "ZHIPUAI_API_KEY", "API Key", secret=True, placeholder="留空表示保留已保存的 Key"),
            _field("endpoint", "ZHIPUAI_WEB_SEARCH_ENDPOINT", "API 地址", placeholder="https://open.bigmodel.cn/api/paas/v4/web_search"),
            _field("engine", "ZHIPUAI_WEB_SEARCH_ENGINE", "搜索引擎", placeholder="search_std"),
            _field("content_size", "ZHIPUAI_WEB_SEARCH_CONTENT_SIZE", "内容长度", input_type="select", options=(("small", "small"), ("medium", "medium"), ("large", "large"))),
            _field("recency_filter", "ZHIPUAI_WEB_SEARCH_RECENCY_FILTER", "时间范围", placeholder="noLimit"),
        ), True,
    ),
    ProviderGroup(
        "data_fred", "FRED 宏观数据", "美股宏观指标数据。", (
            _field("api_key", "FRED_API_KEY", "API Key", secret=True, placeholder="留空表示保留已保存的 Key"),
        ), True,
    ),
    ProviderGroup(
        "data_funda", "Funda 基金数据", "美股指数成分和 ETF 持仓数据。", (
            _field("api_key", "FUNDA_API_KEY", "API Key", secret=True, placeholder="留空表示保留已保存的 Key"),
            _field("api_base", "FUNDA_API_BASE", "API 地址", placeholder="https://api.funda.ai"),
        ), True,
    ),
    ProviderGroup(
        "market_intel_routing", "市场资讯源顺序", "调整账号任务聚合市场新闻与公告时使用的来源顺序。", (
            _field("provider_order", "MARKET_INTEL_PROVIDER_ORDER", "资讯源顺序", placeholder="cailianpress,sina,tradingview,eastmoney,global_index"),
        ),
    ),
    ProviderGroup(
        "feishu_webhook", "飞书机器人 Webhook", "用于账号发起的任务完成通知。连接测试会向该机器人发送一条测试消息。", (
            _field("webhook_url", "FEISHU_WEBHOOK_URL", "Webhook 地址", secret=True, placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."),
        ), True,
    ),
    ProviderGroup(
        "feishu_app", "飞书应用", "用于账号发起的筛选结果文件发送。", (
            _field("app_id", "FEISHU_APP_ID", "App ID"),
            _field("app_secret", "FEISHU_APP_SECRET", "App Secret", secret=True),
            _field("chat_id", "FEISHU_CHAT_ID", "群聊 ID"),
        ), True,
    ),
)

PROVIDER_GROUP_BY_KEY = {group.key: group for group in PROVIDER_GROUPS}
PROVIDER_ENV_NAMES = frozenset(
    {field.env_name for group in PROVIDER_GROUPS for field in group.fields}
    | {"DEEPSEEK_MODEL", "ZHIPU_API_KEY", "BIGMODEL_API_KEY"}
)
PROVIDER_FIELD_BY_ENV_NAME = {
    field.env_name: (group, field)
    for group in PROVIDER_GROUPS
    for field in group.fields
}

_account_provider_values: ContextVar[Optional[Dict[str, str]]] = ContextVar(
    "account_provider_values", default=None,
)
_account_user_id: ContextVar[Optional[str]] = ContextVar("account_provider_user_id", default=None)


def provider_config_value(name: str, default: Optional[str] = None) -> Optional[str]:
    """Read an account-owned provider value or the system environment value.

    Within an account context, managed settings never fall back to process-wide
    credentials. Non-provider environment settings remain process-wide.
    """
    values = _account_provider_values.get()
    if values is not None and name in PROVIDER_ENV_NAMES:
        return values.get(name, default)
    return os.getenv(name, default)


@contextmanager
def account_provider_context(user_id: str, values: Dict[str, str]) -> Iterator[None]:
    values_token = _account_provider_values.set(dict(values))
    user_token = _account_user_id.set(str(user_id))
    try:
        yield
    finally:
        _account_provider_values.reset(values_token)
        _account_user_id.reset(user_token)


def current_account_user_id() -> Optional[str]:
    return _account_user_id.get()


def load_account_provider_values(db, user_id: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for provider_key, ciphertext in db.list_web_provider_config_ciphertexts(user_id).items():
        group = PROVIDER_GROUP_BY_KEY.get(provider_key)
        if not group:
            continue
        stored = decrypt_provider_config(ciphertext)
        for field in group.fields:
            value = stored.get(field.key)
            if value is not None:
                values[field.env_name] = str(value)
    return values


def encrypt_provider_config(values: Dict[str, str]) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _encryption_key()
    nonce = secrets.token_bytes(12)
    plaintext = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return b"APC1" + nonce + AESGCM(key).encrypt(nonce, plaintext, b"MoneyManager:web_account_provider_configs:v1")


def decrypt_provider_config(ciphertext: bytes) -> Dict[str, str]:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    payload = bytes(ciphertext or b"")
    if len(payload) < 32 or not payload.startswith(b"APC1"):
        raise ValueError("Provider configuration ciphertext has an unsupported format")
    nonce = payload[4:16]
    plaintext = AESGCM(_encryption_key()).decrypt(
        nonce, payload[16:], b"MoneyManager:web_account_provider_configs:v1",
    )
    value = json.loads(plaintext.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Provider configuration payload must be an object")
    return {str(key): str(item) for key, item in value.items() if item is not None}


def _encryption_key() -> bytes:
    secret = os.getenv("ACCOUNT_CONFIG_ENCRYPTION_KEY", "").strip()
    if not secret:
        raise RuntimeError("ACCOUNT_CONFIG_ENCRYPTION_KEY is not configured")
    # Hashing a high-entropy deployment key gives AES-GCM the required 256-bit key.
    return hashlib.sha256(secret.encode("utf-8")).digest()


def encryption_key_configured() -> bool:
    return bool(os.getenv("ACCOUNT_CONFIG_ENCRYPTION_KEY", "").strip())


def secret_is_configured(values: Dict[str, str], field: ConfigField) -> bool:
    return bool(values.get(field.env_name))


def public_provider_groups(db, user_id: str) -> list[dict]:
    values = load_account_provider_values(db, user_id)
    groups = []
    for group in PROVIDER_GROUPS:
        public = group.to_public_dict(values)
        public["configured"] = any(values.get(field.env_name) for field in group.fields)
        groups.append(public)
    return groups

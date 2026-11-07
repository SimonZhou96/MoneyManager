from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from account_provider_config import (
    PROVIDER_GROUP_BY_KEY,
    PROVIDER_GROUPS,
    account_provider_context,
    decrypt_provider_config,
    encrypt_provider_config,
    encryption_key_configured,
    load_account_provider_values,
    public_provider_groups,
)
from db import MarketDatabase
from .auth import CurrentUser, get_db, require_user


router = APIRouter(prefix="/api/settings/providers", tags=["provider-settings"])


class ProviderConfigPayload(BaseModel):
    values: Dict[str, str] = Field(default_factory=dict)
    clear_fields: List[str] = Field(default_factory=list)


@router.get("")
def get_provider_settings(
    user: CurrentUser = Depends(require_user),
    db: MarketDatabase = Depends(get_db),
):
    if not encryption_key_configured():
        return {
            "encryption_ready": False,
            "groups": [group.to_public_dict({}) | {"configured": False} for group in PROVIDER_GROUPS],
        }
    try:
        groups = public_provider_groups(db, user.user_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="账号配置无法解密，请检查服务端加密密钥") from exc
    return {"encryption_ready": True, "groups": groups}


@router.put("/{provider_key}")
def update_provider_settings(
    provider_key: str,
    payload: ProviderConfigPayload,
    user: CurrentUser = Depends(require_user),
    db: MarketDatabase = Depends(get_db),
):
    if not encryption_key_configured():
        raise HTTPException(status_code=503, detail="服务端未配置账号配置加密密钥")
    group = PROVIDER_GROUP_BY_KEY.get(provider_key)
    if not group:
        raise HTTPException(status_code=404, detail="Provider 配置项不存在")
    fields = {field.key: field for field in group.fields}
    unknown = (set(payload.values) | set(payload.clear_fields)) - set(fields)
    if unknown:
        raise HTTPException(status_code=422, detail="包含未知配置字段")

    stored = _stored_group_values(db, user.user_id, provider_key)
    for key, raw_value in payload.values.items():
        value = str(raw_value or "").strip()
        if len(value) > 4096:
            raise HTTPException(status_code=422, detail=f"{fields[key].label}长度不能超过 4096 个字符")
        if fields[key].secret and not value:
            continue
        _validate_select_value(fields[key], value)
        if value:
            stored[key] = value
        else:
            stored.pop(key, None)
    for key in payload.clear_fields:
        stored.pop(key, None)

    if stored:
        db.upsert_web_provider_config_ciphertext(
            user.user_id, provider_key, encrypt_provider_config(stored),
        )
    else:
        db.delete_web_provider_config_ciphertext(user.user_id, provider_key)
    return {"ok": True}


@router.post("/{provider_key}/test")
def test_provider_settings(
    provider_key: str,
    payload: ProviderConfigPayload,
    user: CurrentUser = Depends(require_user),
    db: MarketDatabase = Depends(get_db),
):
    if not encryption_key_configured():
        raise HTTPException(status_code=503, detail="服务端未配置账号配置加密密钥")
    group = PROVIDER_GROUP_BY_KEY.get(provider_key)
    if not group or not group.testable:
        raise HTTPException(status_code=404, detail="该配置项不支持连接测试")
    fields = {field.key: field for field in group.fields}
    if set(payload.values) - set(fields) or set(payload.clear_fields) - set(fields):
        raise HTTPException(status_code=422, detail="包含未知配置字段")

    try:
        account_values = load_account_provider_values(db, user.user_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="账号配置无法解密，请检查服务端加密密钥") from exc
    stored = _stored_group_values(db, user.user_id, provider_key)
    for key, raw_value in payload.values.items():
        value = str(raw_value or "").strip()
        field = fields[key]
        if field.secret and not value:
            continue
        _validate_select_value(field, value)
        if value:
            stored[key] = value
        else:
            stored.pop(key, None)
    for key in payload.clear_fields:
        stored.pop(key, None)
    for field in group.fields:
        account_values.pop(field.env_name, None)
        if stored.get(field.key):
            account_values[field.env_name] = stored[field.key]

    try:
        with account_provider_context(user.user_id, account_values):
            message = _test_connection(provider_key, account_values)
        return {"ok": True, "message": message}
    except MissingProviderConfig as exc:
        return {"ok": False, "message": str(exc)}
    except ProviderHttpError as exc:
        return {"ok": False, "message": f"连接失败（HTTP {exc.status_code}）"}
    except Exception as exc:
        # Provider errors can contain request details; only expose the error class.
        return {"ok": False, "message": f"连接失败（{type(exc).__name__}）"}


def _stored_group_values(db: MarketDatabase, user_id: str, provider_key: str) -> dict[str, str]:
    try:
        ciphertext = db.list_web_provider_config_ciphertexts(user_id).get(provider_key)
        return decrypt_provider_config(ciphertext) if ciphertext else {}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="账号配置无法解密，请检查服务端加密密钥") from exc


def _validate_select_value(field, value: str) -> None:
    if field.options and value and value not in {option[0] for option in field.options}:
        raise HTTPException(status_code=422, detail=f"{field.label}的选项无效")


class MissingProviderConfig(ValueError):
    pass


class ProviderHttpError(RuntimeError):
    def __init__(self, status_code: int):
        self.status_code = int(status_code)
        super().__init__(f"HTTP {self.status_code}")


def _require(values: dict, env_name: str, label: str) -> str:
    value = str(values.get(env_name) or "").strip()
    if not value:
        raise MissingProviderConfig(f"请先填写 {label}")
    return value


def _test_connection(provider_key: str, values: dict[str, str]) -> str:
    import requests

    timeout = (3, 8)
    if provider_key in {"llm_openai", "llm_codex", "llm_deepseek"}:
        from signal_analysis.factories import LLMProviderFactory
        from signal_analysis.models import AnalysisSettings

        provider_names = {
            "llm_openai": "openai_compatible",
            "llm_codex": "codex_responses",
            "llm_deepseek": "deepseek",
        }
        provider = LLMProviderFactory._provider_from_env(
            provider_names[provider_key], AnalysisSettings(),
        )
        if not getattr(provider, "is_available", False):
            raise MissingProviderConfig("请填写 API Key 和模型名称")
        headers = {"Authorization": f"Bearer {provider.api_key}", "Content-Type": "application/json"}
        if provider_key == "llm_codex":
            response = requests.post(
                provider.responses_url,
                headers=headers,
                json={"model": provider.model, "input": "Reply with OK.", "max_output_tokens": 8},
                timeout=timeout,
            )
        else:
            response = requests.post(
                provider.chat_completions_url,
                headers=headers,
                json={
                    "model": provider.model,
                    "messages": [{"role": "user", "content": "Reply with OK."}],
                    "max_tokens": 8,
                    "temperature": 0,
                },
                timeout=timeout,
            )
        if response.status_code >= 400:
            raise ProviderHttpError(response.status_code)
        return f"连接成功 · {provider.model}"

    if provider_key in {"search_tavily", "search_zhipu"}:
        from signal_analysis.factories import SearchProviderFactory
        from signal_analysis.models import AnalysisSettings

        provider_name = "tavily" if provider_key == "search_tavily" else "zhipuai"
        provider = SearchProviderFactory._provider_from_env(provider_name, AnalysisSettings())
        if not getattr(provider, "is_available", False):
            raise MissingProviderConfig("请先填写 API Key")
        if provider_key == "search_tavily":
            response = requests.post(
                provider.endpoint,
                json={"api_key": provider.api_key, "query": "MoneyManager API connection check", "search_depth": "basic", "max_results": 1},
                timeout=timeout,
            )
        else:
            response = requests.post(
                provider.endpoint,
                headers={"Authorization": f"Bearer {provider.api_key}", "Content-Type": "application/json"},
                json={
                    "search_query": "MoneyManager API connection check",
                    "search_engine": provider.search_engine,
                    "search_intent": False,
                    "count": 1,
                    "search_recency_filter": provider.recency_filter,
                    "content_size": provider.content_size,
                },
                timeout=timeout,
            )
        if response.status_code >= 400:
            raise ProviderHttpError(response.status_code)
        body = response.json() if response.content else {}
        if isinstance(body, dict) and (body.get("error") or body.get("code") not in (None, 0, "0")):
            raise ProviderHttpError(400)
        return "连接成功 · 已完成 1 次搜索请求"

    if provider_key == "data_fred":
        api_key = _require(values, "FRED_API_KEY", "FRED API Key")
        response = requests.get(
            "https://api.stlouisfed.org/fred/series",
            params={"series_id": "GDP", "api_key": api_key, "file_type": "json"},
            timeout=timeout,
        )
        if response.status_code >= 400:
            raise ProviderHttpError(response.status_code)
        body = response.json() if response.content else {}
        if isinstance(body, dict) and body.get("error_message"):
            raise ProviderHttpError(400)
        return "连接成功 · FRED API 可用"

    if provider_key == "data_funda":
        api_key = _require(values, "FUNDA_API_KEY", "Funda API Key")
        base = str(values.get("FUNDA_API_BASE") or "https://api.funda.ai").strip().rstrip("/")
        response = requests.get(
            f"{base}/v1/funds",
            params={"type": "etf-holdings", "ticker": "SPY"},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        if response.status_code >= 400:
            raise ProviderHttpError(response.status_code)
        return "连接成功 · Funda API 可用"

    if provider_key == "feishu_webhook":
        webhook = _require(values, "FEISHU_WEBHOOK_URL", "飞书 Webhook 地址")
        response = requests.post(
            webhook,
            json={"msg_type": "text", "content": {"text": "MoneyManager API 配置连接测试"}},
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=timeout,
        )
        if response.status_code >= 400:
            raise ProviderHttpError(response.status_code)
        body = response.json() if response.content else {}
        if isinstance(body, dict) and body.get("code", 0) != 0:
            raise RuntimeError("Webhook rejected the message")
        return "连接成功 · 已向机器人发送测试消息"

    if provider_key == "feishu_app":
        app_id = _require(values, "FEISHU_APP_ID", "飞书 App ID")
        app_secret = _require(values, "FEISHU_APP_SECRET", "飞书 App Secret")
        response = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=timeout,
        )
        if response.status_code >= 400:
            raise ProviderHttpError(response.status_code)
        body = response.json() if response.content else {}
        if not isinstance(body, dict) or body.get("code") != 0 or not body.get("tenant_access_token"):
            raise RuntimeError("Feishu app authentication failed")
        return "连接成功 · 飞书应用认证通过"

    raise MissingProviderConfig("该配置项暂不支持连接测试")

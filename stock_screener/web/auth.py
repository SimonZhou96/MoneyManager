from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Optional

from fastapi import Cookie, Depends, Header, HTTPException, Request, Response, status

from db import MarketDatabase, verify_password
from .config import agent_token, cookie_secure, mysql_config_from_env, session_cookie_name, session_ttl_hours


@dataclass(frozen=True)
class CurrentUser:
    id: int
    username: str
    role: str


def get_db():
    db = MarketDatabase(mysql_config_from_env())
    try:
        yield db
    finally:
        db.close()


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def authenticate(db: MarketDatabase, username: str, password: str, ip_address: str) -> tuple[bool, Optional[dict], str]:
    if db.count_recent_failed_logins(username, ip_address) >= 10:
        return False, None, "登录失败次数过多，请稍后再试"
    user = db.get_web_user_by_username(username)
    if not user or not user.get("is_active"):
        db.record_login_attempt(username, ip_address, False)
        return False, None, "账号或密码错误"
    if not verify_password(password, user.get("password_hash") or ""):
        db.record_login_attempt(username, ip_address, False)
        return False, None, "账号或密码错误"
    db.record_login_attempt(username, ip_address, True)
    return True, user, ""


def create_login_session(db: MarketDatabase, response: Response, user_id: int) -> None:
    token = db.create_web_session(user_id=user_id, ttl_hours=session_ttl_hours())
    response.set_cookie(
        key=session_cookie_name(),
        value=token,
        max_age=session_ttl_hours() * 3600,
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        path="/",
    )


def clear_login_session(db: MarketDatabase, response: Response, token: Optional[str]) -> None:
    if token:
        db.delete_web_session(token)
    response.delete_cookie(key=session_cookie_name(), path="/")


def require_user(
    db: MarketDatabase = Depends(get_db),
    token: Optional[str] = Cookie(default=None, alias=session_cookie_name()),
) -> CurrentUser:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    user = db.get_user_by_session_token(token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期")
    return CurrentUser(id=int(user["id"]), username=user["username"], role=user["role"])


def optional_user(
    db: MarketDatabase = Depends(get_db),
    token: Optional[str] = Cookie(default=None, alias=session_cookie_name()),
) -> Optional[CurrentUser]:
    if not token:
        return None
    user = db.get_user_by_session_token(token)
    if not user:
        return None
    return CurrentUser(id=int(user["id"]), username=user["username"], role=user["role"])


def require_agent(authorization: str = Header(default="")) -> None:
    expected = agent_token()
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Agent token 未配置")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Agent 未授权")
    token = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Agent token 无效")

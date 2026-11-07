from __future__ import annotations

from contextlib import contextmanager
from typing import AsyncIterator

from fastapi import Depends
from starlette.concurrency import run_in_threadpool

from account_provider_config import account_provider_context, load_account_provider_values
from db import MarketDatabase, MySqlConfig
from .auth import CurrentUser, get_db, optional_user, require_user
from .config import load_dotenv


async def activate_account_provider_context(
    user: CurrentUser = Depends(require_user),
    db: MarketDatabase = Depends(get_db),
) -> AsyncIterator[None]:
    """Make authenticated API calls use only the caller's provider credentials."""
    try:
        values = await run_in_threadpool(load_account_provider_values, db, user.user_id)
    except Exception:
        # Do not fall back to shared process credentials if the account config
        # cannot be read or decrypted.
        values = {}
    context = account_provider_context(user.user_id, values)
    context.__enter__()
    try:
        yield
    finally:
        context.__exit__(None, None, None)


async def activate_optional_account_provider_context(
    user: CurrentUser | None = Depends(optional_user),
    db: MarketDatabase = Depends(get_db),
) -> AsyncIterator[None]:
    """Apply account settings when a route already permits anonymous access."""
    if user is None:
        yield
        return
    try:
        values = await run_in_threadpool(load_account_provider_values, db, user.user_id)
    except Exception:
        values = {}
    context = account_provider_context(user.user_id, values)
    context.__enter__()
    try:
        yield
    finally:
        context.__exit__(None, None, None)


@contextmanager
def persisted_job_provider_context(
    mysql_config: MySqlConfig,
    task_type: str,
    task_id: str,
):
    """Load the owner config for a background job without passing secrets in its queue payload."""
    load_dotenv()
    db = MarketDatabase(mysql_config)
    try:
        row = db.get_single_stock_run(task_id) if task_type == "single_stock" else db.get_web_screening_job(task_id)
        user_id = str(row.get("user_id") or "") if row else ""
        try:
            values = load_account_provider_values(db, user_id) if user_id else {}
        except Exception:
            values = {}
    finally:
        db.close()
    if user_id:
        with account_provider_context(user_id, values):
            yield
    else:
        yield

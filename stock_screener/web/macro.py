"""Authenticated country-level queries; no stock or screening dependencies."""
from typing import Literal

from fastapi import APIRouter, Depends, Query

from potential_analysis.macro_timeseries import get_macro_timeseries
from .auth import CurrentUser, require_user
from .business import BusinessError

router = APIRouter(prefix="/api/macro", tags=["macro"])


def _query(category, country):
    try:
        return get_macro_timeseries(category, country)
    except Exception as exc:
        raise BusinessError("MACRO_QUERY_FAILED", "宏观数据查询失败，请稍后重试") from exc


@router.get("/inflation")
def inflation(country: Literal["CN", "US"] = Query(...),
              user: CurrentUser = Depends(require_user)):
    return _query("inflation", country)


@router.get("/bond-yields")
def bond_yields(country: Literal["CN", "US"] = Query(...),
                user: CurrentUser = Depends(require_user)):
    return _query("bond_yields", country)

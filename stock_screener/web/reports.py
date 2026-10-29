from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from db import MarketDatabase
from task_reports import ElasticsearchReportRepository
from .auth import CurrentUser, get_db, require_user
from .business import BusinessError


router = APIRouter(prefix='/api/reports', tags=['reports'])


def report_repository():
    store = ElasticsearchReportRepository.from_env()
    try:
        yield store
    finally:
        store.close()


class ReportRetryRequest(BaseModel):
    task_type: Literal['market', 'custom_list', 'single_stock', 'scheduled']
    task_id: str


@router.get('')
def list_reports(q: str = Query(default='', max_length=500), page: int = Query(default=1, ge=1),
                 page_size: int = Query(default=20, ge=1, le=100),
                 _: CurrentUser = Depends(require_user), store=Depends(report_repository)):
    return store.search(q, page, page_size)


@router.post('/retry')
def retry_report(payload: ReportRetryRequest, user: CurrentUser = Depends(require_user), db: MarketDatabase = Depends(get_db)):
    from .jobs import enqueue_report_retry
    return enqueue_report_retry(db, payload.task_type, payload.task_id, user)


@router.get('/{report_id}')
def report_detail(report_id: str, _: CurrentUser = Depends(require_user), store=Depends(report_repository)):
    report = store.get(report_id)
    if report is None:
        raise BusinessError('REPORT_NOT_FOUND', '报告不存在或尚未生成')
    return report

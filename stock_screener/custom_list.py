from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

from api.screen_service import run_screening_task
from db import MarketDatabase, MySqlConfig
from market import normalize_market
from scheduled_daily_job import (
    ScreeningPostProcessor,
    get_default_screening_params,
    load_passed_screening_records,
)
from web.single_stock import normalize_stock_code


CUSTOM_LIST_JOB_KIND = "custom_list"
CUSTOM_LIST_RESULT_SCOPE = "all"
MAX_CUSTOM_LIST_CODES = 200

STATUS_VALID = "valid"
STATUS_INVALID = "invalid"
STATUS_DUPLICATE = "duplicate"

STATUS_TEXT_VALID = "等待筛选"
STATUS_TEXT_INVALID = "代码无效"
STATUS_TEXT_DUPLICATE = "重复跳过"
STATUS_TEXT_PASSED = "已通过"
STATUS_TEXT_FAILED = "未通过"

_US_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")


@dataclass
class CustomListParseResult:
    market: str
    input_codes: List[str] = field(default_factory=list)
    valid_codes: List[str] = field(default_factory=list)
    invalid_inputs: List[dict] = field(default_factory=list)
    duplicate_inputs: List[dict] = field(default_factory=list)
    input_status_rows: List[dict] = field(default_factory=list)

    @property
    def input_summary(self) -> dict:
        return {
            "输入数量": len(self.input_codes),
            "有效代码数": len(self.valid_codes),
            "无效代码数": len(self.invalid_inputs),
            "重复代码数": len(self.duplicate_inputs),
        }


class CustomListCodeParser:
    """Parse and normalize single-market custom stock-code inputs."""

    def __init__(self, max_codes: int = MAX_CUSTOM_LIST_CODES):
        self.max_codes = int(max_codes)

    def parse(self, market: str, codes: List[Any]) -> CustomListParseResult:
        market = normalize_market(market)
        if codes is None:
            codes = []
        if len(codes) > self.max_codes:
            raise ValueError(f"自定义股票代码最多支持 {self.max_codes} 个")
        if not codes:
            raise ValueError("股票代码列表不能为空")

        result = CustomListParseResult(market=market)
        seen: set[str] = set()
        for index, raw in enumerate(codes):
            original = str(raw or "").strip()
            result.input_codes.append(original)
            normalized, reason = self._normalize_one(market, original)
            if not normalized:
                row = self._status_row(index, original, STATUS_INVALID, STATUS_TEXT_INVALID, reason=reason)
                result.invalid_inputs.append(row)
                result.input_status_rows.append(row)
                continue
            if normalized in seen:
                row = self._status_row(
                    index,
                    original,
                    STATUS_DUPLICATE,
                    STATUS_TEXT_DUPLICATE,
                    code=normalized,
                    reason="该代码已在本次输入中出现",
                )
                result.duplicate_inputs.append(row)
                result.input_status_rows.append(row)
                continue
            seen.add(normalized)
            result.valid_codes.append(normalized)
            result.input_status_rows.append(
                self._status_row(index, original, STATUS_VALID, STATUS_TEXT_VALID, code=normalized)
            )
        return result

    def _normalize_one(self, market: str, value: str) -> tuple[Optional[str], str]:
        if not value:
            return None, "股票代码不能为空"
        text = value.strip().upper()
        if self._has_foreign_market_hint(market, text):
            return None, "代码市场不匹配"

        if market == "HK":
            normalized = normalize_stock_code(market, text[:-3] if text.endswith(".HK") else text)
            suffix = normalized[3:] if normalized.startswith("HK.") else ""
            if suffix.isdigit() and 1 <= len(suffix) <= 5:
                return normalized, ""
            return None, "港股代码需为 1-5 位数字或 HK. 前缀"

        if market == "A":
            normalized = normalize_stock_code(market, text)
            if normalized.startswith(("SH.", "SZ.", "BJ.")):
                suffix = normalized[3:]
                if suffix.isdigit() and len(suffix) == 6:
                    return normalized, ""
            return None, "A 股代码需为 6 位数字或 SH./SZ./BJ. 前缀"

        if market == "US":
            normalized = normalize_stock_code(market, text[:-3] if text.endswith(".US") else text)
            suffix = normalized[3:] if normalized.startswith("US.") else normalized
            if _US_TICKER_RE.match(suffix) and any(ch.isalpha() for ch in suffix):
                return normalized if normalized.startswith("US.") else f"US.{normalized}", ""
            return None, "美股代码需为英文 ticker"

        return None, "不支持的市场"

    def _has_foreign_market_hint(self, market: str, text: str) -> bool:
        if market != "HK" and (text.startswith("HK.") or text.endswith(".HK")):
            return True
        if market != "A" and (
            text.startswith(("SH.", "SZ.", "BJ.")) or text.endswith((".SS", ".SZ"))
        ):
            return True
        if market != "US" and text.startswith("US."):
            return True
        return False

    def _status_row(
        self,
        index: int,
        original: str,
        status: str,
        status_text: str,
        code: Optional[str] = None,
        reason: str = "",
    ) -> dict:
        row = {
            "index": index,
            "input": original,
            "code": code,
            "status": status,
            "status_text": status_text,
            "状态": status_text,
        }
        if reason:
            row["reason"] = reason
        return row


def build_custom_list_report_sections(row: dict) -> List[dict]:
    """Build report-style explanation blocks for one custom-list result row."""
    filter_details = row.get("filter_details") if isinstance(row.get("filter_details"), list) else []
    macro_details = _extract_macro_details(row, filter_details)
    sections = [
        _decision_section(row),
        _failure_reason_section(row, filter_details),
        _strategy_process_section(filter_details),
        _score_breakdown_section(row, macro_details),
        _macro_evidence_section(macro_details),
    ]
    return [section for section in sections if section]


def _decision_section(row: dict) -> dict:
    is_passed = row.get("is_passed")
    if is_passed is True:
        decision = "通过"
        status = "pass"
    elif is_passed is False:
        decision = "未通过"
        status = "fail"
    else:
        decision = str(row.get("状态") or row.get("status_text") or "等待筛选")
        status = str(row.get("status") or "queued")
    items = [
        {"label": "结论", "value": decision, "status": status},
        {"label": "代码", "value": row.get("code") or row.get("input") or "-"},
    ]
    if row.get("filter_summary"):
        items.append({"label": "筛选摘要", "value": row.get("filter_summary")})
    elif row.get("reason"):
        items.append({"label": "原因", "value": row.get("reason")})
    return {
        "section_key": "decision",
        "title": "筛选结论",
        "summary": row.get("filter_summary") or row.get("reason") or "",
        "items": items,
    }


def _failure_reason_section(row: dict, filter_details: List[dict]) -> Optional[dict]:
    failures = [
        item for item in filter_details
        if str(item.get("result") or "").lower() in {"fail", "error"}
    ]
    if not failures and row.get("reason"):
        failures = [{"result": row.get("status"), "reason": row.get("reason"), "rule_key": row.get("code") or row.get("input")}]
    if not failures:
        return None
    return {
        "section_key": "failure_reasons",
        "title": "关键未通过原因",
        "summary": f"共 {len(failures)} 条规则未通过或异常",
        "items": [_report_rule_item(item) for item in failures],
    }


def _strategy_process_section(filter_details: List[dict]) -> Optional[dict]:
    if not filter_details:
        return None
    return {
        "section_key": "strategy_process",
        "title": "策略过程",
        "summary": "按规则链执行顺序展示每一条策略/筛选条件的判断结果",
        "items": [_report_rule_item(item) for item in filter_details],
    }


def _score_breakdown_section(row: dict, macro_details: dict) -> Optional[dict]:
    has_score = any(row.get(key) is not None for key in ("technical_score", "macro_score", "final_score"))
    if not has_score and not macro_details:
        return None
    items = []
    for label, key in (("技术分", "technical_score"), ("宏观分", "macro_score"), ("综合分", "final_score")):
        if row.get(key) is not None:
            items.append({"label": label, "value": row.get(key)})
    for key, value in (macro_details.get("sub_scores") or {}).items():
        items.append({"label": _macro_dimension_label(str(key)), "value": value})
    summary = macro_details.get("summary") or row.get("filter_summary") or ""
    return {
        "section_key": "score_breakdown",
        "title": "评分拆解",
        "summary": summary,
        "items": items,
    }


def _macro_evidence_section(macro_details: dict) -> Optional[dict]:
    if not macro_details:
        return None
    items = []
    if macro_details.get("temporal_summary"):
        items.append({"label": "证据时效", "value": macro_details.get("temporal_summary")})
    for value in macro_details.get("risks") or []:
        items.append({"label": "风险提示", "value": value, "status": "fail"})
    for value in macro_details.get("data_gaps") or []:
        items.append({"label": "信息缺口", "value": value, "status": "warning"})
    for index, link in enumerate(_normalize_evidence_links(macro_details), 1):
        items.append({
            "label": link.get("label") or f"来源{index}",
            "value": link.get("title") or link.get("url") or link.get("label"),
            "url": link.get("url"),
            "source_type": link.get("source_type"),
        })
    for finding in macro_details.get("temporal_findings") or []:
        if isinstance(finding, dict):
            items.append({
                "label": str(finding.get("type") or "时间线"),
                "value": str(finding.get("description") or finding.get("summary") or ""),
            })
    if not items:
        return None
    return {
        "section_key": "macro_evidence",
        "title": "宏观证据",
        "summary": macro_details.get("temporal_summary") or macro_details.get("summary") or "",
        "items": items,
    }


def _report_rule_item(item: dict) -> dict:
    title = item.get("rule_key") or item.get("rule_name") or item.get("filter_name") or "规则"
    return {
        "title": title,
        "label": item.get("rule_name") or item.get("filter_name") or title,
        "status": item.get("result") or "unknown",
        "reason": item.get("reason") or "",
        "rule_type": item.get("rule_type") or "",
        "strategy_category": item.get("strategy_category") or "",
        "details": _details_preview(item.get("details") or {}),
    }


def _extract_macro_details(row: dict, filter_details: List[dict]) -> dict:
    score_details = row.get("score_details") if isinstance(row.get("score_details"), dict) else {}
    nested = score_details.get("macro_details")
    if isinstance(nested, dict):
        return nested
    for item in filter_details:
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        if details.get("macro_score") is not None:
            return details
    return {}


def _details_preview(details: dict, max_items: int = 6) -> dict:
    preview = {}
    for key, value in details.items():
        if key in {"evidence_refs", "evidence_links", "factor_citations"}:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            preview[key] = value
        elif isinstance(value, list):
            preview[key] = value[:3]
        if len(preview) >= max_items:
            break
    return preview


def _normalize_evidence_links(macro_details: dict) -> List[dict]:
    links = []
    raw_values = []
    for key in ("evidence_refs", "evidence_links", "引用来源"):
        value = macro_details.get(key)
        if isinstance(value, list):
            raw_values.extend(value)
    for value in macro_details.get("source_urls") or []:
        raw_values.append(value)
    for item in raw_values:
        if isinstance(item, str):
            links.append({"label": item, "url": item, "title": item})
        elif isinstance(item, dict):
            if item.get("url") or item.get("label"):
                links.append(dict(item))
    return links


def _macro_dimension_label(key: str) -> str:
    labels = {
        "company_event_strength": "公司事件强度",
        "sector_heat": "板块热度",
        "news_validation": "新闻验证",
        "impact_direction": "影响方向",
        "source_credibility": "来源可信度",
        "freshness": "时效性",
    }
    return labels.get(key, key)


class CustomListJobService:
    """Create and read custom-list web screening jobs."""

    def __init__(self, db: MarketDatabase):
        self.db = db

    def _params_for_chain(self, chain: dict) -> dict:
        params = get_default_screening_params()
        if chain.get("chain_key"):
            params["chain_key"] = chain.get("chain_key")
        if chain.get("chain_name"):
            params["chain_name"] = chain.get("chain_name")
        return params

    def create_job(
        self,
        user_id: Optional[int],
        market: str,
        timeframe: str,
        chain: dict,
        parse_result: CustomListParseResult,
        enable_ai_analysis: bool = True,
        send_feishu: bool = False,
    ) -> dict:
        if not parse_result.valid_codes:
            raise ValueError("自定义股票列表没有可筛选的有效代码")
        job_id = str(uuid.uuid4())
        task_id = str(uuid.uuid4())
        chain_key = chain["chain_key"]
        params = self._params_for_chain(chain)
        options = {
            "job_kind": CUSTOM_LIST_JOB_KIND,
            "task_id": task_id,
            "enable_ai_analysis": bool(enable_ai_analysis),
            "send_feishu": bool(send_feishu),
            "result_upload_scope": CUSTOM_LIST_RESULT_SCOPE,
            "chain_key": chain_key,
            "chain_timeframe": chain.get("chain_timeframe"),
            "chain_name": chain.get("chain_name"),
            "input_codes": parse_result.input_codes,
            "normalized_codes": parse_result.valid_codes,
            "invalid_inputs": parse_result.invalid_inputs,
            "duplicate_inputs": parse_result.duplicate_inputs,
            "input_status_rows": parse_result.input_status_rows,
            "input_summary": parse_result.input_summary,
            "watchlist_by_market": {
                market: [{"code": code, "name": code} for code in parse_result.valid_codes],
            },
        }
        self.db.create_web_screening_job(
            job_id=job_id,
            user_id=user_id,
            markets=[market],
            timeframe=timeframe,
            options=options,
            execution_mode="web_backend",
        )
        self.db.init_schema(timeframe)
        self.db.create_screening_task(
            task_id=task_id,
            market=market,
            timeframe=timeframe,
            total_count=len(parse_result.valid_codes),
            params_json=params,
            check_date=date.today(),
        )
        self.db.update_web_screening_job(
            job_id,
            "running",
            task_ids=[task_id],
            summary={
                "markets": [market],
                "timeframe": timeframe,
                "chain_key": chain_key,
                "chain_timeframe": chain.get("chain_timeframe"),
                "chain_name": chain.get("chain_name"),
                "input_summary": parse_result.input_summary,
                "current_market": market,
                "stage": "custom_list_screening",
            },
        )
        return {
            "job_id": job_id,
            "task_id": task_id,
            "status": "running",
            "runner": "web_backend",
            "market": market,
            "timeframe": timeframe,
            "chain_key": chain_key,
            "chain_timeframe": chain.get("chain_timeframe"),
            "chain_name": chain.get("chain_name"),
            "input_summary": parse_result.input_summary,
            "input_status_rows": parse_result.input_status_rows,
        }

    def build_results(self, job: dict) -> dict:
        options = job.get("options") or {}
        if options.get("job_kind") != CUSTOM_LIST_JOB_KIND:
            raise ValueError("任务不是自定义股票列表筛选任务")

        task_ids = job.get("task_ids") or []
        task_id = task_ids[0] if task_ids else options.get("task_id")
        rows_by_code: Dict[str, dict] = {}
        if task_id:
            offset = 0
            while True:
                rows = self.db.get_screening_results_by_task(task_id, limit=500, offset=offset, passed_only=False)
                if not rows:
                    break
                for row in rows:
                    rows_by_code[row.get("code")] = row
                offset += 500

        merged_rows = []
        for item in options.get("input_status_rows") or []:
            status = item.get("status")
            code = item.get("code")
            if status == STATUS_VALID and code:
                result = rows_by_code.get(code)
                if result:
                    status_text = STATUS_TEXT_PASSED if result.get("is_passed") else STATUS_TEXT_FAILED
                    merged = {
                        **item,
                        **result,
                        "input": item.get("input"),
                        "status": "passed" if result.get("is_passed") else "failed",
                        "status_text": status_text,
                        "状态": status_text,
                    }
                else:
                    merged = {**item, "status_text": STATUS_TEXT_VALID, "状态": STATUS_TEXT_VALID}
            else:
                merged = dict(item)
            merged["report_sections"] = build_custom_list_report_sections(merged)
            merged_rows.append(merged)

        passed_count = len([row for row in merged_rows if row.get("status_text") == STATUS_TEXT_PASSED])
        failed_count = len([row for row in merged_rows if row.get("status_text") == STATUS_TEXT_FAILED])
        return {
            "job_id": job.get("job_id"),
            "status": job.get("status"),
            "market": (job.get("markets") or [None])[0],
            "timeframe": job.get("timeframe"),
            "chain_key": options.get("chain_key"),
            "chain_timeframe": options.get("chain_timeframe"),
            "chain_name": options.get("chain_name"),
            "task_id": task_id,
            "uploaded_result_scope": options.get("result_upload_scope"),
            "input_summary": {
                **(options.get("input_summary") or {}),
                "通过数量": passed_count,
                "未通过数量": failed_count,
            },
            "rows": merged_rows,
        }


@dataclass
class CustomListRunResult:
    market: str
    task_id: Optional[str] = None
    passed: List[dict] = field(default_factory=list)
    csv_paths: List[str] = field(default_factory=list)
    total_count: int = 0


class CustomListScreeningRunner:
    """Run a custom watchlist through the existing screening service."""

    def __init__(self, mysql_config: MySqlConfig):
        self.mysql_config = mysql_config

    def run(
        self,
        job: dict,
        csv_base: str,
        today_str: str,
        enable_ai_analysis: bool = True,
        task_id: Optional[str] = None,
    ) -> CustomListRunResult:
        options = job.get("options") or {}
        market = normalize_market((job.get("markets") or [None])[0] or options.get("market"))
        timeframe = str(job.get("timeframe") or "1d")
        watchlist = (options.get("watchlist_by_market") or {}).get(market) or []
        if not watchlist:
            raise ValueError("自定义股票列表为空，无法筛选")

        chain_key = job.get("chain_key") or options.get("chain_key")
        params = get_default_screening_params()
        if chain_key:
            params["chain_key"] = chain_key
        if options.get("chain_name"):
            params["chain_name"] = options.get("chain_name")

        existing_task_id = task_id or options.get("task_id")
        task_id = existing_task_id or str(uuid.uuid4())
        if not existing_task_id:
            db = MarketDatabase(self.mysql_config)
            try:
                db.init_schema(timeframe)
                db.create_screening_task(
                    task_id=task_id,
                    market=market,
                    timeframe=timeframe,
                    total_count=len(watchlist),
                    params_json=params,
                    check_date=date.today(),
                )
            finally:
                db.close()

        run_screening_task(
            mysql_config=self.mysql_config,
            task_id=task_id,
            market=market,
            timeframe=timeframe,
            params=params,
            verbose=False,
            watchlist=watchlist,
            progress_log=True,
            chain_key=chain_key,
        )

        passed = load_passed_screening_records(self.mysql_config, task_id, market)
        processor = ScreeningPostProcessor(
            mysql_config=self.mysql_config,
            csv_base=f"{csv_base}_custom_{str(job.get('job_id') or task_id)[:8]}",
            today_str=today_str,
            enable_ai_analysis=enable_ai_analysis,
            chain_key=chain_key,
            chain_name=options.get("chain_name") or chain_key,
        )
        csv_paths = processor.process(
            market=market,
            timeframe=timeframe,
            task_id=task_id,
            passed=passed,
        )
        return CustomListRunResult(
            market=market,
            task_id=task_id,
            passed=passed,
            csv_paths=csv_paths,
            total_count=len(watchlist),
        )


def is_custom_list_job(job: dict) -> bool:
    return (job.get("options") or {}).get("job_kind") == CUSTOM_LIST_JOB_KIND

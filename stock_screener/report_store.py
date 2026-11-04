"""MySQL persistence and bilingual text search for task reports."""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import jieba

from web.business import BusinessError


_TOKEN_PARTS = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+|[A-Za-z0-9]+")
_REPORT_COLUMNS = (
    "report_id", "task_id", "task_type", "title", "summary", "markets_json", "codes_json",
    "names_json", "passed_count", "generated_at", "task_date", "timeframe", "chain_key",
    "content_json", "content_text", "search_tokens", "revision",
)


class ReportTextAnalyzer:
    """Normalize report text and queries into the same MySQL FULLTEXT tokens."""

    @staticmethod
    def flatten(value: Any) -> str:
        if isinstance(value, dict):
            return " ".join(ReportTextAnalyzer.flatten(item) for item in value.values())
        if isinstance(value, (list, tuple, set)):
            return " ".join(ReportTextAnalyzer.flatten(item) for item in value)
        if value is None or isinstance(value, bool):
            return "" if value is None else str(value)
        return str(value)

    def tokenize(self, value: str) -> list[str]:
        tokens = []
        for part in _TOKEN_PARTS.findall(value or ""):
            if _contains_cjk(part):
                tokens.extend(token.strip().lower() for token in jieba.cut(part) if token.strip())
            else:
                tokens.append(part.lower())
        return tokens

    def report_tokens(self, report: dict) -> str:
        searchable = " ".join((
            str(report.get("report_id") or ""), str(report.get("task_id") or ""),
            str(report.get("title") or ""), str(report.get("summary") or ""),
            self.flatten(report.get("names") or []), self.flatten(report.get("codes") or []),
            self.flatten(report.get("content") or {}),
        ))
        return " ".join(self.tokenize(searchable))


class MySqlReportRepository:
    """Stores reports and executes indexed full-text queries on a shared DB connection."""

    def __init__(self, database, analyzer: ReportTextAnalyzer | None = None):
        self.database = database
        self.analyzer = analyzer or ReportTextAnalyzer()

    def save(self, report: dict) -> None:
        content = report.get("content") or {}
        values = (
            report["report_id"], report["task_id"], report["task_type"], report["title"], report["summary"],
            _json(report.get("markets") or []), _json(report.get("codes") or []),
            _json(report.get("names") or []), int(report.get("passed_count") or 0),
            _mysql_datetime(report.get("generated_at")), _mysql_date(report.get("task_date")),
            report.get("timeframe") or "", report.get("chain_key") or "", _json(content),
            self.analyzer.flatten(content), self.analyzer.report_tokens(report), int(report.get("revision") or 1),
        )
        sql = """INSERT INTO screening_reports (
                    report_id, task_id, task_type, title, summary, markets_json, codes_json, names_json,
                    passed_count, generated_at, task_date, timeframe, chain_key, content_json,
                    content_text, search_tokens, revision)
                 VALUES (%s, %s, %s, %s, %s, CAST(%s AS JSON), CAST(%s AS JSON), CAST(%s AS JSON),
                         %s, %s, %s, %s, %s, CAST(%s AS JSON), %s, %s, %s)
                 ON DUPLICATE KEY UPDATE
                    task_id=VALUES(task_id), task_type=VALUES(task_type), title=VALUES(title),
                    summary=VALUES(summary), markets_json=VALUES(markets_json), codes_json=VALUES(codes_json),
                    names_json=VALUES(names_json), passed_count=VALUES(passed_count),
                    generated_at=VALUES(generated_at), task_date=VALUES(task_date), timeframe=VALUES(timeframe),
                    chain_key=VALUES(chain_key), content_json=VALUES(content_json),
                    content_text=VALUES(content_text), search_tokens=VALUES(search_tokens), revision=VALUES(revision)"""
        try:
            with self.database.conn.cursor() as cursor:
                cursor.execute(sql, values)
        except Exception as exc:
            raise BusinessError("REPORT_STORE_UNAVAILABLE", "报告写入 MySQL 失败，已保留草稿，可重试入库") from exc

    def get(self, report_id: str) -> dict | None:
        try:
            with self.database.conn.cursor() as cursor:
                cursor.execute(
                    f"SELECT {', '.join(_REPORT_COLUMNS)} FROM screening_reports WHERE report_id=%s",
                    (report_id,),
                )
                row = cursor.fetchone()
        except Exception as exc:
            raise BusinessError("REPORT_STORE_UNAVAILABLE", "报告详情读取暂不可用，请稍后重试") from exc
        return _row_to_report(row) if row else None

    def search(self, q: str = "", page: int = 1, page_size: int = 20) -> dict:
        if page < 1 or not 1 <= page_size <= 100 or page * page_size > 10000:
            raise BusinessError("INVALID_REPORT_PAGINATION", "报告分页参数超出范围")

        query = q.strip()
        tokens = " ".join(self.analyzer.tokenize(query))
        exact_clause = "(report_id=%s OR task_id=%s OR JSON_CONTAINS(codes_json, CAST(%s AS JSON)))"
        exact_params = [query, query, _json(query)]
        relevance_select = "0 AS relevance"
        rank_select = "0 AS exact_match"
        where = "1=1"
        select_params: list[Any] = []
        where_params: list[Any] = []
        rank_params: list[Any] = []
        if query:
            rank_select = f"CASE WHEN {exact_clause} THEN 1 ELSE 0 END AS exact_match"
            rank_params = list(exact_params)
            if tokens:
                relevance_select = "MATCH(search_tokens) AGAINST (%s IN NATURAL LANGUAGE MODE) AS relevance"
                where = f"(MATCH(search_tokens) AGAINST (%s IN NATURAL LANGUAGE MODE)>0 OR {exact_clause})"
                select_params = [tokens]
                where_params = [tokens, *exact_params]
            else:
                where = exact_clause
                where_params = list(exact_params)

        offset = (page - 1) * page_size
        sql = f"""SELECT report_id, task_id, task_type, title, summary, markets_json, codes_json,
                         names_json, passed_count, generated_at, task_date, timeframe, chain_key,
                         content_text, {relevance_select}, {rank_select}
                  FROM screening_reports WHERE {where}
                  ORDER BY exact_match DESC, relevance DESC, generated_at DESC, report_id ASC
                  LIMIT %s OFFSET %s"""
        count_sql = f"SELECT COUNT(*) FROM screening_reports WHERE {where}"
        try:
            with self.database.conn.cursor() as cursor:
                cursor.execute(count_sql, where_params)
                total = int((cursor.fetchone() or (0,))[0])
                cursor.execute(sql, [*select_params, *rank_params, *where_params, page_size, offset])
                reports = [_row_to_list_report(row) for row in cursor.fetchall() or []]
        except BusinessError:
            raise
        except Exception as exc:
            raise BusinessError("REPORT_STORE_UNAVAILABLE", "报告搜索暂不可用，请稍后重试") from exc

        if query:
            for report in reports:
                report["highlights"] = self._highlights(report, query)
        else:
            for report in reports:
                report.pop("content_text", None)
        return {"reports": reports, "total": total, "page": page, "page_size": page_size}

    def _highlights(self, report: dict, query: str) -> dict:
        tokens = list(dict.fromkeys(self.analyzer.tokenize(query)))
        highlights = {}
        for field in ("title", "summary", "task_id"):
            parts = _highlight_segments(str(report.get(field) or ""), tokens)
            if any(part["matched"] for part in parts):
                highlights[field] = [parts]
        for field in ("names", "codes"):
            text = " · ".join(report.get(field) or [])
            parts = _highlight_segments(text, tokens)
            if any(part["matched"] for part in parts):
                highlights["company_names" if field == "names" else "codes"] = [parts]
        snippets = _highlight_snippets(str(report.pop("content_text", "") or ""), tokens)
        if snippets:
            highlights["content_text"] = snippets
        return highlights


def _highlight_snippets(text: str, tokens: list[str], *, fragment_size: int = 160, limit: int = 2) -> list[list[dict]]:
    spans = _matching_spans(text, tokens)
    if not spans:
        return []
    fragments = []
    used_until = -1
    for start, end in spans:
        fragment_start = max(0, start - fragment_size // 2)
        fragment_end = min(len(text), fragment_start + fragment_size)
        if fragment_start < used_until:
            continue
        clipped = [(max(a, fragment_start) - fragment_start, min(b, fragment_end) - fragment_start)
                   for a, b in spans if a < fragment_end and b > fragment_start]
        fragments.append(_segments_from_spans(text[fragment_start:fragment_end], clipped))
        used_until = fragment_end
        if len(fragments) >= limit:
            break
    return fragments


def _highlight_segments(text: str, tokens: list[str]) -> list[dict]:
    return _segments_from_spans(text, _matching_spans(text, tokens))


def _matching_spans(text: str, tokens: list[str]) -> list[tuple[int, int]]:
    spans = []
    for token in tokens:
        if not token:
            continue
        needle = re.escape(token)
        spans.extend((match.start(), match.end()) for match in re.finditer(needle, text, re.IGNORECASE))
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _segments_from_spans(text: str, spans: list[tuple[int, int]]) -> list[dict]:
    segments = []
    cursor = 0
    for start, end in spans:
        if start > cursor:
            segments.append({"text": text[cursor:start], "matched": False})
        if end > start:
            segments.append({"text": text[start:end], "matched": True})
        cursor = max(cursor, end)
    if cursor < len(text):
        segments.append({"text": text[cursor:], "matched": False})
    return segments or ([{"text": text, "matched": False}] if text else [])


def _contains_cjk(value: str) -> bool:
    return any("\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff" for char in value)


def _json(value: Any) -> str:
    return json.dumps(_json_safe(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _json_safe(value: Any):
    """Convert report values to the JSON types supported by MySQL JSON columns."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        items = sorted(value, key=str) if isinstance(value, set) else value
        return [_json_safe(item) for item in items]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    # Accept NumPy and similar scalar values without depending on that library.
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _json_safe(item())
        except (TypeError, ValueError):
            pass
    return str(value)


def _mysql_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return result.astimezone(timezone.utc).replace(tzinfo=None) if result.tzinfo else result


def _mysql_date(value: str | date) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _row_to_report(row, *, include_content: bool = True) -> dict:
    if not row:
        return {}
    report = dict(zip(_REPORT_COLUMNS, row))
    for field in ("markets_json", "codes_json", "names_json", "content_json"):
        value = report.get(field)
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                value = {} if field == "content_json" else []
        report[field] = value if value is not None else ({} if field == "content_json" else [])
    for source, target in (("markets_json", "markets"), ("codes_json", "codes"),
                           ("names_json", "names"), ("content_json", "content")):
        report[target] = report.pop(source)
    for field in ("generated_at", "task_date"):
        value = report.get(field)
        if isinstance(value, (datetime, date)):
            report[field] = value.isoformat()
    report.pop("search_tokens", None)
    if not include_content:
        report.pop("content", None)
        report.pop("revision", None)
    report.pop("content_text", None)
    return report


def _row_to_list_report(row) -> dict:
    names = (
        "report_id", "task_id", "task_type", "title", "summary", "markets_json", "codes_json",
        "names_json", "passed_count", "generated_at", "task_date", "timeframe", "chain_key",
        "content_text", "relevance", "exact_match",
    )
    report = dict(zip(names, row))
    for source, target in (("markets_json", "markets"), ("codes_json", "codes"), ("names_json", "names")):
        report[target] = _decode_json(report.pop(source))
    for field in ("generated_at", "task_date"):
        value = report.get(field)
        if isinstance(value, (datetime, date)):
            report[field] = value.isoformat()
    report.pop("relevance", None)
    report.pop("exact_match", None)
    return report


def _decode_json(value):
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    return json.loads(value) if isinstance(value, str) else value

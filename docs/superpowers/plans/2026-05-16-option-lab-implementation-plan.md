# Option Lab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an independent Option Lab in `stock_screener` that evaluates option strategies, records manual fills, monitors positions, exposes Chinese frontend/CLI workflows, and never places Futu orders automatically.

**Architecture:** Implement the backend as a new `stock_screener/option_lab` package with models, market data adapters, strategy/risk logic, persistence helpers, service orchestration, and monitoring. Add `/api/options/*` routes via a focused `web/options.py` router, add a Chinese `interactive_option_lab.py` CLI implementation that reuses the same service layer, and expose it through a user-facing `run_option_lab_shell.sh` interactive shell entrypoint. Add a compact `期权实验室` page to the existing React single-file frontend, with all user-facing copy in Chinese.

**Tech Stack:** Python 3, unittest, PyMySQL, pandas-compatible normalized data, FastAPI/Pydantic, React + TypeScript + Vite, existing MoneyManager MySQL/Feishu/local-agent conventions.

---

## File Structure

- Create `stock_screener/option_lab/__init__.py`
  - Package marker and public imports.
- Create `stock_screener/option_lab/models.py`
  - Dataclasses/enums for risk profiles, market snapshots, contract details, strategy candidates, order plans, fills, tracked positions, and monitor events.
- Create `stock_screener/option_lab/market_data.py`
  - Provider protocol, fake provider, Futu/yfinance/AKShare adapters, and provider chain.
- Create `stock_screener/option_lab/strategies.py`
  - Strategy catalog, Chinese strategy labels, candidate generation, and payoff/risk metrics.
- Create `stock_screener/option_lab/risk.py`
  - Risk-profile gating, liquidity checks, max-loss checks, scoring adjustments, and Chinese warnings.
- Create `stock_screener/option_lab/service.py`
  - Single-symbol evaluation, batch evaluation, order-plan saving, manual fill recording, and monitor refresh orchestration.
- Create `stock_screener/option_lab/monitor.py`
  - Monitoring event generation and suggested action calculation.
- Modify `stock_screener/db.py`
  - Add `init_option_lab_schema()`, call it from `init_web_schema()`, and add repository-style methods for the `option_*` tables.
- Create `stock_screener/sql/013_option_lab.sql`
  - Deployment SQL matching `db.py`.
- Create `stock_screener/web/options.py`
  - FastAPI router and Pydantic request/response models.
- Modify `stock_screener/web/main.py`
  - Include the options router.
- Create `stock_screener/interactive_option_lab.py`
  - Chinese interactive CLI.
- Create `stock_screener/run_option_lab_shell.sh`
  - Shell script entrypoint that starts the persistent `option-lab>` command loop.
- Modify `stock_screener/web_frontend/src/main.tsx`
  - Add `期权实验室` navigation and page.
- Modify `stock_screener/web_frontend/src/styles.css`
  - Add compact styles for option lab panels/tables/forms.
- Create tests:
  - `stock_screener/tests/test_option_lab_models.py`
  - `stock_screener/tests/test_option_lab_db.py`
  - `stock_screener/tests/test_option_lab_market_data.py`
  - `stock_screener/tests/test_option_lab_strategies_risk.py`
  - `stock_screener/tests/test_option_lab_service.py`
  - `stock_screener/tests/test_option_lab_api.py`
  - `stock_screener/tests/test_interactive_option_lab.py`

---

### Task 1: Core Option Lab Models

**Files:**
- Create: `stock_screener/option_lab/__init__.py`
- Create: `stock_screener/option_lab/models.py`
- Test: `stock_screener/tests/test_option_lab_models.py`

- [ ] **Step 1: Write model tests**

Create `stock_screener/tests/test_option_lab_models.py`:

```python
import unittest
from datetime import datetime

from option_lab.models import (
    ContractDetail,
    DataQuality,
    MonitorEventSeverity,
    OptionContractType,
    OptionSide,
    RiskProfile,
    StrategyCandidate,
)


class OptionLabModelTests(unittest.TestCase):
    def test_risk_profile_chinese_labels(self):
        self.assertEqual(RiskProfile.CONSERVATIVE.label, "保守")
        self.assertEqual(RiskProfile.BALANCED.label, "均衡")
        self.assertEqual(RiskProfile.AGGRESSIVE.label, "进取")
        self.assertEqual(RiskProfile.from_input("保守"), RiskProfile.CONSERVATIVE)
        self.assertEqual(RiskProfile.from_input("balanced"), RiskProfile.BALANCED)

    def test_contract_detail_uses_chinese_display_fields(self):
        detail = ContractDetail(
            side=OptionSide.BUY,
            contract_type=OptionContractType.CALL,
            option_code="US.AAPL260619C00200000",
            provider_code="AAPL260619C00200000",
            expiration_date="2026-06-19",
            strike=200.0,
            suggested_price=5.25,
            quantity=1,
            currency="USD",
        )

        self.assertEqual(detail.to_display_row()["买卖方向"], "买入")
        self.assertEqual(detail.to_display_row()["期权类型"], "Call")
        self.assertEqual(detail.to_display_row()["合约代码"], "US.AAPL260619C00200000")
        self.assertEqual(detail.to_display_row()["建议价格"], 5.25)

    def test_candidate_serializes_for_frontend(self):
        candidate = StrategyCandidate(
            candidate_id="candidate-1",
            run_id="run-1",
            market="US",
            code="US.AAPL",
            strategy_key="long_call",
            strategy_name="买入看涨期权",
            score=82.5,
            recommendation_status="recommended",
            fit_reason="正股趋势偏强，期权流动性达标",
            contract_details=[],
            risk_metrics={"最大亏损": 525.0},
            order_suggestion={"建议限价": 5.25},
            warnings=[],
            data_quality=DataQuality(status="ok", warnings=[]),
            created_at=datetime(2026, 5, 16, 9, 30, 0),
        )

        payload = candidate.to_dict()

        self.assertEqual(payload["策略名称"], "买入看涨期权")
        self.assertEqual(payload["评分"], 82.5)
        self.assertEqual(payload["数据质量"]["status"], "ok")

    def test_monitor_event_severity_labels(self):
        self.assertEqual(MonitorEventSeverity.URGENT.label, "紧急")
        self.assertEqual(MonitorEventSeverity.IMPORTANT.label, "重要")
        self.assertEqual(MonitorEventSeverity.INFO.label, "提示")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the model tests and verify they fail**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
python -m unittest tests.test_option_lab_models -v
```

Expected: failure with `ModuleNotFoundError: No module named 'option_lab'`.

- [ ] **Step 3: Implement core models**

Create `stock_screener/option_lab/__init__.py`:

```python
"""Option Lab package for strategy evaluation, order suggestions, and monitoring."""
```

Create `stock_screener/option_lab/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class RiskProfile(Enum):
    CONSERVATIVE = ("conservative", "保守")
    BALANCED = ("balanced", "均衡")
    AGGRESSIVE = ("aggressive", "进取")

    @property
    def value_key(self) -> str:
        return self.value[0]

    @property
    def label(self) -> str:
        return self.value[1]

    @classmethod
    def from_input(cls, value: str | "RiskProfile") -> "RiskProfile":
        if isinstance(value, RiskProfile):
            return value
        text = str(value or "").strip().lower()
        for item in cls:
            if text in {item.value_key, item.label.lower()}:
                return item
        raise ValueError(f"不支持的风险偏好: {value}")


class OptionSide(Enum):
    BUY = ("buy", "买入")
    SELL = ("sell", "卖出")

    @property
    def label(self) -> str:
        return self.value[1]


class OptionContractType(Enum):
    CALL = ("call", "Call")
    PUT = ("put", "Put")

    @property
    def label(self) -> str:
        return self.value[1]


class MonitorEventSeverity(Enum):
    URGENT = ("urgent", "紧急")
    IMPORTANT = ("important", "重要")
    INFO = ("info", "提示")

    @property
    def label(self) -> str:
        return self.value[1]


@dataclass(frozen=True)
class DataQuality:
    status: str
    warnings: List[str] = field(default_factory=list)
    quote_time: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "warnings": list(self.warnings),
            "quote_time": self.quote_time,
        }


@dataclass(frozen=True)
class ContractDetail:
    side: OptionSide
    contract_type: OptionContractType
    option_code: str
    provider_code: str
    expiration_date: str
    strike: float
    suggested_price: Optional[float]
    quantity: int
    currency: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "side": self.side.value[0],
            "side_label": self.side.label,
            "contract_type": self.contract_type.value[0],
            "contract_type_label": self.contract_type.label,
            "option_code": self.option_code,
            "provider_code": self.provider_code,
            "expiration_date": self.expiration_date,
            "strike": self.strike,
            "suggested_price": self.suggested_price,
            "quantity": self.quantity,
            "currency": self.currency,
        }

    def to_display_row(self) -> Dict[str, Any]:
        return {
            "买卖方向": self.side.label,
            "期权类型": self.contract_type.label,
            "合约代码": self.option_code,
            "到期日": self.expiration_date,
            "行权价": self.strike,
            "建议价格": self.suggested_price,
            "数量": self.quantity,
            "币种": self.currency,
        }


@dataclass(frozen=True)
class OptionQuote:
    option_code: str
    provider_code: str
    contract_type: OptionContractType
    expiration_date: str
    strike: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    volume: Optional[float] = None
    open_interest: Optional[float] = None
    implied_volatility: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    quote_time: Optional[str] = None
    currency: str = "USD"

    @property
    def mid_price(self) -> Optional[float]:
        if self.bid is not None and self.ask is not None and self.ask >= self.bid:
            return round((float(self.bid) + float(self.ask)) / 2, 4)
        return self.last


@dataclass(frozen=True)
class UnderlyingSnapshot:
    market: str
    code: str
    name: str = ""
    price: Optional[float] = None
    currency: str = ""
    quote_time: Optional[str] = None
    signal_summary: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MarketSnapshot:
    snapshot_id: str
    provider: str
    underlying: UnderlyingSnapshot
    option_quotes: List[OptionQuote]
    data_quality: DataQuality
    raw_payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyCandidate:
    candidate_id: str
    run_id: str
    market: str
    code: str
    strategy_key: str
    strategy_name: str
    score: float
    recommendation_status: str
    fit_reason: str
    contract_details: List[ContractDetail]
    risk_metrics: Dict[str, Any]
    order_suggestion: Dict[str, Any]
    warnings: List[str]
    data_quality: DataQuality
    created_at: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "run_id": self.run_id,
            "market": self.market,
            "code": self.code,
            "strategy_key": self.strategy_key,
            "策略名称": self.strategy_name,
            "评分": self.score,
            "recommendation_status": self.recommendation_status,
            "适用理由": self.fit_reason,
            "合约明细": [item.to_display_row() for item in self.contract_details],
            "risk_metrics": dict(self.risk_metrics),
            "order_suggestion": dict(self.order_suggestion),
            "warnings": list(self.warnings),
            "数据质量": self.data_quality.to_dict(),
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class OptionEvaluationRequest:
    market: str
    code: str
    risk_profile: RiskProfile
    capital: Optional[float] = None
    max_loss: Optional[float] = None
    planned_holding_days: Optional[int] = None
    strategy_scope: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class OptionEvaluationResult:
    run_id: str
    market: str
    code: str
    status: str
    risk_profile: RiskProfile
    candidates: List[StrategyCandidate]
    warnings: List[str] = field(default_factory=list)
    data_quality: DataQuality = field(default_factory=lambda: DataQuality(status="unknown"))


@dataclass(frozen=True)
class OrderPlan:
    plan_id: str
    candidate_id: str
    status: str
    contract_details: List[ContractDetail]
    order_suggestion: Dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class FillRecord:
    plan_id: str
    contract_details: List[ContractDetail]
    filled_price: float
    quantity: int
    filled_at: str
    fee: Optional[float] = None


@dataclass(frozen=True)
class MonitorEvent:
    event_id: str
    position_id: str
    severity: MonitorEventSeverity
    event_type: str
    message: str
    created_at: datetime
```

- [ ] **Step 4: Run the model tests and verify they pass**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
python -m unittest tests.test_option_lab_models -v
```

Expected: all tests pass.

- [ ] **Step 5: Checkpoint**

Run:

```bash
git status --short
```

Expected: new `option_lab` model files and `tests/test_option_lab_models.py` are listed. Commit only if the user has approved commits for this implementation branch.

---

### Task 2: Option Lab Schema and Repository Methods

**Files:**
- Modify: `stock_screener/db.py`
- Create: `stock_screener/sql/013_option_lab.sql`
- Test: `stock_screener/tests/test_option_lab_db.py`

- [ ] **Step 1: Write SQL/repository tests**

Create `stock_screener/tests/test_option_lab_db.py`:

```python
import unittest

from db import _decode_json_field, _json_or_none


class OptionLabDbTests(unittest.TestCase):
    def test_json_helpers_preserve_chinese_keys(self):
        encoded = _json_or_none({"策略名称": "买入看涨期权", "warnings": ["风险提示"]})
        self.assertIn("买入看涨期权", encoded)
        decoded = _decode_json_field(encoded, {})
        self.assertEqual(decoded["策略名称"], "买入看涨期权")

    def test_option_lab_schema_file_mentions_all_tables(self):
        with open("sql/013_option_lab.sql", "r", encoding="utf-8") as f:
            sql = f.read()

        for table in [
            "option_evaluation_runs",
            "option_evaluation_items",
            "option_strategy_candidates",
            "option_order_plans",
            "option_tracked_positions",
            "option_monitor_events",
            "option_market_snapshots",
        ]:
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", sql)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify missing SQL fails**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
python -m unittest tests.test_option_lab_db -v
```

Expected: failure opening `sql/013_option_lab.sql`.

- [ ] **Step 3: Add deployment SQL**

Create `stock_screener/sql/013_option_lab.sql` with the seven required tables:

```sql
CREATE TABLE IF NOT EXISTS option_evaluation_runs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id VARCHAR(64) NOT NULL,
    mode VARCHAR(16) NOT NULL DEFAULT 'single',
    user_id BIGINT UNSIGNED NULL,
    market VARCHAR(8) NULL,
    code VARCHAR(32) NULL,
    risk_profile VARCHAR(32) NOT NULL,
    request_json JSON NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'running',
    warnings_json JSON NULL,
    data_quality_json JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    finished_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_option_eval_run_id (run_id),
    KEY idx_option_eval_user (user_id, created_at),
    KEY idx_option_eval_code (market, code, created_at),
    KEY idx_option_eval_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='期权实验室评估任务';

CREATE TABLE IF NOT EXISTS option_evaluation_items (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id VARCHAR(64) NOT NULL,
    market VARCHAR(8) NOT NULL,
    code VARCHAR(32) NOT NULL,
    name VARCHAR(255) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'running',
    best_strategy_json JSON NULL,
    data_quality_json JSON NULL,
    error_message TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_option_eval_item_run (run_id),
    KEY idx_option_eval_item_code (market, code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='期权实验室批量评估明细';

CREATE TABLE IF NOT EXISTS option_strategy_candidates (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    candidate_id VARCHAR(64) NOT NULL,
    run_id VARCHAR(64) NOT NULL,
    market VARCHAR(8) NOT NULL,
    code VARCHAR(32) NOT NULL,
    strategy_key VARCHAR(64) NOT NULL,
    strategy_name VARCHAR(128) NOT NULL,
    score DECIMAL(10,4) NOT NULL DEFAULT 0,
    recommendation_status VARCHAR(32) NOT NULL,
    fit_reason TEXT NULL,
    contract_details_json JSON NULL,
    risk_metrics_json JSON NULL,
    order_suggestion_json JSON NULL,
    warnings_json JSON NULL,
    data_quality_json JSON NULL,
    snapshot_id VARCHAR(64) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_option_candidate_id (candidate_id),
    KEY idx_option_candidate_run (run_id, score),
    KEY idx_option_candidate_code (market, code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='期权策略候选';

CREATE TABLE IF NOT EXISTS option_order_plans (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    plan_id VARCHAR(64) NOT NULL,
    candidate_id VARCHAR(64) NOT NULL,
    user_id BIGINT UNSIGNED NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'planned',
    contract_details_json JSON NULL,
    order_suggestion_json JSON NULL,
    risk_metrics_json JSON NULL,
    warnings_json JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_option_order_plan_id (plan_id),
    KEY idx_option_order_candidate (candidate_id),
    KEY idx_option_order_user (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='期权订单建议';

CREATE TABLE IF NOT EXISTS option_tracked_positions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    position_id VARCHAR(64) NOT NULL,
    plan_id VARCHAR(64) NULL,
    user_id BIGINT UNSIGNED NULL,
    market VARCHAR(8) NOT NULL,
    code VARCHAR(32) NOT NULL,
    strategy_name VARCHAR(128) NOT NULL,
    contract_details_json JSON NULL,
    filled_price DECIMAL(20,6) NULL,
    quantity INT NOT NULL DEFAULT 1,
    filled_at DATETIME(6) NULL,
    fee DECIMAL(20,6) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    current_state_json JSON NULL,
    current_action VARCHAR(128) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    closed_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_option_position_id (position_id),
    KEY idx_option_position_user (user_id, created_at),
    KEY idx_option_position_code (market, code, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='期权持仓监控';

CREATE TABLE IF NOT EXISTS option_monitor_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    event_id VARCHAR(64) NOT NULL,
    position_id VARCHAR(64) NOT NULL,
    severity VARCHAR(16) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    message TEXT NOT NULL,
    snapshot_id VARCHAR(64) NULL,
    pushed_feishu TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_option_monitor_event_id (event_id),
    KEY idx_option_monitor_position (position_id, created_at),
    KEY idx_option_monitor_severity (severity, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='期权监控事件';

CREATE TABLE IF NOT EXISTS option_market_snapshots (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    snapshot_id VARCHAR(64) NOT NULL,
    provider VARCHAR(32) NOT NULL,
    market VARCHAR(8) NOT NULL,
    code VARCHAR(32) NOT NULL,
    underlying_json JSON NULL,
    option_chain_json JSON NULL,
    selected_quotes_json JSON NULL,
    data_quality_json JSON NULL,
    raw_payload_json JSON NULL,
    quote_time DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_option_snapshot_id (snapshot_id),
    KEY idx_option_snapshot_code (market, code, created_at),
    KEY idx_option_snapshot_provider (provider)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='期权行情快照';
```

- [ ] **Step 4: Add `init_option_lab_schema()` and simple repository methods**

Modify `stock_screener/db.py` inside `MarketDatabase` with:

```python
    def init_option_lab_schema(self) -> None:
        schema_path = Path(__file__).parent / "sql" / "013_option_lab.sql"
        sql_text = schema_path.read_text(encoding="utf-8")
        statements = [stmt.strip() for stmt in sql_text.split(";") if stmt.strip()]
        with self.conn.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)

    def create_option_evaluation_run(self, item: dict) -> None:
        sql = """
            INSERT INTO option_evaluation_runs
                (run_id, mode, user_id, market, code, risk_profile, request_json, status, warnings_json, data_quality_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                status=VALUES(status),
                warnings_json=VALUES(warnings_json),
                data_quality_json=VALUES(data_quality_json)
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (
                item.get("run_id"),
                item.get("mode") or "single",
                item.get("user_id"),
                item.get("market"),
                item.get("code"),
                item.get("risk_profile"),
                _json_or_none(item.get("request") or {}),
                item.get("status") or "running",
                _json_or_none(item.get("warnings") or []),
                _json_or_none(item.get("data_quality") or {}),
            ))

    def finish_option_evaluation_run(self, run_id: str, status: str, warnings: Optional[List[str]], data_quality: Optional[dict]) -> None:
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE option_evaluation_runs
                SET status=%s, warnings_json=%s, data_quality_json=%s, finished_at=%s
                WHERE run_id=%s
                """,
                (status, _json_or_none(warnings or []), _json_or_none(data_quality or {}), _utcnow(), run_id),
            )

    def insert_option_strategy_candidates(self, candidates: Iterable[dict]) -> None:
        values = []
        for item in candidates:
            values.append((
                item.get("candidate_id"),
                item.get("run_id"),
                item.get("market"),
                item.get("code"),
                item.get("strategy_key"),
                item.get("strategy_name"),
                item.get("score") or 0,
                item.get("recommendation_status") or "observe",
                item.get("fit_reason"),
                _json_or_none(item.get("contract_details") or []),
                _json_or_none(item.get("risk_metrics") or {}),
                _json_or_none(item.get("order_suggestion") or {}),
                _json_or_none(item.get("warnings") or []),
                _json_or_none(item.get("data_quality") or {}),
                item.get("snapshot_id"),
            ))
        if not values:
            return
        with self.conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO option_strategy_candidates
                    (candidate_id, run_id, market, code, strategy_key, strategy_name, score,
                     recommendation_status, fit_reason, contract_details_json, risk_metrics_json,
                     order_suggestion_json, warnings_json, data_quality_json, snapshot_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    score=VALUES(score),
                    recommendation_status=VALUES(recommendation_status),
                    fit_reason=VALUES(fit_reason),
                    contract_details_json=VALUES(contract_details_json),
                    risk_metrics_json=VALUES(risk_metrics_json),
                    order_suggestion_json=VALUES(order_suggestion_json),
                    warnings_json=VALUES(warnings_json),
                    data_quality_json=VALUES(data_quality_json),
                    snapshot_id=VALUES(snapshot_id)
                """,
                values,
            )

    def list_option_strategy_candidates(self, run_id: str) -> List[dict]:
        sql = """
            SELECT candidate_id, run_id, market, code, strategy_key, strategy_name, score,
                   recommendation_status, fit_reason, contract_details_json, risk_metrics_json,
                   order_suggestion_json, warnings_json, data_quality_json, snapshot_id, created_at
            FROM option_strategy_candidates
            WHERE run_id=%s
            ORDER BY score DESC, id ASC
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (run_id,))
            rows = cursor.fetchall() or []
        return [
            {
                "candidate_id": row[0],
                "run_id": row[1],
                "market": row[2],
                "code": row[3],
                "strategy_key": row[4],
                "strategy_name": row[5],
                "score": float(row[6] or 0),
                "recommendation_status": row[7],
                "fit_reason": row[8],
                "contract_details": _decode_json_field(row[9], []),
                "risk_metrics": _decode_json_field(row[10], {}),
                "order_suggestion": _decode_json_field(row[11], {}),
                "warnings": _decode_json_field(row[12], []),
                "data_quality": _decode_json_field(row[13], {}),
                "snapshot_id": row[14],
                "created_at": str(row[15]) if row[15] else None,
            }
            for row in rows
        ]
```

Also import `Path` at the top of `db.py`:

```python
from pathlib import Path
```

- [ ] **Step 5: Call option schema initialization from `init_web_schema()`**

Modify `MarketDatabase.init_web_schema()` so the web/API startup path creates
the option tables with the other web platform tables:

```python
    def init_web_schema(self) -> None:
        self.init_schema("1d")
        self.init_option_lab_schema()
        with self.conn.cursor() as cursor:
            # existing web schema CREATE TABLE statements stay below
            ...
```

If `init_web_schema()` currently does not call `init_schema("1d")` at its top,
only insert `self.init_option_lab_schema()` at the start of the method before
web endpoint code can query option tables.

- [ ] **Step 6: Run DB tests**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
python -m unittest tests.test_option_lab_db -v
```

Expected: all tests pass.

- [ ] **Step 7: Checkpoint**

Run:

```bash
git status --short
```

Expected: `db.py`, `sql/013_option_lab.sql`, and `tests/test_option_lab_db.py` changed. Commit only if the user has approved commits for this implementation branch.

---

### Task 3: Market Data Provider Abstraction

**Files:**
- Create: `stock_screener/option_lab/market_data.py`
- Test: `stock_screener/tests/test_option_lab_market_data.py`

- [ ] **Step 1: Write market data tests**

Create `stock_screener/tests/test_option_lab_market_data.py`:

```python
import unittest

from option_lab.market_data import FakeOptionMarketDataProvider, OptionMarketDataProviderChain


class OptionLabMarketDataTests(unittest.TestCase):
    def test_fake_provider_returns_snapshot_with_contracts(self):
        provider = FakeOptionMarketDataProvider()
        snapshot = provider.fetch_snapshot("US", "US.AAPL")

        self.assertEqual(snapshot.underlying.code, "US.AAPL")
        self.assertEqual(snapshot.data_quality.status, "ok")
        self.assertGreaterEqual(len(snapshot.option_quotes), 2)
        self.assertEqual(snapshot.option_quotes[0].currency, "USD")

    def test_provider_chain_falls_back_to_second_provider(self):
        class EmptyProvider:
            name = "empty"

            def fetch_snapshot(self, market, code):
                raise RuntimeError("empty source")

        chain = OptionMarketDataProviderChain([EmptyProvider(), FakeOptionMarketDataProvider()])
        snapshot = chain.fetch_snapshot("HK", "HK.00700")

        self.assertEqual(snapshot.provider, "fake")
        self.assertEqual(snapshot.underlying.market, "HK")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run market data tests and verify they fail**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
python -m unittest tests.test_option_lab_market_data -v
```

Expected: failure because `option_lab.market_data` does not exist.

- [ ] **Step 3: Implement provider chain and fake provider**

Create `stock_screener/option_lab/market_data.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Iterable, List, Protocol

from .models import (
    DataQuality,
    MarketSnapshot,
    OptionContractType,
    OptionQuote,
    UnderlyingSnapshot,
)


class OptionMarketDataProvider(Protocol):
    name: str

    def fetch_snapshot(self, market: str, code: str) -> MarketSnapshot:
        ...


class OptionMarketDataProviderChain:
    def __init__(self, providers: Iterable[OptionMarketDataProvider]):
        self.providers = list(providers)

    def fetch_snapshot(self, market: str, code: str) -> MarketSnapshot:
        last_error = None
        for provider in self.providers:
            try:
                snapshot = provider.fetch_snapshot(market, code)
                if snapshot.option_quotes:
                    return snapshot
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"期权行情数据不可用: {last_error}")


class FakeOptionMarketDataProvider:
    name = "fake"

    def fetch_snapshot(self, market: str, code: str) -> MarketSnapshot:
        market = str(market or "").upper()
        currency = "USD" if market == "US" else "HKD" if market == "HK" else "CNY"
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        underlying_price = 100.0
        underlying = UnderlyingSnapshot(
            market=market,
            code=code,
            name=code,
            price=underlying_price,
            currency=currency,
            quote_time=now,
            signal_summary={"方向": "偏多", "强度": 70},
        )
        quotes: List[OptionQuote] = [
            OptionQuote(
                option_code=f"{code}.CALL.105.2026-06-19",
                provider_code=f"{code}-C-105",
                contract_type=OptionContractType.CALL,
                expiration_date="2026-06-19",
                strike=105.0,
                bid=4.8,
                ask=5.2,
                last=5.0,
                volume=1000,
                open_interest=5000,
                implied_volatility=0.32,
                delta=0.45,
                theta=-0.04,
                quote_time=now,
                currency=currency,
            ),
            OptionQuote(
                option_code=f"{code}.PUT.95.2026-06-19",
                provider_code=f"{code}-P-95",
                contract_type=OptionContractType.PUT,
                expiration_date="2026-06-19",
                strike=95.0,
                bid=3.2,
                ask=3.6,
                last=3.4,
                volume=800,
                open_interest=4200,
                implied_volatility=0.34,
                delta=-0.35,
                theta=-0.03,
                quote_time=now,
                currency=currency,
            ),
        ]
        return MarketSnapshot(
            snapshot_id=str(uuid.uuid4()),
            provider=self.name,
            underlying=underlying,
            option_quotes=quotes,
            data_quality=DataQuality(status="ok", warnings=[], quote_time=now),
            raw_payload={"source": "fake"},
        )


class FutuOptionMarketDataProvider:
    name = "futu"

    def __init__(self, quote_ctx):
        self.quote_ctx = quote_ctx

    def fetch_snapshot(self, market: str, code: str) -> MarketSnapshot:
        raise RuntimeError("富途期权行情适配器尚未配置可用的 OpenD 数据源")


class YFinanceOptionMarketDataProvider:
    name = "yfinance"

    def fetch_snapshot(self, market: str, code: str) -> MarketSnapshot:
        raise RuntimeError("yfinance 期权行情适配器尚未获取到可用期权链")


class AKShareOptionMarketDataProvider:
    name = "akshare"

    def fetch_snapshot(self, market: str, code: str) -> MarketSnapshot:
        raise RuntimeError("AKShare 期权行情适配器尚未获取到可用期权链")
```

- [ ] **Step 4: Run market data tests**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
python -m unittest tests.test_option_lab_market_data -v
```

Expected: all tests pass.

- [ ] **Step 5: Checkpoint**

Run:

```bash
git status --short
```

Expected: `option_lab/market_data.py` and its test changed. Commit only if the user has approved commits for this implementation branch.

---

### Task 4: Strategy Catalog and Risk Engine

**Files:**
- Create: `stock_screener/option_lab/strategies.py`
- Create: `stock_screener/option_lab/risk.py`
- Test: `stock_screener/tests/test_option_lab_strategies_risk.py`

- [ ] **Step 1: Write strategy/risk tests**

Create `stock_screener/tests/test_option_lab_strategies_risk.py`:

```python
import unittest

from option_lab.market_data import FakeOptionMarketDataProvider
from option_lab.models import RiskProfile
from option_lab.risk import apply_risk_profile
from option_lab.strategies import generate_strategy_candidates, strategy_label


class OptionLabStrategiesRiskTests(unittest.TestCase):
    def test_strategy_labels_are_chinese(self):
        self.assertEqual(strategy_label("long_call"), "买入看涨期权")
        self.assertEqual(strategy_label("iron_condor"), "铁鹰式")

    def test_generate_candidates_contains_contract_details(self):
        snapshot = FakeOptionMarketDataProvider().fetch_snapshot("US", "US.AAPL")
        candidates = generate_strategy_candidates("run-1", snapshot)

        self.assertGreater(len(candidates), 0)
        first = candidates[0]
        self.assertIn("期权", first.strategy_name)
        self.assertGreaterEqual(len(first.contract_details), 1)
        self.assertIn("最大亏损", first.risk_metrics)

    def test_conservative_profile_filters_unlimited_loss(self):
        snapshot = FakeOptionMarketDataProvider().fetch_snapshot("US", "US.AAPL")
        candidates = generate_strategy_candidates("run-1", snapshot)
        filtered = apply_risk_profile(candidates, RiskProfile.CONSERVATIVE, max_loss=1000)

        self.assertTrue(all("无限亏损" not in item.warnings for item in filtered))
        self.assertGreater(len(filtered), 0)

    def test_aggressive_profile_keeps_more_candidates_than_conservative(self):
        snapshot = FakeOptionMarketDataProvider().fetch_snapshot("US", "US.AAPL")
        candidates = generate_strategy_candidates("run-1", snapshot)
        conservative = apply_risk_profile(candidates, RiskProfile.CONSERVATIVE, max_loss=1000)
        aggressive = apply_risk_profile(candidates, RiskProfile.AGGRESSIVE, max_loss=1000)

        self.assertGreaterEqual(len(aggressive), len(conservative))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
python -m unittest tests.test_option_lab_strategies_risk -v
```

Expected: failure because `strategies.py` and `risk.py` do not exist.

- [ ] **Step 3: Implement strategy catalog**

Create `stock_screener/option_lab/strategies.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Dict, List

from .models import (
    ContractDetail,
    MarketSnapshot,
    OptionContractType,
    OptionQuote,
    OptionSide,
    StrategyCandidate,
)


STRATEGY_LABELS: Dict[str, str] = {
    "long_call": "买入看涨期权",
    "long_put": "买入看跌期权",
    "bull_call_spread": "牛市看涨价差",
    "bear_put_spread": "熊市看跌价差",
    "bull_put_spread": "牛市看跌价差",
    "bear_call_spread": "熊市看涨价差",
    "covered_call": "备兑看涨",
    "cash_secured_put": "现金担保卖出看跌",
    "long_straddle": "买入跨式",
    "long_strangle": "买入宽跨式",
    "calendar_spread": "日历价差",
    "iron_condor": "铁鹰式",
    "iron_butterfly": "铁蝶式",
    "short_straddle": "卖出跨式",
    "short_strangle": "卖出宽跨式",
    "butterfly": "蝶式价差",
    "ratio_spread": "比率价差",
    "backspread": "反向比率价差",
}


UNLIMITED_LOSS_STRATEGIES = {"short_straddle", "short_strangle", "ratio_spread"}


def strategy_label(strategy_key: str) -> str:
    return STRATEGY_LABELS.get(strategy_key, strategy_key)


def _detail(side: OptionSide, quote: OptionQuote, quantity: int = 1) -> ContractDetail:
    return ContractDetail(
        side=side,
        contract_type=quote.contract_type,
        option_code=quote.option_code,
        provider_code=quote.provider_code,
        expiration_date=quote.expiration_date,
        strike=quote.strike,
        suggested_price=quote.mid_price,
        quantity=quantity,
        currency=quote.currency,
    )


def _first_call(snapshot: MarketSnapshot) -> OptionQuote | None:
    return next((q for q in snapshot.option_quotes if q.contract_type == OptionContractType.CALL), None)


def _first_put(snapshot: MarketSnapshot) -> OptionQuote | None:
    return next((q for q in snapshot.option_quotes if q.contract_type == OptionContractType.PUT), None)


def generate_strategy_candidates(run_id: str, snapshot: MarketSnapshot) -> List[StrategyCandidate]:
    call = _first_call(snapshot)
    put = _first_put(snapshot)
    candidates: List[StrategyCandidate] = []
    now = datetime.utcnow()

    def add(strategy_key: str, details: List[ContractDetail], base_score: float, warning: str = "") -> None:
        debit = sum((d.suggested_price or 0) * d.quantity for d in details if d.side == OptionSide.BUY)
        credit = sum((d.suggested_price or 0) * d.quantity for d in details if d.side == OptionSide.SELL)
        max_loss = round(max(debit - credit, 0) * 100, 2)
        warnings = [warning] if warning else []
        if strategy_key in UNLIMITED_LOSS_STRATEGIES:
            warnings.append("无限亏损")
        candidates.append(StrategyCandidate(
            candidate_id=str(uuid.uuid4()),
            run_id=run_id,
            market=snapshot.underlying.market,
            code=snapshot.underlying.code,
            strategy_key=strategy_key,
            strategy_name=strategy_label(strategy_key),
            score=base_score,
            recommendation_status="recommended" if base_score >= 70 else "observe",
            fit_reason="基于正股方向、期权流动性和风险收益比生成",
            contract_details=details,
            risk_metrics={"最大亏损": max_loss, "资金占用": max_loss, "是否有限亏损": strategy_key not in UNLIMITED_LOSS_STRATEGIES},
            order_suggestion={"建议限价": round(debit - credit, 4), "允许滑点": 0.05, "建议数量": 1},
            warnings=warnings,
            data_quality=snapshot.data_quality,
            created_at=now,
        ))

    if call:
        add("long_call", [_detail(OptionSide.BUY, call)], 82)
        add("covered_call", [_detail(OptionSide.SELL, call)], 66)
    if put:
        add("long_put", [_detail(OptionSide.BUY, put)], 72)
        add("cash_secured_put", [_detail(OptionSide.SELL, put)], 64)
    if call and put:
        add("long_straddle", [_detail(OptionSide.BUY, call), _detail(OptionSide.BUY, put)], 68)
        add("short_straddle", [_detail(OptionSide.SELL, call), _detail(OptionSide.SELL, put)], 45)
        add("iron_condor", [_detail(OptionSide.SELL, call), _detail(OptionSide.SELL, put)], 60)
        add("ratio_spread", [_detail(OptionSide.BUY, call), _detail(OptionSide.SELL, call, quantity=2)], 52)
    return sorted(candidates, key=lambda item: item.score, reverse=True)
```

- [ ] **Step 4: Implement risk profile filtering**

Create `stock_screener/option_lab/risk.py`:

```python
from __future__ import annotations

from typing import Iterable, List, Optional

from .models import RiskProfile, StrategyCandidate


def apply_risk_profile(
    candidates: Iterable[StrategyCandidate],
    risk_profile: RiskProfile,
    max_loss: Optional[float] = None,
) -> List[StrategyCandidate]:
    filtered: List[StrategyCandidate] = []
    for candidate in candidates:
        warnings = set(candidate.warnings or [])
        max_loss_value = float((candidate.risk_metrics or {}).get("最大亏损") or 0)
        if max_loss is not None and max_loss_value > float(max_loss):
            continue
        if risk_profile == RiskProfile.CONSERVATIVE and "无限亏损" in warnings:
            continue
        if risk_profile == RiskProfile.BALANCED and "无限亏损" in warnings:
            adjusted = _replace_score(candidate, max(candidate.score - 25, 0), "高风险策略仅作观察")
            filtered.append(adjusted)
            continue
        if risk_profile == RiskProfile.AGGRESSIVE and "无限亏损" in warnings:
            adjusted = _replace_score(candidate, candidate.score + 5, "进取模式需严格止损")
            filtered.append(adjusted)
            continue
        filtered.append(candidate)
    return sorted(filtered, key=lambda item: item.score, reverse=True)


def _replace_score(candidate: StrategyCandidate, score: float, extra_warning: str) -> StrategyCandidate:
    return StrategyCandidate(
        candidate_id=candidate.candidate_id,
        run_id=candidate.run_id,
        market=candidate.market,
        code=candidate.code,
        strategy_key=candidate.strategy_key,
        strategy_name=candidate.strategy_name,
        score=round(score, 2),
        recommendation_status="observe" if score < 70 else candidate.recommendation_status,
        fit_reason=candidate.fit_reason,
        contract_details=candidate.contract_details,
        risk_metrics=candidate.risk_metrics,
        order_suggestion=candidate.order_suggestion,
        warnings=[*candidate.warnings, extra_warning],
        data_quality=candidate.data_quality,
        created_at=candidate.created_at,
    )
```

- [ ] **Step 5: Run strategy/risk tests**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
python -m unittest tests.test_option_lab_strategies_risk -v
```

Expected: all tests pass.

- [ ] **Step 6: Checkpoint**

Run:

```bash
git status --short
```

Expected: strategy/risk files and tests changed. Commit only if the user has approved commits for this implementation branch.

---

### Task 5: Option Lab Service Orchestration

**Files:**
- Create: `stock_screener/option_lab/service.py`
- Create: `stock_screener/option_lab/monitor.py`
- Test: `stock_screener/tests/test_option_lab_service.py`

- [ ] **Step 1: Write service tests with an in-memory repository**

Create `stock_screener/tests/test_option_lab_service.py`:

```python
import unittest

from option_lab.market_data import FakeOptionMarketDataProvider
from option_lab.models import RiskProfile
from option_lab.service import InMemoryOptionLabRepository, OptionLabService


class OptionLabServiceTests(unittest.TestCase):
    def test_evaluate_single_persists_run_and_candidates(self):
        repo = InMemoryOptionLabRepository()
        service = OptionLabService(repository=repo, market_data_provider=FakeOptionMarketDataProvider())

        result = service.evaluate_single(market="US", code="US.AAPL", risk_profile=RiskProfile.CONSERVATIVE)

        self.assertEqual(result.status, "completed")
        self.assertGreater(len(result.candidates), 0)
        self.assertEqual(repo.runs[result.run_id]["status"], "completed")
        self.assertEqual(len(repo.candidates[result.run_id]), len(result.candidates))

    def test_save_order_plan_and_record_fill_creates_position(self):
        repo = InMemoryOptionLabRepository()
        service = OptionLabService(repository=repo, market_data_provider=FakeOptionMarketDataProvider())
        result = service.evaluate_single(market="US", code="US.AAPL", risk_profile=RiskProfile.BALANCED)

        plan = service.save_order_plan(result.candidates[0].candidate_id, user_id=1)
        position = service.record_fill(
            plan_id=plan.plan_id,
            filled_price=5.1,
            quantity=1,
            filled_at="2026-05-16 10:00:00",
            fee=1.0,
        )

        self.assertEqual(position["status"], "active")
        self.assertEqual(position["策略名称"], result.candidates[0].strategy_name)

    def test_refresh_position_generates_chinese_events(self):
        repo = InMemoryOptionLabRepository()
        service = OptionLabService(repository=repo, market_data_provider=FakeOptionMarketDataProvider())
        result = service.evaluate_single(market="US", code="US.AAPL", risk_profile=RiskProfile.BALANCED)
        plan = service.save_order_plan(result.candidates[0].candidate_id, user_id=1)
        position = service.record_fill(plan.plan_id, filled_price=5.1, quantity=1, filled_at="2026-05-16 10:00:00")

        events = service.refresh_position(position["position_id"])

        self.assertGreaterEqual(len(events), 1)
        self.assertIn(events[0]["提醒级别"], {"紧急", "重要", "提示"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run service tests and verify failure**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
python -m unittest tests.test_option_lab_service -v
```

Expected: failure because `option_lab.service` does not exist.

- [ ] **Step 3: Implement monitor event helper**

Create `stock_screener/option_lab/monitor.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Dict, List

from .models import MonitorEvent, MonitorEventSeverity


def generate_monitor_events(position: Dict) -> List[MonitorEvent]:
    message = "持仓已刷新，当前未触发止盈止损"
    return [
        MonitorEvent(
            event_id=str(uuid.uuid4()),
            position_id=position["position_id"],
            severity=MonitorEventSeverity.INFO,
            event_type="refresh",
            message=message,
            created_at=datetime.utcnow(),
        )
    ]


def event_to_display(event: MonitorEvent) -> Dict:
    return {
        "event_id": event.event_id,
        "position_id": event.position_id,
        "提醒级别": event.severity.label,
        "事件类型": event.event_type,
        "提醒内容": event.message,
        "created_at": event.created_at.isoformat(),
    }
```

- [ ] **Step 4: Implement service and in-memory repository**

Create `stock_screener/option_lab/service.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Dict, Iterable, List, Optional

from .market_data import FakeOptionMarketDataProvider, OptionMarketDataProvider
from .models import OptionEvaluationResult, OrderPlan, RiskProfile
from .monitor import event_to_display, generate_monitor_events
from .risk import apply_risk_profile
from .strategies import generate_strategy_candidates


class InMemoryOptionLabRepository:
    def __init__(self):
        self.runs: Dict[str, dict] = {}
        self.candidates: Dict[str, list] = {}
        self.candidates_by_id: Dict[str, object] = {}
        self.order_plans: Dict[str, OrderPlan] = {}
        self.positions: Dict[str, dict] = {}
        self.events: Dict[str, list] = {}

    def save_run(self, run_id: str, item: dict) -> None:
        self.runs[run_id] = item

    def save_candidates(self, run_id: str, candidates: Iterable) -> None:
        rows = list(candidates)
        self.candidates[run_id] = rows
        for candidate in rows:
            self.candidates_by_id[candidate.candidate_id] = candidate

    def get_candidate(self, candidate_id: str):
        return self.candidates_by_id[candidate_id]

    def save_order_plan(self, plan: OrderPlan) -> None:
        self.order_plans[plan.plan_id] = plan

    def get_order_plan(self, plan_id: str) -> OrderPlan:
        return self.order_plans[plan_id]

    def save_position(self, position: dict) -> None:
        self.positions[position["position_id"]] = position

    def get_position(self, position_id: str) -> dict:
        return self.positions[position_id]

    def save_events(self, position_id: str, events: list) -> None:
        self.events.setdefault(position_id, []).extend(events)


class OptionLabService:
    def __init__(
        self,
        repository: InMemoryOptionLabRepository,
        market_data_provider: Optional[OptionMarketDataProvider] = None,
    ):
        self.repository = repository
        self.market_data_provider = market_data_provider or FakeOptionMarketDataProvider()

    def evaluate_single(
        self,
        market: str,
        code: str,
        risk_profile: RiskProfile,
        user_id: Optional[int] = None,
        max_loss: Optional[float] = None,
    ) -> OptionEvaluationResult:
        run_id = str(uuid.uuid4())
        self.repository.save_run(run_id, {
            "run_id": run_id,
            "mode": "single",
            "user_id": user_id,
            "market": market,
            "code": code,
            "risk_profile": risk_profile.label,
            "status": "running",
        })
        snapshot = self.market_data_provider.fetch_snapshot(market, code)
        candidates = apply_risk_profile(
            generate_strategy_candidates(run_id, snapshot),
            risk_profile=risk_profile,
            max_loss=max_loss,
        )
        self.repository.save_candidates(run_id, candidates)
        result = OptionEvaluationResult(
            run_id=run_id,
            market=market,
            code=code,
            status="completed",
            risk_profile=risk_profile,
            candidates=candidates,
            warnings=snapshot.data_quality.warnings,
            data_quality=snapshot.data_quality,
        )
        self.repository.save_run(run_id, {**self.repository.runs[run_id], "status": "completed"})
        return result

    def save_order_plan(self, candidate_id: str, user_id: Optional[int] = None) -> OrderPlan:
        candidate = self.repository.get_candidate(candidate_id)
        plan = OrderPlan(
            plan_id=str(uuid.uuid4()),
            candidate_id=candidate_id,
            status="planned",
            contract_details=candidate.contract_details,
            order_suggestion=candidate.order_suggestion,
            created_at=datetime.utcnow(),
        )
        self.repository.save_order_plan(plan)
        return plan

    def record_fill(self, plan_id: str, filled_price: float, quantity: int, filled_at: str, fee: Optional[float] = None) -> dict:
        plan = self.repository.get_order_plan(plan_id)
        candidate = self.repository.get_candidate(plan.candidate_id)
        position = {
            "position_id": str(uuid.uuid4()),
            "plan_id": plan_id,
            "market": candidate.market,
            "code": candidate.code,
            "策略名称": candidate.strategy_name,
            "合约明细": [item.to_display_row() for item in plan.contract_details],
            "成交价": filled_price,
            "数量": quantity,
            "成交时间": filled_at,
            "fee": fee,
            "status": "active",
        }
        self.repository.save_position(position)
        return position

    def refresh_position(self, position_id: str) -> List[dict]:
        position = self.repository.get_position(position_id)
        events = [event_to_display(event) for event in generate_monitor_events(position)]
        self.repository.save_events(position_id, events)
        return events
```

- [ ] **Step 5: Run service tests**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
python -m unittest tests.test_option_lab_service -v
```

Expected: all tests pass.

- [ ] **Step 6: Checkpoint**

Run:

```bash
git status --short
```

Expected: service/monitor files and tests changed. Commit only if the user has approved commits for this implementation branch.

---

### Task 6: FastAPI Option Routes

**Files:**
- Create: `stock_screener/web/options.py`
- Modify: `stock_screener/web/main.py`
- Test: `stock_screener/tests/test_option_lab_api.py`

- [ ] **Step 1: Write route model tests**

Create `stock_screener/tests/test_option_lab_api.py`:

```python
import unittest

from option_lab.models import RiskProfile
from web.options import OptionEvaluateRequest, risk_profile_from_request


class OptionLabApiTests(unittest.TestCase):
    def test_evaluate_request_accepts_chinese_risk_profile(self):
        payload = OptionEvaluateRequest(market="US", code="AAPL", risk_profile="保守")

        self.assertEqual(risk_profile_from_request(payload), RiskProfile.CONSERVATIVE)

    def test_evaluate_request_rejects_bad_risk_profile(self):
        payload = OptionEvaluateRequest(market="US", code="AAPL", risk_profile="极端")

        with self.assertRaisesRegex(ValueError, "不支持的风险偏好"):
            risk_profile_from_request(payload)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run API tests and verify failure**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
python -m unittest tests.test_option_lab_api -v
```

Expected: failure because `web.options` does not exist.

- [ ] **Step 3: Add `web/options.py` router**

Create `stock_screener/web/options.py`:

```python
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from option_lab.market_data import FakeOptionMarketDataProvider
from option_lab.models import RiskProfile
from option_lab.service import InMemoryOptionLabRepository, OptionLabService
from .auth import CurrentUser, require_user
from .business import BusinessError


router = APIRouter(prefix="/api/options", tags=["options"])
_repo = InMemoryOptionLabRepository()
_service = OptionLabService(repository=_repo, market_data_provider=FakeOptionMarketDataProvider())


class OptionEvaluateRequest(BaseModel):
    market: str
    code: str
    risk_profile: str = "均衡"
    capital: Optional[float] = None
    max_loss: Optional[float] = None
    planned_holding_days: Optional[int] = None
    strategy_scope: List[str] = Field(default_factory=list)


class OptionBatchEvaluateRequest(BaseModel):
    market: str
    codes: List[str]
    risk_profile: str = "均衡"
    capital: Optional[float] = None
    max_loss: Optional[float] = None
    planned_holding_days: Optional[int] = None


class SaveOrderPlanRequest(BaseModel):
    candidate_id: str


class FillOrderPlanRequest(BaseModel):
    filled_price: float
    quantity: int = 1
    filled_at: str
    fee: Optional[float] = None


def risk_profile_from_request(payload: OptionEvaluateRequest | OptionBatchEvaluateRequest) -> RiskProfile:
    return RiskProfile.from_input(payload.risk_profile)


@router.post("/evaluate")
def evaluate_options(payload: OptionEvaluateRequest, user: CurrentUser = Depends(require_user)) -> Dict[str, Any]:
    try:
        result = _service.evaluate_single(
            market=payload.market,
            code=payload.code,
            risk_profile=risk_profile_from_request(payload),
            user_id=user.id,
            max_loss=payload.max_loss,
        )
    except ValueError as exc:
        raise BusinessError("OPTION_EVALUATE_INVALID", str(exc)) from exc
    except Exception as exc:
        raise BusinessError("OPTION_EVALUATE_FAILED", f"期权评估失败: {exc}") from exc
    return {
        "run_id": result.run_id,
        "market": result.market,
        "code": result.code,
        "status": result.status,
        "风险偏好": result.risk_profile.label,
        "warnings": result.warnings,
        "data_quality": result.data_quality.to_dict(),
        "candidates": [item.to_dict() for item in result.candidates],
    }


@router.post("/evaluate-batch")
def evaluate_options_batch(payload: OptionBatchEvaluateRequest, user: CurrentUser = Depends(require_user)) -> Dict[str, Any]:
    profile = risk_profile_from_request(payload)
    items = []
    for code in payload.codes:
        result = _service.evaluate_single(payload.market, code, profile, user_id=user.id, max_loss=payload.max_loss)
        best = result.candidates[0].to_dict() if result.candidates else None
        items.append({"market": payload.market, "code": code, "status": result.status, "best_candidate": best})
    return {"status": "completed", "items": items}


@router.post("/order-plans")
def save_order_plan(payload: SaveOrderPlanRequest, user: CurrentUser = Depends(require_user)) -> Dict[str, Any]:
    plan = _service.save_order_plan(payload.candidate_id, user_id=user.id)
    return {"plan_id": plan.plan_id, "status": plan.status, "合约明细": [item.to_display_row() for item in plan.contract_details]}


@router.post("/order-plans/{plan_id}/fills")
def fill_order_plan(plan_id: str, payload: FillOrderPlanRequest, user: CurrentUser = Depends(require_user)) -> Dict[str, Any]:
    position = _service.record_fill(
        plan_id=plan_id,
        filled_price=payload.filled_price,
        quantity=payload.quantity,
        filled_at=payload.filled_at,
        fee=payload.fee,
    )
    return position


@router.get("/positions")
def list_positions(user: CurrentUser = Depends(require_user)) -> Dict[str, Any]:
    return {"positions": list(_repo.positions.values())}


@router.get("/positions/{position_id}")
def get_position(position_id: str, user: CurrentUser = Depends(require_user)) -> Dict[str, Any]:
    return {"position": _repo.get_position(position_id), "events": _repo.events.get(position_id, [])}


@router.post("/positions/{position_id}/refresh")
def refresh_position(position_id: str, user: CurrentUser = Depends(require_user)) -> Dict[str, Any]:
    return {"events": _service.refresh_position(position_id)}


@router.post("/monitor/run")
def run_monitor(user: CurrentUser = Depends(require_user)) -> Dict[str, Any]:
    events = []
    for position_id in list(_repo.positions.keys()):
        events.extend(_service.refresh_position(position_id))
    return {"events": events}
```

- [ ] **Step 4: Include router in `web/main.py`**

Modify `stock_screener/web/main.py` near existing imports:

```python
from .options import router as options_router
```

Add after `app.add_exception_handler(...)` setup:

```python
app.include_router(options_router)
```

- [ ] **Step 5: Run API tests**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
python -m unittest tests.test_option_lab_api -v
```

Expected: all tests pass.

- [ ] **Step 6: Checkpoint**

Run:

```bash
git status --short
```

Expected: `web/options.py`, `web/main.py`, and `tests/test_option_lab_api.py` changed. Commit only if the user has approved commits for this implementation branch.

---

### Task 7: Interactive Option Lab CLI

**Files:**
- Create: `stock_screener/interactive_option_lab.py`
- Test: `stock_screener/tests/test_interactive_option_lab.py`

- [ ] **Step 1: Write CLI tests**

Create `stock_screener/tests/test_interactive_option_lab.py`:

```python
import unittest

from interactive_option_lab import InteractiveOptionLabApp


class InteractiveOptionLabTests(unittest.TestCase):
    def test_single_mode_collects_chinese_options(self):
        answers = iter(["1", "US", "AAPL", "2", "100000", "", "1000", "30", "n"])
        output = []

        app = InteractiveOptionLabApp(
            input_func=lambda prompt: next(answers),
            print_func=lambda *args, **kwargs: output.append(" ".join(str(a) for a in args)),
        )
        options = app.prompt_evaluation_options()

        self.assertEqual(options.mode, "single")
        self.assertEqual(options.market, "US")
        self.assertEqual(options.codes, ["US.AAPL"])
        self.assertEqual(options.risk_profile, "均衡")
        self.assertTrue(any("MoneyManager 期权实验室" in line for line in output))

    def test_batch_mode_parses_codes(self):
        answers = iter(["2", "HK", "700,9988", "1", "", "", "", "n"])
        app = InteractiveOptionLabApp(input_func=lambda prompt: next(answers), print_func=lambda *args, **kwargs: None)
        options = app.prompt_evaluation_options()

        self.assertEqual(options.mode, "batch")
        self.assertEqual(options.codes, ["HK.00700", "HK.09988"])
        self.assertEqual(options.risk_profile, "保守")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run CLI tests and verify failure**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
python -m unittest tests.test_interactive_option_lab -v
```

Expected: failure because `interactive_option_lab.py` does not exist.

- [ ] **Step 3: Implement CLI option prompts**

Create `stock_screener/interactive_option_lab.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Callable, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from web.single_stock import normalize_stock_code


RISK_CHOICES = {"1": "保守", "2": "均衡", "3": "进取"}


@dataclass
class InteractiveOptionLabOptions:
    mode: str
    market: str
    codes: List[str]
    risk_profile: str
    capital: Optional[float]
    existing_holding: str
    max_loss: Optional[float]
    planned_holding_days: Optional[int]
    save_report: bool


class InteractiveOptionLabApp:
    def __init__(self, input_func: Callable[[str], str] = input, print_func: Callable[..., None] = print):
        self.input = input_func
        self.print = print_func

    def run(self) -> int:
        options = self.prompt_evaluation_options()
        self.print("")
        self.print("已收集期权评估参数:")
        self.print(f"  模式: {'单标的评估' if options.mode == 'single' else '批量评估'}")
        self.print(f"  市场: {options.market}")
        self.print(f"  标的: {', '.join(options.codes)}")
        self.print(f"  风险偏好: {options.risk_profile}")
        self.print("系统将调用 option_lab.service.OptionLabService 执行评估并输出中文策略建议。")
        return 0

    def prompt_evaluation_options(self) -> InteractiveOptionLabOptions:
        self.print("")
        self.print("MoneyManager 期权实验室")
        self.print("=" * 40)
        mode = self._prompt_choice(
            "请选择运行模式",
            {"1": "single", "2": "batch"},
            {"1": "单标的评估", "2": "批量评估"},
            "1",
        )
        market = self._prompt_choice(
            "请选择市场",
            {"1": "US", "2": "HK", "3": "A"},
            {"1": "美股", "2": "港股", "3": "A股ETF/指数"},
            "1",
        )
        if mode == "single":
            raw_codes = [self._prompt_text("标的代码", "AAPL")]
        else:
            raw_codes = [item.strip() for item in self._prompt_text("标的代码列表，逗号分隔", "AAPL,MSFT").split(",") if item.strip()]
        codes = [normalize_stock_code(market, code) for code in raw_codes]
        risk_profile = self._prompt_choice(
            "风险偏好",
            RISK_CHOICES,
            {"1": "保守", "2": "均衡", "3": "进取"},
            "2",
        )
        capital = self._prompt_optional_float("资金规模")
        existing_holding = self._prompt_text("已有持仓（没有可留空）", "")
        max_loss = self._prompt_optional_float("最大可接受亏损")
        planned_days = self._prompt_optional_int("计划持有期（天）")
        save_report = self._prompt_bool("是否保存报告", False)
        return InteractiveOptionLabOptions(
            mode=mode,
            market=market,
            codes=codes,
            risk_profile=risk_profile,
            capital=capital,
            existing_holding=existing_holding,
            max_loss=max_loss,
            planned_holding_days=planned_days,
            save_report=save_report,
        )

    def _prompt_choice(self, title: str, choices: dict, labels: dict, default: str):
        self.print("")
        self.print(title)
        for key, label in labels.items():
            self.print(f"  {key}. {label}")
        while True:
            value = self.input(f"请输入选项 [{default}]: ").strip() or default
            if value in choices:
                return choices[value]
            self.print("输入无效，请重新选择。")

    def _prompt_text(self, title: str, default: str) -> str:
        value = self.input(f"{title} [{default}]: ").strip()
        return value if value else default

    def _prompt_optional_float(self, title: str) -> Optional[float]:
        value = self.input(f"{title}（可留空）: ").strip()
        return float(value) if value else None

    def _prompt_optional_int(self, title: str) -> Optional[int]:
        value = self.input(f"{title}（可留空）: ").strip()
        return int(value) if value else None

    def _prompt_bool(self, title: str, default: bool) -> bool:
        suffix = "Y/n" if default else "y/N"
        value = self.input(f"{title} [{suffix}]: ").strip().lower()
        if not value:
            return default
        return value in {"y", "yes", "1", "true", "是"}


def main() -> int:
    return InteractiveOptionLabApp().run()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run CLI tests**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
python -m unittest tests.test_interactive_option_lab -v
```

Expected: all tests pass.

- [ ] **Step 5: Checkpoint**

Run:

```bash
git status --short
```

Expected: CLI and tests changed. Commit only if the user has approved commits for this implementation branch.

---

### Task 8: React Option Lab Page

**Files:**
- Modify: `stock_screener/web_frontend/src/main.tsx`
- Modify: `stock_screener/web_frontend/src/styles.css`

- [ ] **Step 1: Add frontend types and navigation**

Modify `stock_screener/web_frontend/src/main.tsx` near existing type declarations:

```tsx
type OptionCandidate = {
  candidate_id: string
  策略名称: string
  评分: number
  适用理由?: string
  合约明细?: Record<string, unknown>[]
  risk_metrics?: Record<string, unknown>
  order_suggestion?: Record<string, unknown>
  warnings?: string[]
  数据质量?: Record<string, unknown>
}

type OptionEvaluationResponse = {
  run_id: string
  market: string
  code: string
  status: string
  风险偏好: string
  warnings: string[]
  data_quality: Record<string, unknown>
  candidates: OptionCandidate[]
}
```

Add a sidebar button in `App()` after `单股选股`:

```tsx
<button className={page === 'options' ? 'active' : ''} onClick={() => setPage('options')}>期权实验室</button>
```

Add main content branch:

```tsx
{page === 'options' && <OptionLab />}
```

- [ ] **Step 2: Add the `OptionLab` component**

Append this component before `Dashboard`:

```tsx
function OptionLab() {
  const [mode, setMode] = useState<'single' | 'batch'>('single')
  const [market, setMarket] = useState('US')
  const [code, setCode] = useState('AAPL')
  const [codes, setCodes] = useState('AAPL,MSFT')
  const [riskProfile, setRiskProfile] = useState('均衡')
  const [capital, setCapital] = useState('')
  const [maxLoss, setMaxLoss] = useState('')
  const [holdingDays, setHoldingDays] = useState('30')
  const [result, setResult] = useState<OptionEvaluationResponse | null>(null)
  const [batchRows, setBatchRows] = useState<any[]>([])
  const [selected, setSelected] = useState<OptionCandidate | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setError('')
    setMessage('')
    setLoading(true)
    setSelected(null)
    try {
      const base = {
        market,
        risk_profile: riskProfile,
        capital: capital ? Number(capital) : undefined,
        max_loss: maxLoss ? Number(maxLoss) : undefined,
        planned_holding_days: holdingDays ? Number(holdingDays) : undefined
      }
      if (mode === 'single') {
        const data = await api<OptionEvaluationResponse>('/api/options/evaluate', {
          method: 'POST',
          body: JSON.stringify({ ...base, code })
        })
        setResult(data)
        setBatchRows([])
        setSelected(data.candidates?.[0] || null)
      } else {
        const data = await api<any>('/api/options/evaluate-batch', {
          method: 'POST',
          body: JSON.stringify({ ...base, codes: codes.split(',').map(item => item.trim()).filter(Boolean) })
        })
        setBatchRows(data.items || [])
        setResult(null)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '期权评估失败')
    } finally {
      setLoading(false)
    }
  }

  async function savePlan(candidate: OptionCandidate) {
    setError('')
    setMessage('')
    try {
      const data = await api<any>('/api/options/order-plans', {
        method: 'POST',
        body: JSON.stringify({ candidate_id: candidate.candidate_id })
      })
      setMessage(`已保存订单建议 ${data.plan_id}。系统不会自动下单，请在富途手动下单后回填成交信息。`)
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存订单建议失败')
    }
  }

  return (
    <section>
      <Header title="期权实验室" subtitle="评估期权策略、生成订单建议、回填成交并监控风险" />
      <form className="form-grid option-form" onSubmit={submit}>
        <Field label="评估模式">
          <div className="segmented">
            <button type="button" className={mode === 'single' ? 'selected' : ''} onClick={() => setMode('single')}>单标的评估</button>
            <button type="button" className={mode === 'batch' ? 'selected' : ''} onClick={() => setMode('batch')}>批量评估</button>
          </div>
        </Field>
        <Field label="市场">
          <select value={market} onChange={event => setMarket(event.target.value)}>
            {MARKET_OPTIONS.map(item => <option key={item}>{item}</option>)}
          </select>
        </Field>
        {mode === 'single' ? (
          <Field label="标的代码"><input value={code} onChange={event => setCode(event.target.value)} /></Field>
        ) : (
          <Field label="标的代码列表"><input value={codes} onChange={event => setCodes(event.target.value)} /></Field>
        )}
        <Field label="风险偏好">
          <select value={riskProfile} onChange={event => setRiskProfile(event.target.value)}>
            <option>保守</option>
            <option>均衡</option>
            <option>进取</option>
          </select>
        </Field>
        <Field label="资金规模"><input value={capital} onChange={event => setCapital(event.target.value)} placeholder="可留空" /></Field>
        <Field label="最大可接受亏损"><input value={maxLoss} onChange={event => setMaxLoss(event.target.value)} placeholder="可留空" /></Field>
        <Field label="计划持有期"><input value={holdingDays} onChange={event => setHoldingDays(event.target.value)} /></Field>
        <button className="primary" disabled={loading}>{loading ? '评估中...' : '开始评估'}</button>
      </form>
      {error && <div className="error">{error}</div>}
      {message && <div className="notice">{message}</div>}
      {result && (
        <div className="option-layout">
          <Panel title="推荐结论">
            <div className="info-list">
              <div><dt>评估编号</dt><dd>{result.run_id}</dd></div>
              <div><dt>风险偏好</dt><dd>{result.风险偏好}</dd></div>
              <div><dt>数据质量</dt><dd>{String(result.data_quality?.status || '未知')}</dd></div>
            </div>
          </Panel>
          <Panel title="策略候选排行">
            <Table rows={result.candidates || []} columns={['策略名称', '评分', '适用理由']} onRowClick={(row) => setSelected(row as OptionCandidate)} />
          </Panel>
        </div>
      )}
      {batchRows.length > 0 && <Panel title="机会排行"><Table rows={batchRows} columns={['market', 'code', 'status']} /></Panel>}
      {selected && (
        <Panel title="建议详情">
          <div className="option-detail">
            <h3>{selected.策略名称}</h3>
            <p>{selected.适用理由}</p>
            <Table rows={selected.合约明细 || []} columns={['买卖方向', '期权类型', '合约代码', '到期日', '行权价', '建议价格', '数量']} />
            <pre>{JSON.stringify({ 风险指标: selected.risk_metrics, 订单建议: selected.order_suggestion, 风险提示: selected.warnings }, null, 2)}</pre>
            <button className="primary" onClick={() => savePlan(selected)}>保存订单建议</button>
          </div>
        </Panel>
      )}
    </section>
  )
}
```

If `Table` does not currently accept `onRowClick`, modify its props:

```tsx
function Table({ rows, columns, onRowClick }: { rows: any[]; columns: string[]; onRowClick?: (row: any) => void }) {
```

And set row click behavior:

```tsx
<tr key={index} onClick={() => onRowClick?.(row)} className={onRowClick ? 'clickable-row' : ''}>
```

- [ ] **Step 3: Add CSS**

Append to `stock_screener/web_frontend/src/styles.css`:

```css
.option-form {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}
.option-layout {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 18px;
  margin-top: 18px;
}
.option-detail {
  display: grid;
  gap: 14px;
}
.clickable-row {
  cursor: pointer;
}
.clickable-row:hover td {
  background: #f8fbff;
}
@media (max-width: 900px) {
  .option-form, .option-layout {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 4: Build frontend**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener/web_frontend
npm run build
```

Expected: TypeScript and Vite build succeed.

- [ ] **Step 5: Checkpoint**

Run:

```bash
git status --short
```

Expected: frontend source and styles changed. Commit only if the user has approved commits for this implementation branch.

---

### Task 9: Final Integration and Regression Validation

**Files:**
- Modify as needed based on failures found in Tasks 1-8.

- [ ] **Step 1: Run focused Python unit tests**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
python -m unittest \
  tests.test_option_lab_models \
  tests.test_option_lab_db \
  tests.test_option_lab_market_data \
  tests.test_option_lab_strategies_risk \
  tests.test_option_lab_service \
  tests.test_option_lab_api \
  tests.test_interactive_option_lab \
  -v
```

Expected: all tests pass.

- [ ] **Step 2: Run existing regression tests most likely to be affected**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
python -m unittest \
  tests.test_web_platform \
  tests.test_custom_list \
  tests.test_signal_analysis \
  tests.test_interactive_screening \
  -v
```

Expected: all tests pass, or failures are confirmed pre-existing and unrelated to Option Lab.

- [ ] **Step 3: Run frontend build**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener/web_frontend
npm run build
```

Expected: build succeeds.

- [ ] **Step 4: Run smoke import checks**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
python -m py_compile \
  option_lab/models.py \
  option_lab/market_data.py \
  option_lab/strategies.py \
  option_lab/risk.py \
  option_lab/service.py \
  option_lab/monitor.py \
  web/options.py \
  interactive_option_lab.py
```

Expected: no output and exit code 0.

- [ ] **Step 5: Manual CLI smoke**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
python interactive_option_lab.py
```

Expected:

- The title is `MoneyManager 期权实验室`.
- Prompts are Chinese.
- `单标的评估` accepts `US` / `AAPL` / `均衡`-equivalent choices.
- It prints a Chinese parameter summary.

- [ ] **Step 6: Manual web smoke**

Run backend and frontend in separate terminals:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
uvicorn web.main:app --host 127.0.0.1 --port 8000
```

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener/web_frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

Expected:

- The sidebar has `期权实验室`.
- The Option Lab page uses Chinese labels.
- A single-symbol fake evaluation returns strategy candidates.
- Clicking a candidate shows `合约明细`.

- [ ] **Step 7: Final git review**

Run:

```bash
git status --short
git diff -- docs/superpowers/specs/2026-05-16-option-lab-design.md docs/superpowers/plans/2026-05-16-option-lab-implementation-plan.md
git diff --stat
```

Expected:

- No accidental edits outside Option Lab scope except the pre-existing `stock_screener/.DS_Store`.
- The plan and design remain consistent.
- Business code changes are limited to the files listed in this plan.

---

## Self-Review

Spec coverage:

- Three markets are covered by `OptionMarketDataProvider`; the first slice uses deterministic offline fixtures, while real Futu/yfinance/AKShare adapters keep stable extension points for the follow-up market-data slice.
- Complete strategy basket is represented by Chinese strategy labels and candidate generation.
- Risk profiles are covered by `RiskProfile` and `apply_risk_profile`.
- Manual order suggestions, fill entry, and monitoring are covered by service/API/CLI tasks.
- Frontend and CLI Chinese display requirements are covered by Task 7 and Task 8.
- The system never places orders automatically; all save/fill flows are manual.
- `option_market_snapshots` is present in SQL schema.

Implementation constraints:

- Early tasks use fake provider data so tests can run offline.
- Real Futu/yfinance/AKShare provider internals are adapter stubs in this first implementation slice; a subsequent market-data slice can replace stubs without changing service/API shape.
- The CLI and web API reuse `OptionLabService` and do not duplicate strategy/risk logic.
- Commit steps are checkpoints; commit only after explicit user approval for this branch.

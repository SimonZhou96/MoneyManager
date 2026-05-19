from __future__ import annotations

import uuid
from typing import Any


class QuantLabService:
    def __init__(self, repository, data_provider=None, strategy=None):
        self.repository = repository
        self.data_provider = data_provider
        self.strategy = strategy

    def submit_backtest(self, payload: dict, user_id: int | None = None) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        self.repository.create_quant_backtest_run(
            {
                "run_id": run_id,
                "user_id": user_id,
                "status": "queued",
                "request": payload,
                "warnings": [],
            }
        )
        return {"run_id": run_id, "status": "queued"}

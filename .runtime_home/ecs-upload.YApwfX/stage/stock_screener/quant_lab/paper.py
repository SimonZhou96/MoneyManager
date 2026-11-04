from __future__ import annotations

import uuid


class PaperTradingService:
    def __init__(self, repository):
        self.repository = repository

    def create_account(self, user_id: int | None, name: str, initial_cash: float) -> dict:
        return {
            "account_id": str(uuid.uuid4()),
            "user_id": user_id,
            "name": name,
            "cash": float(initial_cash),
            "equity": float(initial_cash),
            "status": "active",
            "real_order_enabled": False,
        }

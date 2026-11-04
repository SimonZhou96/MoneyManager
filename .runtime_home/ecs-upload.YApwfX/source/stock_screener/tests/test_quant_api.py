#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import InMemoryQuantRepository
from web.auth import CurrentUser, get_db, require_user
from web.quant import router


class QuantApiTest(unittest.TestCase):
    def test_router_exposes_backtest_schema_error_for_invalid_payload(self):
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[require_user] = lambda: CurrentUser(id=1, username="tester", role="admin")
        app.dependency_overrides[get_db] = lambda: InMemoryQuantRepository()
        client = TestClient(app)

        response = client.post("/api/quant/backtests", json={"market": "US"})

        self.assertEqual(response.status_code, 422)

    def test_backtest_status_returns_progress_and_logs(self):
        app = FastAPI()
        app.include_router(router)
        repo = InMemoryQuantRepository()
        app.dependency_overrides[require_user] = lambda: CurrentUser(id=1, username="tester", role="admin")
        app.dependency_overrides[get_db] = lambda: repo
        client = TestClient(app)

        create_response = client.post(
            "/api/quant/backtests",
            json={
                "market": "US",
                "symbols": ["US.AAPL"],
                "entry_chain_key": "default",
                "exit_policy": {"type": "fixed_holding_days", "days": 5},
                "start": "2026-01-01",
                "end": "2026-01-03",
                "initial_cash": 10000,
            },
        )
        self.assertEqual(create_response.status_code, 200)
        run_id = create_response.json()["run_id"]

        status_response = client.get(f"/api/quant/backtests/{run_id}")

        self.assertEqual(status_response.status_code, 200)
        payload = status_response.json()
        self.assertEqual(payload["run_id"], run_id)
        self.assertIn(payload["status"], {"queued", "running", "failed", "completed"})
        self.assertIsInstance(payload["progress_pct"], int)
        self.assertGreaterEqual(payload["progress_pct"], 0)
        self.assertLessEqual(payload["progress_pct"], 100)
        self.assertTrue(payload["progress_logs"])


if __name__ == "__main__":
    unittest.main()

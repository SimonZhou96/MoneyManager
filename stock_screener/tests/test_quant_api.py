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


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest

from quant_lab.paper import PaperTradingService


class PaperTradingServiceTest(unittest.TestCase):
    def test_create_paper_account_does_not_place_real_orders(self):
        service = PaperTradingService(repository={})
        account = service.create_account(user_id=7, name="demo", initial_cash=100000)

        self.assertEqual(account["user_id"], 7)
        self.assertEqual(account["cash"], 100000)
        self.assertFalse(account["real_order_enabled"])


if __name__ == "__main__":
    unittest.main()

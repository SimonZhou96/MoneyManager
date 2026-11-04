#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import date

from quant_lab.option_adapter import OptionCandidateReplayAdapter


class OptionCandidateReplayAdapterTest(unittest.TestCase):
    def test_missing_option_snapshot_returns_limited_warning(self):
        adapter = OptionCandidateReplayAdapter(snapshot_loader=lambda run_id: None)
        result = adapter.replay(candidate_id="c1", run_id="r1", replay_date=date(2026, 1, 2))

        self.assertEqual(result["mode"], "limited_option_replay")
        self.assertIn("missing_option_snapshot", result["warnings"])


if __name__ == "__main__":
    unittest.main()

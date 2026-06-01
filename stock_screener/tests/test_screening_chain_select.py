#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from interactive_screening import resolve_chain_choice


CHAINS = [
    {"chain_key": "HK_default_zuoyi_and_other", "chain_name": "左一+其他", "enabled": True},
    {"chain_key": "HK_trial_macro", "chain_name": "宏观试跑", "enabled": False},
]


class ChainChoiceTests(unittest.TestCase):
    def test_blank_returns_default_none(self):
        self.assertIsNone(resolve_chain_choice("", CHAINS))

    def test_zero_returns_default_none(self):
        self.assertIsNone(resolve_chain_choice("0", CHAINS))

    def test_number_selects_chain_key(self):
        self.assertEqual(resolve_chain_choice("1", CHAINS), "HK_default_zuoyi_and_other")
        self.assertEqual(resolve_chain_choice("2", CHAINS), "HK_trial_macro")

    def test_direct_key_accepted(self):
        self.assertEqual(resolve_chain_choice("HK_trial_macro", CHAINS), "HK_trial_macro")

    def test_invalid_number_raises(self):
        with self.assertRaises(ValueError):
            resolve_chain_choice("9", CHAINS)

    def test_unknown_key_raises(self):
        with self.assertRaises(ValueError):
            resolve_chain_choice("NOPE", CHAINS)

    def test_no_chains_blank_ok_key_raises(self):
        self.assertIsNone(resolve_chain_choice("", []))
        with self.assertRaises(ValueError):
            resolve_chain_choice("anything", [])


if __name__ == "__main__":
    unittest.main()

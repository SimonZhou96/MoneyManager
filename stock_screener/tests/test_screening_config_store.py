import json
import os
import tempfile
import unittest
from unittest import mock

from screening_config_store import CONFIG_VERSION, load_last_config, save_last_config


class ScreeningConfigStoreTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        answers = {"mode": "aggressive", "risk_limit": "high"}
        with tempfile.TemporaryDirectory() as runtime_home:
            with mock.patch.dict(os.environ, {"RUNTIME_HOME": runtime_home}, clear=False):
                self.assertTrue(save_last_config(answers))
                loaded = load_last_config()
        self.assertEqual(answers, loaded)

    def test_missing_file_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as runtime_home:
            with mock.patch.dict(os.environ, {"RUNTIME_HOME": runtime_home}, clear=False):
                self.assertIsNone(load_last_config())

    def test_incompatible_version_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as runtime_home:
            config_path = os.path.join(runtime_home, "interactive_screening_last.json")
            payload = {"version": 999, "answers": {"foo": "bar"}}
            with open(config_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            with mock.patch.dict(os.environ, {"RUNTIME_HOME": runtime_home}, clear=False):
                self.assertIsNone(load_last_config())

    def test_corrupt_json_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as runtime_home:
            config_path = os.path.join(runtime_home, "interactive_screening_last.json")
            with open(config_path, "w", encoding="utf-8") as handle:
                handle.write("{invalid json")
            with mock.patch.dict(os.environ, {"RUNTIME_HOME": runtime_home}, clear=False):
                self.assertIsNone(load_last_config())

    def test_unwritable_path_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as runtime_home:
            occupied_path = os.path.join(runtime_home, "occupied")
            with open(occupied_path, "w", encoding="utf-8") as handle:
                handle.write("not a directory")
            with mock.patch.dict(os.environ, {"RUNTIME_HOME": occupied_path}, clear=False):
                self.assertFalse(save_last_config({"foo": "bar"}))

    def test_config_version_constant(self) -> None:
        self.assertEqual(1, CONFIG_VERSION)


if __name__ == "__main__":
    unittest.main()

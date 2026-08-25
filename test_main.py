import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import main


class MergePluginParamsTest(unittest.TestCase):
    def test_wan3_th_preserves_duration_up_to_30_seconds(self):
        self.assertEqual(main._normalize_xingyao_duration("wan3.0th", 29), 29)
        self.assertEqual(main._normalize_xingyao_duration("wan3.0th", 30), 30)
        self.assertEqual(main._normalize_xingyao_duration("wan3.0th", 31), 30)

    def test_current_request_duration_overrides_saved_duration(self):
        with patch.object(main, "load_plugin_config", return_value={"duration": 6}):
            merged, disk, host = main._merge_plugin_params({"duration": "15"})

        self.assertEqual(merged["duration"], "15")
        self.assertEqual(disk["duration"], 6)
        self.assertEqual(host["duration"], "15")

    def test_saved_duration_is_used_when_request_omits_it(self):
        with patch.object(main, "load_plugin_config", return_value={"duration": 10}):
            merged, _, _ = main._merge_plugin_params({"model": "seedance-2.0-mini"})

        self.assertEqual(merged["duration"], 10)

    def test_empty_request_secret_does_not_erase_saved_secret(self):
        with patch.object(main, "load_plugin_config", return_value={"api_key": "saved-key"}):
            merged, _, _ = main._merge_plugin_params({"api_key": ""})

        self.assertEqual(merged["api_key"], "saved-key")


if __name__ == "__main__":
    unittest.main()

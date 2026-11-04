#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from unittest.mock import patch

import feishu_app_client


class FeishuAppClientTest(unittest.TestCase):
    def test_token_preflight_uses_feishu_dns_retry_settings(self):
        with patch("feishu_app_client.format_resolution_failures", return_value=["dns down"]) as preflight, \
                patch.dict(
                    "os.environ",
                    {"FEISHU_DNS_RETRY_ATTEMPTS": "6", "FEISHU_DNS_RETRY_DELAY_SEC": "0.3"},
                    clear=False,
                ):
            self.assertIsNone(feishu_app_client.get_tenant_access_token("app", "secret"))

        preflight.assert_called_once_with(
            "[Feishu] token 获取预检失败",
            ["https://open.feishu.cn"],
            attempts=6,
            retry_delay_sec=0.3,
        )


if __name__ == "__main__":
    unittest.main()

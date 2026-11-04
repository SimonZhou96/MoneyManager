#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from unittest.mock import patch

from feishu_notifier import send_feishu_text, send_screening_result


class FakeFeishuResponse:
    status_code = 200
    text = '{"code":0}'

    def json(self):
        return {"code": 0}


class FeishuNotifierTest(unittest.TestCase):
    def test_send_feishu_text_retries_transient_post_error(self):
        with patch("feishu_notifier.format_resolution_failures", return_value=[]), \
                patch(
                    "feishu_notifier.requests.post",
                    side_effect=[OSError("temporary dns failure"), FakeFeishuResponse()],
                ) as post, \
                patch.dict(
                    "os.environ",
                    {"FEISHU_SEND_RETRY_ATTEMPTS": "2", "FEISHU_SEND_RETRY_DELAY_SEC": "0"},
                    clear=False,
                ):
            self.assertTrue(send_feishu_text("https://open.feishu.cn/webhook/test", "hello"))

        self.assertEqual(post.call_count, 2)

    def test_send_feishu_text_uses_feishu_dns_retry_settings_for_preflight(self):
        with patch("feishu_notifier.format_resolution_failures", return_value=["dns down"]) as preflight, \
                patch.dict(
                    "os.environ",
                    {"FEISHU_DNS_RETRY_ATTEMPTS": "5", "FEISHU_DNS_RETRY_DELAY_SEC": "0.2"},
                    clear=False,
                ):
            self.assertFalse(send_feishu_text("https://open.feishu.cn/webhook/test", "hello"))

        preflight.assert_called_once_with(
            "[Feishu] 摘要发送预检失败",
            ["https://open.feishu.cn/webhook/test"],
            attempts=5,
            retry_delay_sec=0.2,
        )

    def test_send_screening_result_file_preflight_uses_send_retry_settings_by_default(self):
        with patch("feishu_notifier.send_feishu_text", return_value=True), \
                patch("feishu_notifier.format_resolution_failures", return_value=["dns down"]) as preflight, \
                patch.dict(
                    "os.environ",
                    {"FEISHU_SEND_RETRY_ATTEMPTS": "4", "FEISHU_SEND_RETRY_DELAY_SEC": "0.1"},
                    clear=True,
                ):
            self.assertFalse(send_screening_result("https://open.feishu.cn/webhook/test", "summary", ["result.csv"]))

        preflight.assert_called_once_with(
            "[Feishu] 文件发送预检失败",
            ["https://open.feishu.cn/webhook/test", "https://open.feishu.cn"],
            attempts=4,
            retry_delay_sec=0.1,
        )


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from unittest.mock import patch

from feishu_notifier import send_feishu_text


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


if __name__ == "__main__":
    unittest.main()

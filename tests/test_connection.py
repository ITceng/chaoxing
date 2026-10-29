# -*- coding: utf-8 -*-
"""面板：AI/题库连接测试接口（鉴权、请求校验、SSRF 防御、探测结果）。

不产生真实外网调用：AI / SiliconFlow 的检查方法被 mock。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import panel  # noqa: E402


class ConnectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = panel.create_app(Path(self.tmp.name), supervisor=False)
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.extensions["chaoxing_panel"]["manager"].shutdown(timeout=1)
        self.tmp.cleanup()

    def _login(self):
        self.client.post("/api/auth/setup", json={
            "username": "admin", "password": "w-Secret-123!",
        })
        response = self.client.post("/api/auth/login", json={
            "username": "admin", "password": "w-Secret-123!",
        })
        self.assertEqual(response.status_code, 200, response.get_json())

    def _post(self, payload):
        return self.client.post("/api/settings/test-connection", json=payload)

    def test_requires_auth(self):
        response = self._post({"provider": "AI"})
        self.assertEqual(response.status_code, 401)

    def test_ai_success(self):
        self._login()
        with mock.patch.object(panel.AI, "check_llm_connection",
                               return_value=(True, "连接成功")):
            response = self._post({
                "provider": "AI",
                "endpoint": "https://api.openai.com/v1",
                "key": "sk-test1234567890abcdef",
                "model": "gpt-3.5-turbo",
            })
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["msg"], "连接成功")

    def test_ai_failure_reported(self):
        self._login()
        with mock.patch.object(panel.AI, "check_llm_connection",
                               return_value=(False, "未收到有效响应")):
            response = self._post({
                "provider": "AI",
                "endpoint": "https://api.openai.com/v1",
                "key": "sk-test1234567890abcdef",
            })
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertFalse(body["ok"])
        self.assertIn("未收到有效响应", body["msg"])

    def test_ai_timeout(self):
        self._login()

        def hang():
            import time as _time
            _time.sleep(60)
            return True, "不应返回"

        with mock.patch.object(panel.AI, "check_llm_connection", side_effect=hang):
            response = self._post({
                "provider": "AI",
                "endpoint": "https://api.openai.com/v1",
                "key": "sk-test1234567890abcdef",
            })
        body = response.get_json()
        self.assertFalse(body["ok"])
        self.assertIn("超时", body["msg"])

    def test_ssrf_private_endpoint_blocked(self):
        self._login()
        response = self._post({
            "provider": "AI",
            "endpoint": "https://127.0.0.1/v1",
            "key": "sk-test1234567890abcdef",
        })
        body = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(body["ok"])
        self.assertIn("内网", body["msg"])

    def test_ssrf_private_proxy_blocked(self):
        self._login()
        with mock.patch.object(panel.AI, "check_llm_connection",
                               return_value=(True, "连接成功")):
            response = self._post({
                "provider": "AI",
                "endpoint": "https://api.openai.com/v1",
                "key": "sk-test1234567890abcdef",
                "http_proxy": "http://127.0.0.1:8080",
            })
        body = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(body["ok"])
        self.assertIn("内网", body["msg"])

    def test_ssrf_non_https_blocked(self):
        self._login()
        response = self._post({
            "provider": "AI",
            "endpoint": "http://example.com/v1",
            "key": "sk-test1234567890abcdef",
        })
        body = response.get_json()
        self.assertFalse(body["ok"])
        self.assertIn("https", body["msg"])

    def test_unsupported_provider(self):
        self._login()
        response = self._post({"provider": "NoSuchProvider"})
        body = response.get_json()
        self.assertFalse(body["ok"])
        self.assertIn("不支持", body["msg"])

    def test_siliconflow_success(self):
        self._login()
        with mock.patch.object(panel.SiliconFlow, "check_llm_connection",
                               return_value=(True, "连接成功")):
            response = self._post({
                "provider": "SiliconFlow",
                "siliconflow_endpoint": "https://api.siliconflow.cn/v1/chat/completions",
                "siliconflow_key": "sk-test1234567890abcdef",
                "siliconflow_model": "deepseek-ai/DeepSeek-V3",
            })
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["msg"], "连接成功")

    def test_invalid_payload(self):
        self._login()
        response = self.client.post("/api/settings/test-connection", json=[1, 2])
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertFalse(body["ok"])

    def test_empty_payload_is_validated_not_500(self):
        self._login()
        response = self._post({})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertFalse(body["ok"])


if __name__ == "__main__":
    unittest.main()

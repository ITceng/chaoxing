# -*- coding: utf-8 -*-
"""错误页与健壮性测试：404/405/500 页面、/error、健康探针、诊断命令."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import panel  # noqa: E402


class ErrorPageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = panel.create_app(Path(self.tmp.name), supervisor=False)
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.extensions["chaoxing_panel"]["manager"].shutdown(timeout=1)
        self.tmp.cleanup()

    def test_unknown_page_returns_panel_404(self):
        response = self.client.get("/some/deep/path")
        self.assertEqual(404, response.status_code)
        self.assertIn("text/html", response.headers["Content-Type"])
        body = response.get_data(as_text=True)
        self.assertIn("页面不存在", body)
        self.assertIn("返回控制面板", body)
        self.assertIn('http-equiv="refresh"', body)     # 5 秒后自动回首页
        self.assertNotIn("Whitelabel", body)
        self.assertNotIn("Werkzeug", body)

    def test_unknown_api_returns_json_404(self):
        response = self.client.get("/api/does-not-exist")
        self.assertEqual(404, response.status_code)
        payload = response.get_json()
        self.assertIn("error", payload)
        self.assertIn("页面不存在", payload["error"])

    def test_error_route_redirects_home(self):
        response = self.client.get("/error")
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith("/"))

    def test_health_aliases(self):
        for path in ("/api/health", "/healthz", "/livez", "/readyz"):
            response = self.client.get(path)
            self.assertEqual(200, response.status_code, path)
            payload = response.get_json()
            self.assertTrue(payload["ok"], path)
            self.assertEqual("chaoxing-panel", payload["app"], path)

    def test_robots_disallow(self):
        response = self.client.get("/robots.txt")
        self.assertEqual(200, response.status_code)
        self.assertIn("Disallow: /", response.get_data(as_text=True))

    def test_method_not_allowed_html_and_json(self):
        page = self.client.post("/")
        self.assertEqual(405, page.status_code)
        self.assertIn("请求方法不被允许", page.get_data(as_text=True))

        api = self.client.post("/api/health")
        self.assertEqual(405, api.status_code)
        self.assertIn("error", api.get_json())

    def test_unexpected_error_page_and_json(self):
        self.app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)
        self.app.add_url_rule("/__boom", "__boom", lambda: 1 / 0)

        page = self.client.get("/__boom")
        self.assertEqual(500, page.status_code)
        body = page.get_data(as_text=True)
        self.assertIn("服务器内部错误", body)
        self.assertIn("错误编号", body)
        self.assertNotIn("ZeroDivisionError", body)   # 不向前端泄露内部细节

        api = self.client.get("/__boom", headers={"Accept": "application/json"})
        self.assertEqual(500, api.status_code)
        self.assertIn("error", api.get_json())
        self.assertTrue(api.headers.get("X-Request-Id"))
        self.assertNotIn("ZeroDivisionError", api.get_data(as_text=True))

    def test_security_headers_present(self):
        headers = self.client.get("/").headers
        self.assertEqual("nosniff", headers["X-Content-Type-Options"])
        self.assertEqual("no-referrer", headers["Referrer-Policy"])
        self.assertEqual("same-origin", headers["Cross-Origin-Opener-Policy"])
        self.assertEqual("same-origin", headers["Cross-Origin-Resource-Policy"])
        self.assertEqual("none", headers["X-Permitted-Cross-Domain-Policies"])
        self.assertIn("frame-ancestors", headers["Content-Security-Policy"])
        # 未启用 HTTPS 时不应该发 HSTS，避免把本地 HTTP 锁死
        self.assertNotIn("Strict-Transport-Security", headers)

    def test_api_responses_are_not_cached(self):
        headers = self.client.get("/api/health").headers
        self.assertEqual("no-store", headers["Cache-Control"])


class DoctorTest(unittest.TestCase):
    def test_doctor_reports_free_port_and_env(self):
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.dict(os.environ, {"PANEL_DATA_DIR": tmp}):
            app = panel.create_app(Path(tmp), supervisor=False)
            try:
                # 选一个几乎不会占用的高位端口
                code = panel.run_doctor(app, port=59321)
            finally:
                app.extensions["chaoxing_panel"]["manager"].shutdown(timeout=1)
            self.assertIn(code, (0, 1))   # 未初始化管理员时会返回 1，但流程必须跑完


import unittest.mock  # noqa: E402  (放在末尾避免污染上面的 import 顺序)

if __name__ == "__main__":
    unittest.main()

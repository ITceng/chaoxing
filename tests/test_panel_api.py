# -*- coding: utf-8 -*-
"""面板 HTTP 接口测试：初始化、登录、CSRF、账号 CRUD、密钥脱敏."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import panel  # noqa: E402


class PanelApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = panel.create_app(Path(self.tmp.name))
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.extensions["chaoxing_panel"]["manager"].shutdown(timeout=1)
        self.tmp.cleanup()

    def _setup(self):
        response = self.client.post("/api/auth/setup", json={
            "username": "admin", "password": "very-secret-123",
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["csrf"]

    def test_auth_setup_and_csrf(self):
        self.assertEqual(self.client.get("/api/auth/status").get_json()["configured"], False)
        csrf = self._setup()
        self.assertEqual(self.client.get("/api/auth/status").get_json()["configured"], True)
        self.assertEqual(self.client.get("/api/accounts").status_code, 200)
        # 写操作必须带 CSRF
        self.assertEqual(self.client.post("/api/accounts", json={
            "name": "A", "username": "13800000000", "password": "pwd123456",
        }).status_code, 403)
        response = self.client.post("/api/accounts", headers={"X-CSRF-Token": csrf}, json={
            "name": "A", "username": "13800000000", "password": "pwd123456",
        })
        self.assertEqual(response.status_code, 201, response.get_json())
        body = str(response.get_json())
        self.assertNotIn("password_enc", body)
        self.assertNotIn("pwd123456", body)

    def test_login_and_bad_origin(self):
        self._setup()
        self.client.post("/api/auth/logout")
        response = self.client.post("/api/auth/login", json={
            "username": "admin", "password": "wrong",
        })
        self.assertEqual(response.status_code, 401)
        response = self.client.post("/api/auth/login", json={
            "username": "admin", "password": "very-secret-123",
        })
        self.assertEqual(response.status_code, 200)
        response = self.client.post("/api/auth/login", json={
            "username": "admin", "password": "very-secret-123",
        }, headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)

    def test_account_validation(self):
        csrf = self._setup()
        response = self.client.post("/api/accounts", headers={"X-CSRF-Token": csrf}, json={
            "name": "A", "username": "13800000000", "password": "pwd", "speed": 9,
        })
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/accounts", headers={"X-CSRF-Token": csrf}, json={
            "name": "A", "username": "13800000000", "password": "pwd", "courses": ["x"],
        })
        self.assertEqual(response.status_code, 201)  # 未知字段被忽略

    def test_settings_mask(self):
        csrf = self._setup()
        response = self.client.put("/api/settings", headers={"X-CSRF-Token": csrf}, json={
            "tiku": {"tokens": "secret-token"},
        })
        self.assertEqual(response.status_code, 200)
        masked = self.client.get("/api/settings").get_json()["settings"]
        self.assertEqual(masked["tiku"]["tokens"], "***")
        self.client.put("/api/settings", headers={"X-CSRF-Token": csrf}, json={
            "tiku": {"tokens": "***"},
        })
        ext = self.app.extensions["chaoxing_panel"]
        self.assertEqual(ext["settings"].get()["tiku"]["tokens"], "secret-token")

    def test_api_key(self):
        self._setup()
        with mock.patch.object(panel, "API_KEY", "test-key"):
            client = self.app.test_client()
            self.assertEqual(client.get("/api/accounts", headers={"X-API-Key": "test-key"}).status_code, 200)
            self.assertEqual(client.get("/api/accounts", headers={"X-API-Key": "bad"}).status_code, 401)
        # 普通会话仍然可用
        self.assertEqual(self.client.get("/api/accounts").status_code, 200)

    def test_audit_requires_auth(self):
        self.assertEqual(self.app.test_client().get("/api/audit").status_code, 401)


if __name__ == "__main__":
    unittest.main()

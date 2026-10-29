# -*- coding: utf-8 -*-
"""安全加固回归测试：限流内存上限、输入清洗、配置损坏备份、安全响应头."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import panel  # noqa: E402
from panel_core.security import LoginThrottle, SecretBox, SlidingWindowLimiter  # noqa: E402
from panel_core.store import AccountStore, SettingsStore  # noqa: E402


class LimiterMemoryTest(unittest.TestCase):
    """攻击者可用随机 key 刷接口，限流器不能无限增长内存."""

    def test_limiter_caps_key_count(self):
        limiter = SlidingWindowLimiter(max_keys=100)
        for i in range(3000):
            limiter.allow(f"ip:10.0.0.{i}", 10, 60)
        self.assertLessEqual(len(limiter._events), 101)

    def test_limiter_still_limits_after_sweep(self):
        limiter = SlidingWindowLimiter(max_keys=100)
        self.assertTrue(limiter.allow("k", 2, 60)[0])
        self.assertTrue(limiter.allow("k", 2, 60)[0])
        self.assertFalse(limiter.allow("k", 2, 60)[0])

    def test_throttle_caps_key_count(self):
        throttle = LoginThrottle(max_failures=5, lock_seconds=600, max_keys=100)
        for i in range(3000):
            throttle.record_failure(f"user:{i}@example.com")
        self.assertLessEqual(len(throttle._failures), 101)

    def test_throttle_lock_still_works(self):
        throttle = LoginThrottle(max_failures=2, lock_seconds=600)
        throttle.record_failure("user:a")
        throttle.record_failure("user:a")
        self.assertTrue(throttle.is_locked("user:a")[0])


class CorruptFileTest(unittest.TestCase):
    def test_accounts_json_is_backed_up_not_lost(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            store = AccountStore(SecretBox(base / "secret.key"), base / "accounts.json")
            store.create({"name": "A", "username": "u", "password": "p"})
            (base / "accounts.json").write_text("{broken", encoding="utf-8")

            self.assertEqual([], store.list_public())
            backups = list(base.glob("accounts.json.corrupt-*"))
            self.assertEqual(1, len(backups), "损坏文件必须被备份")
            self.assertEqual("{broken", backups[0].read_text(encoding="utf-8"))

            store.create({"name": "B", "username": "u2", "password": "p"})
            self.assertEqual(["B"], [a["name"] for a in store.list_public()])

    def test_settings_backed_up_when_undecryptable(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            box = SecretBox(base / "secret.key")
            settings = SettingsStore(box, base / "settings.enc")
            settings.update({"tiku": {"tokens": "secret-token"}})
            (base / "settings.enc").write_text("not-a-fernet-token", encoding="utf-8")

            reloaded = SettingsStore(box, base / "settings.enc")
            self.assertEqual("", reloaded.get()["tiku"]["tokens"])
            backups = list(base.glob("settings.enc.corrupt-*"))
            self.assertEqual(1, len(backups), "设置文件损坏也要备份")
            self.assertTrue(json.dumps(backups[0].read_text(encoding="utf-8")))


class PanelHardeningApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = panel.create_app(Path(self.tmp.name), supervisor=False)
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.extensions["chaoxing_panel"]["manager"].shutdown(timeout=1)
        self.tmp.cleanup()

    def _login(self):
        response = self.client.post("/api/auth/setup", json={
            "username": "admin", "password": "very-secret-123",
        })
        return response.get_json()["csrf"]

    def test_setup_is_rate_limited(self):
        # 已初始化后直接返回 409（不消耗限流额度），真正需要保护的是"未初始化"状态
        statuses = [self.client.post("/api/auth/setup", json={
            "username": "admin", "password": "very-secret-123",
        }).status_code for _ in range(3)]
        self.assertEqual(200, statuses[0])
        self.assertTrue(all(code == 409 for code in statuses[1:]))

        with tempfile.TemporaryDirectory() as tmp:
            app = panel.create_app(Path(tmp), supervisor=False)
            app.config.update(TESTING=True)
            client = app.test_client()
            try:
                # 用非法密码刷初始化接口：每次都过限流但不会真的初始化成功
                statuses = [client.post("/api/auth/setup", json={
                    "username": "admin", "password": "short",
                }).status_code for _ in range(11)]
            finally:
                app.extensions["chaoxing_panel"]["manager"].shutdown(timeout=1)
            self.assertEqual(400, statuses[0])
            self.assertIn(429, statuses, "未初始化时也必须限制初始化尝试频率")

    def test_control_characters_are_stripped(self):
        csrf = self._login()
        response = self.client.post("/api/accounts", headers={"X-CSRF-Token": csrf}, json={
            "name": "正常名字\n伪造日志\r\n第二行",
            "username": "13800000000",
            "password": "secret",
        })
        self.assertEqual(201, response.status_code, response.get_json())
        name = response.get_json()["account"]["name"]
        self.assertNotIn("\n", name)
        self.assertNotIn("\r", name)

    def test_static_path_traversal_blocked(self):
        for path in ("/static/../panel.py", "/static/%2e%2e/panel.py",
                     "/static/../data/secret.key"):
            response = self.client.get(path)
            self.assertIn(response.status_code, (400, 404), path)
            self.assertNotIn(b"APP_VERSION", response.data)

    def test_hsts_only_on_https(self):
        with self.client.get("/") as response:
            self.assertNotIn("Strict-Transport-Security", response.headers)
        with self.client.get("/", base_url="https://panel.example.com") as secure:
            self.assertIn("Strict-Transport-Security", secure.headers)

    def test_x_frame_options_only_when_not_framed_externally(self):
        with mock.patch.object(panel, "FRAME_ANCESTORS", "'self'"):
            with self.client.get("/") as response:
                self.assertEqual("SAMEORIGIN", response.headers.get("X-Frame-Options"))
        with mock.patch.object(panel, "FRAME_ANCESTORS", "'self' https://*.e2b.app"):
            with self.client.get("/") as response:
                self.assertIsNone(response.headers.get("X-Frame-Options"))


if __name__ == "__main__":
    unittest.main()

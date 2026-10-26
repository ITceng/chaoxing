# -*- coding: utf-8 -*-
"""面板安全 / 持久化单元测试（不依赖网络与浏览器）."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panel_core.auth import PanelAuth
from panel_core.security import (
    LoginThrottle,
    SecretBox,
    SlidingWindowLimiter,
    hash_password,
    origin_allowed,
    verify_password,
)
from panel_core.store import AccountStore, SettingsStore


class SecurityTest(unittest.TestCase):
    def test_password_hash_roundtrip(self):
        hashed = hash_password("correct horse battery staple")
        self.assertTrue(verify_password("correct horse battery staple", hashed))
        self.assertFalse(verify_password("wrong", hashed))
        self.assertNotEqual(hash_password("x" * 10), hash_password("x" * 10))

    def test_secret_box_roundtrip_and_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = SecretBox(Path(tmp) / "secret.key")
            token = box.encrypt("敏感数据")
            self.assertNotIn("敏感数据", token)
            self.assertEqual(box.decrypt(token), "敏感数据")
            with self.assertRaises(Exception):
                box.decrypt(token[:-2] + "xx")

    def test_limiter(self):
        limiter = SlidingWindowLimiter()
        allowed = [limiter.allow("k", 2, 10)[0] for _ in range(3)]
        self.assertEqual(allowed, [True, True, False])

    def test_login_throttle(self):
        throttle = LoginThrottle(max_failures=2, lock_seconds=10)
        throttle.record_failure("u")
        self.assertFalse(throttle.is_locked("u")[0])
        throttle.record_failure("u")
        self.assertTrue(throttle.is_locked("u")[0])
        throttle.reset("u")
        self.assertFalse(throttle.is_locked("u")[0])

    def test_origin(self):
        self.assertTrue(origin_allowed(None, "panel.example.com"))
        self.assertTrue(origin_allowed("https://panel.example.com", "panel.example.com"))
        self.assertFalse(origin_allowed("https://evil.example", "panel.example.com"))
        self.assertTrue(origin_allowed("https://evil.example", "panel.example.com",
                                       ["https://evil.example"]))


class PanelStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.box = SecretBox(Path(self.tmp.name) / "secret.key")
        self.accounts = AccountStore(self.box, Path(self.tmp.name) / "accounts.json")
        self.settings = SettingsStore(self.box, Path(self.tmp.name) / "settings.enc")

    def tearDown(self):
        self.tmp.cleanup()

    def test_account_secrets_encrypted(self):
        account = self.accounts.create({
            "name": "测试", "username": "13800000000", "password": "pwd123456",
        })
        raw = self.accounts.get_raw(account["id"])
        self.assertNotIn("13800000000", str(raw))
        self.assertNotIn("pwd123456", str(raw))
        run = self.accounts.get_for_run(account["id"])
        self.assertEqual(run["username"], "13800000000")
        self.assertEqual(run["password"], "pwd123456")

    def test_masked_settings_do_not_overwrite_secret(self):
        self.settings.update({"tiku": {"tokens": "real-token"}})
        masked = self.settings.get_masked()
        self.assertEqual(masked["tiku"]["tokens"], "***")
        self.settings.update({"tiku": {"tokens": "***"}})
        self.assertEqual(self.settings.get()["tiku"]["tokens"], "real-token")

    def test_account_update_keeps_secret_when_blank(self):
        account = self.accounts.create({
            "name": "测试", "username": "13800000000", "password": "pwd123456",
        })
        self.accounts.update(account["id"], {"name": "改名"})
        run = self.accounts.get_for_run(account["id"])
        self.assertEqual(run["username"], "13800000000")
        self.assertEqual(run["password"], "pwd123456")


class PanelAuthTest(unittest.TestCase):
    def test_setup_and_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = PanelAuth(Path(tmp) / "admin.json")
            self.assertFalse(auth.configured)
            auth.set_admin("admin", "very-secret-123")
            self.assertTrue(auth.configured)
            self.assertTrue(auth.verify("admin", "very-secret-123"))
            self.assertFalse(auth.verify("admin", "bad"))
            self.assertFalse(auth.verify("other", "very-secret-123"))
            version = auth.session_version
            self.assertEqual(auth.bump_session_version(), version + 1)

    def test_short_password_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = PanelAuth(Path(tmp) / "admin.json")
            with self.assertRaises(ValueError):
                auth.set_admin("admin", "short")


if __name__ == "__main__":
    unittest.main()

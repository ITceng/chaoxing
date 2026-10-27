# -*- coding: utf-8 -*-
"""管理员密码重置：热加载、命令行重置、非交互保护."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import panel  # noqa: E402
from panel_core.auth import PanelAuth  # noqa: E402
from panel_core.security import LoginThrottle, SlidingWindowLimiter  # noqa: E402


class AuthHotReloadTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "admin.json"
        self.auth = PanelAuth(self.path)
        self.auth.set_admin("admin", "first-password")

    def tearDown(self):
        self._tmp.cleanup()

    def test_external_update_is_picked_up_without_restart(self):
        # 模拟另一个进程（python panel.py --reset-admin）改写 admin.json
        other = PanelAuth(self.path)
        other.set_admin("admin", "second-password-ok")

        self.assertTrue(self.auth.verify("admin", "second-password-ok"))
        self.assertFalse(self.auth.verify("admin", "first-password"))

    def test_session_version_increases_on_reset(self):
        before = self.auth.session_version
        self.auth.set_admin("admin", "another-password")
        self.assertGreater(self.auth.session_version, before)

    def test_username_change_is_visible(self):
        other = PanelAuth(self.path)
        other.set_admin("boss", "second-password-ok")
        self.assertEqual("boss", self.auth.username)
        self.assertTrue(self.auth.verify("boss", "second-password-ok"))
        self.assertFalse(self.auth.verify("admin", "second-password-ok"))

    def test_tampered_json_falls_back_to_unconfigured(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertFalse(self.auth.configured)


class ApplyResetTest(unittest.TestCase):
    def test_apply_reset_changes_password_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = PanelAuth(Path(tmp) / "admin.json")
            auth.set_admin("admin", "first-password")
            ok, message = panel.apply_admin_reset(auth, "admin", "brand-new-password")
            self.assertTrue(ok, message)
            self.assertTrue(auth.verify("admin", "brand-new-password"))

    def test_apply_reset_rejects_short_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = PanelAuth(Path(tmp) / "admin.json")
            auth.set_admin("admin", "first-password")
            ok, message = panel.apply_admin_reset(auth, "admin", "short")
            self.assertFalse(ok)
            self.assertIn("密码长度", message)
            self.assertTrue(auth.verify("admin", "first-password"), "失败时不应改动原密码")


class ThrottleClearTest(unittest.TestCase):
    def test_login_throttle_clear(self):
        throttle = LoginThrottle(max_failures=2, lock_seconds=600)
        for _ in range(2):
            throttle.record_failure("ip:1.2.3.4")
        self.assertTrue(throttle.is_locked("ip:1.2.3.4")[0])
        throttle.clear()
        self.assertFalse(throttle.is_locked("ip:1.2.3.4")[0])

    def test_limiter_clear(self):
        limiter = SlidingWindowLimiter()
        limiter.allow("k", 1, 60)
        self.assertFalse(limiter.allow("k", 1, 60)[0])
        limiter.clear()
        self.assertTrue(limiter.allow("k", 1, 60)[0])


class ResetCommandTest(unittest.TestCase):
    """真实执行 `python panel.py --reset-admin`（子进程）."""

    def _run(self, data_dir, env_extra, stdin=None, args=()):
        env = os.environ.copy()
        env["PANEL_DATA_DIR"] = str(data_dir)
        env.update(env_extra)
        return subprocess.run(
            [sys.executable, "panel.py", "--reset-admin", *args],
            cwd=str(ROOT), env=env, input=stdin,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120,
        )

    def test_env_vars_reset_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            first = self._run(data_dir, {"PANEL_ADMIN_USER": "admin",
                                         "PANEL_ADMIN_PASSWORD": "initial-password"})
            self.assertEqual(0, first.returncode, first.stdout)
            self.assertTrue((data_dir / "admin.json").exists())

            second = self._run(data_dir, {"PANEL_ADMIN_USER": "admin",
                                          "PANEL_ADMIN_PASSWORD": "reset-password-2"})
            self.assertEqual(0, second.returncode, second.stdout)
            self.assertIn("管理员已更新", second.stdout)

            auth = PanelAuth(data_dir / "admin.json")
            self.assertTrue(auth.verify("admin", "reset-password-2"))
            self.assertFalse(auth.verify("admin", "initial-password"))

    def test_non_interactive_without_env_reports_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(Path(tmp), {}, stdin="")
            self.assertEqual(2, result.returncode, result.stdout)
            self.assertIn("不是交互式终端", result.stdout)
            self.assertIn("PANEL_ADMIN_PASSWORD", result.stdout)

    def test_reset_reports_running_panel(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self._run(data_dir, {"PANEL_ADMIN_USER": "admin",
                                 "PANEL_ADMIN_PASSWORD": "initial-password"})
            env = {"PANEL_ADMIN_USER": "admin", "PANEL_ADMIN_PASSWORD": "changed-in-place-3"}
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(panel, "_panel_reachable", return_value=True):
                app = panel.create_app(data_dir, supervisor=False)
                try:
                    code = panel.reset_admin_command(app, port=8080)
                finally:
                    app.extensions["chaoxing_panel"]["manager"].shutdown(timeout=1)
            self.assertEqual(0, code)


if __name__ == "__main__":
    unittest.main()

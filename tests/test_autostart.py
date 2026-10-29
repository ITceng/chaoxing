# -*- coding: utf-8 -*-
"""无人值守测试：autostart 自动启动、失败自动重启、手动停止不被复活."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panel_core import runner as runner_module
from panel_core.runner import RunnerManager
from panel_core.security import SecretBox
from panel_core.store import AccountStore, SettingsStore


class _RateLimiter:
    def limit_rate(self, *args, **kwargs):
        return None


class FakeChaoxing:
    """登录成功、课程为空；fail_times 控制前 N 次运行抛异常."""

    fail_times = 0
    instances = 0

    def __init__(self, config):
        self.config = config
        self.control = config["control"]
        self.rate_limiter = _RateLimiter()
        FakeChaoxing.instances += 1

    def login(self, login_with_cookies=False):
        if FakeChaoxing.fail_times > 0:
            FakeChaoxing.fail_times -= 1
            raise RuntimeError("模拟登录异常")
        return {"status": True, "msg": ""}

    def get_course_list(self):
        return []


class AutostartTest(unittest.TestCase):
    def setUp(self):
        FakeChaoxing.fail_times = 0
        FakeChaoxing.instances = 0
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        box = SecretBox(base / "secret.key")
        self.accounts = AccountStore(box, base / "accounts.json")
        self.settings = SettingsStore(box, base / "settings.enc")
        self.settings.update({"max_concurrent_accounts": 3})
        self.manager = RunnerManager(self.accounts, self.settings)
        self.manager.restart_backoff = 0.0
        self.manager.max_auto_restarts = 3
        self._patchers = [
            mock.patch.object(runner_module, "build_adapter",
                              side_effect=lambda key, conf, tiku=None: FakeChaoxing(conf)),
            mock.patch.object(runner_module, "build_tiku_config", return_value={}),
        ]
        for patcher in self._patchers:
            patcher.start()

    def tearDown(self):
        for patcher in self._patchers:
            patcher.stop()
        self.manager.shutdown(timeout=5)
        self._tmp.cleanup()

    def _create(self, name, **extra):
        payload = {"name": name, "username": f"u-{name}", "password": "p", "use_cookies": False,
                   "course_list": "*", "jobs": 1}
        payload.update(extra)
        return self.accounts.create(payload)["id"]

    def _wait(self, predicate, timeout=10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def test_autostart_starts_idle_account(self):
        account_id = self._create("A", autostart=True)
        disabled_id = self._create("B", autostart=False)
        started = self.manager.supervise_once()
        self.assertIn(account_id, started)
        self.assertNotIn(disabled_id, started)
        runner = self.manager.get(account_id)
        self.assertTrue(runner.alive or runner.final_status == "completed")
        self.assertEqual("auto", runner.started_by)
        self.assertIsNone(self.manager.get(disabled_id))

    def test_failed_account_is_restarted(self):
        FakeChaoxing.fail_times = 1
        account_id = self._create("A", autostart=True, auto_restart=True)
        self.manager.supervise_once()             # 第一次：失败
        runner = self.manager.get(account_id)
        self.assertTrue(self._wait(lambda: not runner.alive and runner.final_status == "failed"))
        self.assertFalse(runner.restart_blocked)

        self.manager.supervise_once()             # 第二次：自动重启
        self.assertTrue(self._wait(lambda: runner.started_by == "auto" and runner.restart_at is None))
        self.assertEqual(1, runner.restart_count)
        self.assertTrue(self._wait(lambda: not runner.alive), "重启后应跑完")
        self.assertEqual("completed", runner.final_status)

    def test_auto_restart_respects_limit(self):
        FakeChaoxing.fail_times = 20
        account_id = self._create("A", autostart=True, auto_restart=True)
        runner = self.manager.get_or_create(account_id)
        for _ in range(10):
            self.manager.supervise_once()
            self._wait(lambda: not runner.alive, timeout=5)
        self.assertTrue(runner.restart_blocked, "超过上限后应停止自动重启")
        self.assertEqual(3, runner.restart_count)
        snapshot = runner.snapshot()
        self.assertTrue(snapshot["restart_blocked"])

    def test_manual_stop_is_not_revived(self):
        account_id = self._create("A", autostart=True)
        runner = self.manager.get_or_create(account_id)
        runner.start()
        self.assertTrue(self._wait(lambda: runner.snapshot()["status"] in ("running", "queued")))
        runner.stop()
        self.assertTrue(self._wait(lambda: not runner.alive))
        self.assertTrue(runner.user_stopped)
        self.manager.supervise_once()
        time.sleep(0.1)
        self.assertFalse(runner.alive, "手动停止的账号被自动重新启动了")
        # 手动启动后恢复自动管理
        runner.start()
        self.assertTrue(self._wait(lambda: runner.alive or runner.final_status == "completed"))
        self.assertFalse(runner.user_stopped)

    def test_stop_while_failing_is_reported_as_stopped(self):
        """用户在失败/重试过程中点停止：状态应为 stopped 而不是 failed，且不再自动重启."""
        FakeChaoxing.fail_times = 20
        account_id = self._create("A", autostart=True, auto_restart=True)
        runner = self.manager.get_or_create(account_id)
        runner.start(source="auto")
        self.assertTrue(self._wait(lambda: runner.started_by == "auto"))
        runner.stop()
        self.assertTrue(self._wait(lambda: not runner.alive))
        self.assertEqual("stopped", runner.final_status)
        self.assertTrue(runner.user_stopped)
        self.manager.supervise_once()
        time.sleep(0.2)
        self.assertFalse(runner.alive)
        self.assertEqual("stopped", runner.final_status)

    def test_auto_restart_disabled_account_stays_failed(self):
        FakeChaoxing.fail_times = 1
        account_id = self._create("A", autostart=True, auto_restart=False)
        self.manager.supervise_once()
        runner = self.manager.get(account_id)
        self.assertTrue(self._wait(lambda: not runner.alive))
        self.manager.supervise_once()
        time.sleep(0.2)
        self.assertFalse(runner.alive)
        self.assertEqual("failed", runner.final_status)

    def test_start_supervisor_is_idempotent(self):
        self.manager.supervisor_interval = 0.05
        self.assertTrue(self.manager.start_supervisor())
        self.assertFalse(self.manager.start_supervisor())
        self.assertTrue(self.manager.supervisor_running)
        self.manager.stop_supervisor()
        self.assertFalse(self.manager.supervisor_running)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""多账号隔离回归测试：停止/暂停一个账号不得影响其他账号.

用假 Chaoxing 替换真实网络调用，验证：
1. 两个账号同时运行，停止 A 后 A 退出、B 继续跑完；
2. 暂停 A 后 B 仍正常推进；
3. 抢到并发名额的瞬间被停止不会泄漏名额。
"""
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.control import RunControl
from panel_core import runner as runner_module
from panel_core.runner import RunnerManager
from panel_core.security import SecretBox
from panel_core.store import AccountStore, SettingsStore

CHAPTERS = 40
CHAPTER_DELAY = 0.05


class _FakeRateLimiter:
    def limit_rate(self, *args, **kwargs):
        return None


class FakeChaoxing:
    """最小可用的假 Chaoxing：登录、课程、章节、任务点都返回固定结构."""

    instances: list["FakeChaoxing"] = []
    lock = threading.Lock()

    def __init__(self, config):
        self.config = config
        self.control = config["control"]
        self.rate_limiter = _FakeRateLimiter()
        self.username = config.get("username", "")
        self.study_calls = 0
        with FakeChaoxing.lock:
            FakeChaoxing.instances.append(self)

    def login(self, login_with_cookies: bool = False):
        return {"status": True, "msg": ""}

    def get_course_list(self):
        return [{"courseId": "c1", "clazzId": "1", "cpi": "1", "title": "测试课程"}]

    def get_course_point(self, course_id, clazz_id, cpi):
        return {"points": [
            {"title": f"章节{i}", "has_finished": False} for i in range(CHAPTERS)
        ]}

    def get_job_list(self, course, point):
        # 用 RunControl.sleep 模拟耗时任务：可暂停、可被停止打断
        self.study_calls += 1
        self.control.sleep(CHAPTER_DELAY)
        return [], {"notOpen": False}


class RunnerIsolationTest(unittest.TestCase):
    def setUp(self):
        FakeChaoxing.instances = []
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        box = SecretBox(base / "secret.key")
        self.accounts = AccountStore(box, base / "accounts.json")
        self.settings = SettingsStore(box, base / "settings.enc")
        self.settings.update({"max_concurrent_accounts": 3})
        self.manager = RunnerManager(self.accounts, self.settings)
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

    # ------------------------------------------------------------------ utils
    def _account(self, name: str) -> str:
        record = self.accounts.create({
            "name": name,
            "username": f"user-{name}",
            "password": "secret-password",
            "use_cookies": False,
            "course_list": "*",
            "jobs": 1,
            "course_serial": True,
        })
        return record["id"]

    def _fake(self, username: str) -> FakeChaoxing:
        for inst in FakeChaoxing.instances:
            if inst.username == username:
                return inst
        raise AssertionError(f"未找到 {username} 的假 Chaoxing 实例")

    def _wait(self, predicate, timeout: float = 10.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return False

    # ------------------------------------------------------------------ tests
    def test_stop_one_account_keeps_other_running(self):
        a_id, b_id = self._account("A"), self._account("B")
        a, b = self.manager.get_or_create(a_id), self.manager.get_or_create(b_id)

        self.assertTrue(a.start()[0])
        self.assertTrue(b.start()[0])
        self.assertTrue(self._wait(lambda: a.snapshot()["status"] == "running"), "A 未进入运行")
        self.assertTrue(self._wait(lambda: b.snapshot()["status"] == "running"), "B 未进入运行")

        self.assertTrue(a.stop()[0])
        self.assertTrue(self._wait(lambda: not a.alive, timeout=10), "A 停止后线程未退出")

        # 关键断言：A 停止的同一时刻，B 仍在运行
        self.assertEqual("stopped", a.final_status)
        self.assertTrue(b.alive, "停止 A 时 B 也被停止了")
        self.assertFalse(b.control.stopped)
        self.assertEqual("running", b.snapshot()["status"])

        self.assertTrue(self._wait(lambda: not b.alive, timeout=30), "B 未能继续完成")
        self.assertEqual("completed", b.final_status)
        self.assertEqual(CHAPTERS, b.summary.get("success"))
        self.assertGreater(self._fake("user-B").study_calls, 0)

    def test_pause_one_account_does_not_pause_other(self):
        a_id, b_id = self._account("A"), self._account("B")
        a, b = self.manager.get_or_create(a_id), self.manager.get_or_create(b_id)
        a.start()
        b.start()
        self.assertTrue(self._wait(lambda: a.snapshot()["status"] == "running"))
        self.assertTrue(self._wait(lambda: b.snapshot()["status"] == "running"))

        self.assertTrue(a.pause()[0])
        self.assertTrue(self._wait(lambda: a.snapshot()["status"] == "paused"), "A 未进入暂停")
        fake_a, fake_b = self._fake("user-A"), self._fake("user-B")
        frozen_calls = fake_a.study_calls
        b_calls = fake_b.study_calls

        self.assertTrue(self._wait(lambda: fake_b.study_calls > b_calls, timeout=5),
                        "B 在 A 暂停期间没有继续推进")
        time.sleep(0.2)
        self.assertEqual(frozen_calls, fake_a.study_calls, "A 暂停后仍在执行任务")
        self.assertFalse(b.control.paused)

        self.assertTrue(a.resume()[0])
        self.assertTrue(self._wait(lambda: fake_a.study_calls > frozen_calls, timeout=5),
                        "A 继续后没有恢复执行")
        self.assertTrue(self._wait(lambda: not b.alive, timeout=30))
        self.assertEqual("completed", b.final_status)
        self.assertTrue(self._wait(lambda: not a.alive, timeout=30), "A 恢复后未能完成")
        self.assertEqual("completed", a.final_status)

    def test_semaphore_not_leaked_when_stopped_after_acquire(self):
        manager = RunnerManager(self.accounts, self.settings)
        manager.update_limit(1)
        control = RunControl()
        acquired = []

        class _Semaphore:
            def acquire(self, timeout=None):
                control.stop()          # 恰好在这一刻被要求停止
                acquired.append(True)
                return True

            def release(self):
                acquired.append(False)

        manager._semaphore = _Semaphore()
        self.assertFalse(manager.acquire(control))
        self.assertEqual([True, False], acquired, "停止后没有归还并发名额")


if __name__ == "__main__":
    unittest.main()

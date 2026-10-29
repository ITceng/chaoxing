# -*- coding: utf-8 -*-
"""多平台接入层：注册表、契约自检、演示平台端到端、面板平台字段."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import panel  # noqa: E402
from api.control import RunControl  # noqa: E402
from panel_core.runner import RunnerManager  # noqa: E402
from panel_core.security import SecretBox  # noqa: E402
from panel_core.store import AccountStore, SettingsStore  # noqa: E402
from platforms import (  # noqa: E402
    PlatformError,
    PlatformNotImplemented,
    PlatformSpec,
    build_adapter,
    get_platform,
    list_platforms,
    register_platform,
    validate_adapter,
)


class RegistryTest(unittest.TestCase):
    def test_chaoxing_is_default_and_available(self):
        spec = get_platform("chaoxing")
        self.assertEqual("chaoxing", spec.key)
        self.assertTrue(spec.available)
        self.assertIn("video", spec.capabilities)

    def test_empty_key_falls_back_to_chaoxing(self):
        self.assertEqual("chaoxing", get_platform("").key)

    def test_unknown_platform_raises_readable_error(self):
        with self.assertRaises(PlatformError) as ctx:
            get_platform("zuoyebang")
        self.assertIn("未知平台", str(ctx.exception))
        self.assertIn("chaoxing", str(ctx.exception))

    def test_list_platforms_excludes_planned_by_default(self):
        keys = [item["key"] for item in list_platforms()]
        self.assertIn("chaoxing", keys)
        self.assertIn("demo", keys)
        self.assertNotIn("zhihuishu", keys)

        all_keys = [item["key"] for item in list_platforms(include_planned=True)]
        self.assertIn("zhihuishu", all_keys)

    def test_planned_platform_cannot_be_built(self):
        with self.assertRaises(PlatformNotImplemented) as ctx:
            build_adapter("zhihuishu", {})
        self.assertIn("尚未实现", str(ctx.exception))

    def test_public_metadata_shape(self):
        item = next(p for p in list_platforms() if p["key"] == "chaoxing")
        for key in ("key", "name", "summary", "status", "available",
                    "capabilities", "credentials", "api_version"):
            self.assertIn(key, item)

    def test_register_rejects_bad_key(self):
        with self.assertRaises(ValueError):
            register_platform(PlatformSpec(key="坏 key!", name="x"))


class ContractTest(unittest.TestCase):
    def test_demo_adapter_satisfies_contract(self):
        adapter = build_adapter("demo", {
            "username": "u", "password": "p", "control": RunControl(),
        })
        self.assertEqual([], validate_adapter(adapter))
        self.assertTrue(adapter.login()["status"])
        self.assertEqual(2, len(adapter.get_course_list()))
        points = adapter.get_course_point("demo-1", "d1", "1")
        self.assertEqual(3, len(points["points"]))
        jobs, job_info = adapter.get_job_list(points["points"][0]["title"] and
                                              {"courseId": "demo-1", "clazzId": "d1"},
                                              points["points"][0])
        self.assertTrue(jobs)
        self.assertIn("notOpen", job_info)

    def test_validate_reports_missing_methods(self):
        class Half:
            control = None
            rate_limiter = None

            def login(self, login_with_cookies=False):
                return {"status": True}

        missing = validate_adapter(Half())
        self.assertIn("get_course_list", missing)
        self.assertIn("study_video", missing)

    def test_login_requires_credentials(self):
        adapter = build_adapter("demo", {"control": RunControl()})
        self.assertFalse(adapter.login()["status"])


class DemoPlatformRunnerTest(unittest.TestCase):
    """不用打桩，直接用演示平台跑完整调度器."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        box = SecretBox(base / "secret.key")
        self.accounts = AccountStore(box, base / "accounts.json")
        self.settings = SettingsStore(box, base / "settings.enc")
        self.settings.update({"max_concurrent_accounts": 2})
        self.manager = RunnerManager(self.accounts, self.settings)

    def tearDown(self):
        self.manager.shutdown(timeout=5)
        self._tmp.cleanup()

    def _wait(self, predicate, timeout=15.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def test_demo_platform_runs_to_completion(self):
        record = self.accounts.create({
            "name": "演示账号", "username": "demo", "password": "demo",
            "platform": "demo", "course_list": "*", "jobs": 1,
            "autostart": False, "demo_step_delay": 0.01,
        })
        runner = self.manager.get_or_create(record["id"])
        ok, message = runner.start()
        self.assertTrue(ok, message)
        self.assertTrue(self._wait(lambda: not runner.alive, timeout=30))
        self.assertEqual("completed", runner.final_status, runner.error)
        self.assertEqual(6, runner.summary.get("success"))
        self.assertEqual([], runner.snapshot()["current"])

    def test_unknown_platform_account_fails_with_clear_message(self):
        record = self.accounts.create({
            "name": "坏平台", "username": "u", "password": "p",
            "platform": "chaoxing", "course_list": "*",
        })
        # 绕过面板校验，直接把存储里的平台改成不存在的值（模拟旧数据/手改文件）
        accounts = self.accounts._load()
        for account in accounts:
            if account["id"] == record["id"]:
                account["platform"] = "not-exist"
        self.accounts._save(accounts)

        runner = self.manager.get_or_create(record["id"])
        runner.start()
        self.assertTrue(self._wait(lambda: not runner.alive))
        self.assertEqual("failed", runner.final_status)
        self.assertIn("未知平台", runner.error)


class PanelPlatformTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = panel.create_app(Path(self.tmp.name), supervisor=False)
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()
        self.csrf = self.client.post("/api/auth/setup", json={
            "username": "admin", "password": "very-secret-123",
        }).get_json()["csrf"]

    def tearDown(self):
        self.app.extensions["chaoxing_panel"]["manager"].shutdown(timeout=1)
        self.tmp.cleanup()

    def test_meta_lists_platforms(self):
        payload = self.client.get("/api/meta").get_json()
        keys = [item["key"] for item in payload["platforms"]]
        self.assertIn("chaoxing", keys)
        self.assertIn("demo", keys)
        self.assertNotIn("zhihuishu", keys)      # planned 平台不可选
        self.assertEqual("chaoxing", payload["default_platform"])

    def test_create_account_with_demo_platform(self):
        response = self.client.post("/api/accounts", headers={"X-CSRF-Token": self.csrf}, json={
            "name": "演示", "username": "demo", "password": "demo", "platform": "demo",
        })
        self.assertEqual(201, response.status_code, response.get_json())
        self.assertEqual("demo", response.get_json()["account"]["platform"])

    def test_create_account_defaults_to_chaoxing(self):
        response = self.client.post("/api/accounts", headers={"X-CSRF-Token": self.csrf}, json={
            "name": "默认平台", "username": "13800000000", "password": "secret",
        })
        self.assertEqual("chaoxing", response.get_json()["account"]["platform"])

    def test_unknown_platform_rejected(self):
        response = self.client.post("/api/accounts", headers={"X-CSRF-Token": self.csrf}, json={
            "name": "坏", "username": "u", "password": "p", "platform": "chaoxingx",
        })
        self.assertEqual(400, response.status_code)
        self.assertIn("未知平台", response.get_json()["error"])

    def test_planned_platform_rejected(self):
        response = self.client.post("/api/accounts", headers={"X-CSRF-Token": self.csrf}, json={
            "name": "智慧树", "username": "u", "password": "p", "platform": "zhihuishu",
        })
        self.assertEqual(400, response.status_code)
        self.assertIn("尚未实现", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()

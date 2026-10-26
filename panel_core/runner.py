# -*- coding: utf-8 -*-
"""账号运行器：每个账号一个独立线程、独立 Session、独立 Cookie 文件、独立控制信号."""
from __future__ import annotations

import threading
import time
import traceback
from typing import Any, Optional

from api.base import SessionManager
from api.control import RunControl, TaskCancelled
from api.logger import (
    get_account_logs,
    logger,
    redact_text,
    reset_account_context,
    set_account_context,
)
from api.notification import Notification
from main import (
    JobProcessor,
    build_chapter_tasks,
    filter_courses,
    init_chaoxing,
)
from panel_core.store import AccountStore, SettingsStore


def build_tiku_config(settings: dict[str, Any], provider_override: str = "") -> dict[str, Any]:
    """把面板设置转换为 Tiku 需要的配置字典."""
    tiku = dict((settings or {}).get("tiku") or {})
    if provider_override:
        tiku["provider"] = provider_override
    tiku.setdefault("submit", "false")
    tiku.setdefault("cover_rate", 0.9)
    tiku.setdefault("delay", 1.0)
    tiku.setdefault("true_list", "正确,对,√,是")
    tiku.setdefault("false_list", "错误,错,×,否,不对,不正确")
    # 兼容 Tiku.init_tiku 的字符串比较
    for key in ("submit", "likeapi_search", "likeapi_vision", "likeapi_retry", "check_llm_connection"):
        if key in tiku and isinstance(tiku[key], bool):
            tiku[key] = "true" if tiku[key] else "false"
    return tiku


def _course_list(value: str) -> list[str]:
    items = [item.strip() for item in str(value or "").split(",") if item.strip()]
    return items or ["*"]


class AccountRunner:
    """单个账号的运行实例."""

    def __init__(self, account_id: str, accounts: AccountStore, settings: SettingsStore,
                 manager: "RunnerManager"):
        self.account_id = account_id
        self.accounts = accounts
        self.settings = settings
        self.manager = manager
        self.control = RunControl()
        self.thread: Optional[threading.Thread] = None
        self.processor: Optional[JobProcessor] = None
        self.tasks: list = []
        self.courses: list[dict[str, Any]] = []
        self.phase = "idle"
        self.final_status = "idle"
        self.error = ""
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.summary: dict[str, Any] = {}
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- controls
    @property
    def alive(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def start(self) -> tuple[bool, str]:
        with self._lock:
            if self.alive:
                return False, "账号正在运行中"
            self.control = RunControl()
            self.processor = None
            self.tasks = []
            self.error = ""
            self.summary = {}
            self.final_status = "starting"
            self.phase = "queued"
            self.started_at = time.time()
            self.finished_at = None
            self.thread = threading.Thread(
                target=self._run, daemon=True, name=f"account-{self.account_id[:8]}"
            )
            self.thread.start()
            return True, "已启动"

    def pause(self) -> tuple[bool, str]:
        if not self.alive:
            return False, "账号未在运行"
        self.control.pause("user")
        return True, "已暂停"

    def resume(self) -> tuple[bool, str]:
        if not self.alive:
            return False, "账号未在运行"
        self.control.resume()
        return True, "已继续"

    def stop(self) -> tuple[bool, str]:
        if not self.alive and self.final_status in ("idle",):
            return False, "账号未在运行"
        self.control.stop()
        if self.processor is not None:
            try:
                self.processor.stop()
            except Exception as e:  # noqa: BLE001
                logger.debug(f"停止调度器时异常: {e}")
        return True, "正在停止"

    # -------------------------------------------------------------------- run
    def _set_phase(self, phase: str) -> None:
        self.phase = phase

    def _fail(self, message: str, status: str = "failed") -> None:
        self.error = redact_text(str(message))[:500]
        self.final_status = status
        self.phase = "done"
        logger.error(f"账号任务失败: {self.error}")

    def _run(self) -> None:
        token = set_account_context(self.account_id)
        acquired = False
        try:
            account = self.accounts.get_for_run(self.account_id)
            name = account.get("name") or account.get("username_hint") or self.account_id[:8]
            self._set_phase("waiting")
            logger.info(f"账号 {name} 等待调度资源...")

            if not self.manager.acquire(self.control):
                self.final_status = "stopped"
                self.phase = "done"
                return
            acquired = True

            settings = self.settings.get()
            cookies_path = self.accounts.cookies_path(self.account_id)
            if account.get("cookies_text"):
                self.accounts.write_cookies(self.account_id, account["cookies_text"])
            if account.get("use_cookies") and not cookies_path.exists():
                raise RuntimeError("已启用 Cookie 登录，但账号没有可用的 Cookie")
            if not account.get("use_cookies") and (not account.get("username") or not account.get("password")):
                raise RuntimeError("账号或密码为空（也不是 Cookie 登录）")

            SessionManager.bind(cookies_path=str(cookies_path))
            common_config = {
                "username": account.get("username", ""),
                "password": account.get("password", ""),
                "use_cookies": bool(account.get("use_cookies", False)),
                "course_list": _course_list(account.get("course_list", "")),
                "speed": min(2.0, max(1.0, float(account.get("speed", 1.0) or 1.0))),
                "jobs": max(1, min(8, int(account.get("jobs", 2) or 2))),
                "notopen_action": account.get("notopen_action", "retry"),
                "retry_interval": 1.0,
                "course_serial": bool(account.get("course_serial", True)),
                "work_redo_enabled": bool(account.get("work_redo_enabled", False)),
                "work_max_retries": max(1, min(10, int(account.get("work_max_retries", 3) or 3))),
                "add_learning_count": bool(account.get("add_learning_count", False)),
                "target_count": max(1, min(10000, int(account.get("target_count", 100) or 100))),
                "cookies_path": str(cookies_path),
                "control": self.control,
                "account_id": self.account_id,
                "non_interactive": True,
            }

            self._set_phase("login")
            logger.info(f"开始登录账号 {name} ...")
            chaoxing = init_chaoxing(
                common_config,
                build_tiku_config(settings, account.get("provider", "")),
                config_path=None,
            )
            login_state = chaoxing.login(login_with_cookies=common_config["use_cookies"])
            if not login_state.get("status"):
                raise RuntimeError(f"登录失败: {login_state.get('msg')}")
            if self.control.stopped:
                raise TaskCancelled("stopped")

            self._set_phase("courses")
            all_course = chaoxing.get_course_list()
            course_task = filter_courses(all_course, common_config["course_list"], interactive=False)
            self.courses = [
                {
                    "courseId": c.get("courseId"),
                    "clazzId": c.get("clazzId"),
                    "title": c.get("title"),
                }
                for c in course_task
            ]
            logger.info(f"账号 {name} 课程筛选完成，共 {len(course_task)} 门课程")

            self._set_phase("chapters")
            tasks = build_chapter_tasks(chaoxing, course_task,
                                        course_serial=common_config["course_serial"])
            self.tasks = tasks
            if not tasks:
                logger.warning(f"账号 {name} 没有可执行章节")
                self.final_status = "completed"
                self.phase = "done"
                return

            self._set_phase("running")
            self.processor = JobProcessor(chaoxing, tasks, common_config, control=self.control)
            summary = self.processor.run()
            self.summary = summary
            if self.control.stopped:
                self.final_status = "stopped"
            elif summary.get("failed", 0) or summary.get("cancelled", 0):
                self.final_status = "failed"
            else:
                self.final_status = "completed"

            # 章节学习次数（若开启）
            if self.final_status == "completed" and common_config["add_learning_count"]:
                self._set_phase("learning_count")
                from api.process import increase_learning_count_for_course
                for course in course_task:
                    self.control.checkpoint()
                    increase_learning_count_for_course(chaoxing, course, common_config)

            self._set_phase("done")
            logger.info(f"账号 {name} 任务结束: {summary}")
            self._notify(settings, name, summary)

        except TaskCancelled:
            self.final_status = "stopped"
            self.phase = "done"
            logger.warning("账号任务已停止")
        except Exception as e:  # noqa: BLE001
            logger.error(f"账号任务异常: {type(e).__name__}: {e}")
            logger.debug(traceback.format_exc())
            self._fail(f"{type(e).__name__}: {e}")
        finally:
            if acquired:
                self.manager.release()
            self.finished_at = time.time()
            reset_account_context(token)

    def _notify(self, settings: dict[str, Any], name: str, summary: dict[str, Any]) -> None:
        conf = dict(settings.get("notification") or {})
        if not conf.get("provider") or not conf.get("url"):
            return
        try:
            notification = Notification()
            notification.config_set(conf)
            notification = notification.get_notification_from_config()
            notification.init_notification()
            notification.send(
                f"chaoxing : 账号 {name} 任务结束，成功 {summary.get('success', 0)}，"
                f"失败 {summary.get('failed', 0)}，跳过 {summary.get('skipped', 0)}"
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"发送通知失败: {e}")

    # ---------------------------------------------------------------- snapshot
    def _task_counts(self) -> dict[str, int]:
        counts = {"pending": 0, "running": 0, "success": 0, "failed": 0,
                  "skipped": 0, "cancelled": 0}
        for task in self.tasks:
            status = getattr(task, "status", "pending")
            counts[status] = counts.get(status, 0) + 1
        return counts

    def snapshot(self) -> dict[str, Any]:
        counts = self._task_counts()
        total = len(self.tasks)
        done = counts["success"] + counts["failed"] + counts["skipped"] + counts["cancelled"]
        if not self.alive:
            status = self.final_status
        elif self.control.stopped:
            status = "stopping"
        elif self.control.paused:
            status = "paused"
        elif self.phase in ("queued", "waiting"):
            status = "queued"
        else:
            status = "running"

        current = [
            {
                "course": getattr(t, "course", {}).get("title", ""),
                "chapter": getattr(t, "point", {}).get("title", ""),
            }
            for t in self.tasks if getattr(t, "status", "") == "running"
        ]
        return {
            "id": self.account_id,
            "status": status,
            "phase": self.phase,
            "alive": self.alive,
            "paused": self.control.paused,
            "stopped": self.control.stopped,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "summary": self.summary,
            "counts": counts,
            "total": total,
            "done": done,
            "progress": round(done / total * 100, 1) if total else 0.0,
            "current": current[:5],
            "courses": self.courses,
            "logs_available": True,
        }

    def logs(self, after: int = 0, limit: int = 200) -> list[dict]:
        return get_account_logs(self.account_id, after=after, limit=limit)


class RunnerManager:
    """管理所有账号运行器，并限制同时运行的账号数量."""

    def __init__(self, accounts: AccountStore, settings: SettingsStore):
        self.accounts = accounts
        self.settings = settings
        self._runners: dict[str, AccountRunner] = {}
        self._lock = threading.RLock()
        self._semaphore = threading.BoundedSemaphore(
            max(1, int(settings.get().get("max_concurrent_accounts", 2) or 2))
        )

    def get(self, account_id: str) -> Optional[AccountRunner]:
        with self._lock:
            return self._runners.get(account_id)

    def get_or_create(self, account_id: str) -> AccountRunner:
        with self._lock:
            runner = self._runners.get(account_id)
            if runner is None:
                runner = AccountRunner(account_id, self.accounts, self.settings, self)
                self._runners[account_id] = runner
            return runner

    def acquire(self, control: RunControl) -> bool:
        while not control.stopped:
            if self._semaphore.acquire(timeout=0.5):
                if control.stopped:
                    # 获取到名额的同一瞬间被要求停止：立刻归还，否则名额永久泄漏
                    self.release()
                    return False
                return True
        return False

    def release(self) -> None:
        try:
            self._semaphore.release()
        except ValueError:
            pass

    def update_limit(self, value: int) -> None:
        new_value = max(1, int(value))
        with self._lock:
            self._semaphore = threading.BoundedSemaphore(new_value)

    def remove(self, account_id: str) -> None:
        with self._lock:
            runner = self._runners.pop(account_id, None)
        if runner:
            try:
                runner.stop()
            except Exception:
                pass

    def shutdown(self, timeout: float = 3.0) -> None:
        with self._lock:
            runners = list(self._runners.values())
        for runner in runners:
            try:
                runner.stop()
            except Exception:
                pass
        deadline = time.time() + timeout
        for runner in runners:
            thread = runner.thread
            if thread and thread.is_alive():
                thread.join(max(0.0, deadline - time.time()))

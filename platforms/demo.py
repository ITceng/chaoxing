# -*- coding: utf-8 -*-
"""演示平台：不访问任何真实网站，用来验证多平台链路（也是写新适配器的模板）.

它的作用是让"新增平台"这件事可以立刻被验证：

* 面板里能选到它、能启动、能看到日志、进度和"完成"状态；
* 单元测试里可以不打桩就跑完整个调度流程；
* 你写新平台时，直接复制这个文件改内部实现即可。

注意：演示平台**不会**发送任何网络请求，也不会修改真实账号数据。
"""
from __future__ import annotations

import hashlib
from typing import Any

from api.base import RateLimiter, StudyResult
from api.control import RunControl
from platforms.base import STATUS_DEMO, PlatformSpec
from platforms.registry import register_platform

# 演示数据：2 门课，每门 3 章，每章若干任务点
DEMO_COURSES = [
    {"courseId": "demo-1", "clazzId": "d1", "cpi": "1", "title": "演示课程 A（视频+文档）"},
    {"courseId": "demo-2", "clazzId": "d2", "cpi": "1", "title": "演示课程 B（章节测验）"},
]
_CHAPTER_TITLES = ["第一章 绪论", "第二章 基本概念", "第三章 综合练习"]


class DemoPlatform:
    """满足 ``platforms/base.py`` 契约的最小实现."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.control: RunControl = config.get("control") or RunControl()
        self.rate_limiter = RateLimiter(call_interval=0.0, control=self.control)
        self.cookies_path = config.get("cookies_path")
        self.account_id = config.get("account_id")
        self.username = config.get("username") or "demo-user"
        self._uid = hashlib.sha1(str(self.username).encode()).hexdigest()[:12]
        self.step_delay = float(config.get("demo_step_delay", 0.05))
        self.calls: list[str] = []

    # ------------------------------------------------------------------ 基础
    def new_session(self):
        return None  # 演示平台不需要 HTTP 会话

    def get_uid(self) -> str:
        return self._uid

    # ------------------------------------------------------------------ 登录
    def login(self, login_with_cookies: bool = False) -> dict[str, Any]:
        self.control.checkpoint()
        if not login_with_cookies and not (self.config.get("username") and self.config.get("password")):
            return {"status": False, "msg": "演示平台需要用户名和密码（或 Cookie 登录）"}
        return {"status": True, "msg": "演示平台登录成功"}

    # -------------------------------------------------------------- 课程/章节
    def get_course_list(self) -> list[dict[str, Any]]:
        self.control.checkpoint()
        return [dict(course) for course in DEMO_COURSES]

    def get_course_point(self, course_id: str, clazz_id: str, cpi: str) -> dict[str, Any]:
        self.control.checkpoint()
        return {
            "points": [
                {
                    "title": title,
                    "has_finished": False,
                    "knowledgeid": f"{course_id}-{index}",
                }
                for index, title in enumerate(_CHAPTER_TITLES)
            ]
        }

    def get_job_list(self, course: dict[str, Any], point: dict[str, Any]):
        self.control.checkpoint()
        if course["courseId"].endswith("2"):
            jobs = [{"type": "workid", "jobid": "demo-work", "name": "章节测验"}]
        else:
            jobs = [
                {"type": "video", "jobid": "demo-video", "name": "视频"},
                {"type": "document", "jobid": "demo-doc", "name": "文档"},
            ]
        return jobs, {"notOpen": False, "knowledgeid": point.get("knowledgeid", ""), "otherInfo": ""}

    # ---------------------------------------------------------------- 任务点
    def _fake_study(self, label: str) -> StudyResult:
        self.control.checkpoint()
        self.calls.append(label)
        self.control.sleep(self.step_delay)   # 可暂停/可停止的等待
        self.control.checkpoint()
        return StudyResult.SUCCESS

    def study_video(self, course, job, job_info, _speed: float = 1.0, _type: str = "Video"):
        return self._fake_study(f"video:{course['courseId']}:{job_info.get('knowledgeid')}")

    def study_document(self, course, job):
        return self._fake_study(f"document:{course['courseId']}")

    def study_work(self, course, job, job_info):
        return self._fake_study(f"work:{course['courseId']}")

    def study_read(self, course, job, job_info):
        return self._fake_study(f"read:{course['courseId']}")


def _build(config: dict[str, Any], tiku_config: dict[str, Any]) -> DemoPlatform:
    return DemoPlatform(config)


SPEC = PlatformSpec(
    key="demo",
    name="演示平台（不联网）",
    summary="用假数据跑完整流程，验证多平台接入是否正常",
    status=STATUS_DEMO,
    capabilities=("video", "document", "work"),
    credentials=("username_password", "cookies"),
    docs="不会访问任何真实网站；账号密码随便填，用于试运行和写适配器时对照。",
    builder=_build,
)

register_platform(SPEC)

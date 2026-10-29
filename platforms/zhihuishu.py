# -*- coding: utf-8 -*-
"""智慧树 / 知到（Zhihuishu）适配器骨架.

状态：**未实现**（``STATUS_PLANNED``）。面板里会显示但不可选，避免误以为能用。

为什么只给骨架：智慧树的登录（扫码/密码）、课程列表、章节任务点、
视频进度上报与超星完全不同（走 ``studyvideoh5.zhihuishu.com`` 的
``videoStutyWT``/``loginStuInit`` 一类接口，且有 ``uuid`` 参数校验与
风控），必须按真实抓包逐项对接，凭空写只会得到一个"看起来能跑但全部失败"
的实现——那比没有更糟。

补全顺序（照着做即可，接口契约在 ``platforms/base.py``）：

1. ``login()``
   实现密码登录：``https://passport.zhihuishu.com/login``（表单/JSON），
   成功后保存 Cookie；扫码登录可后续再加。
2. ``get_course_list()``
   先拿 ``uuid``/``userId``（登录态），再拉"我的学堂"课程列表，
   映射成调度器要求的字段：``courseId``（可用 ``recruitId``）、
   ``clazzId``（``recruitId`` 或班级 id）、``cpi``（占位即可）、``title``。
3. ``get_course_point()``
   拉章节树，返回 ``{"points": [{"title", "has_finished", "knowledgeid"}, ...]}``；
   已完成的章节把 ``has_finished`` 置 True，调度器会自动跳过。
4. ``get_job_list()``
   返回 ``(jobs, job_info)``，job 至少含 ``type`` 与 ``jobid``：
   * 视频 → ``type="video"``
   * 弹题/章节测验 → ``type="workid"``
   * 文档/PPT → ``type="document"``
5. ``study_video()``
   视频是重点：需要按 ``videoid + 学习时长`` 周期上报进度
   （``POST /videoStutyWT`` 之类），并且要处理 ``watchPoint``/倍速校验。
   建议先用 ``control.sleep()`` 控制节奏，让暂停/停止可用。
6. 自检
   ``python panel.py --doctor`` 能看到平台列表；
   用 ``platforms/demo.py`` 的手法先写单元测试，再接真实网络。

需要准备的材料（这也是所有新平台通用的）：

* 该平台的**课程页、章节页、视频播放页**的 HAR 抓包（Chrome DevTools →
  Network → 右键 Save all as HAR）；
* 一个可用的测试账号（或它的 Cookie）；
* 说明要支持哪些任务类型：只看视频？还是要做章节测验/见面课？

拿到这些后，本文件的每个 ``NotImplementedError`` 就是一张待填清单。
"""
from __future__ import annotations

from typing import Any

from platforms.base import (
    STATUS_PLANNED,
    PlatformNotImplemented,
    PlatformSpec,
    UnsupportedCapability,
)
from platforms.registry import register_platform


class ZhihuishuPlatform:
    """骨架：接口齐全但全部未实现，便于逐步填充."""

    capability = "zhihuishu"

    def __init__(self, config: dict[str, Any]):
        from api.base import RateLimiter
        from api.control import RunControl

        self.config = config
        self.control = config.get("control") or RunControl()
        self.rate_limiter = RateLimiter(call_interval=1.0, control=self.control)
        self.cookies_path = config.get("cookies_path")
        self.account_id = config.get("account_id")

    # 这些方法保持"签名正确 + 明确报错"，方便补全时不会写错调用方式
    def new_session(self):
        raise PlatformNotImplemented("智慧树适配器尚未实现：new_session")

    def get_uid(self) -> str:
        raise PlatformNotImplemented("智慧树适配器尚未实现：get_uid")

    def login(self, login_with_cookies: bool = False) -> dict[str, Any]:
        raise PlatformNotImplemented("智慧树适配器尚未实现：login（见模块 docstring 第 1 步）")

    def get_course_list(self):
        raise PlatformNotImplemented("智慧树适配器尚未实现：get_course_list（第 2 步）")

    def get_course_point(self, course_id, clazz_id, cpi):
        raise PlatformNotImplemented("智慧树适配器尚未实现：get_course_point（第 3 步）")

    def get_job_list(self, course, point):
        raise PlatformNotImplemented("智慧树适配器尚未实现：get_job_list（第 4 步）")

    def study_video(self, course, job, job_info, _speed=1.0, _type="Video"):
        raise PlatformNotImplemented("智慧树适配器尚未实现：study_video（第 5 步）")

    def study_document(self, course, job):
        raise UnsupportedCapability("智慧树适配器尚未实现：study_document")

    def study_work(self, course, job, job_info):
        raise UnsupportedCapability("智慧树适配器尚未实现：study_work")

    def study_read(self, course, job, job_info):
        raise UnsupportedCapability("智慧树适配器尚未实现：study_read")


def _build(config: dict[str, Any], tiku_config: dict[str, Any]) -> ZhihuishuPlatform:
    # 即使有人绕过面板直接调用，也要给出明确错误而不是跑出半个流程
    raise PlatformNotImplemented(
        "平台「智慧树 / 知到」目前只有适配器骨架，尚未实现。"
        "请参考 docs/adding-a-platform.md 提供抓包与测试账号后补全。"
    )


SPEC = PlatformSpec(
    key="zhihuishu",
    name="智慧树 / 知到（开发中）",
    summary="适配器骨架已就位，登录与刷课逻辑待实现",
    status=STATUS_PLANNED,
    capabilities=("video",),
    credentials=("username_password", "cookies"),
    docs="需要真实抓包（课程页/章节页/视频上报）才能实现，详见 docs/adding-a-platform.md。",
    builder=_build,
)

register_platform(SPEC)

# -*- coding: utf-8 -*-
"""平台适配器：把"某个网课平台"接入统一的调度器.

调度器（``main.JobProcessor``）对平台的要求非常少，只有 9 个方法：

    login(login_with_cookies=False) -> {"status": bool, "msg": str}
    get_course_list()               -> [{"courseId", "clazzId", "cpi", "title"}, ...]
    get_course_point(courseId, clazzId, cpi) -> {"points": [{"title", "has_finished", ...}]}
    get_job_list(course, point)     -> (jobs, job_info)
    study_video(course, job, job_info, _speed, _type) -> StudyResult
    study_document(course, job)     -> StudyResult
    study_work(course, job, job_info) -> StudyResult
    study_read(course, job, job_info) -> StudyResult
    get_uid()                       -> str

另外还需要三个属性：``control``（RunControl）、``rate_limiter``（有
``limit_rate()``）、``new_session()``（返回给 worker 线程用的独立 requests 会话）。

新平台只要满足这份契约就能直接复用：多账号面板、暂停/停止、重试、日志、
无人值守、通知，全都不需要改。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# 适配器接口版本：接口不兼容变更时 +1，便于第三方适配器自检
PLATFORM_API_VERSION = 1

# 平台状态
STATUS_STABLE = "stable"            # 可用
STATUS_EXPERIMENTAL = "experimental"  # 可用但可能有坑
STATUS_DEMO = "demo"                # 演示：不访问真实平台
STATUS_PLANNED = "planned"          # 只有骨架，尚未实现


class PlatformError(RuntimeError):
    """平台适配器抛出的可读错误（会被面板直接展示给用户）."""


class PlatformNotImplemented(PlatformError):
    """该平台只有骨架，尚未实现."""


class UnsupportedCapability(PlatformError):
    """平台不支持某个任务类型（例如只有视频、没有章节测验）."""


@dataclass(frozen=True)
class PlatformSpec:
    """一个平台的元信息 + 构造函数."""

    key: str                      # 唯一标识，会写进账号配置，不要随意改名
    name: str                     # 展示名，例如 "智慧树 / 知到"
    summary: str = ""             # 一句话说明
    status: str = STATUS_STABLE
    capabilities: tuple[str, ...] = ()   # 支持的任务类型：video/document/work/read/live
    credentials: tuple[str, ...] = ("username_password", "cookies")
    docs: str = ""                # 补充说明（展示在面板提示里）
    builder: Optional[Callable[[dict[str, Any], dict[str, Any]], Any]] = field(
        default=None, compare=False, repr=False
    )

    @property
    def available(self) -> bool:
        """是否可以在面板里选择（planned 状态只是占位，避免误选后一脸懵）."""
        return self.status != STATUS_PLANNED and self.builder is not None

    def public(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "summary": self.summary,
            "status": self.status,
            "available": self.available,
            "capabilities": list(self.capabilities),
            "credentials": list(self.credentials),
            "docs": self.docs,
            "api_version": PLATFORM_API_VERSION,
        }


# 调度器要求的契约（用于自检与文档，不做运行时强校验以免限制灵活性）
REQUIRED_METHODS = (
    "login",
    "get_course_list",
    "get_course_point",
    "get_job_list",
    "study_video",
    "study_document",
    "study_work",
    "study_read",
    "get_uid",
    "new_session",
)
REQUIRED_ATTRIBUTES = ("control", "rate_limiter")


def validate_adapter(adapter: Any) -> list[str]:
    """返回缺失的契约项（空列表表示合格），用于面板/命令行自检."""
    missing = [name for name in REQUIRED_METHODS if not callable(getattr(adapter, name, None))]
    missing += [name for name in REQUIRED_ATTRIBUTES if not hasattr(adapter, name)]
    return missing

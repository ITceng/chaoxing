# -*- coding: utf-8 -*-
"""超星学习通 / 泛雅：现有实现的适配器（薄封装，不改变原有行为）."""
from __future__ import annotations

from typing import Any

from platforms.base import STATUS_STABLE, PlatformSpec
from platforms.registry import register_platform


def _build(config: dict[str, Any], tiku_config: dict[str, Any]) -> Any:
    # 延迟导入：main 会导入 api.*，避免与 platforms 包形成循环导入
    from main import init_chaoxing

    return init_chaoxing(config, tiku_config, config_path=None)


SPEC = PlatformSpec(
    key="chaoxing",
    name="超星学习通 / 泛雅",
    summary="支持视频、文档、章节测验、阅读、直播任务点",
    status=STATUS_STABLE,
    capabilities=("video", "audio", "document", "work", "read", "live"),
    credentials=("username_password", "cookies"),
    docs="最完整的实现：题库、字体解密、直播、章节学习次数都可用的平台。",
    builder=_build,
)

register_platform(SPEC)

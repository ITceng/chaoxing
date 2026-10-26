# -*- coding: utf-8 -*-
"""运行控制：跨线程的暂停 / 继续 / 停止信号.

该模块不依赖项目内其他模块，避免循环导入。所有耗时操作（请求、重试等待、
视频进度循环）都应通过 ``RunControl.sleep`` / ``checkpoint`` 感知外部控制，
从而让面板上的"暂停/停止"在一个可控的时间窗口内生效。
"""
from __future__ import annotations

import threading
import time


class TaskCancelled(Exception):
    """任务被用户停止 / 取消."""


class RunControl:
    """线程安全的运行控制器.

    - ``pause()``：阻塞所有 checkpoint，直到 ``resume()`` 或 ``stop()``
    - ``stop()``：永久停止，checkpoint 抛出 :class:`TaskCancelled`
    - ``sleep(seconds)``：可被暂停/停止打断的等待
    """

    def __init__(self) -> None:
        self._resume_event = threading.Event()
        self._resume_event.set()
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._paused = False
        self._pause_reason = ""

    # ------------------------------------------------------------------ state
    @property
    def stopped(self) -> bool:
        return self._stop_event.is_set()

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused and not self.stopped

    @property
    def pause_reason(self) -> str:
        with self._lock:
            return self._pause_reason

    def pause(self, reason: str = "user") -> None:
        with self._lock:
            if self._stop_event.is_set():
                return
            self._paused = True
            self._pause_reason = reason
            self._resume_event.clear()

    def resume(self) -> None:
        with self._lock:
            self._paused = False
            self._pause_reason = ""
            self._resume_event.set()

    def stop(self) -> None:
        with self._lock:
            self._paused = False
            self._stop_event.set()
            self._resume_event.set()

    # -------------------------------------------------------------- blocking
    def checkpoint(self) -> None:
        """在安全点检查状态；暂停时阻塞，停止时抛异常."""
        if self._stop_event.is_set():
            raise TaskCancelled("stopped")
        while not self._resume_event.is_set():
            if self._stop_event.is_set():
                raise TaskCancelled("stopped")
            # 0.2s 轮询，保证 stop() 能及时生效
            self._resume_event.wait(0.2)
        if self._stop_event.is_set():
            raise TaskCancelled("stopped")

    def sleep(self, seconds: float) -> None:
        """可中断的 sleep：暂停期间不计入等待时间."""
        if seconds is None or seconds <= 0:
            self.checkpoint()
            return
        deadline = time.monotonic() + float(seconds)
        while True:
            self.checkpoint()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            # 用 stop event 等待，stop() 会立即唤醒；其余时间分片轮询，
            # 保证暂停发生在等待期间时也能及时停住。
            self._stop_event.wait(min(remaining, 0.2))
            if self._stop_event.is_set():
                raise TaskCancelled("stopped")
            if self.paused:
                # 暂停可能发生在本次 sleep 期间：暂停结束后重新计算剩余时间。
                self.checkpoint()
                deadline = time.monotonic() + max(0.0, remaining)

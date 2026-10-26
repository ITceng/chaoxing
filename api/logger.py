# -*- coding: utf-8 -*-
"""日志配置：控制台、全局文件、按账号隔离的文件与内存缓冲.

设计目标：
1. CLI 行为保持不变（stderr + chaoxing.log）；
2. Web 面板按账号输出日志，互不混杂；
3. 对 Cookie / Authorization / token / 通知 URL 等敏感信息做统一脱敏；
4. 面板通过 ``get_account_logs`` 读取增量日志，不需要读取文件。
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
from collections import defaultdict, deque
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from loguru import logger
from tqdm import tqdm

tqdm_stream = sys.stderr

# 日志缓冲区，用于在手动答题时缓存后台日志，答题结束后统一输出
log_buffer = []
MAX_LOG_BUFFER_SIZE = 1000

DATA_DIR = Path(os.environ.get("CHAOXING_DATA_DIR", "data")).expanduser()
LOG_DIR = Path(os.environ.get("CHAOXING_LOG_DIR", str(DATA_DIR / "logs"))).expanduser()
ACCOUNT_LOG_MAX_BYTES = int(os.environ.get("CHAOXING_ACCOUNT_LOG_MAX_BYTES", str(5 * 1024 * 1024)))
ACCOUNT_BUFFER_SIZE = int(os.environ.get("CHAOXING_ACCOUNT_LOG_BUFFER", "2000"))

_account_id_var: ContextVar[str | None] = ContextVar("chaoxing_account_id", default=None)
_buffer_lock = threading.RLock()
_account_buffers: dict[str, deque[dict[str, Any]]] = defaultdict(
    lambda: deque(maxlen=ACCOUNT_BUFFER_SIZE)
)
_account_seq: dict[str, int] = defaultdict(int)
_file_lock = threading.RLock()


def set_account_context(account_id: str | None):
    """绑定当前线程/上下文的账号 ID，返回 ContextVar token."""
    return _account_id_var.set(account_id)


def reset_account_context(token) -> None:
    try:
        _account_id_var.reset(token)
    except Exception:
        pass


def get_account_context() -> str | None:
    return _account_id_var.get()


# --------------------------------------------------------------------- redact
_REDACT_PATTERNS = [
    # Cookie: xxx / Authorization: Bearer xxx / token=xxx / password=xxx
    (re.compile(r"(?i)\b(cookie|authorization|proxy-authorization|api[_-]?token|access[_-]?token|token|password|passwd|secret|sendkey|tokens)\b\s*[:=]\s*[^\s,;]+"),
     r"\1=***"),
    # 通知/题库 URL 中常见的内嵌 key（Server酱、Bark、Telegram bot、各种 ?key=）
    (re.compile(r"https?://[^\s\"'<>]*(?:sendkey|send\.|/send|/bot|bark|access_token|api[_-]?key|token)[^\s\"'<>]*", re.I),
     "<redacted-url>"),
    # Cookie 串的兜底（防止有人直接拼接 cookie 文本）
    (re.compile(r"(?i)\b(?:UID|_uid|fid|uf|_d|vc3|route)=[^;\s]+(?:;\s*(?:UID|_uid|fid|uf|_d|vc3|route)=[^;\s]+)+"),
     "<redacted-cookie>"),
]


def redact_text(text: str) -> str:
    if not text:
        return text
    result = str(text)
    for pattern, replacement in _REDACT_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def _patcher(record: dict) -> None:
    account_id = _account_id_var.get()
    if account_id:
        record["extra"].setdefault("account_id", account_id)
    record["message"] = redact_text(record["message"])


# ---------------------------------------------------------------------- sinks
def tqdm_sink(msg):
    manual_locked = False
    try:
        # 动态获取 api.answer 模块中的 TikuManual 锁，避免循环导入
        if "api.answer" in sys.modules:
            TikuManual = getattr(sys.modules["api.answer"], "TikuManual", None)
            if TikuManual and getattr(TikuManual, "_manual_lock", None):
                manual_locked = TikuManual._manual_lock.locked()
    except (AttributeError, KeyError, ImportError):
        pass

    if manual_locked:
        if len(log_buffer) < MAX_LOG_BUFFER_SIZE:
            log_buffer.append(msg)
    else:
        if log_buffer:
            for buffered_msg in log_buffer:
                tqdm.write(buffered_msg.rstrip(), file=tqdm_stream)
            log_buffer.clear()
        tqdm.write(msg.rstrip(), file=tqdm_stream)
    tqdm_stream.flush()


def _format_line(record: dict) -> str:
    ts = record["time"].strftime("%Y-%m-%d %H:%M:%S")
    level = record["level"].name
    name = record["name"]
    func = record["function"]
    line = record["line"]
    message = record["message"]
    return f"{ts} | {level:<8} | {name}:{func}:{line} - {message}"


def _account_log_path(account_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(account_id))[:64]
    return LOG_DIR / f"{safe_id}.log"


def _rotate_if_needed(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size >= ACCOUNT_LOG_MAX_BYTES:
            backup = path.with_suffix(path.suffix + f".{int(time.time())}")
            path.replace(backup)
    except OSError:
        pass


def _append_file(account_id: str, line: str) -> None:
    path = _account_log_path(account_id)
    with _file_lock:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            _rotate_if_needed(path)
            is_new = not path.exists()
            with path.open("a", encoding="utf-8") as fp:
                fp.write(line + "\n")
            if is_new:
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
        except OSError:
            # 日志失败不应影响主流程
            pass


def _account_sink(message) -> None:
    record = message.record
    account_id = record["extra"].get("account_id")
    if not account_id:
        return
    account_id = str(account_id)
    line = _format_line(record)
    with _buffer_lock:
        _account_seq[account_id] += 1
        _account_buffers[account_id].append(
            {
                "seq": _account_seq[account_id],
                "time": record["time"].strftime("%Y-%m-%d %H:%M:%S"),
                "level": record["level"].name,
                "message": record["message"],
                "name": record["name"],
            }
        )
    _append_file(account_id, line)


def get_account_logs(account_id: str, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
    """返回增量日志（seq > after），最多 limit 条."""
    with _buffer_lock:
        items = list(_account_buffers.get(str(account_id), ()))
    result = [item for item in items if item["seq"] > int(after or 0)]
    if limit and len(result) > limit:
        result = result[-int(limit):]
    return result


def get_account_log_file(account_id: str) -> Path:
    return _account_log_path(account_id)


def clear_account_logs(account_id: str) -> None:
    with _buffer_lock:
        _account_buffers.pop(str(account_id), None)
        _account_seq.pop(str(account_id), None)
    try:
        path = _account_log_path(account_id)
        if path.exists():
            path.unlink()
    except OSError:
        pass


logger.remove()
logger.configure(patcher=_patcher)
logger.add(tqdm_sink, colorize=True, enqueue=True)
# 全局文件只记录非账号日志（CLI / 面板启动），账号日志写独立文件
logger.add(
    "chaoxing.log",
    rotation="10 MB",
    level="TRACE",
    filter=lambda r: not r["extra"].get("account_id"),
)
logger.add(_account_sink, level="DEBUG", enqueue=True)

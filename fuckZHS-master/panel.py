#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智慧树刷课 Web 面板。

运行:
    python panel.py
    python panel.py --host 0.0.0.0 --port 5000

默认只监听 127.0.0.1，避免把账号会话暴露到局域网。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import sys
import threading
import time
import traceback
from collections import deque
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from requests.utils import cookiejar_from_dict

import zhs_auth
from fucker import Fucker, TimeLimitExceeded
from logger import logger
from utils import getConfigPath, getRealPath, list_to_cookie_jar

BASE_DIR = Path(__file__).resolve().parent

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "web" / "templates"),
    static_folder=str(BASE_DIR / "web" / "static"),
)
app.config["JSON_AS_ASCII"] = False
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# 面板里不输出过于频繁的 DEBUG 日志
logger.setLevel("INFO")


# ---------------------------------------------------------------------------
# 状态与日志
# ---------------------------------------------------------------------------
class PanelState:
    def __init__(self):
        self.lock = threading.RLock()
        self.cookies = None
        self.logged_in = False
        self.account = ""
        self.login_method = ""
        self.task_thread: threading.Thread | None = None
        self.task_running = False
        self.stop_event = threading.Event()
        self.capture_thread_id: int | None = None
        self.logs: deque[dict] = deque(maxlen=5000)
        self.log_counter = 0
        self.qr_sessions: dict[str, dict] = {}
        self.courses: list[dict] = []
        self.progress = {
            "running": False,
            "event": "",
            "message": "等待任务",
            "course_index": 0,
            "course_total": 0,
            "course_id": "",
            "course_name": "",
            "course_type": "",
            "video_index": 0,
            "video_total": 0,
            "video_id": "",
            "video_name": "",
            "played": 0.0,
            "duration": 0.0,
            "video_percent": 0.0,
            "overall_percent": 0.0,
            "started_at": None,
            "updated_at": None,
        }

    def reset_progress(self):
        with self.lock:
            self.progress.update({
                "running": False,
                "event": "",
                "message": "等待任务",
                "course_index": 0,
                "course_total": 0,
                "course_id": "",
                "course_name": "",
                "course_type": "",
                "video_index": 0,
                "video_total": 0,
                "video_id": "",
                "video_name": "",
                "played": 0.0,
                "duration": 0.0,
                "video_percent": 0.0,
                "overall_percent": 0.0,
                "started_at": None,
                "updated_at": None,
            })

    def update_progress(self, data: dict):
        """接收 Fucker 的进度事件，计算课程/视频/总进度。"""
        event = data.get("event", "")
        with self.lock:
            p = self.progress

            if event == "task_start":
                p.update({
                    "running": True,
                    "event": event,
                    "message": "任务运行中",
                    "course_index": 0,
                    "course_total": int(data.get("course_total") or 0),
                    "course_id": "",
                    "course_name": "",
                    "course_type": "",
                    "video_index": 0,
                    "video_total": 0,
                    "video_id": "",
                    "video_name": "",
                    "played": 0.0,
                    "duration": 0.0,
                    "video_percent": 0.0,
                    "overall_percent": 0.0,
                    "started_at": time.time(),
                    "updated_at": time.time(),
                })
                return

            if event == "task_done":
                p["running"] = False
                p["event"] = event
                p["message"] = data.get("message") or "任务结束"
                if data.get("completed", True):
                    p["overall_percent"] = 100.0
                    p["video_percent"] = 100.0
                p["updated_at"] = time.time()
                return

            if event == "course_start":
                if data.get("course_index") is not None:
                    p["course_index"] = int(data["course_index"])
                if data.get("course_total") is not None:
                    p["course_total"] = int(data["course_total"])
                p["course_id"] = str(data.get("course_id") or p["course_id"])
                if data.get("course_name"):
                    p["course_name"] = data["course_name"]
                p["course_type"] = data.get("course_type") or p["course_type"]
                p["video_index"] = 0
                p["video_total"] = int(data.get("video_total") or 0)
                p["video_id"] = ""
                p["video_name"] = ""
                p["played"] = 0.0
                p["duration"] = 0.0
                p["video_percent"] = 0.0
                p["message"] = "正在刷课程"
            elif event == "video_start":
                if data.get("video_index") is not None:
                    p["video_index"] = int(data["video_index"])
                if data.get("video_total") is not None:
                    p["video_total"] = int(data["video_total"])
                p["video_id"] = str(data.get("video_id") or "")
                p["video_name"] = data.get("video_name") or p["video_id"]
                p["played"] = 0.0
                p["duration"] = 0.0
                p["video_percent"] = 0.0
                p["message"] = "正在观看视频"
            elif event == "video_progress":
                p["video_id"] = str(data.get("video_id") or p["video_id"])
                if data.get("video_name"):
                    p["video_name"] = data["video_name"]
                p["played"] = float(data.get("played") or 0)
                p["duration"] = float(data.get("duration") or 0)
                if p["duration"] > 0:
                    p["video_percent"] = max(
                        0.0, min(100.0, p["played"] / p["duration"] * 100.0))
                elif data.get("percent") is not None:
                    p["video_percent"] = max(
                        0.0, min(100.0, float(data["percent"])))
                p["message"] = "正在观看视频"
            elif event == "video_done":
                if data.get("video_index") is not None:
                    p["video_index"] = int(data["video_index"])
                if data.get("video_total") is not None:
                    p["video_total"] = int(data["video_total"])
                p["video_percent"] = 100.0
                p["message"] = "视频完成"
            elif event == "course_done":
                p["video_percent"] = 100.0
                p["message"] = "课程完成"

            p["event"] = event
            p["updated_at"] = time.time()

            course_total = max(0, p["course_total"])
            course_index = max(0, p["course_index"])
            video_total = max(0, p["video_total"])
            video_index = max(0, p["video_index"])
            if video_total > 0:
                course_fraction = ((video_index - 1) + p["video_percent"] / 100.0) / video_total
            else:
                course_fraction = 0.0
            course_fraction = max(0.0, min(1.0, course_fraction))
            if course_total > 0 and course_index > 0:
                overall = ((course_index - 1) + course_fraction) / course_total * 100.0
            else:
                overall = course_fraction * 100.0
            if event == "course_done" and course_total > 0 and course_index > 0:
                overall = max(overall, course_index / course_total * 100.0)
            p["overall_percent"] = round(max(0.0, min(100.0, overall)), 1)

    def add_log(self, text, level: str = "INFO"):
        text = str(text).replace("\r", "")
        if not text.strip():
            return
        with self.lock:
            for line in text.splitlines():
                if not line.strip():
                    continue
                self.log_counter += 1
                self.logs.append({
                    "id": self.log_counter,
                    "time": time.strftime("%H:%M:%S"),
                    "level": level,
                    "text": line,
                })


state = PanelState()


class PanelLogHandler(logging.Handler):
    def emit(self, record):
        try:
            state.add_log(self.format(record), record.levelname)
        except Exception:
            pass


_log_handler = PanelLogHandler()
_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
logger.addHandler(_log_handler)


class ThreadAwareStream:
    """只捕获任务线程的输出，不影响 Flask/Werkzeug 自己的日志。"""

    encoding = "utf-8"

    def __init__(self, original):
        self.original = original

    def write(self, s):
        if state.capture_thread_id is not None and threading.get_ident() == state.capture_thread_id:
            # 跳过用 \r 原地刷新的进度条，避免日志被刷屏
            if "\r" in s and "\n" not in s:
                return len(s)
            state.add_log(s)
        else:
            self.original.write(s)
        return len(s)

    def flush(self):
        self.original.flush()

    def isatty(self):
        return False


_ORIGINAL_STDOUT = sys.stdout
_ORIGINAL_STDERR = sys.stderr
sys.stdout = ThreadAwareStream(_ORIGINAL_STDOUT)
sys.stderr = ThreadAwareStream(_ORIGINAL_STDERR)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
def load_config() -> dict:
    try:
        with open(getConfigPath(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg: dict):
    with open(getConfigPath(), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)


def public_config() -> dict:
    """只返回面板前端需要的非敏感配置，避免泄露密码 / API Key / 推送 token。"""
    cfg = load_config()
    return {
        "username": cfg.get("username", ""),
        "speed": cfg.get("speed"),
        "threshold": cfg.get("threshold"),
        "limit": cfg.get("limit", 0),
        "tree_view": cfg.get("tree_view", True),
        "progressbar_view": cfg.get("progressbar_view", False),
    }


def make_fucker(cookies=None, **options) -> Fucker:
    cfg = load_config()
    push_cfg = cfg.get("pushplus") or {}
    bark_cfg = cfg.get("bark") or {}
    speed = options.get("speed")
    if speed in ("", 0, "0"):
        speed = None
    threshold = options.get("threshold")
    if threshold in ("", None):
        threshold = None
    return Fucker(
        cookies=cookies if cookies is not None else state.cookies,
        proxies=cfg.get("proxies") or {},
        limit=int(options.get("limit") or 0),
        speed=float(speed) if speed not in (None, "") else None,
        end_thre=float(threshold) if threshold not in (None, "") else None,
        pushplus_token=push_cfg.get("token", "") if push_cfg.get("enable") else "",
        bark_token=bark_cfg.get("token", "") if bark_cfg.get("enable") else "",
        tree_view=bool(options.get("tree_view", True)),
        progressbar_view=bool(options.get("progressbar_view", False)),
        image_path=cfg.get("image_path", "") or "",
    )


def set_logged_in(cookies, account: str = "", method: str = ""):
    with state.lock:
        state.cookies = cookies
        state.logged_in = cookies is not None
        state.account = account
        state.login_method = method
    if cookies is not None:
        state.add_log(f"登录成功（{method or 'cookies'}）{account}")


def restore_saved_cookies():
    """尝试恢复项目根目录下的 cookies.json。"""
    path = getRealPath("./cookies.json")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        jar = list_to_cookie_jar(raw) if isinstance(raw, list) else cookiejar_from_dict(raw)
        # 用 Fucker 的 setter 校验 CASLOGC
        Fucker(cookies=jar)
        set_logged_in(jar, account="已保存的 Cookie", method="cookies")
    except Exception as e:
        state.add_log(f"恢复 cookies.json 失败：{e}", "WARNING")


def cookie_jar_from_input(raw: str):
    parsed = zhs_auth.parse_cookie_input(raw)
    if isinstance(parsed, list):
        jar = list_to_cookie_jar(parsed)
    elif isinstance(parsed, dict):
        jar = cookiejar_from_dict(parsed)
    else:
        raise ValueError("Cookie 必须是 JSON 数组、JSON 对象或 k=v; k2=v2 字符串")
    if not any(c.name == "CASLOGC" for c in jar):
        raise ValueError("Cookie 中缺少 CASLOGC，无法用于智慧树登录")
    return jar


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------
@app.post("/api/login/password")
def api_login_password():
    data = request.get_json(force=True, silent=True) or {}
    account = (data.get("account") or "").strip()
    password = data.get("password") or ""
    validate = data.get("validate") or ""
    if not account or not password:
        return jsonify(ok=False, status=-1, message="账号或密码不能为空"), 400

    try:
        result = zhs_auth.account_login(
            account, password, validate=validate,
            proxies=load_config().get("proxies") or {},
        )
    except zhs_auth.LoginError as e:
        status = 400 if e.need_captcha else 401
        return jsonify(
            ok=False,
            status=e.status,
            message=e.message,
            need_captcha=e.need_captcha,
        ), status
    except Exception as e:
        logger.exception(e)
        return jsonify(ok=False, status=None, message=f"登录异常：{e}"), 500

    set_logged_in(result["cookies"], account=account, method="password")
    return jsonify(ok=True, message="登录成功", account=account)


@app.post("/api/login/qr/start")
def api_qr_start():
    try:
        result = zhs_auth.qr_start(proxies=load_config().get("proxies") or {})
    except Exception as e:
        logger.exception(e)
        return jsonify(ok=False, message=f"获取二维码失败：{e}"), 500

    sid = secrets.token_urlsafe(18)
    with state.lock:
        # 顺手清理超过 5 分钟的会话
        now = time.time()
        for old_sid, item in list(state.qr_sessions.items()):
            if now - item["created"] > 300:
                state.qr_sessions.pop(old_sid, None)
        state.qr_sessions[sid] = {
            "session": result["session"],
            "qrToken": result["qrToken"],
            "created": now,
        }
    return jsonify(
        ok=True,
        sid=sid,
        image="data:image/png;base64," + result["img"],
        message="请使用智慧树 App 扫码",
    )


@app.get("/api/login/qr/poll")
def api_qr_poll():
    sid = request.args.get("sid", "")
    with state.lock:
        item = state.qr_sessions.get(sid)
    if not item:
        return jsonify(ok=False, status=None, message="二维码会话不存在或已过期"), 404

    try:
        result = zhs_auth.qr_poll(item["session"], item["qrToken"])
    except Exception as e:
        logger.exception(e)
        with state.lock:
            state.qr_sessions.pop(sid, None)
        return jsonify(ok=False, status=None, message=f"二维码状态查询失败：{e}"), 500

    status = result.get("status")
    if status == 1:
        with state.lock:
            state.qr_sessions.pop(sid, None)
        set_logged_in(result["cookies"], account="扫码登录", method="qrcode")
        return jsonify(ok=True, status=status, message="登录成功", logged_in=True)
    if status in (2, 3):
        with state.lock:
            state.qr_sessions.pop(sid, None)
    return jsonify(ok=True, status=status, message=result.get("message"), logged_in=False)


@app.post("/api/login/cookies")
def api_login_cookies():
    data = request.get_json(force=True, silent=True) or {}
    raw = data.get("cookies") or data.get("raw") or ""
    try:
        jar = cookie_jar_from_input(raw)
        # 校验 setter 能解析 CASLOGC 中的 uuid
        Fucker(cookies=jar)
    except Exception as e:
        return jsonify(ok=False, message=f"Cookie 无效：{e}"), 400
    set_logged_in(jar, account="Cookie 导入", method="cookies")
    return jsonify(ok=True, message="Cookie 导入成功")


@app.post("/api/logout")
def api_logout():
    with state.lock:
        state.cookies = None
        state.logged_in = False
        state.account = ""
        state.login_method = ""
        state.courses = []
        if state.task_running:
            state.stop_event.set()
        state.reset_progress()
    state.add_log("已退出登录")
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# 状态 / 配置
# ---------------------------------------------------------------------------
@app.get("/api/status")
def api_status():
    with state.lock:
        return jsonify(
            ok=True,
            logged_in=state.logged_in,
            account=state.account,
            login_method=state.login_method,
            task_running=state.task_running,
            stop_requested=state.stop_event.is_set(),
            config=public_config(),
        )


@app.get("/api/config")
def api_get_config():
    return jsonify(ok=True, config=public_config())


@app.post("/api/config")
def api_set_config():
    data = request.get_json(force=True, silent=True) or {}
    cfg = load_config()
    for key in ("speed", "threshold", "limit", "tree_view", "progressbar_view", "username"):
        if key in data:
            cfg[key] = data[key]
    save_config(cfg)
    return jsonify(ok=True, message="设置已保存")


# ---------------------------------------------------------------------------
# 课程
# ---------------------------------------------------------------------------
@app.get("/api/courses")
def api_courses():
    if not state.cookies:
        return jsonify(ok=False, message="请先登录"), 401
    try:
        fucker = make_fucker()
        zhidao = fucker.getZhidaoList() or []
        hike = fucker.getHikeList() or []
    except Exception as e:
        logger.exception(e)
        return jsonify(ok=False, message=f"获取课程失败：{e}"), 401

    courses = []
    for c in zhidao:
        courses.append({
            "id": str(c.secret),
            "name": c.courseName,
            "type": "zhidao",
        })
    for c in hike:
        courses.append({
            "id": str(c.courseId),
            "name": c.courseName,
            "type": "hike",
        })
    with state.lock:
        state.courses = courses
    return jsonify(ok=True, courses=courses)


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------
def _run_task(courses: list[str], videos: list, options: dict, fuck_all: bool):
    state.capture_thread_id = threading.get_ident()
    state.add_log("=" * 60)
    state.add_log("任务开始")
    state.update_progress({
        "event": "task_start",
        "course_total": 0 if fuck_all else len(courses),
    })
    aborted = False
    try:
        fucker = make_fucker(**options)
        fucker.progress_callback = state.update_progress
        original_check = fucker._checkTimeLimit

        def check_time_limit(cid):
            if state.stop_event.is_set():
                raise TimeLimitExceeded("用户停止任务")
            return original_check(cid)

        fucker._checkTimeLimit = check_time_limit

        if fuck_all:
            fucker.fuckWhatever()
        else:
            course_names = {str(c.get("id")): c.get("name") for c in state.courses}
            total_courses = len(courses)
            for index, course_id in enumerate(courses, 1):
                if state.stop_event.is_set():
                    break
                state.update_progress({
                    "event": "course_start",
                    "course_id": str(course_id),
                    "course_name": course_names.get(str(course_id), str(course_id)),
                    "course_index": index,
                    "course_total": total_courses,
                })
                state.add_log(f"开始课程：{course_id}")
                try:
                    fucker.fuckCourse(str(course_id))
                except Exception as e:
                    logger.exception(e)
                    state.add_log(f"课程 {course_id} 执行失败：{e}", "ERROR")

            total_videos = len(videos)
            if total_videos:
                state.update_progress({
                    "event": "course_start",
                    "course_name": "指定视频",
                    "course_index": 1,
                    "course_total": 1,
                    "video_total": total_videos,
                })
            for index, item in enumerate(videos, 1):
                if state.stop_event.is_set():
                    break
                course_id = ""
                video_id = ""
                try:
                    if isinstance(item, dict):
                        course_id = str(item.get("course") or item.get("course_id"))
                        video_id = str(item.get("video") or item.get("video_id"))
                    else:
                        course_id, video_id = str(item).split(":", 1)
                    state.update_progress({
                        "event": "video_start",
                        "course_id": course_id,
                        "video_id": video_id,
                        "video_index": index,
                        "video_total": total_videos,
                    })
                    state.add_log(f"开始视频：{course_id} / {video_id}")
                    fucker.fuckVideo(course_id, video_id)
                except Exception as e:
                    logger.exception(e)
                    state.add_log(f"视频任务执行失败：{e}", "ERROR")
                finally:
                    if course_id and video_id:
                        state.update_progress({
                            "event": "video_done",
                            "course_id": course_id,
                            "video_id": video_id,
                            "video_index": index,
                            "video_total": total_videos,
                            "percent": 100,
                        })
    except Exception as e:
        aborted = True
        logger.exception(e)
        state.add_log(traceback.format_exc(), "ERROR")
    finally:
        state.capture_thread_id = None
        with state.lock:
            state.task_running = False
            state.task_thread = None
        stopped = state.stop_event.is_set()
        completed = not stopped and not aborted
        message = "任务已停止" if stopped else ("任务异常结束" if aborted else "任务完成")
        state.update_progress({
            "event": "task_done",
            "completed": completed,
            "message": message,
        })
        state.add_log(message)
        state.add_log("=" * 60)


@app.post("/api/task/start")
def api_task_start():
    if not state.cookies:
        return jsonify(ok=False, message="请先登录"), 401
    with state.lock:
        if state.task_running:
            return jsonify(ok=False, message="已有任务在运行"), 409

    data = request.get_json(force=True, silent=True) or {}
    fuck_all = bool(data.get("all"))
    courses = [str(x) for x in (data.get("courses") or [])]
    videos = data.get("videos") or []
    if not fuck_all and not courses and not videos:
        return jsonify(ok=False, message="请选择课程或填写视频"), 400

    options = {
        "speed": data.get("speed"),
        "threshold": data.get("threshold"),
        "limit": data.get("limit"),
        "tree_view": data.get("tree_view", True),
        "progressbar_view": data.get("progressbar_view", False),
    }

    with state.lock:
        state.stop_event.clear()
        state.task_running = True
        thread = threading.Thread(
            target=_run_task,
            args=(courses, videos, options, fuck_all),
            daemon=True,
            name="zhs-task",
        )
        state.task_thread = thread
    thread.start()
    return jsonify(ok=True, message="任务已启动")


@app.post("/api/task/stop")
def api_task_stop():
    if not state.task_running:
        return jsonify(ok=False, message="当前没有运行中的任务"), 400
    state.stop_event.set()
    state.add_log("收到停止请求，将在当前视频结束后停止", "WARNING")
    return jsonify(ok=True, message="已发送停止请求")


@app.get("/api/task/status")
def api_task_status():
    with state.lock:
        return jsonify(
            ok=True,
            running=state.task_running,
            stop_requested=state.stop_event.is_set(),
        )


@app.get("/api/task/progress")
def api_task_progress():
    with state.lock:
        return jsonify(ok=True, progress=dict(state.progress))


@app.get("/api/logs")
def api_logs():
    try:
        since = int(request.args.get("since", 0) or 0)
    except ValueError:
        since = 0
    with state.lock:
        items = [item for item in state.logs if item["id"] > since]
    last = items[-1]["id"] if items else since
    return jsonify(ok=True, logs=items, last=last)


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="ZHS Fucker Web Panel")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=5000, help="端口，默认 5000")
    args = parser.parse_args()

    restore_saved_cookies()
    state.add_log("面板已启动")
    print(f"* ZHS Fucker Panel: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
新版智慧树登录工具。

- 账号密码登录：AES-CBC 加密请求体，调用 login.zhihuishu.com 背后实际使用的
  ``user.zhihuishu.com/zhsuser/login/account`` 接口。
- 二维码登录：沿用 passport.zhihuishu.com 的二维码接口。
- Cookie 解析：支持 cookies.json 数组、字典，以及 ``k=v; k2=v2`` 字符串。

账号密码登录目前强制要求滑动验证码，``validate`` 需要通过面板中的
AbleCaptcha 完成验证后获得，或在浏览器里自行取得后传入。
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any, Optional
from urllib.parse import quote

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

SERVICE = "https://onlineservice-api.zhihuishu.com/login/gologin"
LOGIN_PAGE = "https://passport.zhihuishu.com/login"
NEW_LOGIN_PAGE = "https://login.zhihuishu.com/"
ACCOUNT_API = "https://user.zhihuishu.com/zhsuser/login/account"
QR_IMG_API = "https://passport.zhihuishu.com/qrCodeLogin/getLoginQrImg"
QR_INFO_API = "https://passport.zhihuishu.com/qrCodeLogin/getLoginQrInfo"

# 从 login.zhihuishu.com 前端 bundle 中提取
AES_KEY = b"bdas85990328dfpl"
AES_IV = b"563216db1e1744c7"

DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

QR_STATUS = {
    -1: "等待扫码",
    0: "已扫码，请在手机上确认",
    1: "登录成功",
    2: "二维码已过期",
    3: "已取消登录",
}

LOGIN_STATUS = {
    1: "登录成功",
    2: "需要选择登录账号，请使用官方页面完成登录",
    -1: "账号、密码或参数错误",
    -2: "账号或密码错误",
    -3: "账号被禁用",
    -4: "滑动验证码校验失败，请重新验证",
    -5: "密码过于简单，需要先修改密码",
    -6: "密码错误次数过多",
    -7: "账号未绑定手机号",
    -8: "该账号不允许使用邮箱登录",
    -9: "密码错误次数过多，登录受限",
    -10: "验证码错误次数过多",
    -11: "异地登录或黑名单限制",
    -12: "需要完成安全验证",
    -13: "异地登录，需要完成安全验证",
    -16: "需要完成安全验证",
    -100: "账号已被禁用",
}


class LoginError(Exception):
    """登录失败。``status`` 为智慧树接口返回的状态码。"""

    def __init__(self, status: int | str | None, message: str, data: dict | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.data = data or {}

    @property
    def need_captcha(self) -> bool:
        return self.status == -4


def new_session(proxies: Optional[dict] = None) -> requests.Session:
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    if proxies:
        session.proxies.update(proxies)
    return session


def encrypt_payload(payload: dict) -> str:
    """按官方前端逻辑加密登录请求体。"""
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.b64encode(cipher.encrypt(pad(raw, AES.block_size))).decode("ascii")


def _has_cas_logc(session: requests.Session) -> bool:
    return any(cookie.name == "CASLOGC" for cookie in session.cookies)


def _ensure_cas_cookies(session: requests.Session) -> requests.cookies.RequestsCookieJar:
    if not _has_cas_logc(session):
        raise LoginError(
            None,
            "登录请求已通过，但没有拿到 CASLOGC cookie，请检查 service 参数或稍后重试",
        )
    return session.cookies.copy()


def account_login(
    account: str,
    password: str,
    validate: str = "",
    session: Optional[requests.Session] = None,
    service: str = SERVICE,
    timeout: int = 15,
    proxies: Optional[dict] = None,
) -> dict[str, Any]:
    """
    使用账号密码登录。

    :param validate: 滑动验证码校验通过后得到的 validate token
    :return: ``{"status", "message", "cookies", "data", "account"}``
    :raises LoginError: 账号密码错误、需要验证码、风控等
    """
    account = (account or "").strip()
    password = password or ""
    validate = validate or ""
    if not account or not password:
        raise LoginError(-1, "账号或密码不能为空")

    session = session or new_session(proxies)
    if proxies:
        session.proxies.update(proxies)

    # 建立 login.zhihuishu.com 的会话（与官方前端一致）
    session.get(
        NEW_LOGIN_PAGE,
        params={"origin": "zhs", "service": service},
        timeout=timeout,
    )

    payload = {"account": account, "password": password, "validate": validate}
    response = session.post(
        ACCOUNT_API,
        data=encrypt_payload(payload),
        headers={
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": "https://login.zhihuishu.com",
            "Referer": "https://login.zhihuishu.com/",
        },
        timeout=timeout,
    )
    try:
        data = response.json()
    except ValueError:
        raise LoginError(None, f"登录接口返回了非 JSON 内容（HTTP {response.status_code}）")

    status = data.get("status")
    if status != 1:
        raise LoginError(status, LOGIN_STATUS.get(status, f"登录失败（status={status}）"), data)

    rt = data.get("rt") or {}
    once_pwd = rt.get("pwd")
    if not once_pwd:
        raise LoginError(status, "登录成功但未返回一次性密码（rt.pwd）", data)

    # 官方前端在拿到 oncePwd 后跳转到 passport 完成 CAS 登录
    session.get(
        LOGIN_PAGE,
        params={"pwd": once_pwd, "service": service},
        timeout=timeout,
    )
    cookies = _ensure_cas_cookies(session)
    return {
        "status": status,
        "message": "登录成功",
        "cookies": cookies,
        "data": data,
        "account": account,
    }


def qr_start(
    session: Optional[requests.Session] = None,
    timeout: int = 15,
    proxies: Optional[dict] = None,
) -> dict[str, Any]:
    """获取登录二维码，返回 ``session``/``qrToken``/``img``（base64 PNG）。"""
    session = session or new_session(proxies)
    if proxies:
        session.proxies.update(proxies)
    response = session.get(QR_IMG_API, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    token = data.get("qrToken")
    image = data.get("img")
    if not token or not image:
        raise LoginError(None, "获取二维码失败：接口未返回 qrToken/img", data)
    return {"session": session, "qrToken": token, "img": image}


def qr_poll(
    session: requests.Session,
    qr_token: str,
    service: str = SERVICE,
    timeout: int = 15,
) -> dict[str, Any]:
    """轮询二维码状态；成功时返回 cookies。"""
    response = session.get(QR_INFO_API, params={"qrToken": qr_token}, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    status = data.get("status")
    if status == 1:
        once_pwd = data.get("oncePassword")
        if not once_pwd:
            raise LoginError(status, "扫码成功但未返回一次性密码", data)
        session.get(
            LOGIN_PAGE,
            params={"pwd": once_pwd, "service": service},
            timeout=timeout,
        )
        cookies = _ensure_cas_cookies(session)
        return {
            "status": status,
            "message": "登录成功",
            "cookies": cookies,
            "data": data,
        }
    return {
        "status": status,
        "message": QR_STATUS.get(status, f"未知二维码状态：{status}"),
        "data": data,
    }


def parse_cookie_input(raw: str):
    """
    解析用户粘贴的 Cookie。

    支持：
    - cookies.json 的数组格式（utils.cookie_jar_to_list 的产物）
    - ``{"name": "value", ...}`` 字典
    - ``a=1; b=2`` 字符串
    """
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("Cookie 不能为空")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        result: dict[str, str] = {}
        for part in raw.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            key, value = part.split("=", 1)
            result[key.strip()] = value.strip()
        if not result:
            raise ValueError("无法解析 Cookie，请粘贴 JSON 或 a=1; b=2 格式")
        return result


def build_login_url(service: str = SERVICE) -> str:
    return f"{NEW_LOGIN_PAGE}?origin=zhs&service={quote(service, safe='')}"


# 兼容/调试用：返回当前时间戳（避免某些调用方把模块当成无副作用库）
def now_ms() -> int:
    return int(time.time() * 1000)

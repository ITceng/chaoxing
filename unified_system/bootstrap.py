from __future__ import annotations

from sqlalchemy import select

from unified_system.db import get_db
from unified_system.models import Permission, Platform, Role, RolePermission

PERMISSIONS = {
    "users.read": "View users within authorized scope",
    "users.manage": "Manage users within authorized scope",
    "invitations.manage": "Create and revoke invitation codes",
    "platforms.manage": "Manage verified platform catalog settings",
    "ai.manage": "Manage AI provider system settings",
    "logs.read": "View audit logs within authorized scope",
    "profile.manage": "Manage own profile and password",
    "platform.accounts.manage": "Manage own personal platform accounts",
    "shared_accounts.read": "View shared accounts granted to the current user",
    "shared_accounts.manage": "Create and administer shared platform accounts within scope",
    "tasks.create": "Submit platform tasks through authorized accounts",
    "tasks.read": "View own tasks and task events",
    "ai.use": "Use permitted learning assistance",
}

ROLE_PERMISSIONS = {
    "admin": {"users.read", "users.manage", "logs.read", "shared_accounts.manage", "profile.manage", "platform.accounts.manage", "shared_accounts.read", "tasks.create", "tasks.read"},
    "user": {"profile.manage", "platform.accounts.manage", "shared_accounts.read", "tasks.create", "tasks.read", "ai.use"},
}

PLATFORM_CATALOG = [
    {
        "key": "chaoxing", "name": "学习通 / 超星", "code_available": True,
        "integration_status": "pending", "auth_methods": ["username_password", "cookies"],
        "capabilities": [],
        "status_note": "仓库根目录有 Python 实现；尚未接入本系统的隔离 Worker，未做本系统端到端验证。",
    },
    {
        "key": "zhihuishu", "name": "智慧树 / 知到", "code_available": True,
        "integration_status": "pending", "auth_methods": ["username_password", "qr_code", "cookies"],
        "capabilities": [],
        "status_note": "fuckZHS-master 有 Python 登录/自动化代码；尚未接入本系统，验证码与平台接口待验证。",
    },
    {
        "key": "yinghua", "name": "英华", "code_available": False,
        "integration_status": "pending", "auth_methods": [], "capabilities": [],
        "status_note": "当前工作区没有 mooc-main；链接分支源码只展示 Yinghua 包且缺 go.mod，未通过 Linux 构建。",
    },
]


def seed_catalog() -> None:
    db = get_db()
    for name, description in PERMISSIONS.items():
        if db.scalar(select(Permission).where(Permission.name == name)) is None:
            db.add(Permission(name=name, description=description))
    for role_name, description in (
        ("superadmin", "Global system administrator"),
        ("admin", "Scoped administrator"),
        ("user", "Standard user"),
    ):
        role = db.scalar(select(Role).where(Role.name == role_name))
        if role is None:
            role = Role(name=role_name, description=description)
            db.add(role)
            db.flush()
        for permission_name in ROLE_PERMISSIONS.get(role_name, set()):
            permission = db.scalar(select(Permission).where(Permission.name == permission_name))
            if permission is not None and db.get(RolePermission, (role.id, permission.id)) is None:
                db.add(RolePermission(role_id=role.id, permission_id=permission.id))
    for item in PLATFORM_CATALOG:
        if db.scalar(select(Platform).where(Platform.key == item["key"])) is None:
            db.add(Platform(**item))
    db.commit()

"""Session, CSRF and device-token helpers."""

from __future__ import annotations

import functools
import hashlib
import hmac
import secrets

from flask import g, jsonify, request, session

from .database import get_db


def api_error(message: str, status: int = 400, code: str | None = None):
    payload = {"error": message}
    if code:
        payload["code"] = code
    return jsonify(payload), status


def csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(24)
        session["csrf_token"] = token
    return token


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        admin_id = session.get("admin_id")
        if not admin_id:
            return api_error("请先登录", 401, "authentication_required")
        admin = get_db().execute(
            "SELECT id, username, last_login_at FROM admins WHERE id = ?", (admin_id,)
        ).fetchone()
        if not admin:
            session.clear()
            return api_error("登录状态已失效", 401, "authentication_required")
        g.admin = admin
        return view(*args, **kwargs)

    return wrapped


def csrf_protect(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        expected = session.get("csrf_token", "")
        supplied = request.headers.get("X-CSRF-Token", "") or request.form.get("csrf_token", "")
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            return api_error("请求校验失败，请刷新页面后重试", 403, "csrf_failed")
        return view(*args, **kwargs)

    return wrapped


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_api_token() -> str:
    return "zh_" + secrets.token_urlsafe(32)


def device_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            return api_error("缺少设备令牌", 401, "device_token_required")
        token = authorization[7:].strip()
        if not token:
            return api_error("缺少设备令牌", 401, "device_token_required")
        device = get_db().execute(
            "SELECT * FROM devices WHERE api_token_hash = ?", (hash_token(token),)
        ).fetchone()
        if not device:
            return api_error("设备令牌无效", 401, "invalid_device_token")
        if device["status"] == "disabled":
            return api_error("设备已停用", 403, "device_disabled")
        if device["status"] != "active":
            return api_error("设备尚未激活", 403, "device_not_active")
        g.device = device
        return view(*args, **kwargs)

    return wrapped

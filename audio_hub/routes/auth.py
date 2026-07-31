from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from flask import Blueprint, current_app, g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from ..database import get_db, utcnow
from ..security import api_error, csrf_protect, csrf_token, json_object, login_required


auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")
_attempts: dict[str, deque[float]] = defaultdict(deque)
_attempt_lock = threading.Lock()
_WINDOW_SECONDS = 10 * 60
_MAX_ATTEMPTS = 8


def _is_rate_limited(ip: str) -> bool:
    now = time.monotonic()
    with _attempt_lock:
        attempts = _attempts[ip]
        while attempts and now - attempts[0] > _WINDOW_SECONDS:
            attempts.popleft()
        return len(attempts) >= _MAX_ATTEMPTS


def _record_failure(ip: str) -> None:
    with _attempt_lock:
        _attempts[ip].append(time.monotonic())


def _clear_failures(ip: str) -> None:
    with _attempt_lock:
        _attempts.pop(ip, None)


@auth_bp.post("/login")
@csrf_protect
def login():
    ip = request.remote_addr or "unknown"
    if _is_rate_limited(ip):
        return api_error("登录失败次数过多，请 10 分钟后重试", 429, "rate_limited")

    payload = json_object()
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    admin = get_db().execute(
        "SELECT * FROM admins WHERE username = ? COLLATE NOCASE", (username,)
    ).fetchone()
    if not admin or not check_password_hash(admin["password_hash"], password):
        _record_failure(ip)
        return api_error("用户名或密码错误", 401, "invalid_credentials")

    _clear_failures(ip)
    now = utcnow()
    get_db().execute("UPDATE admins SET last_login_at = ? WHERE id = ?", (now, admin["id"]))
    get_db().commit()
    session.clear()
    session.permanent = True
    session["admin_id"] = admin["id"]
    session["username"] = admin["username"]
    session["csrf_token"] = csrf_token()
    return jsonify(
        ok=True,
        admin={"id": admin["id"], "username": admin["username"]},
        csrf_token=session["csrf_token"],
    )


@auth_bp.get("/session")
@login_required
def get_session():
    return jsonify(
        authenticated=True,
        admin={"id": g.admin["id"], "username": g.admin["username"]},
        csrf_token=csrf_token(),
    )


@auth_bp.post("/logout")
@login_required
@csrf_protect
def logout():
    session.clear()
    return jsonify(ok=True)


@auth_bp.post("/change-password")
@login_required
@csrf_protect
def change_password():
    payload = json_object()
    current_password = str(payload.get("current_password", ""))
    new_password = str(payload.get("new_password", ""))
    if not new_password:
        return api_error("新密码不能为空")

    admin = get_db().execute("SELECT * FROM admins WHERE id = ?", (session["admin_id"],)).fetchone()
    if not admin or not check_password_hash(admin["password_hash"], current_password):
        return api_error("当前密码不正确", 403, "invalid_current_password")

    get_db().execute(
        "UPDATE admins SET password_hash = ? WHERE id = ?",
        (generate_password_hash(new_password), admin["id"]),
    )
    get_db().commit()
    current_app.logger.info("管理员 %s 修改了密码", admin["username"])
    return jsonify(ok=True)

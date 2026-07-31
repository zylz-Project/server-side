from __future__ import annotations

import hmac
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, g, jsonify, request, send_file

from ..catalog import PRODUCTS, AudioStoreError
from ..database import get_db, utcnow
from ..security import (
    api_error,
    csrf_protect,
    device_required,
    hash_token,
    json_object,
    login_required,
    new_api_token,
    provisioned_api_token,
)


devices_bp = Blueprint("devices", __name__, url_prefix="/api")
DEVICE_UID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{3,63}$")


def _json() -> dict:
    return json_object()


def _normalize_uid(value: object) -> str:
    uid = str(value or "").strip().upper()
    if not DEVICE_UID_RE.fullmatch(uid):
        raise ValueError("设备 ID 需为 4-64 位字母、数字、冒号、点、下划线或连字符")
    return uid


def _validate_product(product_id: object) -> str:
    value = str(product_id or "").strip()
    if value not in PRODUCTS:
        raise ValueError("无效的产品型号")
    return value


def _activation_code() -> str:
    database = get_db()
    for _ in range(20):
        code = f"{secrets.randbelow(1_000_000):06d}"
        if not database.execute(
            "SELECT 1 FROM devices WHERE activation_code = ?", (code,)
        ).fetchone():
            return code
    raise RuntimeError("无法生成唯一激活码")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _serialize_device(row) -> dict:
    last_seen = _parse_time(row["last_seen_at"])
    now = datetime.now(timezone.utc)
    online = bool(
        row["status"] == "active"
        and last_seen
        and (now - last_seen).total_seconds() <= current_app.config["DEVICE_ONLINE_SECONDS"]
    )
    return {
        "id": row["id"],
        "device_uid": row["device_uid"],
        "name": row["name"],
        "product_id": row["product_id"],
        "status": row["status"],
        "online": online,
        "activation_code": row["activation_code"] if row["status"] == "pending" else None,
        "activation_expires_at": row["activation_expires_at"],
        "token_prefix": row["token_prefix"],
        "firmware_version": row["firmware_version"],
        "ip_address": row["ip_address"],
        "battery_level": row["battery_level"],
        "flash_free": row["flash_free"],
        "last_seen_at": row["last_seen_at"],
        "created_at": row["created_at"],
        "activated_at": row["activated_at"],
    }


@devices_bp.get("/admin/overview")
@login_required
def overview():
    rows = get_db().execute("SELECT * FROM devices ORDER BY created_at DESC").fetchall()
    devices = [_serialize_device(row) for row in rows]
    store = current_app.extensions["audio_store"]
    product_stats = []
    for product_id, product in PRODUCTS.items():
        audio = store.summary(product_id)
        related = [device for device in devices if device["product_id"] == product_id]
        product_stats.append(
            {
                **product,
                "device_count": len(related),
                "online_count": sum(1 for device in related if device["online"]),
                "audio_count": audio["total"],
                "audio_size": audio["total_size"],
                "revision": audio["revision"],
            }
        )
    return jsonify(
        devices={
            "total": len(devices),
            "online": sum(1 for device in devices if device["online"]),
            "pending": sum(1 for device in devices if device["status"] == "pending"),
            "disabled": sum(1 for device in devices if device["status"] == "disabled"),
        },
        audio={
            "total": sum(item["audio_count"] for item in product_stats),
            "total_size": sum(item["audio_size"] for item in product_stats),
        },
        products=product_stats,
        recent_devices=devices[:6],
    )


@devices_bp.get("/admin/devices")
@login_required
def list_devices():
    rows = get_db().execute("SELECT * FROM devices ORDER BY created_at DESC").fetchall()
    return jsonify(devices=[_serialize_device(row) for row in rows])


@devices_bp.post("/admin/devices")
@login_required
@csrf_protect
def create_device():
    payload = _json()
    try:
        uid = _normalize_uid(payload.get("device_uid"))
        product_id = _validate_product(payload.get("product_id"))
    except ValueError as exc:
        return api_error(str(exc))
    name = str(payload.get("name") or PRODUCTS[product_id]["short_name"]).strip()[:80]
    token = new_api_token()
    try:
        cursor = get_db().execute(
            """
            INSERT INTO devices(
                device_uid, name, product_id, status, api_token_hash, token_prefix,
                created_at, activated_at
            ) VALUES (?, ?, ?, 'active', ?, ?, ?, ?)
            """,
            (uid, name, product_id, hash_token(token), token[:10], utcnow(), utcnow()),
        )
        get_db().commit()
    except sqlite3.IntegrityError:
        return api_error("该设备 ID 已存在", 409, "device_exists")
    row = get_db().execute("SELECT * FROM devices WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return jsonify(device=_serialize_device(row), api_token=token), 201


@devices_bp.post("/admin/devices/activate")
@login_required
@csrf_protect
def activate_by_code():
    code = str(_json().get("activation_code", "")).strip()
    if not re.fullmatch(r"\d{6}", code):
        return api_error("请输入 6 位激活码")
    row = get_db().execute(
        "SELECT * FROM devices WHERE activation_code = ? AND status = 'pending'", (code,)
    ).fetchone()
    if not row:
        return api_error("激活码无效", 404, "activation_not_found")
    expires_at = _parse_time(row["activation_expires_at"])
    if not expires_at or expires_at < datetime.now(timezone.utc):
        return api_error("激活码已过期，请让设备重新注册", 410, "activation_expired")
    now = utcnow()
    get_db().execute(
        """
        UPDATE devices SET status='active', activation_code=NULL,
            activation_expires_at=NULL, activated_at=? WHERE id=?
        """,
        (now, row["id"]),
    )
    get_db().commit()
    updated = get_db().execute("SELECT * FROM devices WHERE id = ?", (row["id"],)).fetchone()
    return jsonify(ok=True, device=_serialize_device(updated))


@devices_bp.patch("/admin/devices/<int:device_id>")
@login_required
@csrf_protect
def update_device(device_id: int):
    row = get_db().execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
    if not row:
        return api_error("设备不存在", 404)
    payload = _json()
    name = str(payload.get("name", row["name"])).strip()[:80]
    status = str(payload.get("status", row["status"])).strip()
    product_id = str(payload.get("product_id", row["product_id"])).strip()
    if not name:
        return api_error("设备名称不能为空")
    if status not in {"pending", "active", "disabled"}:
        return api_error("无效的设备状态")
    if status == "pending" and row["status"] != "pending":
        return api_error("已处理的设备不能恢复为待激活状态")
    try:
        product_id = _validate_product(product_id)
    except ValueError as exc:
        return api_error(str(exc))
    get_db().execute(
        "UPDATE devices SET name=?, status=?, product_id=? WHERE id=?",
        (name, status, product_id, device_id),
    )
    get_db().commit()
    updated = get_db().execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
    return jsonify(ok=True, device=_serialize_device(updated))


@devices_bp.post("/admin/devices/<int:device_id>/rotate-token")
@login_required
@csrf_protect
def rotate_device_token(device_id: int):
    row = get_db().execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
    if not row:
        return api_error("设备不存在", 404)
    token = new_api_token()
    get_db().execute(
        "UPDATE devices SET api_token_hash=?, token_prefix=? WHERE id=?",
        (hash_token(token), token[:10], device_id),
    )
    get_db().commit()
    return jsonify(ok=True, api_token=token)


@devices_bp.delete("/admin/devices/<int:device_id>")
@login_required
@csrf_protect
def delete_device(device_id: int):
    row = get_db().execute("SELECT id FROM devices WHERE id = ?", (device_id,)).fetchone()
    if not row:
        return api_error("设备不存在", 404)
    get_db().execute("DELETE FROM devices WHERE id = ?", (device_id,))
    get_db().commit()
    return jsonify(ok=True)


@devices_bp.post("/device/register")
def device_register():
    payload = _json()
    try:
        uid = _normalize_uid(payload.get("device_id"))
        product_id = _validate_product(payload.get("product_id"))
    except ValueError as exc:
        return api_error(str(exc))
    firmware = str(payload.get("firmware_version", "")).strip()[:64] or None
    existing = get_db().execute("SELECT * FROM devices WHERE device_uid = ?", (uid,)).fetchone()
    if existing:
        if existing["product_id"] != product_id:
            return api_error("设备型号与已登记信息不一致", 409, "product_mismatch")
        expires_at = _parse_time(existing["activation_expires_at"])
        if (
            existing["status"] == "pending"
            and (not expires_at or expires_at < datetime.now(timezone.utc))
        ):
            claim_token = "claim_" + secrets.token_urlsafe(24)
            code = _activation_code()
            new_expires_at = (
                datetime.now(timezone.utc)
                + timedelta(seconds=current_app.config["ACTIVATION_TTL_SECONDS"])
            ).isoformat(timespec="seconds")
            get_db().execute(
                """
                UPDATE devices SET activation_code=?, activation_expires_at=?,
                    claim_token_hash=?, firmware_version=COALESCE(?, firmware_version),
                    ip_address=? WHERE id=?
                """,
                (
                    code,
                    new_expires_at,
                    hash_token(claim_token),
                    firmware,
                    request.remote_addr,
                    existing["id"],
                ),
            )
            get_db().commit()
            return (
                jsonify(
                    status="pending",
                    device_id=uid,
                    activation_code=code,
                    expires_at=new_expires_at,
                    claim_token=claim_token,
                    poll_after=3,
                ),
                201,
            )
        if firmware:
            get_db().execute(
                "UPDATE devices SET firmware_version=?, ip_address=? WHERE id=?",
                (firmware, request.remote_addr, existing["id"]),
            )
            get_db().commit()
        return jsonify(status=existing["status"], device_id=uid)

    claim_token = "claim_" + secrets.token_urlsafe(24)
    code = _activation_code()
    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(seconds=current_app.config["ACTIVATION_TTL_SECONDS"])
    ).isoformat(timespec="seconds")
    get_db().execute(
        """
        INSERT INTO devices(
            device_uid, name, product_id, status, activation_code,
            activation_expires_at, claim_token_hash, firmware_version,
            ip_address, created_at
        ) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
        """,
        (
            uid,
            PRODUCTS[product_id]["short_name"] + "（待激活）",
            product_id,
            code,
            expires_at,
            hash_token(claim_token),
            firmware,
            request.remote_addr,
            utcnow(),
        ),
    )
    get_db().commit()
    return (
        jsonify(
            status="pending",
            device_id=uid,
            activation_code=code,
            expires_at=expires_at,
            claim_token=claim_token,
            poll_after=3,
        ),
        201,
    )


@devices_bp.post("/device/activate")
def device_activation_poll():
    payload = _json()
    try:
        uid = _normalize_uid(payload.get("device_id"))
    except ValueError as exc:
        return api_error(str(exc))
    claim_token = str(payload.get("claim_token", ""))
    row = get_db().execute("SELECT * FROM devices WHERE device_uid = ?", (uid,)).fetchone()
    if not row or not claim_token or not row["claim_token_hash"]:
        return api_error("设备注册信息无效", 401, "invalid_claim")
    if not hmac.compare_digest(row["claim_token_hash"], hash_token(claim_token)):
        return api_error("设备注册凭证无效", 401, "invalid_claim")
    if row["status"] == "pending":
        expires_at = _parse_time(row["activation_expires_at"])
        if not expires_at or expires_at < datetime.now(timezone.utc):
            return api_error("激活码已过期，请重新注册", 410, "activation_expired")
        return jsonify(status="pending", poll_after=3)
    if row["status"] == "disabled":
        return api_error("设备已停用", 403, "device_disabled")
    token = provisioned_api_token(uid, claim_token)
    token_hash = hash_token(token)
    if row["api_token_hash"] and not hmac.compare_digest(row["api_token_hash"], token_hash):
        return api_error("设备令牌已由管理员更新，请重新配置设备", 409, "token_rotated")
    get_db().execute(
        """
        UPDATE devices SET api_token_hash=?, token_prefix=?,
            last_seen_at=?, ip_address=? WHERE id=?
        """,
        (token_hash, token[:10], utcnow(), request.remote_addr, row["id"]),
    )
    get_db().commit()
    return jsonify(status="active", api_token=token, product_id=row["product_id"])


@devices_bp.post("/device/v1/check-in")
@device_required
def device_check_in():
    payload = _json()
    firmware = str(payload.get("firmware_version", "")).strip()[:64] or None
    battery = payload.get("battery_level")
    flash_free = payload.get("flash_free")
    if battery is not None:
        try:
            battery = max(0, min(100, int(battery)))
        except (TypeError, ValueError):
            return api_error("电量值无效")
    if flash_free is not None:
        try:
            flash_free = max(0, int(flash_free))
        except (TypeError, ValueError):
            return api_error("Flash 剩余空间无效")
    now = utcnow()
    get_db().execute(
        """
        UPDATE devices SET firmware_version=COALESCE(?, firmware_version),
            battery_level=COALESCE(?, battery_level),
            flash_free=COALESCE(?, flash_free), ip_address=?, last_seen_at=?,
            claim_token_hash=NULL
        WHERE id=?
        """,
        (firmware, battery, flash_free, request.remote_addr, now, g.device["id"]),
    )
    get_db().commit()
    store = current_app.extensions["audio_store"]
    product_id = g.device["product_id"]
    return jsonify(
        ok=True,
        server_time=now,
        device={"id": g.device["id"], "name": g.device["name"], "product_id": product_id},
        sync={"revision": store.revision(product_id), "manifest_url": "/api/device/v1/files"},
        heartbeat_interval=60,
    )


@devices_bp.get("/device/v1/files")
@device_required
def device_files():
    store = current_app.extensions["audio_store"]
    product_id = g.device["product_id"]
    category = request.args.get("category", "").strip() or None
    try:
        items = store.manifest(product_id, category)
    except AudioStoreError as exc:
        return api_error(str(exc))
    return jsonify(
        product=product_id,
        category=category or "all",
        revision=store.revision(product_id),
        files=items,
    )


@devices_bp.get("/device/v1/download/<int:index>")
@device_required
def device_download(index: int):
    store = current_app.extensions["audio_store"]
    try:
        path = store.path_by_index(g.device["product_id"], index, request.args.get("category"))
    except (AudioStoreError, FileNotFoundError):
        return api_error("音频索引不存在", 404)
    return send_file(path, mimetype="application/octet-stream", download_name=path.name, conditional=False)

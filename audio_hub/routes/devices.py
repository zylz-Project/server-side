from __future__ import annotations

import hmac
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, g, jsonify, request

from ..catalog import PRODUCTS, AudioStoreError
from ..database import get_db, utcnow
from ..device_voice import DeviceVoiceClient, DeviceVoiceError, lan_ipv4, normalize_mac
from ..discovery import DISCOVERY_PREFIX, discovery_proof
from ..downloads import send_audio
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


@devices_bp.errorhandler(DeviceVoiceError)
def _device_error(error):
    return api_error(str(error), error.status, error.code)


def _device_row(device_id: int):
    row = get_db().execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
    if row is None:
        raise DeviceVoiceError("设备不存在", 404, "device_not_found")
    return row


def _device_console(device) -> DeviceVoiceClient:
    if device["status"] != "active":
        raise DeviceVoiceError("设备登记尚未启用", 409, "device_not_active")
    mac = normalize_mac(device["device_uid"])
    if mac is None:
        raise DeviceVoiceError("这条记录缺少设备 MAC。请让设备连接工厂 Wi-Fi，再选择按热点名自动出现的记录", 409, "device_identity_required")
    return _console_at_address(device["ip_address"], mac)


def _console_at_address(address: str, mac: str | None) -> DeviceVoiceClient:
    credential = current_app.config["DEVICE_DEVELOPER_PASSWORD"]
    if not isinstance(credential, str) or not 1 <= len(credential) <= 128 or not credential.isprintable():
        raise DeviceVoiceError("本机的出厂设备授权尚未配置", 503, "device_console_unconfigured")
    return DeviceVoiceClient(address, mac, credential, current_app.extensions["device_voice_sessions"])


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


def _hotspot_name(device_uid: str, product_id: str) -> str | None:
    mac = normalize_mac(device_uid)
    product = PRODUCTS.get(product_id)
    if mac is None or product is None:
        return None
    # The three S3 builds derive SoftAP from STA by incrementing the last byte.
    last_byte = (int(mac[-2:], 16) + 1) % 256
    return f"{product['ap_prefix']}-{mac[-4:-2]}{last_byte:02X}"


def _serialize_device(row) -> dict:
    last_seen = _parse_time(row["last_seen_at"])
    now = datetime.now(timezone.utc)
    online = bool(
        row["status"] == "active"
        and last_seen
        and (now - last_seen).total_seconds() <= current_app.config["DEVICE_ONLINE_SECONDS"]
    )
    hotspot = _hotspot_name(row["device_uid"], row["product_id"])
    saved_name = re.sub(r"（待激活）|\(待激活\)|待激活", "", row["name"]).strip()
    address = lan_ipv4(row["ip_address"]) if row["status"] == "active" else None
    return {
        "id": row["id"],
        "device_uid": row["device_uid"],
        "name": hotspot or saved_name or row["device_uid"],
        "hotspot_name": hotspot,
        "product_id": row["product_id"],
        "status": row["status"],
        "online": online,
        "activation_code": row["activation_code"] if row["status"] == "pending" else None,
        "activation_expires_at": row["activation_expires_at"],
        "token_prefix": row["token_prefix"],
        "firmware_version": row["firmware_version"],
        "ip_address": row["ip_address"],
        "developer_url": f"http://{address}:8080/" if address else None,
        "battery_level": row["battery_level"],
        "flash_free": row["flash_free"],
        "last_seen_at": row["last_seen_at"],
        "created_at": row["created_at"],
        "activated_at": row["activated_at"],
    }


@devices_bp.get("/admin/overview")
@login_required
def overview():
    cutoff = (datetime.now(timezone.utc) - timedelta(
        seconds=current_app.config["DEVICE_ONLINE_SECONDS"],
    )).isoformat()
    rows = get_db().execute(
        """
        SELECT product_id, COUNT(*) AS total,
            SUM(activated_at IS NOT NULL AND activated_at != '') AS bound,
            SUM(status='active' AND COALESCE(last_seen_at >= ?, 0)) AS online,
            SUM(status='pending') AS pending, SUM(status='disabled') AS disabled
        FROM devices GROUP BY product_id
        """,
        (cutoff,),
    ).fetchall()
    counts = {row["product_id"]: dict(row) for row in rows}
    totals = {key: sum(row[key] for row in rows)
              for key in ("total", "bound", "online", "pending", "disabled")}
    recent = get_db().execute(
        "SELECT * FROM devices ORDER BY created_at DESC, id DESC LIMIT 6",
    ).fetchall()
    store = current_app.extensions["audio_store"]
    product_stats = []
    for product_id, product in PRODUCTS.items():
        audio = store.summary(product_id)
        related = counts.get(product_id, {})
        product_stats.append({
            **product, "device_count": related.get("bound", 0),
            "online_count": related.get("online", 0),
            "audio_count": audio["total"], "audio_size": audio["total_size"],
            "revision": audio["revision"],
        })
    response = jsonify(
        devices=totals,
        audio={"total": sum(item["audio_count"] for item in product_stats),
               "total_size": sum(item["audio_size"] for item in product_stats)},
        products=product_stats,
        recent_devices=[_serialize_device(row) for row in recent],
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@devices_bp.get("/admin/devices")
@login_required
def list_devices():
    rows = get_db().execute(
        """
        SELECT d.*, k.label AS voice_key_label, b.state AS voice_key_state
        FROM devices d
        LEFT JOIN device_voice_keys b ON b.device_id=d.id
        LEFT JOIN voice_keys k ON k.id=b.key_id
        ORDER BY d.last_seen_at DESC, d.id DESC
        """,
    ).fetchall()
    return jsonify(devices=[{
        **_serialize_device(row),
        "voice_key": {"label": row["voice_key_label"], "state": row["voice_key_state"]}
        if row["voice_key_label"] else None,
    } for row in rows])


@devices_bp.get("/admin/devices/<int:device_id>/factory")
@login_required
def factory_status(device_id: int):
    status = _device_console(_device_row(device_id)).read_device()
    audio = status.get("audio")
    volume = audio.get("volume") if isinstance(audio, dict) else None
    flash = status.get("flash")
    files = flash.get("files") if isinstance(flash, dict) else None
    return jsonify(
        volume=volume if type(volume) is int and 0 <= volume <= 100 else None,
        audio_files=files if type(files) is int and files >= 0 else None,
    )


@devices_bp.post("/admin/devices/connect")
@login_required
@csrf_protect
def connect_device():
    address = _json().get("ip")
    address = lan_ipv4(address.strip()) if isinstance(address, str) else None
    if address is None:
        return api_error("请输入设备的局域网 IPv4 地址")
    console = _console_at_address(address, None)
    status = console.read_device()
    product = next((key for key, value in PRODUCTS.items() if value["console_build"] == status.get("build")), None)
    if product is None:
        return api_error("该设备型号暂不支持出厂接入", 409, "device_product_unsupported")
    if status.get("transport") in ("cellular", "probing"):
        return api_error("请先让设备切到工厂 Wi-Fi", 409, "device_wifi_required")
    uid = "ZYLZ-" + console.mac
    registration = get_db().execute("SELECT status FROM devices WHERE device_uid=?", (uid,)).fetchone()
    if registration and registration["status"] == "disabled":
        return api_error("这台设备已停用，请先启用原登记", 409, "device_not_active")
    console.check_audio()
    return jsonify(ok=True, device_uid=uid, name=_hotspot_name(uid, product)), 202


@devices_bp.put("/admin/devices/<int:device_id>/factory/volume")
@login_required
@csrf_protect
def factory_volume(device_id: int):
    volume = _json().get("volume")
    if type(volume) is not int or not 0 <= volume <= 100:
        return api_error("音量须为 0 到 100 的整数")
    console = _device_console(_device_row(device_id))
    console.read_device()
    console.set_volume(volume)
    return jsonify(ok=True), 202


@devices_bp.get("/admin/devices/<int:device_id>/factory/audio")
@login_required
def factory_audio_status(device_id: int):
    console = _device_console(_device_row(device_id))
    console.read_device()
    return jsonify(console.audio_status())


@devices_bp.post("/admin/devices/<int:device_id>/factory/audio")
@login_required
@csrf_protect
def factory_audio(device_id: int):
    payload = _json()
    action = payload.get("action")
    if action not in ("check", "write"):
        return api_error("请选择检查清单或写入音频")

    device = _device_row(device_id)
    console = _device_console(device)
    console.read_device()
    if action == "check":
        console.check_audio()
        return jsonify(ok=True), 202

    names, revision = payload.get("names"), payload.get("revision")
    if not isinstance(names, list) or not 1 <= len(names) <= 32 or not all(
        isinstance(name, str) and 1 <= len(name) <= 63 and name.isprintable()
        for name in names
    ) or len(set(names)) != len(names):
        return api_error("请选择要写入的音频文件")
    audio = console.audio_status()
    if audio["running"]:
        return api_error("设备正在处理音频", 409, "device_audio_busy")
    store = current_app.extensions["audio_store"]
    try:
        current_revision = store.revision(device["product_id"])
    except AudioStoreError as error:
        return api_error(str(error))
    if not revision or revision != audio["revision"] or revision != current_revision:
        return api_error("音频清单已变化，请重新检查后再写入", 409, "device_audio_stale")
    available = {item["name"] for item in audio["files"] if item["state"] != "local"}
    if not set(names) <= available:
        return api_error("所选文件不在当前清单中，请重新检查", 409, "device_audio_selection")
    console.write_audio(names)
    return jsonify(ok=True), 202


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
    db = get_db()
    try:
        result = db.execute("DELETE FROM devices WHERE id = ?", (device_id,))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return api_error("请先在音色管理中解除这台设备的 Key", 409, "voice_key_in_use")
    if not result.rowcount:
        return api_error("设备不存在", 404)
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
    # Serialize the read-and-create decision across Waitress worker threads.
    get_db().execute("BEGIN IMMEDIATE")
    existing = get_db().execute("SELECT * FROM devices WHERE device_uid = ?", (uid,)).fetchone()
    if existing and existing["product_id"] != product_id:
        return api_error("设备型号与已登记信息不一致", 409, "product_mismatch")
    if existing and existing["status"] == "disabled":
        return api_error("设备已停用", 403, "device_disabled")
    if current_app.config["DEVICE_AUTO_ACTIVATE"]:
        # Keep a claim for older firmware, which still calls /device/activate.
        # New firmware can persist the API token from this response immediately.
        claim_token = "claim_" + secrets.token_urlsafe(24)
        token = provisioned_api_token(uid, claim_token)
        now = utcnow()
        if existing:
            get_db().execute(
                """
                UPDATE devices SET status='active', activation_code=NULL,
                    activation_expires_at=NULL, claim_token_hash=?,
                    api_token_hash=?, token_prefix=?,
                    firmware_version=COALESCE(?, firmware_version), ip_address=?,
                    activated_at=COALESCE(activated_at, ?) WHERE id=?
                """,
                (hash_token(claim_token), hash_token(token), token[:10],
                 firmware, request.remote_addr, now, existing["id"]),
            )
        else:
            get_db().execute(
                """
                INSERT INTO devices(
                    device_uid, name, product_id, status, claim_token_hash,
                    api_token_hash, token_prefix, firmware_version, ip_address,
                    created_at, activated_at
                ) VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
                """,
                (uid, PRODUCTS[product_id]["short_name"], product_id,
                 hash_token(claim_token), hash_token(token), token[:10],
                 firmware, request.remote_addr, now, now),
            )
        get_db().commit()
        return jsonify(
            status="active", device_id=uid, product_id=product_id,
            api_token=token, claim_token=claim_token, activation_code="", poll_after=0,
        ), 200 if existing else 201
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


@devices_bp.get("/device/discovery")
def device_discovery():
    nonce = request.args.get("nonce", "")
    credential = current_app.config["DEVICE_DEVELOPER_PASSWORD"]
    if (not credential or not current_app.config["DEVICE_AUTO_ACTIVATE"]
            or not lan_ipv4(request.remote_addr) or not re.fullmatch(r"[0-9a-f]{32}", nonce)):
        return api_error("无法验证局域网接入", 404)
    return current_app.response_class(
        discovery_proof(credential, DISCOVERY_PREFIX + "verify " + nonce),
        content_type="text/plain", headers={"Cache-Control": "no-store"},
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
        if current_app.config["DEVICE_AUTO_ACTIVATE"]:
            # Devices already waiting with a saved claim need no new code,
            # including claims whose manual activation window has expired.
            get_db().execute(
                """
                UPDATE devices SET status='active', activation_code=NULL,
                    activation_expires_at=NULL, activated_at=? WHERE id=?
                """,
                (utcnow(), row["id"]),
            )
        else:
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
    # A cached token proves ownership of the board after reflashing another product.
    # Old clients may omit both fields and keep their existing registration.
    product_id = g.device["product_id"]
    if "device_id" in payload or "product_id" in payload:
        try:
            uid = _normalize_uid(payload.get("device_id"))
            product_id = _validate_product(payload.get("product_id"))
        except ValueError as exc:
            return api_error(str(exc))
        if uid != g.device["device_uid"]:
            return api_error("设备 ID 与令牌不匹配", 409, "device_mismatch")
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
    previous_product = g.device["product_id"]
    product_changed = product_id != previous_product
    name = g.device["name"]
    # Update generated names, while retaining names supplied by an administrator.
    if product_changed and name in {
        PRODUCTS[previous_product]["short_name"],
        PRODUCTS[previous_product]["short_name"] + "（待激活）",
    }:
        name = PRODUCTS[product_id]["short_name"]
    get_db().execute(
        """
        UPDATE devices SET product_id=?, name=?, firmware_version=COALESCE(?, firmware_version),
            battery_level=COALESCE(?, battery_level),
            flash_free=COALESCE(?, flash_free), ip_address=?, last_seen_at=?,
            claim_token_hash=NULL
        WHERE id=?
        """,
        (product_id, name, firmware, battery, flash_free, request.remote_addr, now, g.device["id"]),
    )
    get_db().commit()
    store = current_app.extensions["audio_store"]
    if product_changed:
        current_app.logger.info("设备 %s 切换产品: %s -> %s",
                                g.device["device_uid"], previous_product, product_id)
    hotspot = _hotspot_name(g.device["device_uid"], product_id)
    return jsonify(
        ok=True,
        product_changed=product_changed,
        server_time=now,
        device={"id": g.device["id"], "name": hotspot or name,
                "hotspot_name": hotspot, "product_id": product_id},
        sync={"revision": store.revision(product_id), "manifest_url": "/api/device/v1/files"},
        heartbeat_interval=60,
    )


@devices_bp.get("/device/v1/files")
@device_required
def device_files():
    store = current_app.extensions["audio_store"]
    product_id = g.device["product_id"]
    expected_product = request.args.get("product")
    if expected_product is not None and expected_product != product_id:
        return api_error("音频产品与当前固件不一致，请重新上报心跳", 409, "product_mismatch")
    category = request.args.get("category", "").strip() or None
    try:
        payload = store.listing(product_id, category)
    except AudioStoreError as exc:
        return api_error(str(exc))
    response = jsonify(payload)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@devices_bp.get("/device/v1/download/<int:index>")
@device_required
def device_download(index: int):
    store = current_app.extensions["audio_store"]
    expected_product = request.args.get("product")
    if expected_product is not None and expected_product != g.device["product_id"]:
        return api_error("音频产品与当前固件不一致，请重新上报心跳", 409, "product_mismatch")
    try:
        path = store.path_by_index(g.device["product_id"], index, request.args.get("category"))
        return send_audio(path)
    except (AudioStoreError, FileNotFoundError):
        return api_error("音频索引不存在", 404)

"""Admin-only Key and per-device voice management."""

from __future__ import annotations

import time

from flask import Blueprint, jsonify, request

from ..database import get_db
from ..device_voice import DeviceVoiceError, NATIVE_VOICE, check_aliyun_key, normalize_mac
from ..security import api_error, csrf_protect, json_object, login_required
from ..voice_keys import KEY_LIMIT, VoiceKeyError, add_key, delete_key, finish_assignment, read_key, start_assignment
from .devices import _device_console, _device_row, _serialize_device


voices_bp = Blueprint("voices", __name__, url_prefix="/api/admin/voice")


@voices_bp.errorhandler(VoiceKeyError)
@voices_bp.errorhandler(DeviceVoiceError)
def voice_error(error):
    return api_error(str(error), error.status, error.code)


@voices_bp.get("")
@login_required
def dashboard():
    db = get_db()
    keys = db.execute(
        "SELECT k.id, k.label, k.suffix, k.created_at, COUNT(b.device_id) AS count "
        "FROM voice_keys k LEFT JOIN device_voice_keys b ON b.key_id=k.id "
        "GROUP BY k.id ORDER BY k.id DESC"
    ).fetchall()
    devices = db.execute(
        "SELECT d.*, b.key_id, b.state AS key_state, b.last_error, b.lease_until "
        "FROM devices d LEFT JOIN device_voice_keys b ON d.id=b.device_id "
        "ORDER BY d.last_seen_at DESC, d.id DESC"
    ).fetchall()
    return jsonify(
        key_limit=KEY_LIMIT,
        keys=[dict(row) for row in keys],
        devices=[{
            **_serialize_device(row), "key_id": row["key_id"], "key_state": row["key_state"],
            "key_error": row["last_error"] or "", "key_syncing": (row["lease_until"] or 0) > time.time(),
            "voice_identifiable": normalize_mac(row["device_uid"]) is not None,
        } for row in devices],
    )


@voices_bp.post("/keys")
@login_required
@csrf_protect
def create_key():
    payload = json_object()
    return jsonify(ok=True, id=add_key(payload.get("label"), payload.get("key"))), 201


@voices_bp.delete("/keys/<int:key_id>")
@login_required
@csrf_protect
def remove_key(key_id: int):
    delete_key(key_id)
    return jsonify(ok=True)


@voices_bp.post("/keys/<int:key_id>/check")
@login_required
@csrf_protect
def check_key(key_id: int):
    check_aliyun_key(read_key(key_id))
    return jsonify(ok=True)


@voices_bp.route("/devices/<int:device_id>/key", methods=["PUT", "DELETE"])
@login_required
@csrf_protect
def set_device_key(device_id: int):
    device = _device_row(device_id)
    payload = json_object()
    clearing = request.method == "DELETE"
    key_id = None if clearing else payload.get("key_id")
    if not clearing and (type(key_id) is not int or not 1 <= key_id < 2**63):
        return api_error("请选择要分配的 Key", 400, "invalid_voice_key_id")
    if clearing and not get_db().execute(
        "SELECT 1 FROM device_voice_keys WHERE device_id=?", (device_id,)
    ).fetchone():
        return jsonify(ok=True)

    console = _device_console(device)
    # Verify LAN identity and developer access before reserving a slot.
    status = console.read_status()
    if status["busy"] or status["enrollment_status"] in ("starting", "prompt", "recording", "processing") or status["preview_status"] in ("queued", "playing"):
        return api_error("请等待设备当前的音色操作完成", 409, "device_voice_busy")
    assignment = start_assignment(device_id, console.mac, key_id)
    if assignment is None:
        return jsonify(ok=True)
    operation, secret = assignment
    try:
        console.configure("ali_key", secret)
        snapshot = console.read_status()
        if snapshot["has_key"] == clearing:
            raise DeviceVoiceError("设备未确认 Key 状态，请重试同步", 409, "device_key_unconfirmed")
    except DeviceVoiceError as error:
        finish_assignment(device_id, operation, clearing=clearing, error=str(error))
        raise
    finish_assignment(device_id, operation, clearing=clearing)
    return jsonify(ok=True, device_key_configured=snapshot["has_key"])


@voices_bp.post("/devices/<int:device_id>/catalog")
@login_required
@csrf_protect
def device_catalog(device_id: int):
    payload = json_object()
    action = payload.get("action", "status")
    if action not in ("status", "list", "select", "rename", "delete", "record", "preview", "original"):
        return api_error("不支持的音色操作", 400, "invalid_voice_action")
    console = _device_console(_device_row(device_id))
    snapshot = console.read_status()
    if action == "status":
        return jsonify(snapshot)
    syncing = get_db().execute(
        "SELECT 1 FROM device_voice_keys WHERE device_id=? AND lease_until>?", (device_id, int(time.time()))
    ).fetchone()
    if syncing:
        return api_error("设备正同步 Key，请稍后重试", 409, "voice_key_sync_busy")
    if action == "original":
        console.configure("ali_voice", NATIVE_VOICE)
        return jsonify(ok=True)
    if not snapshot["has_key"]:
        return api_error("设备还没有配置音色 Key", 409, "device_voice_key_required")
    if action == "list":
        console.manage("list")
    elif action == "preview":
        if snapshot["current_voice"] == NATIVE_VOICE:
            return api_error("原声会在正常对话中播放", 409, "voice_native_preview")
        console.configure("ali_preview", "")
    else:
        name = payload.get("name", "") if action in ("rename", "record") else ""
        if action in ("rename", "record"):
            name = name.strip() if isinstance(name, str) else ""
            try:
                valid_name = 1 <= len(name.encode("utf-8")) <= 48 and all(ord(char) >= 32 and ord(char) != 127 for char in name)
            except UnicodeError:
                valid_name = False
            if not valid_name:
                return api_error("请输入音色名称（最多 48 字节）", 400, "invalid_voice_name")
        if action == "record":
            console.configure("device_record", name)
        else:
            voice_id = payload.get("id")
            voice = next((item for item in snapshot["voices"] if item["id"] == voice_id), None)
            if not voice or (action in ("rename", "delete") and voice["builtin"]):
                return api_error("请从当前设备的音色列表中选择", 404, "device_voice_not_found")
            if voice_id == NATIVE_VOICE:
                console.configure("ali_voice", NATIVE_VOICE)
                return jsonify(ok=True)
            console.manage(action, voice_id, name)
    return jsonify(ok=True), 202

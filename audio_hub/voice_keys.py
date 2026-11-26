"""Persistent Aliyun Key storage and device assignment."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
import tempfile
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

from .database import get_db, utcnow


KEY_LIMIT = 5


class VoiceKeyError(Exception):
    def __init__(self, message: str, status: int, code: str):
        super().__init__(message)
        self.status = status
        self.code = code


def load_vault(data_dir: Path) -> Fernet:
    path = data_dir / "voice-vault.key"
    if not path.exists():
        # Publish a complete private file even if two workers start together.
        with tempfile.NamedTemporaryFile(dir=data_dir) as draft:
            draft.write(Fernet.generate_key())
            draft.flush()
            try:
                os.link(draft.name, path)
            except FileExistsError:
                pass
    return Fernet(path.read_bytes().strip())


def _unwrap_key_copy(value: str) -> str:
    value = value.strip().strip("\ufeff\u200b").strip()
    if len(value) >= 2 and (value[0], value[-1]) in {
        ('"', '"'), ("'", "'"), ('“', '”'), ('‘', '’'), ('`', '`'),
    }:
        return value[1:-1].strip()
    return value


def _parse_voice_key(value: object) -> str:
    key = _unwrap_key_copy(value) if isinstance(value, str) else ""
    key = re.sub(r"\A(?:(?:Authorization:[ \t]*)?Bearer[ \t]+|"
                 r"(?:export[ \t]+)?DASHSCOPE_API_KEY[ \t]*=[ \t]*)", "", key, flags=re.IGNORECASE)
    key = _unwrap_key_copy(key)
    if not key:
        raise VoiceKeyError("请粘贴完整的百炼 API Key", 400, "invalid_voice_key")
    if key.startswith("st-"):
        raise VoiceKeyError("这是临时 Key。请从百炼密钥管理复制北京地域的长期 Key", 400, "temporary_voice_key")
    if key.startswith("sk-sp-"):
        raise VoiceKeyError("这是套餐专属 Key。音色服务需要北京地域的通用百炼 API Key", 400, "plan_voice_key")
    if key.startswith("LTAI"):
        raise VoiceKeyError("这是阿里云 AccessKey ID。请从百炼密钥管理复制 sk- 开头的 API Key", 400, "access_key_supplied")
    if key.startswith("sk-") and (any(char in key for char in "*•…") or "..." in key):
        raise VoiceKeyError("粘贴的是脱敏或省略后的 Key，请复制完整 Key", 400, "masked_voice_key")
    # The firmware stores up to 256 bytes. Accept bearer-token characters without
    # assuming a provider-specific key length or allowing header delimiters.
    if (len(key) > 256 or not re.fullmatch(r"sk-[A-Za-z0-9._~+/-]+=*", key)
            or not any(char.isalnum() for char in key[3:])):
        raise VoiceKeyError("请从百炼密钥管理复制北京地域完整的 sk- 开头 API Key", 400, "invalid_voice_key")
    return key


def add_key(label: object, key: object) -> int:
    label = label.strip() if isinstance(label, str) else ""
    if not 1 <= len(label) <= 60 or not label.isprintable():
        raise VoiceKeyError("请输入 1–60 字的 Key 名称", 400, "invalid_key_label")
    key = _parse_voice_key(key)
    secret = key.encode("ascii")
    vault = current_app.extensions["voice_vault"]
    db = get_db()
    try:
        with db:
            result = db.execute(
                "INSERT INTO voice_keys(label, encrypted_key, fingerprint, suffix, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (label, vault.encrypt(secret), hashlib.sha256(secret).hexdigest(), key[-4:], utcnow()),
            )
    except sqlite3.IntegrityError:
        raise VoiceKeyError("这个 Key 已经添加，继续使用现有记录", 409, "duplicate_voice_key") from None
    return result.lastrowid


def delete_key(key_id: int) -> None:
    db = get_db()
    try:
        with db:
            result = db.execute("DELETE FROM voice_keys WHERE id = ?", (key_id,))
    except sqlite3.IntegrityError:
        raise VoiceKeyError("请先解除该 Key 下的设备", 409, "voice_key_in_use") from None
    if not result.rowcount:
        raise VoiceKeyError("Key 不存在", 404, "voice_key_not_found")


def read_key(key_id: int) -> str:
    row = get_db().execute("SELECT encrypted_key FROM voice_keys WHERE id = ?", (key_id,)).fetchone()
    if row is None:
        raise VoiceKeyError("Key 不存在", 404, "voice_key_not_found")
    try:
        return current_app.extensions["voice_vault"].decrypt(row["encrypted_key"]).decode("ascii")
    except (InvalidToken, UnicodeError):
        raise VoiceKeyError("无法解密 Key，请检查 Audio Hub 备份中的密钥文件", 503, "voice_vault_unavailable") from None


def start_assignment(device_id: int, device_mac: str, key_id: int | None) -> tuple[str, str] | None:
    """Reserve a slot and a short write lease before sending any Key to hardware."""
    db = get_db()
    with db:
        db.execute("BEGIN IMMEDIATE")
        device = db.execute("SELECT status FROM devices WHERE id = ?", (device_id,)).fetchone()
        if not device:
            raise VoiceKeyError("设备不存在", 404, "device_not_found")
        if key_id is not None and device["status"] != "active":
            raise VoiceKeyError("请先启用设备", 409, "device_not_active")

        binding = db.execute("SELECT * FROM device_voice_keys WHERE device_id = ?", (device_id,)).fetchone()
        now = int(time.time())
        if binding and binding["lease_until"] > now:
            raise VoiceKeyError("设备正同步 Key，请稍后重试", 409, "voice_key_sync_busy")
        if binding and key_id is not None and binding["key_id"] != key_id:
            raise VoiceKeyError("请先解除设备原有的 Key 绑定", 409, "voice_key_already_assigned")
        if not binding and key_id is None:
            return None
        if not binding and db.execute(
            "SELECT 1 FROM device_voice_keys WHERE device_mac=?", (device_mac,)
        ).fetchone():
            raise VoiceKeyError("同一台设备已通过另一条登记绑定，请在原记录管理 Key", 409, "voice_device_already_assigned")

        operation = secrets.token_hex(16)
        key = read_key(key_id) if key_id is not None else ""
        state = "pending" if key_id is not None else "clearing"
        if binding:
            db.execute(
                "UPDATE device_voice_keys SET state=?, operation=?, lease_until=?, last_error='', updated_at=? "
                "WHERE device_id=?",
                (state, operation, now + 60, utcnow(), device_id),
            )
        else:
            try:
                db.execute(
                    "INSERT INTO device_voice_keys(device_id, device_mac, key_id, state, operation, lease_until, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (device_id, device_mac, key_id, state, operation, now + 60, utcnow()),
                )
            except sqlite3.IntegrityError as exc:
                if str(exc) != "voice_key_capacity":
                    raise
                raise VoiceKeyError("此 Key 已分配给五台设备", 409, "voice_key_capacity") from None
        return operation, key


def finish_assignment(device_id: int, operation: str, *, clearing: bool, error: str = "") -> None:
    db = get_db()
    with db:
        if clearing and not error:
            result = db.execute(
                "DELETE FROM device_voice_keys WHERE device_id=? AND operation=?", (device_id, operation)
            )
        else:
            result = db.execute(
                "UPDATE device_voice_keys SET state=?, operation=NULL, lease_until=0, last_error=?, updated_at=? "
                "WHERE device_id=? AND operation=?",
                ("clearing" if clearing else "pending" if error else "applied", error, utcnow(), device_id, operation),
            )
        if not result.rowcount:
            raise VoiceKeyError("这次写入结果已过期，请刷新设备状态", 409, "voice_key_sync_expired")

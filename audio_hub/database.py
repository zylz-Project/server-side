"""SQLite persistence for administrators and devices."""

from __future__ import annotations

import secrets
import sqlite3
from pathlib import Path

from flask import Flask, current_app, g
from werkzeug.security import generate_password_hash


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_uid TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL,
    product_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'active', 'disabled')),
    activation_code TEXT UNIQUE,
    activation_expires_at TEXT,
    claim_token_hash TEXT,
    api_token_hash TEXT UNIQUE,
    token_prefix TEXT,
    firmware_version TEXT,
    ip_address TEXT,
    battery_level INTEGER,
    flash_free INTEGER,
    last_seen_at TEXT,
    created_at TEXT NOT NULL,
    activated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_devices_product ON devices(product_id);
CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status);
CREATE INDEX IF NOT EXISTS idx_devices_last_seen ON devices(last_seen_at);
CREATE INDEX IF NOT EXISTS idx_devices_created_at ON devices(created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS voice_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    encrypted_key BLOB NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE,
    suffix TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS device_voice_keys (
    device_id INTEGER PRIMARY KEY REFERENCES devices(id) ON DELETE RESTRICT,
    device_mac TEXT NOT NULL UNIQUE,
    key_id INTEGER NOT NULL REFERENCES voice_keys(id) ON DELETE RESTRICT,
    state TEXT NOT NULL CHECK(state IN ('pending', 'applied', 'clearing')),
    operation TEXT,
    lease_until INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_device_voice_keys_key_id ON device_voice_keys(key_id);

CREATE TRIGGER IF NOT EXISTS voice_keys_limit_insert
BEFORE INSERT ON device_voice_keys
WHEN (SELECT COUNT(*) FROM device_voice_keys WHERE key_id = NEW.key_id) >= 5
BEGIN
    SELECT RAISE(ABORT, 'voice_key_capacity');
END;

CREATE TRIGGER IF NOT EXISTS voice_keys_limit_update
BEFORE UPDATE OF key_id ON device_voice_keys
WHEN NEW.key_id != OLD.key_id
     AND (SELECT COUNT(*) FROM device_voice_keys WHERE key_id = NEW.key_id) >= 5
BEGIN
    SELECT RAISE(ABORT, 'voice_key_capacity');
END;
"""


def utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        connection = sqlite3.connect(current_app.config["DATABASE"], timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        g.db = connection
    return g.db


def close_db(_error=None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db(app: Flask) -> None:
    database = Path(app.config["DATABASE"])
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA)
        connection.commit()
    finally:
        connection.close()
    try:
        database.chmod(0o600)
    except OSError:
        pass


def ensure_admin(app: Flask) -> str | None:
    connection = sqlite3.connect(app.config["DATABASE"])
    try:
        row = connection.execute("SELECT id FROM admins LIMIT 1").fetchone()
        if row:
            return None
        password = app.config.get("ADMIN_PASSWORD") or secrets.token_urlsafe(12)
        connection.execute(
            "INSERT INTO admins(username, password_hash, created_at) VALUES (?, ?, ?)",
            (
                app.config["ADMIN_USERNAME"],
                generate_password_hash(password),
                utcnow(),
            ),
        )
        connection.commit()
        return None if app.config.get("ADMIN_PASSWORD") else password
    finally:
        connection.close()

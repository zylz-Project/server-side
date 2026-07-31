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
"""


def utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        connection = sqlite3.connect(current_app.config["DATABASE"], timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
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

"""Audio Hub Flask application factory."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from flask import Flask, jsonify, request

from .catalog import AudioStore
from .database import close_db, ensure_admin, init_db


def _load_or_create_secret(data_dir: Path) -> str:
    configured = os.environ.get("AUDIO_HUB_SECRET_KEY", "").strip()
    if configured:
        return configured

    path = data_dir / "session.key"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()

    value = secrets.token_urlsafe(48)
    path.write_text(value, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return value


def create_app(test_config: dict | None = None) -> Flask:
    base_dir = Path(__file__).resolve().parent.parent
    data_dir = Path((test_config or {}).get("DATA_DIR", base_dir / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_mapping(
        AUDIO_DIR=str(base_dir / "audio_files"),
        DATA_DIR=str(data_dir),
        DATABASE=str(data_dir / "audio_hub.db"),
        SECRET_KEY=_load_or_create_secret(data_dir),
        ADMIN_USERNAME=os.environ.get("AUDIO_HUB_ADMIN_USERNAME", "admin"),
        ADMIN_PASSWORD=os.environ.get("AUDIO_HUB_ADMIN_PASSWORD", ""),
        MAX_FILE_SIZE=32 * 1024 * 1024,
        MAX_CONTENT_LENGTH=128 * 1024 * 1024,
        DEVICE_ONLINE_SECONDS=120,
        ACTIVATION_TTL_SECONDS=30 * 60,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("AUDIO_HUB_COOKIE_SECURE") == "1",
        PERMANENT_SESSION_LIFETIME=8 * 60 * 60,
        JSON_SORT_KEYS=False,
    )
    if test_config:
        app.config.update(test_config)

    Path(app.config["DATA_DIR"]).mkdir(parents=True, exist_ok=True)
    store = AudioStore(Path(app.config["AUDIO_DIR"]), app.config["MAX_FILE_SIZE"])
    store.ensure_directories()
    app.extensions["audio_store"] = store

    init_db(app)
    generated_password = ensure_admin(app)
    if generated_password:
        app.logger.warning("首次启动管理员账号: %s", app.config["ADMIN_USERNAME"])
        app.logger.warning("首次启动管理员密码: %s", generated_password)
        app.logger.warning("请登录后尽快通过环境变量配置固定强密码并妥善保存。")

    from .routes.audio import audio_bp
    from .routes.auth import auth_bp
    from .routes.devices import devices_bp
    from .routes.pages import pages_bp
    from .routes.system import system_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(audio_bp)
    app.register_blueprint(devices_bp)
    app.register_blueprint(system_bp)
    app.teardown_appcontext(close_db)

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
        )
        if request.path.startswith("/api/auth") or request.path in {"/", "/login"}:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(413)
    def request_too_large(_error):
        return jsonify(error="请求过大，单个音频不能超过 32 MB"), 413

    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api/"):
            return jsonify(error="接口不存在"), 404
        return "Not Found", 404

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok", service="audio-hub")

    return app

#!/usr/bin/env python3
"""Audio Hub launcher."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from audio_hub import create_app


BASE_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audio Hub device and audio console")
    parser.add_argument("--host", default="0.0.0.0", help="listen address")
    parser.add_argument("--port", type=int, default=5000, help="listen port")
    parser.add_argument(
        "--dir",
        dest="audio_dir",
        default=str(BASE_DIR / "audio_files"),
        help="audio storage directory",
    )
    parser.add_argument(
        "--data-dir",
        default=str(BASE_DIR / "data"),
        help="database and secret-key directory",
    )
    parser.add_argument(
        "--production",
        action="store_true",
        help="serve with Waitress instead of Flask's development server",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = create_app(
        {
            "AUDIO_DIR": str(Path(args.audio_dir).resolve()),
            "DATA_DIR": str(Path(args.data_dir).resolve()),
        }
    )

    print("=" * 62)
    print("  Audio Hub — 智能玩具设备与音频管理平台")
    print(f"  地址: http://{args.host}:{args.port}")
    print(f"  音频: {app.config['AUDIO_DIR']}")
    print(f"  数据: {app.config['DATABASE']}")
    print("=" * 62)

    if args.production:
        try:
            from waitress import serve
        except ImportError as exc:  # pragma: no cover - depends on deployment extras
            raise SystemExit("生产模式需要安装依赖：python3 -m pip install waitress") from exc
        serve(app, host=args.host, port=args.port, threads=8)
    else:
        app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()

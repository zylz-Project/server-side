from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.request

from flask import Blueprint, jsonify, request

from ..security import api_error, csrf_protect, login_required


system_bp = Blueprint("system", __name__, url_prefix="/api")


def _device_ip() -> str:
    value = request.args.get("ip", "").strip()
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError("请输入有效的设备 IP 地址") from exc
    if address.is_unspecified or address.is_multicast or address.is_reserved:
        raise ValueError("不允许访问未指定、组播或保留地址")
    if not (address.is_private or address.is_loopback or address.is_link_local):
        raise ValueError("只允许访问局域网设备地址")
    return f"[{address}]" if address.version == 6 else str(address)


def _open_device(path: str, method: str = "GET") -> bytes:
    ip = _device_ip()
    req = urllib.request.Request(f"http://{ip}{path}", method=method)
    with urllib.request.urlopen(req, timeout=8) as response:
        data = response.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError("设备响应过大")
    return data


@system_bp.get("/proxy-flash")
@login_required
def proxy_flash_status():
    try:
        payload = json.loads(_open_device("/api/flash/status").decode("utf-8"))
        return jsonify(payload)
    except ValueError as exc:
        return api_error(str(exc))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return api_error(f"无法连接设备：{exc}", 502)


@system_bp.post("/proxy-flash-erase")
@login_required
@csrf_protect
def proxy_flash_erase():
    try:
        payload = _open_device("/api/flash/erase", method="POST").decode("utf-8")
        return jsonify(ok=True, message=payload)
    except ValueError as exc:
        return api_error(str(exc))
    except (urllib.error.URLError, TimeoutError) as exc:
        return api_error(f"无法连接设备：{exc}", 502)

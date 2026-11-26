"""Access the developer console on a registered LAN device."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import json
import re
import time
from collections import OrderedDict
from threading import Lock


LAN_NETWORKS = tuple(ipaddress.IPv4Network(block) for block in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
VOICE_ID = re.compile(r"[!-~]{1,128}")
NATIVE_VOICE = "xiaozhi-native"
CLOUD_VOICE_PREFIX = "qwen-audio-3.1-tts-flash-"
MANAGEMENT_STATUSES = {"idle", "ok", "busy", "invalid", "config", "network", "full", "save", "cancelled", "uncertain", "memory"}
ENROLLMENT_STATUSES = {"idle", "starting", "prompt", "recording", "processing", "success", "failed", "cancelled"}
PREVIEW_STATUSES = {"idle", "queued", "playing", "complete", "failed", "cancelled"}


class DeviceVoiceError(Exception):
    def __init__(self, message: str, status: int, code: str):
        super().__init__(message)
        self.status = status
        self.code = code


def normalize_mac(uid: object) -> str | None:
    if not isinstance(uid, str):
        return None
    compact = uid.strip().upper().removeprefix("ZYLZ-").replace(":", "").replace("-", "")
    return compact if re.fullmatch(r"[0-9A-F]{12}", compact) else None


def lan_ipv4(value: str | None) -> str | None:
    try:
        address = ipaddress.IPv4Address(value or "")
    except ipaddress.AddressValueError:
        return None
    return str(address) if any(address in network for network in LAN_NETWORKS) else None


def voice_prefix(mac: str) -> str:
    # Same fixed-width base-36 encoding as the firmware's VoicePrefixFromMac.
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    value = int(mac, 16)
    encoded = []
    for _ in range(10):
        value, digit = divmod(value, 36)
        encoded.append(digits[digit])
    return CLOUD_VOICE_PREFIX + "".join(reversed(encoded)) + "-"


def _request_json(connection, method: str, path: str, payload=None, headers=None) -> tuple[int, dict]:
    # Fixed destinations and direct HTTP connections avoid following redirects
    # or forwarding a device password through a host-wide HTTP proxy.
    request_headers = {"Accept": "application/json", **(headers or {})}
    body = None
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        connection.request(method, path, body, request_headers)
        with connection.getresponse() as response:
            status = response.status
            limit = 64 * 1024
            raw = response.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("response too large")
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError):
            data = {}
        return status, data if isinstance(data, dict) else {}
    finally:
        connection.close()


class DeviceVoiceSessions:
    def __init__(self):
        self._entries = OrderedDict()
        self._lock = Lock()

    def get(self, identity: tuple, password_hash: bytes) -> str:
        with self._lock:
            entry = self._entries.get(identity)
            if entry and entry[2] > time.monotonic() and hmac.compare_digest(entry[0], password_hash):
                self._entries.move_to_end(identity)
                return entry[1]
        return ""

    def put(self, identity: tuple, password_hash: bytes, token: str, lifetime: int) -> None:
        with self._lock:
            self._entries[identity] = (password_hash, token, time.monotonic() + lifetime)
            self._entries.move_to_end(identity)
            if len(self._entries) > 128:
                self._entries.popitem(last=False)


class DeviceVoiceClient:
    def __init__(self, address: str, mac: str | None, password: str, sessions: DeviceVoiceSessions):
        address = lan_ipv4(address)
        if address is None:
            raise DeviceVoiceError("设备需要连接同一局域网并向 Audio Hub 上报 Wi-Fi 地址", 409, "device_lan_required")
        self.address = address
        self.mac = mac
        self.password = password
        self.password_hash = hashlib.sha256(password.encode("utf-8")).digest()
        self.sessions = sessions
        self.identity = (self.address, self.mac)
        self.token = sessions.get(self.identity, self.password_hash)

    def _request(self, method: str, path: str, payload=None, *, authenticated=True) -> tuple[int, dict]:
        connection = http.client.HTTPConnection(self.address, 8080, timeout=5)
        headers = {"X-Developer-Token": self.token} if authenticated else {}
        try:
            return _request_json(connection, method, path, payload, headers)
        except (OSError, ValueError, http.client.HTTPException):
            raise DeviceVoiceError("无法连接设备开发者页面，请确认设备仍在同一 Wi-Fi", 503, "device_unreachable") from None

    def _login(self) -> None:
        status, body = self._request("POST", "/api/developer/login", {"password": self.password}, authenticated=False)
        if status == 429:
            raise DeviceVoiceError("设备开发者登录尝试过多，请稍后重试", 429, "device_login_rate_limited")
        if status in (401, 403):
            raise DeviceVoiceError("设备拒绝出厂连接，请核对当前固件和本机设备授权", 403, "device_login_failed")
        token = body.get("token")
        if status != 200 or not isinstance(token, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", token):
            raise DeviceVoiceError("设备未返回可用的开发者会话，请检查设备固件", 502, "device_login_unavailable")
        self.token = token

    def read_device(self) -> dict:
        if self.token:
            status, body = self._request("GET", "/api/developer/status")
        else:
            status, body = 401, {}
        logged_in = status in (401, 403)
        if logged_in:
            self._login()
            status, body = self._request("GET", "/api/developer/status")
        if status != 200:
            raise DeviceVoiceError("无法读取设备状态，请稍后重试", 502, "device_status_unavailable")
        reported_mac = normalize_mac(body.get("sta_mac"))
        if reported_mac is None or (self.mac is not None and reported_mac != self.mac):
            raise DeviceVoiceError("设备 IP 对应的 MAC 与登记记录不符，请等待设备重新上报", 409, "device_identity_mismatch")
        self.mac = reported_mac
        self.identity = (self.address, reported_mac)
        if logged_in:
            self.sessions.put(self.identity, self.password_hash, self.token, 25 * 60)
        return body

    def read_status(self) -> dict:
        body = self.read_device()
        if not isinstance(body.get("ali_key_configured"), bool) or not isinstance(body.get("voice_catalog"), list):
            raise DeviceVoiceError("设备固件未提供音色管理接口", 409, "device_voice_unsupported")
        return self._voice_status(body)

    def _voice_status(self, body: dict) -> dict:
        prefix = voice_prefix(self.mac)
        configured = body["ali_key_configured"]
        selected = body.get("ali_voice_id")
        current = selected if configured and isinstance(selected, str) and VOICE_ID.fullmatch(selected) else NATIVE_VOICE
        voices = []
        for voice in body["voice_catalog"][:32]:
            if not isinstance(voice, dict):
                continue
            voice_id, name = voice.get("id"), voice.get("name")
            if not isinstance(voice_id, str) or not VOICE_ID.fullmatch(voice_id) or not isinstance(name, str):
                continue
            builtin = voice.get("builtin") is True and not voice_id.startswith(CLOUD_VOICE_PREFIX)
            if voice_id != NATIVE_VOICE and (not configured or (not builtin and not voice_id.startswith(prefix))):
                continue
            voices.append({"id": voice_id, "name": name[:64], "builtin": builtin, "current": voice_id == current})
        return {
            "has_key": configured,
            "voices": voices,
            "catalog_loaded": configured and body.get("voice_management_loaded") is True,
            "busy": body.get("voice_management_busy") is True,
            "management_status": self._phase(body, "voice_management_status", MANAGEMENT_STATUSES),
            "enrollment_status": self._phase(body, "voice_enrollment_status", ENROLLMENT_STATUSES),
            "preview_status": self._phase(body, "voice_preview_status", PREVIEW_STATUSES),
            "current_voice": current,
        }

    @staticmethod
    def _phase(body: dict, field: str, allowed: set[str]) -> str:
        value = body.get(field)
        return value if isinstance(value, str) and value in allowed else "idle"

    def _write(self, path: str, payload: dict, accepted: str = "ok") -> None:
        status, body = self._request("POST", path, payload)
        if status in (401, 403):
            raise DeviceVoiceError("设备开发者会话已失效，请重试", 409, "device_session_expired")
        if status not in (200, 202) or body.get(accepted) is not True:
            raise DeviceVoiceError("设备未接受操作，请在设备空闲后重试", 409, "device_operation_rejected")

    def configure(self, kind: str, value: str) -> None:
        self._write("/api/developer/voice-config", {"kind": kind, "value": value})

    def manage(self, action: str, voice_id: str = "", name: str = "") -> None:
        self._write("/api/developer/voice-manage", {"action": action, "id": voice_id, "name": name})

    def set_volume(self, volume: int) -> None:
        self._write("/api/developer/volume", {"volume": volume}, accepted="accepted")

    def check_audio(self) -> None:
        self._write("/api/hub/check", {})

    def write_audio(self, names: list[str]) -> None:
        self._write("/api/hub/update", {"names": names})

    def audio_status(self) -> dict:
        status, body = self._request("GET", "/api/hub/status")
        hub = body.get("hub")
        if status != 200 or not isinstance(hub, dict) or hub.get("available") is not True:
            raise DeviceVoiceError("设备未提供出厂音频接口，请检查固件和外置 Flash", 409, "device_audio_unavailable")
        running = hub.get("job_running") is True
        revision, files = "", []
        if not running and hub.get("has_diff") is True:
            status, diff = self._request("GET", "/api/hub/diff")
            if status != 200:
                raise DeviceVoiceError("读取设备音频差异失败", 502, "device_audio_diff_unavailable")
            if diff.get("ready") is True and isinstance(diff.get("revision"), str) and isinstance(diff.get("items"), list):
                revision = diff["revision"][:128]
                for item in diff["items"][:64]:
                    if (isinstance(item, dict) and isinstance(item.get("name"), str) and
                        item.get("state") in ("new", "changed", "same", "local")):
                        files.append({"name": item["name"][:64], "state": item["state"]})
        message = hub.get("job_message")
        return {"running": running, "ok": hub.get("job_ok") is True,
                "message": message[:240] if isinstance(message, str) else "",
                "revision": revision, "files": files}


def check_aliyun_key(key: str) -> None:
    connection = http.client.HTTPSConnection("dashscope.aliyuncs.com", timeout=8)
    payload = {"model": "voice-enrollment", "input": {
        "action": "list_voice", "prefix": "wzhhealth", "page_size": 1, "page_index": 0,
    }}
    try:
        status, data = _request_json(connection, "POST", "/api/v1/services/audio/tts/customization", payload,
                                     {"Authorization": "Bearer " + key})
    except (OSError, ValueError, http.client.HTTPException):
        raise DeviceVoiceError("Audio Hub 暂时无法连接阿里云百炼", 503, "voice_provider_unreachable") from None
    output = data.get("output")
    if status == 200 and isinstance(output, dict) and isinstance(output.get("voice_list"), list):
        return
    if data.get("code") == "Arrearage":
        raise DeviceVoiceError("此 Key 所属阿里云账号欠费，请在费用与成本核对账单", 422, "aliyun_arrearage")
    if status in (401, 403):
        raise DeviceVoiceError("阿里云拒绝访问，请核对 Key、北京地域和音色服务权限", 422, "voice_provider_denied")
    raise DeviceVoiceError("阿里云音色列表查询失败，请检查语音服务后重试", 502, "voice_provider_failed")

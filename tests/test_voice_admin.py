from __future__ import annotations

import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Lock
from unittest.mock import patch

from audio_hub import create_app
from audio_hub.database import get_db
from audio_hub.device_voice import voice_prefix


ADMIN = "/api/admin/voice"
SECRET = "sk-fixture-example-12345678"
OTHER_SECRET = "sk-fixture-other-87654321"
DEVICE_PASSWORD = "fixture-only"


class DeviceNetwork:
    def __init__(self):
        self.boards = {}
        self.writes = []
        self.factory_calls = []
        self.drop_reply = False
        self.provider_status = (200, {"output": {"voice_list": []}})
        self.lock = Lock()

    def request(self, connection, method, path, payload, headers):
        with self.lock:
            if connection.host == "dashscope.aliyuncs.com":
                assert headers["Authorization"] == f"Bearer {SECRET}"
                assert payload["input"]["action"] == "list_voice"
                return self.provider_status
            board = self.boards[connection.host]
            if path == "/api/developer/login":
                return (200, {"token": "a" * 32}) if payload == {"password": DEVICE_PASSWORD} else (403, {})
            assert headers["X-Developer-Token"] == "a" * 32
            if method == "GET" and path == "/api/developer/status":
                return 200, {
                    "sta_mac": board["mac"], "ali_key_configured": bool(board["key"]),
                    "build": board["build"], "transport": board.get("transport", "wifi"),
                    "audio": {"volume": board["volume"]}, "flash": {"files": 2},
                    "ali_voice_id": board["selected"], "voice_catalog": board["voices"],
                    "voice_management_loaded": True, "voice_management_status": "ok",
                    "voice_enrollment_status": board.get("enrollment", "idle"),
                    "voice_preview_status": board.get("preview", "idle"),
                }
            if path == "/api/developer/volume":
                board["volume"] = payload["volume"]
                self.factory_calls.append((path, payload))
                return 202, {"accepted": True}
            if path == "/api/hub/status":
                return 200, {"hub": {"available": True, "job_running": False, "job_ok": True,
                                     "has_diff": "diff" in board, "job_message": "检查完成"}}
            if path == "/api/hub/diff":
                return 200, board["diff"]
            if path in ("/api/hub/check", "/api/hub/update"):
                self.factory_calls.append((path, payload))
                return 202, {"ok": True}
            if path == "/api/developer/voice-config":
                if payload["kind"] == "ali_key":
                    board["key"] = payload["value"]
                    self.writes.append(connection.host)
                    if self.drop_reply:
                        self.drop_reply = False
                        raise ConnectionResetError()
                if payload["kind"] == "ali_voice":
                    board["selected"] = payload["value"]
                elif payload["kind"] == "ali_preview":
                    board["preview"] = "complete"
                elif payload["kind"] == "device_record":
                    new_voice = voice_prefix(board["mac"]) + "recorded"
                    board["voices"].append({"id": new_voice, "name": payload["value"], "builtin": False})
                    board["selected"] = new_voice
                    board["enrollment"] = "success"
                return 200, {"ok": True}
            if path == "/api/developer/voice-manage":
                action, voice_id = payload["action"], payload["id"]
                if action == "select":
                    board["selected"] = voice_id
                elif action == "rename":
                    for voice in board["voices"]:
                        if voice["id"] == voice_id:
                            voice["name"] = payload["name"]
                elif action == "delete":
                    board["voices"] = [voice for voice in board["voices"] if voice["id"] != voice_id]
                return 200, {"ok": True}
            raise AssertionError(path)


class VoiceAdminTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = {
            "TESTING": True, "AUDIO_DIR": str(self.root / "audio"), "DATA_DIR": str(self.root / "data"),
            "DATABASE": str(self.root / "data" / "test.db"), "SECRET_KEY": "fixture-session-secret",
            "ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "fixture-admin-password",
            "DEVICE_DEVELOPER_PASSWORD": DEVICE_PASSWORD,
        }
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session.update(admin_id=1, csrf_token="fixture-csrf")
        self.csrf = {"X-CSRF-Token": "fixture-csrf"}
        self.network = DeviceNetwork()
        mock = patch("audio_hub.device_voice._request_json", side_effect=self.network.request)
        mock.start()
        self.addCleanup(mock.stop)

    def create_key(self, key=SECRET):
        response = self.client.post(f"{ADMIN}/keys", headers=self.csrf, json={"label": "北京账号", "key": key})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["id"]

    def create_device(self, serial, *, device_uid=None, ip=None):
        mac = f"02ABCDEF{serial:04X}"
        address = ip or f"192.168.50.{serial + 10}"
        product = ("dinosaur", "crawling-panda", "tail-wagging-panda")[serial % 3]
        response = self.client.post("/api/admin/devices", headers=self.csrf, json={
            "device_uid": device_uid or f"ZYLZ-{mac}", "name": f"设备 {serial}", "product_id": product,
        })
        self.assertEqual(response.status_code, 201, response.get_json())
        payload = response.get_json()
        token = payload["api_token"]
        response = self.app.test_client().post("/api/device/v1/check-in", json={"firmware_version": "2.4.1"},
                                               headers={"Authorization": f"Bearer {token}"},
                                               environ_overrides={"REMOTE_ADDR": address})
        self.assertEqual(response.status_code, 200)
        build = {"dinosaur": "wzh-dinosaur", "crawling-panda": "wzh-crawlpanda",
                 "tail-wagging-panda": "wzh-tailredpanda"}[product]
        self.network.boards.setdefault(address, {
            "build": build,
            "volume": 40,
            "key": "", "mac": mac, "selected": "xiaozhi-native", "voices": [
                {"id": "xiaozhi-native", "name": "原声", "builtin": True},
                {"id": voice_prefix(mac) + "sample", "name": f"独立音色 {serial}", "builtin": False},
            ],
        })
        return payload["device"]["id"], address

    def assign(self, device_id, key_id, client=None):
        return (client or self.client).put(f"{ADMIN}/devices/{device_id}/key", headers=self.csrf, json={"key_id": key_id})

    def test_pasted_key_wrappers_are_removed_before_saving(self):
        response = self.client.post(f"{ADMIN}/keys", headers=self.csrf, json={
            "label": "从配置复制",
            "key": f'\ufeff export DASHSCOPE_API_KEY = "{SECRET}"\n',
        })
        self.assertEqual(response.status_code, 201)
        key_id = response.get_json()["id"]
        checked = self.client.post(f"{ADMIN}/keys/{key_id}/check", headers=self.csrf)
        self.assertEqual(checked.status_code, 200)
        duplicate = self.client.post(f"{ADMIN}/keys", headers=self.csrf, json={
            "label": "从请求头复制", "key": f"Authorization: Bearer {SECRET}",
        })
        self.assertEqual(duplicate.get_json()["code"], "duplicate_voice_key")

    def test_wrong_credential_types_and_masked_values_explain_the_rejection(self):
        for value, code in (
            ("sk-••••1234", "masked_voice_key"),
            ("sk-…1234", "masked_voice_key"),
            ("st-fixture-example", "temporary_voice_key"),
            ("sk-sp-fixture-example", "plan_voice_key"),
            ("LTAIfixture", "access_key_supplied"),
            ("sk-fixture\r\nExtra-Header: value", "invalid_voice_key"),
        ):
            with self.subTest(code=code):
                response = self.client.post(f"{ADMIN}/keys", headers=self.csrf,
                                            json={"label": "测试凭据", "key": value})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()["code"], code)
        self.assertEqual(self.client.get(ADMIN).get_json()["keys"], [])

    def test_workbench_connect_and_write_require_admin_but_no_device_password(self):
        device_id, address = self.create_device(3)
        endpoint = f"/api/admin/devices/{device_id}/factory"
        self.assertEqual(self.app.test_client().get(endpoint).status_code, 401)
        self.assertEqual(self.client.put(endpoint + "/volume", json={"volume": 70}).status_code, 403)
        connect = self.client.post("/api/admin/devices/connect", headers=self.csrf, json={"ip": address})
        self.assertEqual(connect.status_code, 202)
        self.assertEqual(connect.get_json()["name"], "Dino-0004")
        self.assertEqual(self.client.get(endpoint).get_json(), {"volume": 40, "audio_files": 2})
        self.assertEqual(self.client.put(endpoint + "/volume", headers=self.csrf, json={"volume": 0}).status_code, 202)
        self.assertEqual(self.client.get(endpoint).get_json()["volume"], 0)
        self.network.boards[address]["mac"] = "AABBCCDDEEFF"
        failure = self.client.put(endpoint + "/volume", headers=self.csrf, json={"volume": 90})
        self.assertEqual(failure.get_json()["code"], "device_identity_mismatch")
        self.assertEqual(self.network.boards[address]["volume"], 0)

    def test_workbench_writes_only_files_from_the_current_audio_diff(self):
        device_id, address = self.create_device(3)
        endpoint = f"/api/admin/devices/{device_id}/factory/audio"
        store = self.app.extensions["audio_store"]
        (store.directory("dinosaur", "animal") / "叫声.opus").write_bytes(b"fixture")
        revision = store.revision("dinosaur")
        self.network.boards[address]["diff"] = {
            "ready": True, "revision": revision,
            "items": [{"name": "叫声", "state": "changed"}, {"name": "设备录音", "state": "local"}],
        }
        self.assertEqual(self.client.post(endpoint, headers=self.csrf, json={"action": "check"}).status_code, 202)
        self.assertEqual(self.client.get(endpoint).get_json()["revision"], revision)
        wrong = self.client.post(endpoint, headers=self.csrf, json={
            "action": "write", "names": ["设备录音"], "revision": revision,
        })
        self.assertEqual(wrong.get_json()["code"], "device_audio_selection")
        response = self.client.post(endpoint, headers=self.csrf, json={
            "action": "write", "names": ["叫声"], "revision": revision,
        })
        self.assertEqual(response.status_code, 202)
        (store.directory("dinosaur", "animal") / "新增.opus").write_bytes(b"another")
        stale = self.client.post(endpoint, headers=self.csrf, json={
            "action": "write", "names": ["叫声"], "revision": revision,
        })
        self.assertEqual(stale.get_json()["code"], "device_audio_stale")
        updates = [body for path, body in self.network.factory_calls if path == "/api/hub/update"]
        self.assertEqual(updates, [{"names": ["叫声"]}])

    def test_admin_and_csrf_protect_keys_and_vault_survives_restart(self):
        anon = self.app.test_client()
        self.assertEqual(anon.get(ADMIN).status_code, 401)
        self.assertEqual(anon.post(f"{ADMIN}/keys", json={"label": "a", "key": SECRET}).status_code, 401)
        self.assertEqual(self.client.post(f"{ADMIN}/keys", json={"label": "a", "key": SECRET}).status_code, 403)
        key_id = self.create_key()
        duplicate = self.client.post(f"{ADMIN}/keys", headers=self.csrf, json={"label": "重复", "key": SECRET})
        self.assertEqual(duplicate.get_json()["code"], "duplicate_voice_key")
        dashboard = self.client.get(ADMIN)
        self.assertEqual(dashboard.headers["Cache-Control"], "no-store")
        self.assertNotIn(SECRET, dashboard.get_data(as_text=True))
        self.assertEqual(dashboard.get_json()["keys"][0]["suffix"], SECRET[-4:])
        self.assertEqual(os.stat(self.root / "data" / "voice-vault.key").st_mode & 0o777, 0o600)
        with self.app.app_context():
            raw = get_db().execute("SELECT encrypted_key FROM voice_keys WHERE id=?", (key_id,)).fetchone()[0]
            self.assertNotIn(SECRET.encode(), raw)
        app = create_app(self.config)
        client = app.test_client()
        with client.session_transaction() as session:
            session.update(admin_id=1, csrf_token="fixture-csrf")
        self.assertEqual(client.post(f"{ADMIN}/keys/{key_id}/check", headers=self.csrf).status_code, 200)

    def test_seven_concurrent_devices_reserve_only_five_slots(self):
        key_id = self.create_key()
        devices = [self.create_device(i)[0] for i in range(1, 8)]
        barrier = Barrier(len(devices))

        def bind(device_id):
            client = self.app.test_client()
            with client.session_transaction() as session:
                session.update(admin_id=1, csrf_token="fixture-csrf")
            barrier.wait(timeout=5)
            return self.assign(device_id, key_id, client).status_code

        with ThreadPoolExecutor(max_workers=len(devices)) as pool:
            codes = list(pool.map(bind, devices))
        self.assertCountEqual(codes, [200] * 5 + [409] * 2)
        self.assertEqual(len(set(self.network.writes)), 5)
        self.assertEqual(self.client.get(ADMIN).get_json()["keys"][0]["count"], 5)

    def test_lost_reply_does_not_release_reserved_slot_and_retry_is_idempotent(self):
        key_id = self.create_key()
        device_id, address = self.create_device(1)
        replacement = self.create_key(OTHER_SECRET)
        self.network.drop_reply = True
        failed = self.assign(device_id, key_id)
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(self.network.boards[address]["key"], SECRET)
        device = self.client.get(ADMIN).get_json()["devices"][0]
        self.assertEqual(device["key_state"], "pending")
        self.assertEqual(self.assign(device_id, replacement).status_code, 409)
        self.assertEqual(self.assign(device_id, key_id).status_code, 200)
        self.assertEqual(self.client.delete(f"{ADMIN}/keys/{key_id}", headers=self.csrf).status_code, 409)
        self.assertEqual(self.client.delete(f"/api/admin/devices/{device_id}", headers=self.csrf).status_code, 409)

        self.network.drop_reply = True
        clear = f"{ADMIN}/devices/{device_id}/key"
        self.assertEqual(self.client.delete(clear, headers=self.csrf).status_code, 503)
        self.assertEqual(self.client.get(ADMIN).get_json()["devices"][0]["key_state"], "clearing")
        self.assertEqual(self.client.delete(f"{ADMIN}/keys/{key_id}", headers=self.csrf).status_code, 409)
        self.assertEqual(self.client.delete(clear, headers=self.csrf).status_code, 200)
        self.assertEqual(self.client.delete(f"{ADMIN}/keys/{key_id}", headers=self.csrf).status_code, 200)
        self.assertEqual(self.assign(device_id, replacement).status_code, 200)
        self.assertEqual(self.network.boards[address]["key"], OTHER_SECRET)

    def test_mac_mismatch_and_duplicate_registration_cannot_write_key(self):
        key_id = self.create_key()
        device_id, address = self.create_device(1)
        self.network.boards[address]["mac"] = "02ABCDEF0002"
        response = self.assign(device_id, key_id)
        self.assertEqual(response.get_json()["code"], "device_identity_mismatch")
        self.assertFalse(self.network.writes)
        self.network.boards[address]["mac"] = "02ABCDEF0001"
        self.assertEqual(self.assign(device_id, key_id).status_code, 200)
        alias, _ = self.create_device(1, device_uid="02:AB:CD:EF:00:01")
        self.assertEqual(self.assign(alias, key_id).get_json()["code"], "voice_device_already_assigned")
        self.assertEqual(self.network.writes, [address])

    def test_two_devices_sharing_key_manage_only_own_voices(self):
        key_id = self.create_key()
        first, first_ip = self.create_device(1)
        second, second_ip = self.create_device(2)
        for device in (first, second):
            self.assertEqual(self.assign(device, key_id).status_code, 200)
        first_voice = self.network.boards[first_ip]["voices"][1]["id"]
        second_voice = self.network.boards[second_ip]["voices"][1]["id"]
        foreign = {**self.network.boards[second_ip]["voices"][1], "builtin": True}
        self.network.boards[first_ip]["voices"].append(foreign)

        def catalog(device, action, **data):
            return self.client.post(f"{ADMIN}/devices/{device}/catalog", headers=self.csrf, json={"action": action, **data})

        voices = catalog(first, "status").get_json()["voices"]
        self.assertEqual([voice["id"] for voice in voices], ["xiaozhi-native", first_voice])
        self.assertEqual(catalog(first, "delete", id=second_voice).status_code, 404)
        self.assertEqual(catalog(first, "rename", id=first_voice, name="第一台").status_code, 202)
        self.assertEqual(catalog(first, "select", id=first_voice).status_code, 202)
        self.assertEqual(catalog(first, "status").get_json()["current_voice"], first_voice)
        self.assertEqual(catalog(second, "status").get_json()["voices"][1]["name"], "独立音色 2")
        self.assertEqual(catalog(first, "original").status_code, 200)
        self.assertEqual(catalog(first, "delete", id=first_voice).status_code, 202)
        self.assertEqual(len(catalog(first, "status").get_json()["voices"]), 1)
        self.assertEqual(len(catalog(second, "status").get_json()["voices"]), 2)

    def test_key_check_keeps_aliyun_account_error_without_echoing_secret(self):
        key_id = self.create_key()
        self.network.provider_status = (400, {"code": "Arrearage", "message": SECRET})
        response = self.client.post(f"{ADMIN}/keys/{key_id}/check", headers=self.csrf)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["code"], "aliyun_arrearage")
        self.assertIn("欠费", response.get_json()["error"])
        self.assertNotIn(SECRET, response.get_data(as_text=True))

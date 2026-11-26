from __future__ import annotations

import io
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier

from audio_hub import create_app
from audio_hub.catalog import AudioStoreError
from audio_hub.database import get_db
from audio_hub.discovery import DISCOVERY_PREFIX, announcement, discovery_proof
from audio_hub.routes.system import _device_ip


class AudioHubTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.app = create_app(
            {
                "TESTING": True,
                "AUDIO_DIR": str(root / "audio"),
                "DATA_DIR": str(root / "data"),
                "DATABASE": str(root / "data" / "test.db"),
                "SECRET_KEY": "test-secret",
                "ADMIN_USERNAME": "admin",
                "ADMIN_PASSWORD": "test-password-123",
                "MAX_FILE_SIZE": 1024,
                "DEVICE_AUTO_ACTIVATE": True,
            }
        )
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def csrf(self) -> str:
        with self.client.session_transaction() as session:
            return session["csrf_token"]

    def login(self) -> str:
        self.client.get("/login")
        response = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "test-password-123"},
            headers={"X-CSRF-Token": self.csrf()},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()["csrf_token"]

    def test_admin_login_and_csrf_protection(self) -> None:
        response = self.client.get("/api/admin/devices")
        self.assertEqual(response.status_code, 401)

        self.client.get("/login")
        response = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong"},
            headers={"X-CSRF-Token": self.csrf()},
        )
        self.assertEqual(response.status_code, 401)

        self.login()
        response = self.client.post(
            "/api/admin/devices",
            json={
                "device_uid": "ESP32-001",
                "product_id": "dinosaur",
                "name": "测试恐龙",
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_same_lan_device_registers_and_updates_ip_without_an_admin_action(self) -> None:
        credential = "test-factory-credential"
        self.app.config["DEVICE_DEVELOPER_PASSWORD"] = credential
        nonce = "0123456789abcdef" * 2
        datagram = announcement(credential, "192.168.1.7", 5000).decode("ascii")
        message, proof = datagram.rsplit(" ", 1)
        self.assertEqual(message, f"{DISCOVERY_PREFIX}192.168.1.7 5000")
        self.assertNotEqual(proof, discovery_proof(credential, f"{DISCOVERY_PREFIX}192.168.1.8 5000"))

        peer = {"REMOTE_ADDR": "192.168.1.80"}
        discovery = self.client.get(f"/api/device/discovery?nonce={nonce}", environ_overrides=peer)
        self.assertEqual(discovery.status_code, 200)
        self.assertEqual(discovery.get_data(as_text=True),
                         discovery_proof(credential, DISCOVERY_PREFIX + "verify " + nonce))
        self.assertEqual(self.client.get(f"/api/device/discovery?nonce={nonce}").status_code, 404)
        self.assertEqual(self.client.get("/api/device/discovery?nonce=bad", environ_overrides=peer).status_code, 404)

        identity = {"device_id": "ZYLZ-102030401234", "product_id": "dinosaur"}
        registered = self.client.post("/api/device/register", json=identity, environ_overrides=peer)
        self.assertEqual(registered.status_code, 201)
        token = registered.get_json()["api_token"]
        for ip in ("192.168.1.80", "192.168.1.81"):
            check = self.client.post("/api/device/v1/check-in", json=identity,
                                     headers={"Authorization": f"Bearer {token}"},
                                     environ_overrides={"REMOTE_ADDR": ip})
            self.assertEqual(check.status_code, 200)

        self.login()
        devices = self.client.get("/api/admin/devices").get_json()["devices"]
        self.assertEqual([(row["name"], row["ip_address"], row["online"]) for row in devices],
                         [("Dino-1235", "192.168.1.81", True)])
        self.assertNotIn('id="connect-device-form"', self.client.get("/").get_data(as_text=True))

    def test_short_password_is_allowed_and_dialogs_can_close(self) -> None:
        csrf_token = self.login()
        response = self.client.post(
            "/api/auth/change-password",
            json={
                "current_password": "test-password-123",
                "new_password": "1",
            },
            headers={"X-CSRF-Token": csrf_token},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

        dashboard = self.client.get("/")
        html = dashboard.get_data(as_text=True)
        self.assertIn(
            'type="button" data-close-dialog="add-device-modal"',
            html,
        )
        self.assertNotIn('minlength="10"', html)

        response = self.client.post(
            "/api/auth/logout",
            headers={"X-CSRF-Token": csrf_token},
        )
        self.assertEqual(response.status_code, 200)
        self.client.get("/login")
        response = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "1"},
            headers={"X-CSRF-Token": self.csrf()},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

    def test_audio_upload_and_legacy_manifest(self) -> None:
        csrf_token = self.login()
        response = self.client.post(
            "/api/upload",
            data={
                "product": "tail-wagging-panda",
                "category": "animal",
                "file": (io.BytesIO(b"opus-data"), "熊猫叫.opus"),
            },
            headers={"X-CSRF-Token": csrf_token},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))

        response = self.client.get("/api/files?product=tail-wagging-panda")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["files"][0]["name"], "熊猫叫.opus")
        self.assertEqual(payload["files"][0]["category"], "animal")
        self.assertIn("熊猫叫.opus".encode("utf-8"), response.data)
        self.assertNotIn(b"\\u718a", response.data)
        item_start = response.data.index(b'"index":0')
        name_position = response.data.index(b'"name":', item_start)
        size_position = response.data.index(b'"size":', name_position)
        category_position = response.data.index(b'"category":', size_position)
        self.assertLess(name_position, size_position)
        self.assertLess(size_position, category_position)

        summary = self.client.get(
            "/api/summary?product=tail-wagging-panda"
        )
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.get_json()["total_size"], len(b"opus-data"))
        dashboard = self.client.get("/").get_data(as_text=True)
        self.assertIn('id="audio-total-size"', dashboard)

        download = self.client.get(
            "/api/download-idx/0?product=tail-wagging-panda"
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.data, b"opus-data")
        download.close()

    def test_rejects_bad_audio_names_and_oversized_files(self) -> None:
        csrf_token = self.login()
        for filename, content in [
            ("not-opus.mp3", b"data"),
            ('bad"name.opus', b"data"),
            ("很长的中文音频文件名称超过设备端所能保存的最大长度限制.opus", b"data"),
            ("large.opus", b"x" * 1025),
        ]:
            with self.subTest(filename=filename):
                response = self.client.post(
                    "/api/upload",
                    data={
                        "product": "dinosaur",
                        "category": "animal",
                        "file": (io.BytesIO(content), filename),
                    },
                    headers={"X-CSRF-Token": csrf_token},
                    content_type="multipart/form-data",
                )
                self.assertEqual(response.status_code, 400)

    def test_audio_names_are_device_safe_across_categories(self) -> None:
        csrf_token = self.login()
        first = self.client.post(
            "/api/upload",
            data={
                "product": "dinosaur",
                "category": "animal",
                "file": (io.BytesIO(b"first"), "SOUND.OPUS"),
            },
            headers={"X-CSRF-Token": csrf_token},
            content_type="multipart/form-data",
        )
        self.assertEqual(first.status_code, 201, first.get_data(as_text=True))
        self.assertEqual(first.get_json()["files"][0]["name"], "SOUND.opus")

        duplicate = self.client.post(
            "/api/upload",
            data={
                "product": "dinosaur",
                "category": "ambient",
                "file": (io.BytesIO(b"second"), "SOUND.opus"),
            },
            headers={"X-CSRF-Token": csrf_token},
            content_type="multipart/form-data",
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn("另一个分类已存在同名文件", duplicate.get_json()["error"])

    def test_manual_device_and_authenticated_check_in(self) -> None:
        csrf_token = self.login()
        response = self.client.post(
            "/api/admin/devices",
            json={
                "device_uid": "AA:BB:CC:00:00:01",
                "product_id": "dinosaur",
                "name": "办公室恐龙",
            },
            headers={"X-CSRF-Token": csrf_token},
        )
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        token = response.get_json()["api_token"]

        response = self.client.post(
            "/api/device/v1/check-in",
            json={"firmware_version": "1.2.3", "battery_level": 82, "flash_free": 4096},
            headers={"Authorization": f"Bearer {token}"},
            environ_overrides={"REMOTE_ADDR": "192.168.1.98"},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.get_json()["device"]["product_id"], "dinosaur")
        devices = self.client.get("/api/admin/devices").get_json()["devices"]
        self.assertEqual(devices[0]["developer_url"], "http://192.168.1.98:8080/")
        self.assertEqual(devices[0]["ip_address"], "192.168.1.98")

        response = self.client.post(
            "/api/device/v1/check-in", json={},
            headers={"Authorization": f"Bearer {token}"},
            environ_overrides={"REMOTE_ADDR": "8.8.8.8"},
        )
        self.assertEqual(response.status_code, 200)
        devices = self.client.get("/api/admin/devices").get_json()["devices"]
        self.assertIsNone(devices[0]["developer_url"])

        response = self.client.get(
            "/api/device/v1/files",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 200)

    def test_auto_registration_downloads_without_admin_activation(self) -> None:
        for product in ("tail-wagging-panda", "crawling-panda", "dinosaur"):
            with self.subTest(product=product):
                store = self.app.extensions["audio_store"]
                (store.directory(product, "animal") / "test.opus").write_bytes(b"audio")
                response = self.client.post(
                    "/api/device/register",
                    json={"device_id": f"ESP32-{product}", "product_id": product},
                )
                self.assertEqual(response.status_code, 201)
                registration = response.get_json()
                self.assertEqual(registration["status"], "active")
                self.assertEqual(registration["activation_code"], "")
                headers = {"Authorization": f"Bearer {registration['api_token']}"}
                manifest = self.client.get("/api/device/v1/files", headers=headers)
                self.assertEqual(manifest.status_code, 200)
                self.assertEqual(manifest.get_json()["product"], product)
                download = self.client.get("/api/device/v1/download/0", headers=headers)
                self.assertEqual(download.status_code, 200)
                self.assertEqual(download.data, b"audio")
                download.close()
                # Reboots use the cached token without registering again.
                for _ in range(2):
                    self.assertEqual(self.client.post(
                        "/api/device/v1/check-in", json={}, headers=headers,
                    ).status_code, 200)
                with self.app.app_context():
                    row = get_db().execute(
                        "SELECT * FROM devices WHERE device_uid=?",
                        (f"ESP32-{product}".upper(),),
                    ).fetchone()
                    self.assertIsNone(row["activation_code"])
                    self.assertIsNotNone(row["activated_at"])
                    self.assertNotEqual(row["api_token_hash"], registration["api_token"])
        self.login()
        overview = self.client.get("/api/admin/overview").get_json()
        self.assertEqual(overview["devices"]["bound"], 3)
        self.assertEqual(overview["devices"]["pending"], 0)

    def test_reused_board_switches_products_with_cached_token(self) -> None:
        registration = self.client.post(
            "/api/device/register",
            json={"device_id": "ZYLZ-REUSED-BOARD", "product_id": "dinosaur"},
        ).get_json()
        headers = {"Authorization": f"Bearer {registration['api_token']}"}
        store = self.app.extensions["audio_store"]
        with self.app.app_context():
            original = dict(get_db().execute("SELECT * FROM devices").fetchone())
        # Round-trip between all products, including equal names and file sizes.
        for product in ("tail-wagging-panda", "crawling-panda", "dinosaur"):
            with self.subTest(product=product):
                content = product.encode().ljust(32, b".")
                (store.directory(product, "animal") / "same.opus").write_bytes(content)
                response = self.client.post(
                    "/api/device/v1/check-in", headers=headers,
                    json={"device_id": "zylz-reused-board", "product_id": product,
                          "firmware_version": "new-firmware"},
                )
                self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
                self.assertTrue(response.get_json()["product_changed"])
                self.assertEqual(response.get_json()["device"]["product_id"], product)
                manifest = self.client.get(
                    f"/api/device/v1/files?product={product}", headers=headers,
                ).get_json()
                self.assertEqual(manifest["product"], product)
                self.assertEqual(manifest["files"][0]["name"], "same.opus")
                download = self.client.get(
                    f"/api/device/v1/download/0?product={product}", headers=headers,
                )
                self.assertEqual(download.status_code, 200)
                self.assertEqual(download.data, content)
                download.close()
                # Reboots and legacy clients keep the new identity and token.
                for payload in ({"device_id": "ZYLZ-REUSED-BOARD", "product_id": product}, {}):
                    unchanged = self.client.post(
                        "/api/device/v1/check-in", json=payload, headers=headers,
                    )
                    self.assertEqual(unchanged.status_code, 200)
                    self.assertFalse(unchanged.get_json()["product_changed"])
                with self.app.app_context():
                    rows = get_db().execute("SELECT * FROM devices").fetchall()
                    self.assertEqual(len(rows), 1)
                    row = rows[0]
                    for field in ("id", "device_uid", "created_at", "activated_at", "api_token_hash"):
                        self.assertEqual(row[field], original[field])
                    self.assertEqual(row["name"], {"tail-wagging-panda": "摇尾熊猫",
                                                  "crawling-panda": "爬行熊猫",
                                                  "dinosaur": "恐龙"}[product])

    def test_product_switch_requires_matching_identity_and_valid_token(self) -> None:
        registration = self.client.post(
            "/api/device/register", json={"device_id": "ESP32-SWITCH", "product_id": "dinosaur"},
        ).get_json()
        headers = {"Authorization": f"Bearer {registration['api_token']}"}
        valid = {"device_id": "ESP32-SWITCH", "product_id": "tail-wagging-panda"}
        for payload, auth, status in (
            (valid, {}, 401),
            (valid, {"Authorization": "Bearer invalid"}, 401),
            ({**valid, "device_id": "OTHER-BOARD"}, headers, 409),
            ({**valid, "product_id": "unknown"}, headers, 400),
            ({"product_id": "tail-wagging-panda"}, headers, 400),
            ({"device_id": "ESP32-SWITCH"}, headers, 400),
            ({**valid, "battery_level": "invalid"}, headers, 400),
        ):
            with self.subTest(payload=payload, auth=bool(auth)):
                response = self.client.post("/api/device/v1/check-in", json=payload, headers=auth)
                self.assertEqual(response.status_code, status)
                with self.app.app_context():
                    row = get_db().execute("SELECT * FROM devices").fetchone()
                    self.assertEqual(row["product_id"], "dinosaur")
        for endpoint in ("files", "download/0"):
            response = self.client.get(
                f"/api/device/v1/{endpoint}?product=tail-wagging-panda", headers=headers,
            )
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.get_json()["code"], "product_mismatch")
        with self.app.app_context():
            get_db().execute("UPDATE devices SET status='disabled'")
            get_db().commit()
        response = self.client.post("/api/device/v1/check-in", json=valid, headers=headers)
        self.assertEqual(response.status_code, 403)
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT product_id FROM devices").fetchone()[0], "dinosaur")

    def test_product_switch_preserves_custom_name_in_manual_activation_mode(self) -> None:
        registration = self.client.post(
            "/api/device/register", json={"device_id": "ESP32-NAMED", "product_id": "dinosaur"},
        ).get_json()
        self.app.config["DEVICE_AUTO_ACTIVATE"] = False
        with self.app.app_context():
            get_db().execute("UPDATE devices SET name='实验室旧板'")
            get_db().commit()
        response = self.client.post(
            "/api/device/v1/check-in",
            headers={"Authorization": f"Bearer {registration['api_token']}"},
            json={"device_id": "ESP32-NAMED", "product_id": "tail-wagging-panda"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["device"]["name"], "实验室旧板")
        self.assertEqual(response.get_json()["device"]["product_id"], "tail-wagging-panda")

    def test_admin_workbench_and_voice_show_the_product_hotspot_name(self) -> None:
        self.login()
        cases = (
            ("ZYLZ-102030403ADC", "tail-wagging-panda", "RedPanda-3ADD"),
            ("ZYLZ-1020304099FF", "crawling-panda", "CrawlPanda-9900"),
            ("ZYLZ-102030401234", "dinosaur", "Dino-1235"),
        )
        for uid, product, _ in cases:
            response = self.client.post("/api/device/register", json={"device_id": uid, "product_id": product})
            self.assertEqual(response.status_code, 201)
        for endpoint in ("/api/admin/devices", "/api/admin/voice"):
            devices = self.client.get(endpoint).get_json()["devices"]
            by_uid = {device["device_uid"]: device for device in devices}
            for uid, _, hotspot in cases:
                with self.subTest(endpoint=endpoint, uid=uid):
                    self.assertEqual(by_uid[uid]["name"], hotspot)
                    self.assertEqual(by_uid[uid]["hotspot_name"], hotspot)

    def test_old_firmware_claim_works_with_auto_registration(self) -> None:
        registration = self.client.post(
            "/api/device/register",
            json={"device_id": "ESP32-LEGACY", "product_id": "dinosaur"},
        ).get_json()
        for _ in range(2):
            response = self.client.post(
                "/api/device/activate",
                json={"device_id": "ESP32-LEGACY", "claim_token": registration["claim_token"]},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["api_token"], registration["api_token"])

    def test_existing_pending_claim_is_automatically_activated(self) -> None:
        self.app.config["DEVICE_AUTO_ACTIVATE"] = False
        registration = self.client.post(
            "/api/device/register",
            json={"device_id": "ESP32-UPGRADE", "product_id": "dinosaur"},
        ).get_json()
        with self.app.app_context():
            get_db().execute("UPDATE devices SET activation_expires_at='2000-01-01T00:00:00+00:00'")
            get_db().commit()
        self.app.config["DEVICE_AUTO_ACTIVATE"] = True
        invalid = self.client.post(
            "/api/device/activate", json={"device_id": "ESP32-UPGRADE", "claim_token": "bad"},
        )
        self.assertEqual(invalid.status_code, 401)
        response = self.client.post(
            "/api/device/activate",
            json={"device_id": "ESP32-UPGRADE", "claim_token": registration["claim_token"]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "active")
        headers = {"Authorization": f"Bearer {response.get_json()['api_token']}"}
        self.assertEqual(self.client.get("/api/device/v1/files", headers=headers).status_code, 200)

    def test_auto_registration_recovers_credentials_and_respects_disabled_devices(self) -> None:
        payload = {"device_id": "ESP32-RECOVERY", "product_id": "dinosaur"}
        first = self.client.post("/api/device/register", json=payload).get_json()
        second = self.client.post("/api/device/register", json=payload)
        self.assertEqual(second.status_code, 200)
        headers = {"Authorization": f"Bearer {second.get_json()['api_token']}"}
        self.assertEqual(self.client.post("/api/device/v1/check-in", json={}, headers=headers).status_code, 200)
        wrong_product = self.client.post(
            "/api/device/register", json={**payload, "product_id": "crawling-panda"},
        )
        self.assertEqual(wrong_product.status_code, 409)
        csrf_token = self.login()
        devices = self.client.get("/api/admin/devices").get_json()["devices"]
        self.assertEqual(len(devices), 1)
        device_id = devices[0]["id"]
        self.client.patch(
            f"/api/admin/devices/{device_id}", json={"status": "disabled"},
            headers={"X-CSRF-Token": csrf_token},
        )
        self.assertEqual(self.client.post("/api/device/register", json=payload).status_code, 403)
        self.assertEqual(self.client.get("/api/device/v1/files", headers=headers).status_code, 403)
        claim = self.client.post(
            "/api/device/activate",
            json={"device_id": payload["device_id"], "claim_token": first["claim_token"]},
        )
        self.assertEqual(claim.status_code, 401)

    def test_dashboard_reflects_registration_mode(self) -> None:
        self.login()
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("设备工作台", html)
        self.assertIn("设备将在列表中自动出现", html)
        self.assertNotIn('id="connect-device-form"', html)
        self.assertNotIn('id="voice-device-password"', html)
        self.assertIn('id="open-device-developer"', html)
        self.assertNotIn('data-action="open-activate"', html)
        self.assertNotIn('id="activate-form"', html)
        self.app.config["DEVICE_AUTO_ACTIVATE"] = False
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('data-action="open-activate"', html)
        self.assertIn('id="activate-form"', html)

    def test_auto_reregistration_promotes_pending_and_disabled_claim_stays_blocked(self) -> None:
        self.app.config["DEVICE_AUTO_ACTIVATE"] = False
        payload = {"device_id": "ESP32-PENDING-RECOVER", "product_id": "dinosaur"}
        registration = self.client.post("/api/device/register", json=payload).get_json()
        self.app.config["DEVICE_AUTO_ACTIVATE"] = True
        promoted = self.client.post("/api/device/register", json=payload)
        self.assertEqual(promoted.status_code, 200)
        self.assertEqual(promoted.get_json()["status"], "active")
        csrf_token = self.login()
        devices = self.client.get("/api/admin/devices").get_json()["devices"]
        self.assertEqual(len(devices), 1)
        self.client.patch(
            f"/api/admin/devices/{devices[0]['id']}", json={"status": "disabled"},
            headers={"X-CSRF-Token": csrf_token},
        )
        blocked = self.client.post(
            "/api/device/activate",
            json={"device_id": payload["device_id"], "claim_token": promoted.get_json()["claim_token"]},
        )
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.get_json()["code"], "device_disabled")

    def test_audio_snapshot_tracks_external_changes_and_isolates_callers(self) -> None:
        store = self.app.extensions["audio_store"]
        animal = store.directory("dinosaur", "animal") / "a.opus"
        ambient = store.directory("dinosaur", "ambient") / "b.opus"
        animal.write_bytes(b"first")
        ambient.write_bytes(b"ambient")
        full = store.listing("dinosaur", include_summary=True)
        category = store.listing("dinosaur", "ambient")
        self.assertEqual([item["index"] for item in full["files"]], [0, 1])
        self.assertEqual(category["files"][0]["index"], 0)
        self.assertEqual(category["revision"], full["revision"])
        self.assertEqual(full["summary"]["total_size"], 12)
        full["files"][0]["name"] = "changed-by-caller.opus"
        self.assertEqual(store.manifest("dinosaur")[0]["name"], "a.opus")
        animal.unlink()
        updated = store.listing("dinosaur", include_summary=True)
        self.assertNotEqual(updated["revision"], full["revision"])
        self.assertEqual(updated["summary"]["total"], 1)
        (store.directory("dinosaur", "animal") / "b.opus").write_bytes(b"duplicate")
        with self.assertRaises(AudioStoreError):
            store.listing("dinosaur")

    def test_cached_catalog_updates_after_upload_delete_and_clear(self) -> None:
        store = self.app.extensions["audio_store"]
        before = store.revision("dinosaur")
        store.save("dinosaur", "animal", "a.opus", io.BytesIO(b"new"))
        self.assertNotEqual(store.revision("dinosaur"), before)
        self.assertEqual(store.summary("dinosaur")["total"], 1)
        store.delete("dinosaur", "animal", "a.opus")
        self.assertEqual(store.revision("dinosaur"), before)
        store.save("dinosaur", "ambient", "b.opus", io.BytesIO(b"new"))
        self.assertEqual(store.clear("dinosaur", "ambient"), 1)
        self.assertEqual(store.summary("dinosaur")["total"], 0)

    def test_admin_audio_bundle_is_authenticated_and_consistent(self) -> None:
        url = "/api/admin/audio?product=dinosaur&category=ambient"
        self.assertEqual(self.client.get(url).status_code, 401)
        self.login()
        store = self.app.extensions["audio_store"]
        (store.directory("dinosaur", "animal") / "a.opus").write_bytes(b"animal")
        (store.directory("dinosaur", "ambient") / "b.opus").write_bytes(b"ambient")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        bundle = response.get_json()
        self.assertEqual(bundle["files"][0]["name"], "b.opus")
        self.assertEqual(bundle["files"][0]["index"], 0)
        self.assertEqual(bundle["summary"]["total"], 2)
        self.assertEqual(bundle["revision"], bundle["summary"]["revision"])

    def test_audio_preview_requires_login_and_uses_named_opus_file(self) -> None:
        preview = "/api/admin/audio/preview/b.opus?product=dinosaur&category=ambient"
        self.assertEqual(self.client.get(preview).status_code, 401)
        self.login()
        store = self.app.extensions["audio_store"]
        (store.directory("dinosaur", "animal") / "a.opus").write_bytes(b"animal")
        (store.directory("dinosaur", "ambient") / "b.opus").write_bytes(b"ambient")
        with self.client.get(preview, headers={"Range": "bytes=0-2"}) as response:
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.mimetype, "audio/ogg")
            self.assertEqual(response.data, b"amb")
        self.assertEqual(self.client.get(preview.replace("ambient", "animal")).status_code, 404)

    def test_download_ranges_and_precise_etags(self) -> None:
        store = self.app.extensions["audio_store"]
        path = store.directory("dinosaur", "animal") / "range.opus"
        path.write_bytes(b"0123456789")
        registration = self.client.post(
            "/api/device/register", json={"device_id": "ESP32-RANGE", "product_id": "dinosaur"},
        ).get_json()
        auth = {"Authorization": f"Bearer {registration['api_token']}"}
        urls = [
            ("/api/download/range.opus?product=dinosaur", {}),
            ("/api/download-idx/0?product=dinosaur", {}),
            ("/api/device/v1/download/0", auth),
        ]
        for url, headers in urls:
            with self.subTest(url=url):
                full = self.client.get(url, headers=headers)
                self.assertEqual(full.status_code, 200)
                self.assertEqual(full.data, b"0123456789")
                etag = full.headers["ETag"]
                full.close()
                partial = self.client.get(url, headers={**headers, "Range": "bytes=3-6"})
                self.assertEqual(partial.status_code, 206)
                self.assertEqual(partial.data, b"3456")
                self.assertEqual(partial.headers["Content-Range"], "bytes 3-6/10")
                partial.close()
                cached = self.client.get(url, headers={**headers, "If-None-Match": etag})
                self.assertEqual(cached.status_code, 304)
                cached.close()
                self.assertEqual(self.client.get(
                    url, headers={**headers, "Range": "bytes=100-200"},
                ).status_code, 416)
        original_stat = path.stat()
        path.write_bytes(b"abcdefghij")
        os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns + 1))
        changed = self.client.get(urls[0][0], headers={"If-None-Match": etag})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.data, b"abcdefghij")
        self.assertNotEqual(changed.headers["ETag"], etag)
        changed.close()

    def test_same_device_can_register_concurrently_without_database_errors(self) -> None:
        barrier = Barrier(6)
        def register(_index):
            with self.app.test_client() as client:
                barrier.wait(timeout=5)
                return client.post(
                    "/api/device/register", json={"device_id": "ESP32-CONCURRENT", "product_id": "dinosaur"},
                ).status_code
        with ThreadPoolExecutor(max_workers=6) as pool:
            statuses = list(pool.map(register, range(6)))
        self.assertEqual(statuses.count(201), 1)
        self.assertEqual(statuses.count(200), 5)
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM devices").fetchone()[0], 1)

    def test_overview_counts_all_devices_but_limits_recent_rows(self) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.app.app_context():
            for index in range(10):
                status = "active" if index < 6 else "disabled" if index < 8 else "pending"
                seen = now if index < 3 or status == "disabled" else "2000-01-01T00:00:00+00:00"
                get_db().execute(
                    """INSERT INTO devices(device_uid, name, product_id, status,
                        created_at, activated_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (f"ESP32-STATS-{index}", f"统计设备{index}", "dinosaur", status,
                     now, now if status != "pending" else None, seen),
                )
            get_db().commit()
        self.login()
        overview = self.client.get("/api/admin/overview").get_json()
        self.assertEqual(overview["devices"], {
            "total": 10, "bound": 8, "online": 3, "pending": 2, "disabled": 2,
        })
        self.assertEqual(len(overview["recent_devices"]), 6)
        self.assertEqual(overview["recent_devices"][0]["device_uid"], "ESP32-STATS-9")
        dinosaur = next(item for item in overview["products"] if item["id"] == "dinosaur")
        self.assertEqual(dinosaur["device_count"], 8)
        self.assertEqual(dinosaur["online_count"], 3)

    def test_six_digit_activation_flow(self) -> None:
        self.app.config["DEVICE_AUTO_ACTIVATE"] = False
        register = self.client.post(
            "/api/device/register",
            json={
                "device_id": "ESP32-TAIL-001",
                "product_id": "tail-wagging-panda",
                "firmware_version": "0.9.0",
            },
        )
        self.assertEqual(register.status_code, 201, register.get_data(as_text=True))
        registration = register.get_json()
        self.assertRegex(registration["activation_code"], r"^\d{6}$")

        pending = self.client.post(
            "/api/device/activate",
            json={
                "device_id": "ESP32-TAIL-001",
                "claim_token": registration["claim_token"],
            },
        )
        self.assertEqual(pending.get_json()["status"], "pending")

        csrf_token = self.login()
        activated = self.client.post(
            "/api/admin/devices/activate",
            json={"activation_code": registration["activation_code"]},
            headers={"X-CSRF-Token": csrf_token},
        )
        self.assertEqual(activated.status_code, 200, activated.get_data(as_text=True))

        provisioned = self.client.post(
            "/api/device/activate",
            json={
                "device_id": "ESP32-TAIL-001",
                "claim_token": registration["claim_token"],
            },
        )
        self.assertEqual(provisioned.status_code, 200)
        token = provisioned.get_json()["api_token"]

        replayed = self.client.post(
            "/api/device/activate",
            json={
                "device_id": "ESP32-TAIL-001",
                "claim_token": registration["claim_token"],
            },
        )
        self.assertEqual(replayed.status_code, 200)
        self.assertEqual(replayed.get_json()["api_token"], token)

        check_in = self.client.post(
            "/api/device/v1/check-in",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(check_in.status_code, 200)

        reused_claim = self.client.post(
            "/api/device/activate",
            json={
                "device_id": "ESP32-TAIL-001",
                "claim_token": registration["claim_token"],
            },
        )
        self.assertEqual(reused_claim.status_code, 401)

    def test_pending_device_edit_does_not_activate_it(self) -> None:
        self.app.config["DEVICE_AUTO_ACTIVATE"] = False
        registration = self.client.post(
            "/api/device/register",
            json={
                "device_id": "ESP32-PENDING-EDIT",
                "product_id": "dinosaur",
            },
        )
        self.assertEqual(registration.status_code, 201)
        csrf_token = self.login()
        devices = self.client.get("/api/admin/devices").get_json()["devices"]
        pending = next(item for item in devices if item["device_uid"] == "ESP32-PENDING-EDIT")
        response = self.client.patch(
            f"/api/admin/devices/{pending['id']}",
            json={
                "name": "待激活测试设备",
                "product_id": "dinosaur",
                "status": "pending",
            },
            headers={"X-CSRF-Token": csrf_token},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["device"]["status"], "pending")

        overview = self.client.get("/api/admin/overview").get_json()
        self.assertEqual(overview["devices"]["bound"], 0)
        self.assertEqual(overview["devices"]["pending"], 1)

    def test_non_object_json_never_returns_500(self) -> None:
        csrf_token = self.login()
        cases = [
            ("POST", "/api/auth/login", True),
            ("POST", "/api/auth/change-password", True),
            ("POST", "/api/admin/devices", True),
            ("POST", "/api/admin/devices/activate", True),
            ("POST", "/api/device/register", False),
            ("POST", "/api/device/activate", False),
        ]
        for method, path, use_csrf in cases:
            with self.subTest(path=path):
                headers = {"X-CSRF-Token": csrf_token} if use_csrf else {}
                response = self.client.open(
                    path,
                    method=method,
                    json=[1],
                    headers=headers,
                )
                self.assertLess(response.status_code, 500)
                self.assertEqual(response.content_type, "application/json")

    def test_runtime_database_is_private(self) -> None:
        database = Path(self.app.config["DATABASE"])
        self.assertEqual(database.stat().st_mode & 0o777, 0o600)
        self.assertEqual(database.parent.stat().st_mode & 0o777, 0o700)

    def test_favicon_and_proxy_address_validation(self) -> None:
        favicon = self.client.get("/static/favicon.svg")
        self.assertEqual(favicon.status_code, 200)
        favicon.close()
        for address in ["0.0.0.0", "224.0.0.1", "255.255.255.255"]:
            with self.subTest(address=address):
                with self.app.test_request_context(f"/?ip={address}"):
                    with self.assertRaises(ValueError):
                        _device_ip()
        with self.app.test_request_context("/?ip=192.168.1.88"):
            self.assertEqual(_device_ip(), "192.168.1.88")

    def test_revision_changes_for_same_size_fast_replacement(self) -> None:
        store = self.app.extensions["audio_store"]
        path = store.directory("dinosaur", "animal") / "same-size.opus"
        path.write_bytes(b"first")
        first_revision = store.revision("dinosaur")
        first_stat = path.stat()
        path.write_bytes(b"other")
        os.utime(
            path,
            ns=(first_stat.st_atime_ns, first_stat.st_mtime_ns + 1),
        )
        self.assertNotEqual(first_revision, store.revision("dinosaur"))

    def test_expired_activation_can_be_registered_again(self) -> None:
        self.app.config["DEVICE_AUTO_ACTIVATE"] = False
        first = self.client.post(
            "/api/device/register",
            json={
                "device_id": "ESP32-RETRY-001",
                "product_id": "crawling-panda",
            },
        ).get_json()
        with self.app.app_context():
            get_db().execute(
                """
                UPDATE devices SET activation_expires_at =
                    '2000-01-01T00:00:00+00:00'
                WHERE device_uid = 'ESP32-RETRY-001'
                """
            )
            get_db().commit()

        expired = self.client.post(
            "/api/device/activate",
            json={
                "device_id": "ESP32-RETRY-001",
                "claim_token": first["claim_token"],
            },
        )
        self.assertEqual(expired.status_code, 410)

        second = self.client.post(
            "/api/device/register",
            json={
                "device_id": "ESP32-RETRY-001",
                "product_id": "crawling-panda",
            },
        )
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(
            second.get_json()["claim_token"],
            first["claim_token"],
        )


if __name__ == "__main__":
    unittest.main()

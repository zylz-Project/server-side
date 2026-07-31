from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from audio_hub import create_app
from audio_hub.database import get_db


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
            'type="button" data-close-dialog="activate-modal"',
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
        self.assertIn(b'"name":"', response.data)

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
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.get_json()["device"]["product_id"], "dinosaur")

        response = self.client.get(
            "/api/device/v1/files",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 200)

    def test_six_digit_activation_flow(self) -> None:
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

    def test_expired_activation_can_be_registered_again(self) -> None:
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

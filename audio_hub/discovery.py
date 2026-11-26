"""Announce the local Audio Hub on IPv4 networks shared with its devices."""

from __future__ import annotations

import hmac
import json
import socket
import subprocess
import threading
import time

from .device_voice import lan_ipv4


DISCOVERY_PORT = 39876
DISCOVERY_PREFIX = "WZH-HUB/1 "


def discovery_proof(credential: str, message: str) -> str:
    return hmac.digest(credential.encode("utf-8"), message.encode("ascii"), "sha256").hex()


def announcement(credential: str, address: str, port: int) -> bytes:
    message = f"{DISCOVERY_PREFIX}{address} {port}"
    return f"{message} {discovery_proof(credential, message)}".encode("ascii")


def lan_broadcasts(host: str) -> list[tuple[str, str]]:
    if host not in ("", "0.0.0.0") and not lan_ipv4(host):
        return []
    try:
        result = subprocess.run(
            ["ip", "-j", "-4", "addr", "show", "up", "scope", "global"],
            check=True, capture_output=True, text=True, timeout=2,
        )
        interfaces = json.loads(result.stdout)
        endpoints = {
            (address["local"], address["broadcast"])
            for interface in interfaces
            if "BROADCAST" in interface["flags"]
            for address in interface["addr_info"]
            if lan_ipv4(address.get("local")) and lan_ipv4(address.get("broadcast"))
            and host in ("", "0.0.0.0", address["local"])
        }
        return sorted(endpoints)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        # On hosts without iproute2, use the interface selected for broadcast.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                if host not in ("", "0.0.0.0"):
                    sock.bind((host, 0))
                sock.connect(("255.255.255.255", DISCOVERY_PORT))
                address = lan_ipv4(sock.getsockname()[0])
        except OSError:
            return []
        return [(address, "255.255.255.255")] if address else []


def start_lan_discovery(app, host: str, port: int) -> None:
    credential = app.config["DEVICE_DEVELOPER_PASSWORD"]
    if not credential or not app.config["DEVICE_AUTO_ACTIVATE"]:
        return

    def announce() -> None:
        while True:
            for address, broadcast in lan_broadcasts(host):
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                        sock.bind((address, 0))
                        sock.sendto(announcement(credential, address, port),
                                    (broadcast, DISCOVERY_PORT))
                except OSError:
                    continue  # The server may be between Wi-Fi connections.
            time.sleep(4)

    threading.Thread(target=announce, name="audio-hub-lan", daemon=True).start()

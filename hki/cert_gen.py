"""Self-signed TLS certificate for LAN HTTPS (Wake Lock / secure context)."""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

from hki import config

CERT_DIR = config.BASE_DIR / "data" / "certs"
CERT_FILE = CERT_DIR / "hki.crt"
KEY_FILE = CERT_DIR / "hki.key"


def _detect_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def generate_self_signed_cert(extra_ips: list[str] | None = None) -> tuple[Path, Path]:
    """Create hki.crt / hki.key with SAN for localhost and LAN IP(s)."""
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    ips = {"127.0.0.1", _detect_lan_ip()}
    if extra_ips:
        ips.update(i for i in extra_ips if i)
    san_parts = ["DNS:localhost", "DNS:hki.local"] + [f"IP:{ip}" for ip in sorted(ips)]
    san = ",".join(san_parts)

    if KEY_FILE.exists():
        KEY_FILE.unlink()
    if CERT_FILE.exists():
        CERT_FILE.unlink()

    cmd = [
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-keyout",
        str(KEY_FILE),
        "-out",
        str(CERT_FILE),
        "-days",
        "825",
        "-nodes",
        "-subj",
        "/CN=HKI-LAN/O=HKI/C=AR",
        "-addext",
        f"subjectAltName={san}",
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        print("openssl no encontrado — instale OpenSSL.", file=sys.stderr)
        raise
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        print(f"openssl falló: {stderr}", file=sys.stderr)
        raise

    return CERT_FILE, KEY_FILE

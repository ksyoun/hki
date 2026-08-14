"""HKI CLI — serve and check commands."""

from __future__ import annotations

import logging
import os
import socket
import ssl
import sys
import threading
from pathlib import Path

import click
import uvicorn

from hki import config
from hki.cert_gen import generate_self_signed_cert


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def _validate_ssl() -> tuple[dict[str, str], list[str]]:
    """Build uvicorn ssl kwargs; return warnings/errors if files are missing or invalid."""
    issues: list[str] = []
    cert = config.SSL_CERTFILE
    key = config.SSL_KEYFILE
    if not cert or not key:
        if os.getenv("HKI_HTTPS", "").lower() in ("1", "true", "yes", "on"):
            issues.append(
                "HKI_HTTPS=true pero faltan certificado/clave — "
                "ejecute: python -m hki gen-cert"
            )
        return {}, issues

    cert_path = Path(cert)
    key_path = Path(key)
    if not cert_path.is_file():
        issues.append(f"Certificado SSL no encontrado: {cert_path}")
        return {}, issues
    if not key_path.is_file():
        issues.append(f"Clave SSL no encontrada: {key_path}")
        return {}, issues

    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert_path), str(key_path))
    except ssl.SSLError as exc:
        issues.append(f"Error al cargar SSL ({cert_path}): {exc}")
        return {}, issues

    return {"ssl_certfile": str(cert_path), "ssl_keyfile": str(key_path)}, issues


def _start_http_guide(host: str) -> None:
    """HTTP join guide (no cert warning) while main app runs HTTPS."""
    guide_port = config.HTTP_GUIDE_PORT
    if guide_port == config.PORT:
        return

    def run() -> None:
        uvicorn.run(
            "hki.server.guide_app:guide_app",
            host=host,
            port=guide_port,
            reload=False,
            log_level="warning",
        )

    thread = threading.Thread(target=run, name="hki-http-guide", daemon=True)
    thread.start()


@click.group()
def main():
    """HKI 교회 실시간 번역 서비스."""


@main.command()
@click.option("--host", default=config.HOST, help="Bind host")
@click.option("--port", default=config.PORT, help="Bind port")
def serve(host: str, port: int):
    """Start the live translation server."""
    level_name = os.getenv("HKI_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not config.OPENAI_API_KEY:
        click.echo(
            "⚠ OPENAI_API_KEY no configurada — UI, QR y monitor de entrada disponibles; "
            "transmisión/prueba de archivo requieren clave en .env",
            err=True,
        )
    scheme = config.public_scheme()
    base_local = f"{scheme}://localhost:{port}"
    ip_hint = "localhost"
    click.echo(f"HKI servidor: {scheme}://{host}:{port}")
    click.echo(f"Operador: {base_local}/")
    if config.is_https():
        guide_local = config.audience_join_url(ip_hint)
        click.echo(f"Audiencia (QR): {guide_local}")
        click.echo(f"  → HTTP sin advertencia; luego HTTPS al tocar Continuar")
    else:
        click.echo(f"Audiencia: {config.audience_join_url(ip_hint)}")
    click.echo(f"Subtítulos: {config.captions_public_url(ip_hint)}")
    if config.is_https():
        click.echo("HTTPS: activo (Wake Lock en Chrome/Android)")
        if config.HTTP_GUIDE_PORT != port:
            click.echo(f"Guía HTTP: http://{host}:{config.HTTP_GUIDE_PORT}/join")
    else:
        click.echo(
            "HTTP — para Wake Lock en teléfonos: HKI_HTTPS=true y hki gen-cert",
            err=True,
        )
    tts = "ON" if config.TTS_ENABLED else "OFF"
    click.echo(f"Voz TTS: {tts}")

    lan_ip = _local_ip()
    click.echo(f"IP LAN detectada: {lan_ip}")
    if lan_ip == "127.0.0.1":
        click.echo(
            "  ⚠ Sin red activa — otros dispositivos no podrán conectar",
            err=True,
        )

    ssl_kwargs, ssl_issues = _validate_ssl()
    for msg in ssl_issues:
        click.echo(f"  ⚠ {msg}", err=True)

    if ssl_kwargs:
        click.echo(f"HTTPS: cert={ssl_kwargs['ssl_certfile']}")
        _start_http_guide(host)
        click.echo(
            f"Prueba LAN (otro PC): http://{lan_ip}:{config.HTTP_GUIDE_PORT}/api/health"
        )
        click.echo(
            f"Prueba LAN (otro PC): https://{lan_ip}:{port}/api/health"
        )
    else:
        click.echo("HTTPS: inactivo — solo HTTP en puerto principal")
        if config.HTTP_GUIDE_PORT != port:
            click.echo(
                f"  Guía HTTP :{config.HTTP_GUIDE_PORT} no se inicia sin HTTPS",
                err=True,
            )

    click.echo(
        "Si otro PC no conecta: router AP isolation, perfil de red Windows, "
        "o antivirus bloqueando python.exe",
        err=True,
    )

    uvicorn.run(
        "hki.server.app:app",
        host=host,
        port=port,
        reload=False,
        access_log=False,
        **ssl_kwargs,
    )


@main.command("gen-cert")
@click.option(
    "--ip",
    multiple=True,
    help="IP adicional para SAN (ej. IP fija del servidor)",
)
def gen_cert(ip: tuple[str, ...]):
    """Generar certificado autofirmado para HTTPS en LAN (data/certs/)."""
    try:
        cert, key = generate_self_signed_cert(extra_ips=list(ip) if ip else None)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
    click.echo(f"Certificado: {cert}")
    click.echo(f"Clave:      {key}")
    click.echo("")
    click.echo("En .env (opcional si usa rutas por defecto):")
    click.echo("  HKI_HTTPS=true")
    click.echo("")
    click.echo("Reinicie el servidor. En el teléfono:")
    click.echo("  https://<IP-LAN>:8765/captions")
    click.echo("  → Aceptar advertencia de certificado")


@main.command()
def check():
    """Check dependencies and audio devices."""
    click.echo("=== HKI 환경 점검 ===\n")

    # Python version
    click.echo(f"Python: {sys.version.split()[0]}")

    # API key
    if config.OPENAI_API_KEY:
        click.echo(f"OPENAI_API_KEY: 설정됨 ({config.OPENAI_API_KEY[:8]}...)")
    else:
        click.echo("OPENAI_API_KEY: ❌ 미설정 — .env 파일을 확인하세요")

    # sounddevice
    try:
        import sounddevice as sd

        click.echo(f"sounddevice: OK (PortAudio {sd.get_portaudio_version()})")
    except ImportError:
        click.echo("sounddevice: ❌ 미설치 — pip install sounddevice")
        return

    # Audio devices
    from hki.live.audio import default_input_device, find_scarlett, list_devices

    devices = list_devices()
    click.echo(f"\n입력 디바이스 ({len(devices)}개) — 캡처는 OS 기본 입력:")
    for d in devices:
        click.echo(f"  [{d.index}] {d.name} ({d.sample_rate:.0f} Hz)")

    try:
        default = default_input_device()
        click.echo(f"\n✓ OS 기본 입력: [{default.index}] {default.name}")
    except ValueError as e:
        click.echo(f"\n⚠ OS 기본 입력 없음: {e}")

    scarlett = find_scarlett()
    if scarlett:
        click.echo(f"  (참고) Scarlett: [{scarlett.index}] {scarlett.name}")

    click.echo(f"\nVoz TTS: {'ON' if config.TTS_ENABLED else 'OFF'}")

    lan_ip = _local_ip()
    click.echo(f"\nRed LAN:")
    click.echo(f"  IP detectada: {lan_ip}")
    click.echo(f"  Bind host:    {config.HOST}:{config.PORT}")
    if config.is_https():
        click.echo(f"  HTTPS: ON ({config.SSL_CERTFILE})")
        click.echo(
            f"  Guía HTTP:    http://{lan_ip}:{config.HTTP_GUIDE_PORT}/join"
        )
        click.echo(
            f"  Subtítulos:   https://{lan_ip}:{config.PORT}/captions"
        )
        _check_cert_san(lan_ip)
    else:
        click.echo("  HTTPS: OFF — python -m hki gen-cert && HKI_HTTPS=true")
        click.echo(f"  Subtítulos:   http://{lan_ip}:{config.PORT}/captions")

    ssl_kwargs, ssl_issues = _validate_ssl()
    for msg in ssl_issues:
        click.echo(f"  ⚠ {msg}")

    # OpenAI client
    try:
        from openai import OpenAI

        client = OpenAI(api_key=config.OPENAI_API_KEY)
        click.echo("\nOpenAI API: 연결 확인 중...")
        client.models.list()
        click.echo("OpenAI API: OK")
    except Exception as e:
        click.echo(f"OpenAI API: ❌ {e}")

    click.echo("\n점검 완료.")


def _check_cert_san(lan_ip: str) -> None:
    """Warn if the self-signed cert does not include the current LAN IP."""
    cert = config.SSL_CERTFILE
    if not cert or not Path(cert).is_file():
        return
    try:
        import subprocess

        result = subprocess.run(
            ["openssl", "x509", "-in", cert, "-noout", "-text"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        san_text = result.stdout
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return
    if lan_ip in san_text:
        click.echo(f"  Cert SAN: incluye IP LAN {lan_ip}")
        return
    click.echo(
        f"  ⚠ Certificado sin IP LAN actual ({lan_ip}) — "
        f"regenere: python -m hki gen-cert --ip {lan_ip}",
        err=True,
    )


if __name__ == "__main__":
    main()

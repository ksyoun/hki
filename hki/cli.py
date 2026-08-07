"""HKI CLI — serve and check commands."""

from __future__ import annotations

import logging
import os
import sys
import threading

import click
import uvicorn

from hki import config
from hki.cert_gen import generate_self_signed_cert


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
    ssl_kwargs = {}
    if config.SSL_CERTFILE and config.SSL_KEYFILE:
        ssl_kwargs["ssl_certfile"] = config.SSL_CERTFILE
        ssl_kwargs["ssl_keyfile"] = config.SSL_KEYFILE
        _start_http_guide(host)
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
    if config.is_https():
        click.echo(f"HTTPS: ON ({config.SSL_CERTFILE})")
    else:
        click.echo("HTTPS: OFF — python -m hki gen-cert && HKI_HTTPS=true")

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


if __name__ == "__main__":
    main()

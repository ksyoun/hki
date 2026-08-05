"""HKI CLI — serve and check commands."""

from __future__ import annotations

import logging
import sys

import click
import uvicorn

from hki import config


@click.group()
def main():
    """HKI 교회 실시간 번역 서비스."""


@main.command()
@click.option("--host", default=config.HOST, help="Bind host")
@click.option("--port", default=config.PORT, help="Bind port")
def serve(host: str, port: int):
    """Start the live translation server."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not config.OPENAI_API_KEY:
        click.echo(
            "⚠ OPENAI_API_KEY 미설정 — UI·QR·입력 테스트는 가능, "
            "스트리밍/파일 테스트는 .env에 키 필요",
            err=True,
        )
    click.echo(f"HKI 서버 시작: http://{host}:{port}")
    click.echo(f"운영자: http://localhost:{port}/")
    click.echo(f"자막:   http://localhost:{port}/captions")
    draft = "ON" if config.DRAFT_ENABLED else "OFF"
    click.echo(f"임시 번역(draft): {draft}")
    uvicorn.run("hki.server.app:app", host=host, port=port, reload=False)


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
    from hki.live.audio import find_scarlett, list_devices

    devices = list_devices()
    click.echo(f"\n입력 디바이스 ({len(devices)}개):")
    for d in devices:
        marker = " ← Scarlett" if "scarlett" in d.name.lower() else ""
        click.echo(f"  [{d.index}] {d.name} ({d.sample_rate:.0f} Hz){marker}")

    scarlett = find_scarlett()
    if scarlett:
        click.echo(f"\n✓ Scarlett 자동 탐지: [{scarlett.index}] {scarlett.name}")
    else:
        click.echo("\n⚠ Scarlett 미탐지 — 설정에서 수동 선택 필요")

    # OpenAI client
    try:
        from openai import OpenAI

        client = OpenAI(api_key=config.OPENAI_API_KEY)
        click.echo("\nOpenAI API: 연결 확인 중...")
        models = client.models.list()
        click.echo("OpenAI API: OK")
    except Exception as e:
        click.echo(f"OpenAI API: ❌ {e}")

    click.echo("\n점검 완료.")


if __name__ == "__main__":
    main()

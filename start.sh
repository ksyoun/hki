#!/bin/bash
# Mac 서버 시작 (더블클릭: HKI.command 사용)
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "최초 설치가 필요합니다. setup-mac.sh 실행 중..."
  bash setup-mac.sh
fi

source .venv/bin/activate

if [ ! -f .env ]; then
  cp .env.example .env
  echo "⚠ .env 생성됨 — API 키는 나중에 넣어도 UI는 열립니다."
fi

echo ""
python -m hki serve

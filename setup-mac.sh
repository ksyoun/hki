#!/bin/bash
# Mac 최초 1회 설치
set -e
cd "$(dirname "$0")"

echo "=== HKI Mac 설치 ==="

if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3가 없습니다. https://www.python.org/downloads/ 에서 설치하세요."
  exit 1
fi

echo "Python: $(python3 --version)"

if [ ! -d .venv ]; then
  echo "가상환경 생성 중..."
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
pip install -q -e .

if [ ! -f .env ]; then
  cp .env.example .env
  echo ""
  echo "✓ .env 파일 생성됨"
  echo "  스트리밍/번역 사용 시 OPENAI_API_KEY를 .env에 입력하세요."
  echo "  UI만 볼 때는 비워둬도 서버는 시작됩니다."
else
  echo "✓ .env 이미 있음"
fi

echo ""
echo "설치 완료. 서버 시작: ./start.sh"
python -m hki check

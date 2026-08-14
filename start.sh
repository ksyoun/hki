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

# HTTPS (Wake Lock en teléfonos — secure context)
CERT_CRT="data/certs/hki.crt"
CERT_KEY="data/certs/hki.key"
if [ ! -f "$CERT_CRT" ] || [ ! -f "$CERT_KEY" ]; then
  echo ""
  echo "Certificado HTTPS no encontrado — generando (gen-cert)..."
  if ! command -v openssl &>/dev/null; then
    echo "⚠ openssl no instalado — Wake Lock en teléfonos requiere HTTPS."
    echo "  Instale OpenSSL o use: brew install openssl"
  else
    python -m hki gen-cert
  fi
fi

if [ -f "$CERT_CRT" ] && grep -q '^HKI_HTTPS=false' .env 2>/dev/null; then
  echo "⚠ Certificado listo — recomendado: HKI_HTTPS=true en .env (Wake Lock)"
fi

echo ""
python3 <<'PY'
from hki import config
from hki.server.app import _local_ip

scheme = config.public_scheme()
port = config.PORT
local = f"{scheme}://localhost:{port}"
ip = _local_ip()
join = config.audience_join_url(ip)
direct = config.captions_public_url(ip)

print(f"Operador (PC):       {local}/")
print(f"QR primera vez:      {join}")
if config.is_https():
    print(f"QR directo:          {direct}")
    print(f"  Guía HTTP :{config.HTTP_GUIDE_PORT} → Continuar → certificado en :{port}")
    print(f"  Directo   HTTPS :{port}/captions (ya aceptó certificado)")
else:
    print(f"Subtítulos:          {direct}")
if config.is_https():
    print("HTTPS: activo — Wake Lock + guía HTTP en puerto", config.HTTP_GUIDE_PORT)
else:
    print("HTTP — para Wake Lock: HKI_HTTPS=true y python -m hki gen-cert")
print("")
PY

OPEN_URL=$(python3 -c "from hki import config; print(f'{config.public_scheme()}://localhost:{config.PORT}/')")
(sleep 2 && open "$OPEN_URL") &

python -m hki serve

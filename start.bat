@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ========================================
echo   HKI — Traduccion en vivo
echo   Operador: https://localhost:8765
echo   QR 1a vez: http://^<IP^>:8766/join
echo   QR directo: https://^<IP^>:8765/captions
echo ========================================
echo.

if not exist ".venv\Scripts\activate.bat" (
  echo ERROR: .venv no encontrado.
  echo Instale Python 3.11+ y ejecute setup en README.txt
  pause
  exit /b 1
)

call .venv\Scripts\activate.bat

if not exist ".env" (
  copy /y .env.example .env >nul
  echo .env creado — puede agregar OPENAI_API_KEY despues.
)

if not exist "data\certs\hki.crt" if not exist "data\certs\hki.key" (
  echo.
  echo Certificado HTTPS no encontrado — generando ^(gen-cert^)...
  where openssl >nul 2>&1
  if errorlevel 1 (
    echo openssl no encontrado — instale OpenSSL o use Git for Windows openssl.
    echo Wake Lock en telefonos requiere HTTPS.
  ) else (
    python -m hki gen-cert
  )
)

echo.
python -c "from hki import config; from hki.server.app import _local_ip; ip=_local_ip(); p=config.PORT; s=config.public_scheme(); local=f'{s}://localhost:{p}'; join=config.audience_join_url(ip); direct=config.captions_public_url(ip); print(f'Operador (PC):       {local}/'); print(f'QR primera vez:      {join}'); print(f'QR directo:          {direct}' if config.is_https() else f'Subtitulos:          {direct}'); (config.is_https() and print(f'  Guia HTTP :{config.HTTP_GUIDE_PORT} -> Continuar -> certificado en :{p}')); print('HTTPS: activo - Wake Lock + guia HTTP puerto '+str(config.HTTP_GUIDE_PORT) if config.is_https() else 'HTTP - para Wake Lock: HKI_HTTPS=true y python -m hki gen-cert'); print()"
echo.

start /b python -c "import time,webbrowser; from hki import config; time.sleep(2); webbrowser.open(f'{config.public_scheme()}://localhost:{config.PORT}/')"

python -m hki serve
pause

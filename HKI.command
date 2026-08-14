#!/bin/bash
# Finder 더블클릭 — HKI 서버
# HTTPS :8765 (operador + subtítulos) · HTTP guía :8766 (QR primera vez)
cd "$(dirname "$0")"
clear
echo "========================================"
echo "  HKI — Traducción en vivo"
echo "  Operador: https://localhost:8765"
echo "  QR 1ª vez: http://<IP>:8766/join"
echo "  QR directo: https://<IP>:8765/captions"
echo "========================================"
echo ""
bash start.sh
echo ""
read -r -p "종료하려면 Enter..." _

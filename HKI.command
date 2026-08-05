#!/bin/bash
# Finder에서 더블클릭으로 서버 시작
cd "$(dirname "$0")"
bash start.sh
echo ""
read -r -p "종료하려면 Enter..." _

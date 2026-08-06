========================================
  HKI 교회 실시간 번역 — 사용 안내
========================================

이 문서는 Windows 교회 PC에 처음 설치할 때부터
매주 설교 때 프로그램을 켜기까지의 전체 절차입니다.


========================================
  Mac 개발/테스트 (집에서 미리 해보기)
========================================

  [최초 1회]
    cd HKI
    ./setup-mac.sh

  [서버 시작]
    ./start.sh
    또는 Finder에서 HKI.command 더블클릭

  → Chrome: http://localhost:8765/
  → API 키 없어도 UI·QR·입력 테스트 가능
  → 스트리밍/파일 테스트는 .env 에 OPENAI_API_KEY 필요
  → 스마트폰 테스트: 같은 Wi-Fi에서 운영자 페이지의 자막 URL 사용


========================================
  1. 사전 준비 (한 번만)
========================================

필요한 것:
  - Windows 10/11 PC (교회 사운드 데스크용)
  - 인터넷 연결 (Wi-Fi 또는 유선 LAN)
  - Focusrite Scarlett 오디오 인터페이스 + USB 케이블
  - OpenAI API 키 (platform.openai.com 에서 발급)
  - Git (선택) — 아래 "방법 B" 없이 ZIP으로 받을 경우 불필요

[1-1] Python 설치
  1. https://www.python.org/downloads/ 접속
  2. Python 3.11 이상 다운로드 및 설치
  3. 설치 화면에서 "Add python.exe to PATH" 반드시 체크
  4. 설치 후 CMD 열어서 확인:
       python --version
     → Python 3.11.x 이상이 나오면 OK

[1-2] Scarlett 드라이버 설치
  1. https://focusrite.com/downloads 접속
  2. 사용 중인 Scarlett 모델 드라이버 다운로드
  3. 설치 후 PC 재부팅
  4. Scarlett를 USB로 연결
  5. Windows 설정 → 시스템 → 소리 → 입력 장치에 Scarlett가 보이는지 확인


========================================
  2. 프로그램 받기 (Git 클론)
========================================

[방법 A] Git으로 받기 (권장)

  1. Git 설치: https://git-scm.com/download/win
  2. CMD 또는 PowerShell 열기
  3. 원하는 폴더로 이동 (예: 바탕화면)
       cd %USERPROFILE%\Desktop
  4. 저장소 클론:
       git clone https://github.com/ksyoun/hki.git
  5. HKI 폴더로 이동:
       cd hki

[방법 B] ZIP으로 받기

  1. 브라우저에서 https://github.com/ksyoun/hki 접속
  2. Code → Download ZIP
  3. 압축 해제 후 폴더 이름을 HKI 로 맞추기
  4. CMD에서 해당 폴더로 이동:
       cd C:\Users\사용자이름\Desktop\HKI


========================================
  3. 최초 설치 (한 번만, HKI 폴더에서)
========================================

CMD에서 HKI 폴더 안에 있는지 확인한 뒤 아래를 순서대로 실행합니다.

  1. 가상환경 만들기
       python -m venv .venv

  2. 가상환경 활성화
       .venv\Scripts\activate
     → 프롬프트 앞에 (.venv) 가 붙으면 OK

  3. 패키지 설치
       pip install -r requirements.txt

  4. API 키 설정
       copy .env.example .env
     → 메모장으로 .env 파일 열기
     → OPENAI_API_KEY=sk-your-key-here 를 실제 키로 교체
     → 저장

  5. 환경 점검
       python -m hki check
     → sounddevice OK, Scarlett 탐지, OpenAI API OK 확인

  6. (선택) MP3 테스트 파일 사용 시 ffmpeg 설치
     → https://ffmpeg.org/download.html (Windows builds)
     → PATH에 추가하거나, WAV 파일만 사용해도 됨


========================================
  4. 라우터 고정 IP 설정 (한 번만, 권장)
========================================

스마트폰 자막 URL이 매번 바뀌지 않도록 교회 PC에 고정 IP를
부여합니다. 라우터에서 "DHCP 예약" 방식을 권장합니다.

[4-1] PC의 현재 IP와 MAC 주소 확인

  1. CMD에서:
       ipconfig /all
  2. 사용 중인 어댑터(이더넷 또는 Wi-Fi)에서 아래 두 값을 메모:
       - IPv4 주소        예: 192.168.0.23
       - 물리적 주소(MAC)  예: AA-BB-CC-DD-EE-FF

[4-2] 라우터 관리 페이지 접속

  라우터마다 주소가 다릅니다. 흔한 주소:
    192.168.0.1  /  192.168.1.1  /  192.168.219.1

  1. Chrome 주소창에 위 주소 중 하나 입력
  2. 관리자 ID/비밀번호 입력
     (라우터 뒷면 스티커 또는 통신사 설치 기사에게 문의)
  3. 로그인

[4-3] DHCP 예약(고정 IP) 설정

  메뉴 이름은 제조사마다 다릅니다. 아래 중 하나를 찾으세요:
    - DHCP 예약 / 주소 예약 / 고정 IP / Static DHCP / IP Binding

  1. "새 예약" 또는 "추가" 클릭
  2. MAC 주소: [4-1]에서 메모한 물리적 주소 입력
  3. IP 주소: 현재 쓰는 IP 그대로 지정 (예: 192.168.0.23)
     ※ DHCP 범위 밖의 IP는 피하세요. 보통 .2 ~ .254 사이 사용
  4. 저장 / 적용
  5. PC 재부팅 후 ipconfig 로 같은 IP인지 확인

[4-4] 고정 IP 확인

  CMD:
    ipconfig
  → IPv4 주소가 예약한 IP와 같으면 OK

  이 IP를 메모해 두세요. 청중 자막 주소는:
    http://<고정IP>:8765/captions
  예: http://192.168.0.23:8765/captions


========================================
  5. Windows 방화벽 설정 (한 번만)
========================================

같은 Wi-Fi의 스마트폰에서 자막을 보려면 8765 포트를 열어야 합니다.

  1. Windows 검색 → "Windows Defender 방화벽" → "고급 설정"
  2. 인바운드 규칙 → 새 규칙
  3. 규칙 종류: 포트
  4. TCP, 특정 로컬 포트: 8765
  5. 연결 허용
  6. 이름: HKI Live Translation
  7. 완료

※ 교회 LAN 내부에서만 쓰므로 인터넷에 포트를 열 필요는 없습니다.


========================================
  6. 바로가기 만들기 (한 번만)
========================================

  1. HKI 폴더의 start.bat 우클릭
  2. "바로 가기 만들기"
  3. 만들어진 바로가기를 바탕화면으로 드래그
  4. (선택) 바로가기 이름을 "HKI 실시간 번역"으로 변경


========================================
  7. 설교 당일 — 프로그램 시작
========================================

[시작 순서]

  1. Scarlett USB 케이블 연결 확인
  2. 바탕화면 "HKI 실시간 번역" (start.bat) 더블클릭
  3. 검은 CMD 창이 열리면 닫지 말고 그대로 두기
     → "HKI 서버 시작" 메시지가 보이면 OK
  4. Chrome에서 운영자 페이지 접속:
       http://localhost:8765/
  5. 운영자 페이지 상단의 "Subtítulos" 링크를 클릭하거나
     스마트폰 Chrome에서 접속:
       http://<고정IP>:8765/captions
     예: http://192.168.0.23:8765/captions

[설교 전 준비 (운영자 페이지)]

  1. "Entrada de audio" 카드에서 Scarlett 입력 디바이스 선택
  2. "Nivel de entrada" 바로 레벨 확인, Gain 슬라이더로 조절
  3. "Texto bíblico", "Texto del sermón"에 한국어 원문 붙여넣기
  4. (선택) 🧪 Prueba → 오디오 파일로 자막 미리 확인
  5. "▶ Iniciar transmisión" 클릭

[찬양 시간]
  - "⏸ Pausar" → API 비용 절약
  - 찬양 끝나면 "▶ Reanudar" 클릭

[종료]
  1. 운영자 페이지에서 "■ Finalizar transmisión" 클릭
  2. start.bat CMD 창 닫기


========================================
  8. 음성 출력 (TTS) 켜기/끄기
========================================

확정 번역을 아르헨티나 스페인어 음성으로 들을 수 있습니다.
.env 마스터 스위치가 켜져 있어야 하며, /captions에서 altavoz를 켠 청중이 있을 때만 생성됩니다.

[서버 설정 — .env]

  1. HKI 폴더의 .env 파일을 엽니다
  2. 아래 줄을 추가하거나 수정합니다:

     기능 허용 (마스터 스위치):
     HKI_TTS_ENABLED=true

     기능 비허용 (기본값):
     HKI_TTS_ENABLED=false

  3. 서버를 재시작합니다

  선택 옵션:
  HKI_TTS_MODEL=gpt-4o-mini-tts
  HKI_TTS_VOICE=onyx

[청중 (스마트폰 /captions)]

  - 하단 🔈 버튼 → 탭하면 🔊 altavoz 활성화 (기본: silenciado)
  - 음성은 확정(final) 번역만 재생됩니다

[운영자 페이지]

  - "Nivel de salida" — 음성 출력 레벨 모니터
  - "Salida de voz (TTS)" — altavoz 요청 여부 상태 표시 (read-only)

  ※ OpenAI 음성은 영어 최적화이므로 아르헨티나 억양은 근사치입니다.


========================================
  9. 문제 해결
========================================

  - Scarlett 인식 안 됨
    → Focusrite 드라이버 재설치, USB 포트 변경, PC 재부팅

  - 자막 페이지 접속 안 됨 (스마트폰)
    → PC와 스마트폰이 같은 Wi-Fi인지 확인
    → 방화벽 8765 포트 허용 확인 (섹션 5)
    → ipconfig 로 IP가 바뀌지 않았는지 확인 (고정 IP 재설정)

  - API 오류
    → .env 파일의 OPENAI_API_KEY 확인
    → OpenAI 계정 잔액/한도 확인

  - 전사 안 됨
    → 설정에서 "입력 테스트"로 레벨 확인, Gain 올리기
    → Scarlett 입력이 설교 마이크 채널에 맞게 연결됐는지 확인

  - 소리 찢김(클리핑)
    → Gain 내리기 (레벨 바가 빨간색일 때)

  - 프로그램 업데이트 (Git 사용 시)
    → HKI 폴더에서:
        git pull
        .venv\Scripts\activate
        pip install -r requirements.txt


========================================
  10. 요약 체크리스트
========================================

  [최초 1회]
  □ Python 3.11+ 설치 (PATH 체크)
  □ Scarlett 드라이버 설치
  □ git clone 또는 ZIP 다운로드
  □ venv + pip install -r requirements.txt
  □ .env 에 OPENAI_API_KEY 입력
  □ python -m hki check 통과
  □ 라우터 DHCP 예약으로 PC 고정 IP 설정
  □ Windows 방화벽 TCP 8765 허용
  □ start.bat 바탕화면 바로가기

  [매주 설교]
  □ Scarlett 연결
  □ start.bat 실행
  □ http://localhost:8765/ 에서 "▶ Iniciar transmisión" 클릭
  □ 스마트폰: http://<고정IP>:8765/captions

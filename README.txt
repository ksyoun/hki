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

  → 운영자 (PC): https://localhost:8765/
  → QR primera vez (청중): http://<LAN-IP>:8766/join
  → QR directo (청중): https://<LAN-IP>:8765/captions
  → API 키 없어도 UI·QR·입력 테스트 가능
  → 스트리밍/파일 테스트는 .env 에 OPENAI_API_KEY 필요
  → 입력: macOS **시스템 설정 → 사운드 → 입력** 기본 장치 사용
  → HTTPS 인증서: start 시 자동 gen-cert (또는 python -m hki gen-cert)
  → 스마트폰: 운영자 페이지 하단 QR / 링크 사용 (Primera vez / Directo)


========================================
  1. 사전 준비 (한 번만)
========================================

필요한 것:
  - Windows 10/11 PC (교회 사운드 데스크용)
  - 인터넷 연결 (Wi-Fi 또는 유선 LAN)
  - USB 오디오 인터페이스 (예: Focusrite Scarlett) + 설교 마이크 — 권장
  - OpenAI API 키 (platform.openai.com 에서 발급)
  - Git (선택) — 아래 "방법 B" 없이 ZIP으로 받을 경우 불필요

  ※ HKI는 Windows/macOS에서 **기본 입력 마이크**를 자동 사용합니다.
    운영자 UI에서 장치를 고르지 않습니다 — OS 소리 설정에서 입력 장치를 맞추세요.

[1-1] Python 설치
  1. https://www.python.org/downloads/ 접속
  2. Python 3.11 이상 다운로드 및 설치
  3. 설치 화면에서 "Add python.exe to PATH" 반드시 체크
  4. 설치 후 CMD 열어서 확인:
       python --version
     → Python 3.11.x 이상이 나오면 OK

[1-2] 오디오 입력 (Scarlett 등, 권장)

  1. 사용 중인 USB 오디오 인터페이스 드라이버 설치 (Scarlett: https://focusrite.com/downloads)
  2. USB 연결 후 PC 재부팅(필요 시)
  3. Windows 설정 → 시스템 → 소리 → **입력**
     → 설교 마이크가 연결된 장치를 **기본 입력 장치**로 선택
  4. (Scarlett) 하드웨어 Gain/볼륨은 인터페이스 노브로 조절 (앱에 Gain 슬라이더 없음)


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

  4. API 키 및 HTTPS 설정
       copy .env.example .env
     → 메모장으로 .env 파일 열기
     → OPENAI_API_KEY=sk-your-key-here 를 실제 키로 교체
     → (권장) 스마트폰 화면 유지·Wake Lock용:
         HKI_HTTPS=true
     → 저장
     ※ start.bat 첫 실행 시 .env가 없으면 .env.example에서 자동 생성됩니다.

  5. HTTPS 인증서 생성 (권장, 한 번만)
       python -m hki gen-cert
     → openssl 필요 (Git for Windows에 포함된 openssl 사용 가능)
     → start.bat 실행 시 인증서 없으면 자동 gen-cert 시도
     ※ data/certs/ 에 인증서가 있으면 HKI_HTTPS=false여도 HTTPS가 켜질 수 있음

  6. 환경 점검
       python -m hki check
     → sounddevice OK, **OS 기본 입력** 장치, OpenAI API, HTTPS 상태 확인

  7. (선택) MP3 테스트 파일 사용 시 ffmpeg 설치
     → https://ffmpeg.org/download.html (Windows builds)
     → PATH에 추가하거나, WAV 파일만 사용해도 됨

  ※ 이후 매주: start.bat 더블클릭만 하면 됩니다 (venv·pip는 위 1~3을 이미 했다면 생략).


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

  이 IP를 메모해 두세요. 청중 접속 주소 (HTTPS 기본):

    QR primera vez (인증서 안내):  http://<고정IP>:8766/join
    QR directo (이미 인증서 허용): https://<고정IP>:8765/captions
    운영자 PC:                     https://localhost:8765/

  예:
    http://192.168.0.23:8766/join
    https://192.168.0.23:8765/captions

  ※ primera vez QR은 HTTP(8766)로 안내 페이지를 먼저 보여 주고,
    Continuar 후 HTTPS(8765)에서 인증서를 한 번만 허용하면 됩니다.


========================================
  5. Windows 방화벽 설정 (한 번만)
========================================

같은 Wi-Fi의 스마트폰에서 접속하려면 아래 포트를 열어야 합니다.

  [포트]
    8765 — HTTPS 운영자·자막·WebSocket (메인 서버)
    8766 — HTTP 청중 안내 /join (HTTPS 사용 시, QR primera vez)

  1. Windows 검색 → "Windows Defender 방화벽" → "고급 설정"
  2. 인바운드 규칙 → 새 규칙 (8765, 8766 각각 또는 한 규칙에 8765,8766)
  3. 규칙 종류: 포트
  4. TCP, 특정 로컬 포트: 8765, 8766
  5. 연결 허용
  6. 이름: HKI Live Translation
  7. 완료

※ HKI_HTTPS=false (HTTP만)일 때는 8765만 필요합니다.

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

  1. USB 오디오·마이크 연결, Windows **기본 입력** 장치 확인 (섹션 1-2)
  2. 바탕화면 "HKI 실시간 번역" (start.bat) 더블클릭
  3. 검은 CMD 창이 열리면 닫지 말고 그대로 두기
     → Operador / QR primera vez / QR directo URL이 출력되면 OK
     → 잠시 후 Chrome이 운영자 페이지를 자동으로 엽니다
  4. 운영자 페이지 (HTTPS):
       https://localhost:8765/
  5. 청중 스마트폰 — 운영자 페이지 맨 아래 QR 카드:

     [Primera vez] — 처음 접속·인증서 안내
       QR guía / http://<고정IP>:8766/join
       → 안내 읽기 → Continuar → «비공개 연결» 한 번 허용
       → Android Chrome: Avanzado → Acceder (no seguro)
       → iPhone Safari: Mostrar detalles → visitar este sitio web

     [Directo] — 이미 인증서를 허용한 사람
       QR directo / https://<고정IP>:8765/captions
       → 자막 화면 (화면 한 번 터치 → Wake Lock)

  6. 운영자 화면 "Audiencia: N" 이 1 이상이면 청중 연결 OK

[설교 전 준비 (운영자 페이지)]

  1. "Entrada de audio" — **Conectado**(초록)이면 입력 모니터 연결 OK
     (Sin conexión이면 서버 재시작 또는 Windows 기본 입력 장치 확인)
  2. "Salida de voz" — TTS 켜짐(.env HKI_TTS_ENABLED=true)이면 **Conectado**
  3. "Texto bíblico", "Texto del sermón"에 한국어 원문 붙여넣기 → Contextualizar
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

[파이프라인 (자막 + 스피커)]

  번역 LLM (1회/문장) → OutputComposer 배치 재조합 → ReleasePacer
    → translation 자막 (모든 청중, 동일 텍스트·타이밍)
    → (스피커 ON) TTS 합성 — 같은 텍스트로 음성+자막

[유실 금지]

  송출 중 재조합·release·TTS·번역 큐에서 항목을 버리지 않습니다.
  Pausar는 잔여 큐 전량 flush → TTS drain 후 pausado.
  Finalizar만 의도적으로 중단합니다.

[적체 시 재생 가속]

  서버 ReleasePacer가 큐 depth에 따라 간격을 줄입니다 (base/√depth).
  TTS 백로그가 쌓이면 /captions도 재생 속도를 조금 올립니다 (1.0 → 최대 1.15).

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
  HKI_OUTPUT_BATCH_SIZE=2
  HKI_OUTPUT_TIMEOUT_MS=2500
  HKI_OUTPUT_RELEASE_BASE_MS=1500
  HKI_OUTPUT_RELEASE_MIN_MS=700
  HKI_CAPTION_MAX_LINES=8
  HKI_TTS_PLAYBACK_SPEED_THRESHOLD=3
  HKI_TTS_PLAYBACK_SPEED_MAX=1.15

[청중 (스마트폰 /captions)]

  - HTTPS 접속 후 화면 아무 곳이나 한 번 터치 → Wake Lock (화면 유지)
  - 하단 🔈 → Aceptar → 🔊 altavoz (화면 꺼져도 음성 계속)
  - 스피커 ON: 자막은 TTS 재생 시 표시; OFF: translation 자막 즉시
  - primera vez는 http://IP:8766/join 에서 인증서 안내 후 접속 권장

[운영자 페이지]

  - "Entrada de audio" / "Salida de voz" — 연결 상태 (Conectado / Sin conexión)
  - "Salida de voz (TTS)" 패널 — altavoz 요청·송출 상태 (read-only)
  - "Audiencia: N" — 연결된 /captions 청중 수
  - 하단 QR: Primera vez / Directo / Imprimir ambos QR
  - translation 미리보기는 OutputComposer release와 동일 (재조합 텍스트)

  ※ OpenAI 음성은 영어 최적화이므로 아르헨티나 억양은 근사치입니다.
  ※ 재조합 LLM + ReleasePacer — 자막-only도 배치 대기가 있어 이전보다 약간 늦을 수 있습니다.


========================================
  9. 문제 해결
========================================

  - 입력(마이크) 안 잡힘 / Entrada Sin conexión
    → Windows 설정 → 소리 → 입력 → 올바른 장치를 **기본**으로 지정
    → USB 오디오 드라이버·케이블 확인, PC 재부팅
    → python -m hki check 로 "OS 기본 입력" 이름 확인

  - 자막 페이지 접속 안 됨 (스마트폰)
    → PC와 스마트폰이 같은 Wi-Fi인지 확인
    → 방화벽 TCP 8765, 8766 허용 확인 (섹션 5)
    → URL에 포트 포함: https://IP:8765/captions (포트 생략 시 실패)
    → ipconfig 로 IP가 바뀌지 않았는지 확인 (고정 IP 재설정)

  - «비공개 연결» / 인증서 경고 (HTTPS)
    → 교회 PC 자체 인증서 — 정상. primera vez QR(8766)에서 안내 확인
    → Android: Avanzado → Acceder al sitio (no seguro)
    → iPhone Safari: Mostrar detalles → visitar este sitio web
    → 한 번 허용 후에는 Directo QR(https://…/captions) 사용

  - Audiencia: 0 (청중 연결 안 됨)
    → 스마트폰이 https://IP:8765/captions 인지 확인 (:8765 필수)
    → 운영자도 https://localhost:8765/ 사용 (http 혼용 시 WS 끊김)
    → CMD/start 창에 WebSocket 오류 없는지 확인

  - API 오류
    → .env 파일의 OPENAI_API_KEY 확인
    → OpenAI 계정 잔액/한도 확인

  - 전사 안 됨
    → 🧪 Prueba로 파일 테스트 또는 설교 중 마이크·기본 입력 장치 확인
    → Scarlett 등: 올바른 입력 채널에 마이크 연결, 하드웨어 Gain 적당히

  - 소리 찢김(클리핑)
    → 오디오 인터페이스·믹서에서 입력 Gain 낮추기 (앱 Gain 슬라이더 없음)

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
  □ USB 오디오 드라이버 + Windows 기본 입력 장치 설정
  □ git clone 또는 ZIP 다운로드
  □ venv + pip install -r requirements.txt
  □ .env 에 OPENAI_API_KEY 입력
  □ (권장) HKI_HTTPS=true + python -m hki gen-cert
  □ python -m hki check 통과 (OS 기본 입력 확인)
  □ 라우터 DHCP 예약으로 PC 고정 IP 설정
  □ Windows 방화벽 TCP 8765, 8766 허용
  □ start.bat 바탕화면 바로가기

  [매주 설교]
  □ 마이크·USB 오디오 연결, Windows 기본 입력 확인
  □ start.bat 실행 → https://localhost:8765/
  □ Entrada Conectado 확인
  □ "▶ Iniciar transmisión" 전 Audiencia: 1 이상 확인
  □ 스마트폰 primera vez: http://<고정IP>:8766/join
  □ 스마트폰 directo:     https://<고정IP>:8765/captions


========================================
  11. 번역 프롬프트 확인 (설교 ON/OFF)
========================================

번역은 OpenAI에 보낼 때 system prompt가 세 가지 모드로 바뀝니다.

  general         — 기도·광고·인사 (설교 OFF, 맥락 NVI/요약 미사용)
  sermon_context  — 설교 ON + Contextualizar 완료 (본문 NVI + 요약·용어)
  sermon_fallback — 설교 ON이지만 맥락 없음 (설교 규칙만)

[운영자 화면으로 확인]

  1. 송출 중 "Transcripción / traducción" 상태:
       Activo — servicio general  → general 프롬프트
       Activo — sermón            → sermon_context (맥락 있을 때)
  2. "▶ Iniciar sermón" / "■ Fin del sermón" 버튼 상태
  3. Contextualizar 후에만 설교 맥락이 sermon_context에 들어갑니다.

[API로 확인 (권장)]

  서버가 켜진 상태에서 status API를 호출하면, 지금 번역에 쓰는
  프롬프트 모드가 JSON으로 나옵니다.

  [브라우저]
    https://localhost:8765/api/live/status
    (HTTP만 쓸 때: http://localhost:8765/api/live/status)

  [Windows CMD]
    curl -k -s https://localhost:8765/api/live/status

  [Mac / Linux — 보기 좋게]
    curl -k -s https://localhost:8765/api/live/status | python -m json.tool

  번역 프롬프트 필드:

    translation_prompt_mode
      general         — 기도·광고용 (설교 OFF)
      sermon_context  — 설교 ON + Contextualizar 맥락 포함
      sermon_fallback — 설교 ON이지만 맥락 없음

    translation_prompt_label   — 모드 설명 (스페인어)
    translation_prompt_preview — system prompt 앞 160자
    translation_prompt_len     — 전체 길이
    translation_prompt_includes_context_summary — 요약 포함 여부
    translation_prompt_includes_nvi           — NVI 절 포함 여부
    translator_live            — 송출 중 live 번역기 상태 반영

  함께 보기:
    sermon_on, context_ready

  [점검 순서]

    1) Contextualizar 후 status:
       → context_ready=true, sermon_on=false
       → translation_prompt_mode=general
       → translation_prompt_includes_nvi=false
         (맥락 있어도 설교 OFF면 NVI 미사용)

    2) 설교 ON:
       curl -k -s -X POST https://localhost:8765/api/live/sermon-on
       → translation_prompt_mode=sermon_context
       → translation_prompt_includes_nvi=true
       → preview에 "Contexto del sermón" 또는 "Resumen:"

    3) 설교 OFF:
       curl -k -s -X POST https://localhost:8765/api/live/sermon-off
       → translation_prompt_mode=general
       → translation_prompt_includes_nvi=false

  ※ OpenAI 전문 전체는 API에 없음 (미리보기·길이만).
    전문은 HKI_TRANSLATION_LOG_PROMPTS 로그 사용.

[운영자 화면으로 확인]

  1. 송출 중 "Transcripción / traducción" 상태:
       Activo — servicio general  → general 프롬프트
       Activo — sermón            → sermon_context (맥락 있을 때)
  2. "▶ Iniciar sermón" / "■ Fin del sermón" 버튼 상태
  3. Contextualizar 후에만 설교 맥락이 sermon_context에 들어갑니다.

[서버 로그로 확인 (CMD / start.bat 창)]

  설교 ON/OFF, Contextualizar 시 자동으로 한 줄 로그:
    Translation system prompt mode=general event=sermon_mode len=… preview=…
    Translation system prompt mode=sermon_context event=sermon_mode len=… preview=…

  preview에 "Modo servicio general" → general
  preview에 "Contexto del sermón" 또는 "Resumen:" → sermon_context

[번역할 때마다 로그 보기 (선택)]

  .env 에 추가:
    HKI_TRANSLATION_LOG_PROMPTS=true

  서버 재시작 후, 스페인어 자막이 나올 때마다 같은 형식 로그가 추가됩니다.
  (문장이 많으면 로그가 길어지므로 점검 때만 켜세요.)

[코드 테스트 (개발용)]

    python -m pytest tests/test_sermon_mode.py tests/test_api_prompt_status.py -v

[맥락이 설교에 안 붙을 때]

  □ Contextualizar 했는지 (context_ready=true)
  □ "▶ Iniciar sermón" 눌렀는지 (sermon_on=true)
  □ 서버 재시작 후 Contextualizar 다시 했는지
  □ API translation_prompt_mode가 sermon_context인지 확인

자세한 파이프라인·튜닝: docs/TUNING.md

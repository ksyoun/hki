========================================
  HKI 교회 실시간 번역 — 사용 안내
========================================

[실행 방법]
1. Scarlett USB 케이블 연결 확인
2. HKI 폴더에서 start.bat 더블클릭
3. 검은 창(CMD)이 열리면 닫지 말고 그대로 두기
4. Chrome에서 http://localhost:8765/ 접속

[바탕화면 바로가기 만들기]
- start.bat 우클릭 → "바로 가기 만들기"
- 만들어진 바로가기를 바탕화면으로 드래그

[청중 자막 주소 (스마트폰)]
- 같은 Wi-Fi에 연결 후 Chrome에서 접속:
  http://<서버IP>:8765/captions
- 서버 IP 확인: CMD에서 ipconfig 입력
- 운영자 페이지 상단에도 자막 URL이 표시됩니다

[설교 전 준비]
1. Chrome 운영자 페이지 접속
2. ⚙ 설정 → Scarlett 디바이스 선택
3. "입력 테스트"로 레벨 확인, Gain 슬라이더로 조절
4. 성경 본문, 설교 원고 붙여넣기
5. "스트리밍 시작" 클릭

[임시 번역(draft) 켜기/끄기]
설교 중 말하는 동안 먼저 나오는 회색 임시 자막(draft)을 끄거나 켤 수 있습니다.
final(확정) 번역만 사용하면 API 비용이 줄어듭니다.

1. HKI 폴더의 .env 파일을 메모장으로 엽니다
2. 아래 줄을 추가하거나 수정합니다:

   켜기 (기본값):
   HKI_DRAFT_ENABLED=true

   끄기 (확정 자막만):
   HKI_DRAFT_ENABLED=false

3. start.bat 창을 닫고 다시 start.bat 실행

- draft ON:  말하는 중 회색 임시 자막 → 문장 끝에 흰색 확정 자막
- draft OFF: 문장이 끝난 뒤 흰색 확정 자막만 표시 (비용 절약)

[찬양 시간]
- "일시정지" 버튼 클릭 → API 비용 절약
- 찬양 끝나면 "재개" 클릭

[종료 방법]
1. Chrome 운영자 페이지에서 "방송 종료" 클릭
2. start.bat 검은 창 닫기

[최초 설치 (한 번만)]
1. Python 3.11+ 설치 (python.org, Add to PATH 체크)
2. Focusrite Scarlett 드라이버 설치
3. CMD에서 HKI 폴더로 이동:
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
4. .env.example을 .env로 복사 후 OPENAI_API_KEY 입력
5. python -m hki check 로 환경 확인

[문제 해결]
- Scarlett 인식 안 됨 → Focusrite 드라이버 재설치
- 자막 안 나옴 → 방화벽 8765 포트 허용 확인
- API 오류 → .env 파일의 OPENAI_API_KEY 확인
- 전사 안 됨 → 설정에서 "입력 테스트"로 레벨 확인, gain 올리기
- 소리 찢김(클리핑) → gain 내리기 (빨간색 표시 시)

[방화벽 설정 (LAN 청취자용)]
Windows Defender 방화벽 → 고급 설정 → 인바운드 규칙
→ 새 규칙 → 포트 → TCP 8765 → 연결 허용

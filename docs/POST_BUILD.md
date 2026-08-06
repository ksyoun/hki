# 빌드 이후 남은 작업 (Speech Analytics 이후)

라이브 발화 패턴 분석(`buffer_projection`)과 디스크 저장이 들어간 **현재 빌드** 기준으로, 아직 구현되지 않았거나 후속 튜닝이 필요한 항목입니다.

---

## 1. TTS 버퍼 / Polish (핵심 기능)

**현재:** `buffer_projection`은 *「N문장 버퍼를 쓰면 얼마나 늦어질까」* 를 **통계로만** 추정합니다. 실제 재생 정책은 바뀌지 않았습니다.

| 항목 | 설명 |
|------|------|
| N문장 버퍼 큐 | 번역 완료된 문장을 N개 모은 뒤, 가장 오래된 것부터 TTS 재생 시작 |
| Polish 단계 | 번역 직후 스페인어 다듬기(LLM 또는 규칙). `HKI_POLISH_MS_PER_UTTERANCE`로 지연 예약만 반영 중 |
| 버퍼 + 실시간 혼합 | 버퍼가 비면 실시간 따라가기, 쌓이면 지연 허용 — 운영 정책 결정 필요 |

**완료 기준:** 운영자가 control UI에서 버퍼 깊이(N) 선택 → captions/TTS 동작이 projection과 일치하는지 라이브로 검증.

### TTS ON 시 자막·음성 동기 출력 (핵심 UX)

**현재:** 번역이 끝나면 **자막은 즉시** WebSocket으로 나가고, TTS는 **별도 직렬 큐**에서 뒤처질 수 있음 → 청중 화면에는 스페인어가 먼저 보이고, 스피커 음성은 수 초~수십 초 늦게 들림.

**목표 (TTS 활성화 시):**

| 경로 | 동작 |
|------|------|
| **번역** | 백그라운드 처리 — 자막/TTS를 막지 않고 계속 큐에서 소비 |
| **자막 + 음성** | **같은 utterance 단위로 묶어서** 동시에 보냄 (한 문장의 ES 텍스트와 TTS PCM이 함께 releasable) |

즉 TTS가 켜진 모드에서는 *「번역 완료 = 자막 표시」* 가 아니라, *「번역 완료 → (버퍼/polish) → 자막과 음성 동시 송출」* 이 되어야 함.

**구현 방향 (초안):**

1. **출력 게이트** — TTS ON이면 `translation` final 이벤트를 captions에 바로 브로드캐스트하지 않고, **재생 준비 큐**에 적재.
2. **동기 릴리스** — 해당 utterance의 TTS 합성(또는 N문장 버퍼의 블록)이 준비되면 `translation` + `tts`를 **같은 타임스탬프/순서**로 방출.
3. **TTS OFF** — 기존과 동일: 번역 final 즉시 자막만 (음성 없음).
4. **버퍼 정책** — §1의 N문장 버퍼와 결합: 버퍼가 찰 때까지 자막도 대기 → 음성과 항상 맞춤.

**완료 기준:** `/captions`에서 스페인어 자막이 바뀌는 순간과 스피커에서 같은 문장이 들리기 시작하는 시점이 체감상 일치 (또는 의도한 고정 지연 N문장 이내).

---

## 2. 측정 정확도 개선

| 항목 | 현재 | 개선 |
|------|------|------|
| 발화 시작 시각 | `first_delta` (전사 첫 토큰) | Realtime API `speech_started` 이벤트 훅 |
| TTS lag 검증 | `observed_tts_lag_ms` (completed → PCM 전송) | 실제 스피커 출력 시각(재생 큐)과 비교 |
| `MS_PER_ES_CHAR_TTS_EST` | 기본 45ms/자 | TTS ON 세션 2~3회 후 `summary.json`으로 보정 |

---

## 3. 파일 테스트 분석

**현재:** 발화 분석은 **라이브 방송만** (`Iniciar transmisión`). 파일 테스트는 기존 `latency` 리포트만 제공.

**후속:** `start_test_streaming`에도 optional collector → 동일 `buffer_projection` UI에서 비교 (리허설 파일 vs 실제 설교).

---

## 4. UI / 운영

- [ ] **Patrones** 버튼: 라이브 종료 후 자동 표시 확인 (captions 미연결 시 utterance 0건 안내)
- [ ] 누적 `summary.json` 추세 그래프 (N=5 p50 세션별)
- [ ] control 패널에 *「예상 TTS 지연 ~Xs」* 미니 위젯 (최근 세션 `buffer_projection[5]`)

---

## 5. 환경 변수 튜닝

```env
# Polish 예약 (버퍼 구현 전 projection 가정치)
HKI_POLISH_MS_PER_UTTERANCE=0

# TTS 미측정 시 문자당 재생 추정 (ms)
HKI_MS_PER_ES_CHAR_TTS_EST=45
```

라이브 1~2회 후 `docs/TUNING.md` §10 절차에 따라 `MS_PER_ES_CHAR` 조정.

---

## 6. 데이터 / 저장

- 세션 JSON: `.hki/analytics/sessions/{timestamp}_{label}.json`
- 누적: `.hki/analytics/summary.json`
- `.gitignore`에 `.hki/analytics/` 포함 — **백업·공유는 수동** (USB/클라우드)

**후속 (선택):** 오래된 세션 자동 정리, export CSV, 다중 PC summary 병합.

---

## 7. 테스트 체크리스트

1. `/captions` 1대 연결 + 라이브 10문장 이상
2. 종료 후 **Patrones** → `buffer_projection["5"].total_est_ms.p50` 존재
3. TTS ON 세션: `observed_tts_lag_ms` vs projection 5 비교
4. 2번째 세션 후 `summary.json`의 `buffer_projection_cumulative` 갱신
5. captions 없이 라이브 → 리포트 없음 또는 0 utterance (정상)

---

## 8. 우선순위 제안

1. **TTS ON 시 자막·음성 동기 출력** — 번역은 백그라운드, 송출은 쌍으로  
2. **라이브 2회 수집** → projection 숫자 신뢰도 확보  
3. **`MS_PER_ES_CHAR` 보정**  
4. **TTS N문장 버퍼 구현** (projection 검증, 동기 게이트와 통합)  
5. Polish 단계 (필요 시)  
6. 파일 테스트·`speech_started`·UI 고도화  

---

## 관련 문서

| 파일 | 내용 |
|------|------|
| `docs/TUNING.md` | VAD, 큐, `buffer_projection` 해석 |
| `hki/live/speech_analytics.py` | 수집·산출·저장 |
| `hki/server/static/speech-analytics.html` | Patrones UI |

*마지막 업데이트: 2026-08-05 — TTS ON 자막·음성 동기 요구사항 추가*

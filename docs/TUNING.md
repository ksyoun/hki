# HKI 튜닝 가이드

실시간 설교 번역의 **파이프라인**, **권장 운영 순서**, **문제가 있을 때만** 건드릴 설정을 짧게 정리합니다.

현재 코드 기본 + 일반적인 로컬 튜닝(VAD 500ms, `gpt-4o-mini` 번역, 히스토리 7, temperature 0.1)은 **시간·비용·정확성 균형이 좋은 구간**입니다. 특별한 증상이 없으면 크게 바꾸지 않는 것을 권장합니다.

---

## 1. 파이프라인

```
오디오 → VAD(말 끝) → Realtime 전사 → [번역 큐] → gpt-4o-mini 번역 → 자막
                                              ↘ (선택) TTS 큐 → 음성
```

**운영 순서 (A / B / C)**

| 단계 | 동작 |
|------|------|
| **Guardar** | 성경+원문 → 참조 추출 → NVI API(`nvies`) → 맥락(요약·outline·용어) |
| **Iniciar** | 전사·번역 시작. 맥락은 `translation_context`가 번역 system prompt로 들어감 |
| **Finalizar** | 방송만 종료. 맥락·잠금은 유지 |

- 번역 system prompt에는 **성경·원문 전문이 아님** — Guardar로 만든 요약·용어·NVI 절만 (`format_context_for_system`).
- Guardar 없이 Iniciar → 경고 후 fallback 번역 (품질·성경 표기 약함).
- 맥락 없을 때만 **방송 중 Guardar** 가능 → 성공 시 실행 중 번역기에 즉시 반영. 맥락 잠금 후에는 서버 재시작까지 재저장 불가.

**게이트:** `/captions` 청중 ≥ `HKI_MIN_AUDIENCE_COUNT`일 때만 전사·번역. TTS는 청중이 스피커 ON일 때만.

---

## 2. 권장 설정 (현재 적정 구간)

| 항목 | 값 | 위치 | 메모 |
|------|-----|------|------|
| VAD 침묵 | **500–600ms** | `.env` | 500=조금 빠름, 600=코드 기본. **450 이하 비권장** |
| VAD prefix | **300ms** | `.env` | 유지 |
| 번역 모델 | **gpt-4o-mini** | `HKI_FINAL_MODEL` | 속도·비용. 큐 적체 최소화 |
| Guardar 모델 | **gpt-4o** | `HKI_CONTEXT_MODEL` | 참조·맥락 전용 |
| 히스토리 | **7** 쌍 | `config.py` | `FINAL_HISTORY_LINES` |
| temperature | **0.1** | `translate.py` | 통역 일관성 |
| TTS | **false** (기본) | `HKI_TTS_ENABLED` | 자막만이면 OFF |

`.env.example`와 로컬 `.env`는 다를 수 있습니다. VAD·TTS는 환경에 맞게만 조정하세요.

---

## 3. 체감 지연 (알아두기)

```
자막 지연 ≈ VAD 대기 + 전사 + (큐에 쌓인 문장 수 × 번역 1회) + 네트워크
```

- 번역은 **한 문장씩 직렬** 처리 (순서·히스토리 유지). 빠른 연설 시 큐가 쌓이면 뒤 자막이 밀립니다.
- `latency.html` 리포트는 **큐 대기 없이** 문장 1개 기준 — 라이브에서 더 느릴 수 있습니다.
- TTS는 번역 **뒤** 또 한 번 큐 대기. 자막이 괜찮은데 음성만 늦으면 TTS 병목.

---

## 4. 증상별 — 이때만 조정

| 증상 | 조치 |
|------|------|
| 자막이 점점 늦어짐 | Guardar 전 완료 권장, NVI 절 범위 과다 여부 확인, TTS OFF, VAD를 더 낮추지 않기 |
| 문장이 잘게 쪼개짐 | VAD **올리기** (550–600) |
| 한국어 오인식 많음 | `HKI_TRANSCRIPTION_MODEL` → `gpt-4o-transcribe` **만** |
| 스페인어 품질만 아쉬움 | Guardar 용어·outline 보강 → 그래도 부족하면 `HKI_FINAL_MODEL` → `gpt-4o` |
| 음성만 크게 밀림 | `HKI_TTS_ENABLED=false` 또는 빠른 연설 구간 **Pausar** |
| 성경 자막·낭독 불일치 | **Guardar** 필수, NVI slug `nvies`, 참조 형식 `Mateo 1:1` |

**피하기:** VAD ≤400, `FINAL_HISTORY_LINES` ≥15, 전사·번역 모델 **동시** 업그레이드.

---

## 5. Guardar · env (필수만)

```env
HKI_CONTEXT_MODEL=gpt-4o
HKI_BIBLE_API_BASE=https://api.midvash.com/v1
HKI_BIBLE_VERSION=nvies
HKI_FINAL_MODEL=gpt-4o-mini
HKI_VAD_SILENCE_DURATION_MS=500
HKI_TTS_ENABLED=false
```

Midvash 스페인어 NVI는 slug **`nvies`** (Portuguese `nvi`와 다름).

---

## 6. 놓치기 쉬운 운영 포인트

1. **라이브 전 Guardar** — 맥락·NVI·용어집이 없으면 실시간 품질과 성경 표기가 떨어집니다.
2. **맥락 없이 시작했다면** — 방송 중 Guardar 가능(`context_ready`가 되기 전). 성공 후 utterance부터 맥락 반영, 입력 카드 잠금.
3. **찬양·기도** — **Pausar**로 전사·번역 API 절약 (`MIN_AUDIENCE_COUNT`는 청중 수 기준).
4. **큐 적체** — 설정보다 **연설 속도·문장 길이** 영향이 큽니다. 리허설 5분으로 밀림 여부 확인.
5. **서버 재시작 / Liberar contexto** — `context_ready`·입력 카드 잠금 초기화. UI **Liberar contexto**로 재시작 없이 Guardar 다시 가능 (방송 중이면 이후 문장은 fallback 품질).
6. **청중 게이트** — `/captions` 연결 ≥ `HKI_MIN_AUDIENCE_COUNT`일 때만 전사·번역. 운영 화면에 「Esperando audiencia」 배너 표시.

---

## 7. 관련 파일

| 파일 | 역할 |
|------|------|
| `hki/live/pipeline.py` | 전사 → 번역, `apply_translation_context` |
| `hki/live/translate.py` | 번역 큐, system prompt |
| `hki/live/context.py` | Guardar, `format_context_for_system` |
| `hki/config.py` | env, `FINAL_HISTORY_LINES` |
| `.env.example` | env 템플릿 |

---

*2026-08-06 — 간결판: 현재 설정을 적정 구간으로 문서화*

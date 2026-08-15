# HKI 튜닝 가이드

실시간 설교 번역의 **파이프라인**, **권장 운영 순서**, **문제가 있을 때만** 건드릴 설정을 짧게 정리합니다.

현재 코드 기본 + 일반적인 로컬 튜닝(VAD 500ms, `gpt-4o-mini` 번역, 히스토리 7, temperature 0.1)은 **시간·비용·정확성 균형이 좋은 구간**입니다. 특별한 증상이 없으면 크게 바꾸지 않는 것을 권장합니다.

---

## 1. 파이프라인

```
오디오 → VAD → Realtime 전사 → [번역 큐] → fragment ES
  → OutputComposer (배치 재조합) → ReleasePacer → 자막 (translation)
                                              ↘ (스피커 ON) TTS 큐 → 음성+자막(tts, 동일 텍스트)
```

**운영 순서 (A / B / C)**

| 단계 | 동작 |
|------|------|
| **Contextualizar** | 성경+원문 → 참조 추출 → NVI API(`nvies`) → 맥락(요약·outline·용어) |
| **Iniciar** | 전사·번역 시작. 기본은 **modo general** (기도·안내). 설교 구간에서 **▶ Sermón** |
| **Finalizar** | 방송 종료. **Pausar**와 같이 translator·composer drain 후 종료 (미전송 fragment 최대한 방출) |

- 번역 system prompt에는 **성경·원문 전문이 아님** — Contextualizar로 만든 요약·용어·NVI 절만 (`format_context_for_system`).
- Contextualizar 없이 Iniciar → 경고 후 fallback 번역 (품질·성경 표기 약함).
- Contextualizar 후 Iniciar → **modo sermón은 자동이 아님** (`sermon_on=false`). 패시지 읽기 전 **▶ Sermón** 또는 `HKI_AUTO_SERMON_ON=true`.
- `HKI_AUTO_SERMON_ON=true` → Contextualizar 완료 시 Iniciar 직후 sermon 모드 (기도 먼저면 false 유지).
- Iniciar 직후 context 있으면 운영 UI에 **「Activar sermón」** 모달 (auto 아닐 때).
- 번역 전 `key_names.stt_variants`로 KO STT 정규화 (`normalize_ko_stt`). recombine 앵커 매칭에도 동일 적용.
- 운영자 자막 미리보기: `had_incierto` / `repair_rejected` / `recombine_flags` 경고. **관객 `/captions`에는 `[INCIERTO]` 미표시** (strip).
- 맥락 없을 때만 **방송 중 Contextualizar** 가능 → 성공 시 실행 중 번역기에 즉시 반영. 잠금 후에는 **Liberar contexto**(`/api/live/reset-context`)로 해제 후 다시 Contextualizar 가능.

**게이트:** `/captions` 청중 ≥ `HKI_MIN_AUDIENCE_COUNT`일 때만 전사·번역. TTS는 청중이 스피커 ON일 때만.

---

## 2. 권장 설정 (현재 적정 구간)

| 항목 | 값 | 위치 | 메모 |
|------|-----|------|------|
| VAD 침묵 | **500–600ms** | `.env` | 500=조금 빠름, 600=코드 기본. **450 이하 비권장** |
| VAD prefix | **300ms** | `.env` | 유지 |
| 번역 모델 | **gpt-4o-mini** | `HKI_FINAL_MODEL` | 속도·비용. 큐 적체 최소화 |
| Contextualizar 모델 | **gpt-4o** | `HKI_CONTEXT_MODEL` | 참조·맥락 전용 |
| 히스토리 | **7** 쌍 | `config.py` | `FINAL_HISTORY_LINES` |
| temperature (번역) | **0.1** | `HKI_FINAL_TEMPERATURE` | gpt-4o·mini만. Luna(gpt-5*)는 무시 |
| temperature (recombine) | **0.05** | `HKI_RECOMBINE_TEMPERATURE` | OUTPUT_PREP / FINAL fallback |
| Sermón auto | **false** | `HKI_AUTO_SERMON_ON` | Contextualizar 후 Iniciar 시 sermon_on |
| Recombine 항상 | **false** | `HKI_OUTPUT_ALWAYS_RECOMBINE` | 단일 fragment도 LLM 재조합 (구두점) |
| TTS | **false** (기본) | `HKI_TTS_ENABLED` | 자막만이면 OFF |
| Output 배치 | **2** | `HKI_OUTPUT_BATCH_SIZE` | fragment 재조합 후 1줄 release (`HKI_TTS_PREP_*` alias) |
| Output timeout | **2500ms** | `HKI_OUTPUT_TIMEOUT_MS` | 1문장만 쌓일 때 flush |
| Release base | **1500ms** | `HKI_OUTPUT_RELEASE_BASE_MS` | 큐 여유 시 줄 간격 |
| Release min | **700ms** | `HKI_OUTPUT_RELEASE_MIN_MS` | 백로그 가속 하한 (`base/√depth`) |
| Caption lines (operador) | **8** | `HKI_CAPTION_MAX_LINES` | Vista previa en control: máx. líneas en DOM (fade-out). **Pantalla pública `/captions` no borra** — acumula y scroll |
| 재생 가속 threshold | **3** | `HKI_TTS_PLAYBACK_SPEED_THRESHOLD` | 클라이언트 큐 깊이 |
| 재생 가속 max | **1.15** | `HKI_TTS_PLAYBACK_SPEED_MAX` | 1.2 초과 비권장 |

`.env.example`와 로컬 `.env`는 다를 수 있습니다. VAD·TTS는 환경에 맞게만 조정하세요.

---

## 3. 체감 지연 (알아두기)

```
자막 지연 ≈ VAD 대기 + 전사 + (큐에 쌓인 문장 수 × 번역 1회) + 네트워크
```

- 번역은 **한 문장씩 직렬** 처리 (순서·히스토리 유지). 빠른 연설 시 큐가 쌓이면 뒤 자막이 밀립니다.
- `latency.html` 리포트는 **큐 대기 없이** 문장 1개 기준 — 라이브에서 더 느릴 수 있습니다.
- **OutputComposer**가 fragment를 배치·재조합한 뒤 **ReleasePacer**로 자막·TTS를 동시에 내보냅니다 (스피커 ON/OFF 동일 텍스트).
- 백로그 시 항목 drop 없음 — Pacer가 `base/√depth`로 가속, 클라이언트 TTS 재생도 `HKI_TTS_PLAYBACK_SPEED_*`로 따라가기.

---

## 4. OutputComposer · ReleasePacer 튜닝

| 목표 | `HKI_OUTPUT_BATCH_SIZE` | `HKI_OUTPUT_TIMEOUT_MS` | release |
|------|-------------------------|-------------------------|---------|
| 더 빠름 (덜 매끄러움) | 1 | 1500–2000 | base↓ / min↓ |
| 균형 (기본) | 2 | 2500 | base 1500, min 700 |
| 더 자연스러운 연결 | 3 | 3000–3500 | base 유지 |

- 배치↑ → 재조합·TTS 호출 감소, **첫 줄 지연↑**
- timeout↓ → 1문장도 빨리 나가지만 재조합 이점 감소
- 큐 depth↑ → 간격 ≈ `max(min, base/√depth)` 로 가속 (다다다닥 방지 + 과도한 밀림 완화)
- 적체 시 클라이언트 `HKI_TTS_PLAYBACK_SPEED_MAX` (기본 1.15) — 삭제 없음

`HKI_TTS_PREP_BATCH_SIZE` / `HKI_TTS_PREP_TIMEOUT_MS` 는 동일 설정의 alias입니다.

---

## 5. 증상별 — 이때만 조정

| 증상 | 조치 |
|------|------|
| 자막이 점점 늦어짐 | Contextualizar 전 완료 권장, NVI 절 범위 과다 여부 확인, TTS OFF, VAD를 더 낮추지 않기 |
| 문장이 잘게 쪼개짐 | VAD **올리기** (550–600) |
| 한국어 오인식 많음 | `HKI_TRANSCRIPTION_MODEL` → `gpt-4o-transcribe` **만** |
| 스페인어 품질만 아쉬움 | Contextualizar 용어·outline 보강 → 그래도 부족하면 `HKI_FINAL_MODEL` → `gpt-4o` |
| 음성만 크게 밀림 | `HKI_OUTPUT_BATCH_SIZE=1`, timeout·release base 낮추기, 또는 빠른 연설 구간 **Pausar** |
| 설교인데 기도체 번역 | Iniciar 후 **▶ Sermón** 눌렀는지 확인 (`sermon_on`) |
| recombine «uno dos»만 | LLM reject/fallback — 운영자 경고 확인; Contextualizar `critical_sentences.es` 보강 |
| 자막이 다다다닥 | `HKI_OUTPUT_RELEASE_MIN_MS` 올리기 (예: 900) |
| 성경 자막·낭독 불일치 | **Contextualizar** 필수, NVI slug `nvies`, 참조 형식 `Mateo 1:1` |

**피하기:** VAD ≤400, `FINAL_HISTORY_LINES` ≥15, 전사·번역 모델 **동시** 업그레이드.

---

## 6. Contextualizar · env (필수만)

```env
HKI_CONTEXT_MODEL=gpt-4o
HKI_BIBLE_API_BASE=https://api.midvash.com/v1
HKI_BIBLE_VERSION=nvies
HKI_FINAL_MODEL=gpt-4o-mini
HKI_FINAL_TEMPERATURE=0.1
HKI_RECOMBINE_TEMPERATURE=0.05
HKI_VAD_SILENCE_DURATION_MS=500
HKI_TTS_ENABLED=false
HKI_OUTPUT_BATCH_SIZE=2
HKI_OUTPUT_TIMEOUT_MS=2500
HKI_OUTPUT_RELEASE_BASE_MS=1500
HKI_OUTPUT_RELEASE_MIN_MS=700
HKI_CAPTION_MAX_LINES=8
# HKI_AUTO_SERMON_ON=true
# HKI_OUTPUT_ALWAYS_RECOMBINE=true
# aliases: HKI_TTS_PREP_BATCH_SIZE / HKI_TTS_PREP_TIMEOUT_MS
```

Midvash 스페인어 NVI는 slug **`nvies`** (Portuguese `nvi`와 다름).

---

## 7. 놓치기 쉬운 운영 포인트

1. **라이브 전 Contextualizar** — 맥락·NVI·용어집·`critical_sentences` ko/es. 없으면 실시간 품질·앵커 수리 약함.
2. **Iniciar 후 Sermón** — Contextualizar만 하고 Sermón 안 누르면 general 프롬프트로 설교 번역됨. `HKI_AUTO_SERMON_ON` 또는 모달로 보완.
3. **맥락 없이 시작했다면** — 방송 중 Contextualizar 가능. 성공 후 utterance부터 맥락 반영.
4. **찬양·기도** — **Pausar** (전사·번역 절약 + 큐 drain). **Finalizar**도 drain 후 종료 — 급하게 끊으면 마지막 1–2 fragment만 늦게 나올 수 있음.
4. **큐 적체** — 설정보다 **연설 속도·문장 길이** 영향이 큽니다. 리허설 5분으로 밀림 여부 확인.
5. **서버 재시작 / Liberar contexto** — `context_ready`·입력 카드 잠금 초기화.
6. **청중 게이트** — `/captions` 연결 ≥ `HKI_MIN_AUDIENCE_COUNT`일 때만 전사·번역.

---

## 8. 관련 파일

| 파일 | 역할 |
|------|------|
| `hki/live/pipeline.py` | 전사 → 번역 → OutputComposer → TTS |
| `hki/live/output_composer.py` | 배치 재조합 + 적응형 release pacing |
| `hki/live/tts.py` | TTS 합성 큐 |
| `hki/live/translate.py` | 번역 큐, system prompt |
| `hki/live/context.py` | Contextualizar, `format_context_for_system` |
| `hki/config.py` | env, `FINAL_HISTORY_LINES` |
| `.env.example` | env 템플릿 |

---

*2026-08-06 — 간결판: 현재 설정을 적정 구간으로 문서화*

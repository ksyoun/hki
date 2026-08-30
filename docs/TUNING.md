# HKI 튜닝 가이드

실시간 설교 번역의 **파이프라인**, **권장 운영 순서**, **문제가 있을 때만** 건드릴 설정을 짧게 정리합니다.

현재 코드 기본 + 일반적인 로컬 튜닝(VAD 500ms, `gpt-4o-mini` 번역, 히스토리 7, temperature 0.1)은 **시간·비용·정확성 균형이 좋은 구간**입니다. 특별한 증상이 없으면 크게 바꾸지 않는 것을 권장합니다.

---

## 1. 파이프라인

방송 시작 시 **모달로 고르지 않습니다.** `.env`로 켜고 끕니다. 둘 다 `true`(기본)이면 **Realtime STT가 둘**입니다(비용 약 2배). 운영자 `/captions`·라이브 KO는 **클래식 STT**. 오라시온은 짧은 VAD STT를 쓰고 `/log` Por oración의 `original_stt`에만 쌓입니다. 관객 자막/TTS는 **clásico**. 종료 후 `/log`에서 STT · clásico · por oración 세 탭을 비교합니다. A/B는 **같은 STT 컷이 아닙니다.**

```
HKI_PIPELINE_LEGACY=true
HKI_PIPELINE_SENTENCE=true
```

| 설정 | 동작 |
|------|------|
| 둘 다 true | A/B: 자막 라이브 = clásico, sentence는 로그 전용 |
| legacy만 true | 기존 fragment + recombine만 |
| sentence만 true | por oración가 자막/TTS |
| 둘 다 false | 안전 기본값으로 legacy만 켭니다 |

### Clásico (legacy)

```
오디오 → VAD → Realtime 전사 → [번역 큐] → fragment ES
  → OutputComposer (배치 재조합) → ReleasePacer → 자막 (translation)
                                              ↘ (스피커 ON) TTS 큐
```

### Por oración (KO) — sentence

```
오디오 → STT oración (VAD 250ms, 클래식과 별도 세션)
  → KO pending. 마지막 조각이 열린 어미면 SENTENCE_INCOMPLETE_TIMEOUT_MS 대기
  → KO Recombine 1회 (표면 어미로 잇기. NVI 없음)
  → 마지막 unit이 아직 열려 있으면 leftover (force가 아닐 때)
  → unit마다 Translate (ES + NVI = reference, FRAGMENT_ENDING_RULES)
  → ReleasePacer → (단독일 때 자막/TTS, A/B일 때 /log만)
```

문장판단 Understand LLM은 없다. debounce는 **문장 경계가 아니라** 발화 묶음 타이머다. `fragment_looks_open_ko`가 타이밍 권위(클래식 ES 규칙은 안 봄).

| 항목 | Clásico | Por oración |
|------|---------|----------------|
| STT | `HKI_VAD_SILENCE_DURATION_MS` (운영자 KO) | `HKI_SENTENCE_VAD_SILENCE_DURATION_MS` (기본 250) |
| 번역 | fragment당 1 LLM | release당 Recombine 1 + unit당 Translate |
| 묶음 | ES recombine (번역 후) | KO recombine (번역 전) |
| 열린 조각 | `OUTPUT_INCOMPLETE_TIMEOUT_MS` | `SENTENCE_INCOMPLETE_TIMEOUT_MS` |
| 튜닝 | `OUTPUT_BATCH_SIZE` | `SENTENCE_RELEASE_PAUSE_MS`, `SENTENCE_MAX_PENDING`, `SENTENCE_MAX_BUFFER_MS` |

`/log`는 클래식·오라시온이 **같은 트레이스 필드**를 쓴다 (`hki/live/trace_schema.py`). `release_reason`은 `closed_immediate` / `partner_arrived` / `incomplete_cap_expired` / `max_pending` / `max_duration` / `drain` / `recombine_fallback` / `translation_failed`. 오라시온의 옛 `vad_release`는 닫힘이면 `closed_immediate`, 캡 만료면 `incomplete_cap_expired`. `max_duration`이 잦으면 debounce가 길거나 VAD fragment가 긴 신호다. 종료 drain 후 미번역 pending은 `translation_failed`.

### 릴리스 트레이스 필드 (`action=release` 한 줄)

시각은 **unix ms 정수**(UTC). 지연은 monotonic 차분. Realtime은 샘플 PTS가 없어서 `t_audio_start`는 VAD/`speech_started` 폴백이다.

| 필드 | 의미 |
|------|------|
| `t_audio_start` | 이 출력의 발화 시작 |
| `t_audio_start_source` | 항상 하나: `speech_started` / `first_delta` / `fallback` (`fallback` = `t_stt_final`과 같음) |
| `t_stt_final` | 이 줄에 속한 마지막 STT `completed` |
| `t_release` | 페이서가 화면/TTS로 방출한 시각 |
| `latency_stt_to_release` | `t_release - t_stt_final` (옛 오라시온 last→cap) |
| `latency_speech_to_release` | `t_release - t_audio_start`. source가 `fallback`이면 **실제보다 짧게** 보임 (발화 시작~STT 확정이 빠짐) |
| `used_llm_translate` / `translate_llm_ms` | 번역 LLM을 불렀을 때만 ms, 아니면 0 |
| `used_llm_recombine` / `recombine_llm_ms` | 재조합 LLM을 불렀을 때만 ms (passthrough 벽시계는 넣지 않음) |
| `hold_ms` / `hold_reason` | 열림·배치로 **추가로** 쉰 시간. 클래식 닫힘 즉시 flush는 0. 2500ms incomplete 대기는 여기 (페이서 아님) |
| `pacer_wait_ms` | 그 줄이 페이서 큐에서 방출까지 |
| `fragment_open_final` | 클래식=`fragment_looks_open(ko, es)`, 오라시온=`fragment_looks_open_ko` |
| `tokens_translate_*` / `tokens_recombine_*` | 줄 단위. 공유 recombine은 **줄마다 전체 복사** (쪼개지 않음) |

한 번의 KO recombine이 unit 여러 줄을 만들면 `recombine_id`·`recombine_llm_ms`·recombine 토큰·`hold_ms`를 각 줄에 그대로 복사한다. **합산은 `recombine_id`당 한 줄** (`unit_index == 0`). 줄별 `mean(hold_ms)`는 대기를 unit 수만큼 반복하므로 쓰지 않는다. `pacer_wait_ms`와 `translate_llm_ms` / `tokens_translate_*`는 줄마다 고유.

세션 코멘트 `audio_start: speech_started N / first_delta N / fallback N`으로 파이프라인별 폴백 비율을 본다. 짧은 오라시온 VAD(200–300ms)에서는 `speech_started`가 빠지거나 늦을 수 있고, `fallback`이 많으면 E2E가 과소측정된다. 주간 비교는 신규 세션 JSON만 (`parse_release_trace`). 옛 `r=`/`t=`/`last→cap`/`release_latency_ms`/`through_index`/`passthrough`/`vad_release`는 새 트레이스에 없다.

클래식 OutputComposer: **닫힌 fragment는 배치 대기 없이 즉시 flush.** 열린 fragment(말줄임표·연결어미)만 `OUTPUT_INCOMPLETE_TIMEOUT_MS`(4500) 동안 다음 조각을 기다린다. `OUTPUT_TIMEOUT_MS`는 클래식 flush에 쓰지 않는다. VAD 값은 이 변경과 독립이다. 남은 중간 절단은 배포 후 `HKI_VAD_SILENCE_DURATION_MS` 500→600 실험.

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
| VAD 침묵 (클래식) | **500–600ms** | `.env` | 운영자 KO. 500=조금 빠름, 600=코드 기본. **450 이하 비권장** |
| VAD prefix | **300ms** | `.env` | 클래식·오라시온 공통 기본 |
| VAD 침묵 (오라시온) | **250ms** | `HKI_SENTENCE_VAD_SILENCE_DURATION_MS` | 실험 200–300. A/B면 STT 세션 2개 |
| 번역 모델 | **gpt-4o-mini** | `HKI_FINAL_MODEL` | 속도·비용. 큐 적체 최소화 |
| Contextualizar 모델 | **gpt-4o** | `HKI_CONTEXT_MODEL` | 참조·맥락 전용 |
| 히스토리 | **7** 쌍 | `config.py` | `FINAL_HISTORY_LINES` |
| temperature (번역) | **0.1** | `HKI_FINAL_TEMPERATURE` | gpt-4o·mini만. Luna(gpt-5*)는 무시 |
| temperature (recombine) | **0.05** | `HKI_RECOMBINE_TEMPERATURE` | OUTPUT_PREP / FINAL fallback |
| Sermón auto | **false** | `HKI_AUTO_SERMON_ON` | Contextualizar 후 Iniciar 시 sermon_on |
| Recombine 항상 | **false** | `HKI_OUTPUT_ALWAYS_RECOMBINE` | 단일 fragment도 LLM 재조합 (구두점) |
| TTS | **false** (기본) | `HKI_TTS_ENABLED` | 자막만이면 OFF |
| Output 배치 | **2** | `HKI_OUTPUT_BATCH_SIZE` | 열린 조각이 짝을 만나면 즉시 recombine (`HKI_TTS_PREP_*` alias) |
| Output 미완성 대기 | **4500ms** | `HKI_OUTPUT_INCOMPLETE_TIMEOUT_MS` | 열린 fragment만. 닫힌 조각은 즉시 flush |
| Output timeout | (미사용) | `HKI_OUTPUT_TIMEOUT_MS` | 클래식 flush에 쓰지 않음 (compat) |
| Release base | **1500ms** | `HKI_OUTPUT_RELEASE_BASE_MS` | 큐 여유 시 줄 간격 |
| Release min | **700ms** | `HKI_OUTPUT_RELEASE_MIN_MS` | 백로그 가속 하한 (`base/√depth`) |
| Caption lines (operador) | **8** | `HKI_CAPTION_MAX_LINES` | Vista previa en control: máx. líneas en DOM (fade-out). **Pantalla pública `/captions` no borra** — acumula y scroll |
| Pipeline clásico | **true** | `HKI_PIPELINE_LEGACY` | fragment + recombine. A/B 시 자막 라이브 담당 |
| Pipeline por oración | **true** | `HKI_PIPELINE_SENTENCE` | KO buffer + recombine. A/B 시 /log 비교용 |
| Sentence debounce | **400ms** | `HKI_SENTENCE_RELEASE_PAUSE_MS` | 마지막 STT completed 기준. 문장 판단 아님 |
| Sentence 미완성 대기 | **4500ms** | `HKI_SENTENCE_INCOMPLETE_TIMEOUT_MS` | 마지막 KO가 열린 어미일 때만. 클래식 incomplete와 별개 |
| Sentence max buffer | **8000ms** | `HKI_SENTENCE_MAX_BUFFER_MS` | 연속 발화 safety. 문장 판단 아님 |
| Sentence max pending | **6** | `HKI_SENTENCE_MAX_PENDING` | fragment 상한 초과 시 강제 recombine |
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

| 목표 | `HKI_OUTPUT_BATCH_SIZE` | `HKI_OUTPUT_INCOMPLETE_TIMEOUT_MS` | release |
|------|-------------------------|-----------------------------------|---------|
| 더 빠름 (덜 매끄러움) | 1 | 2500–3500 | base↓ / min↓ |
| 균형 (기본) | 2 | 4500 | base 1500, min 700 |
| 더 자연스러운 연결 | 3 | 5000–6000 | base 유지 |

- 닫힌 fragment는 timeout과 무관하게 **즉시** flush
- 배치↑ → 열린 조각 재조합 기회↑, 닫힌 조각 지연은 없음
- 미완성 timeout↓ → 열린 조각도 빨리 나가지만 이음 이점 감소
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
| 음성만 크게 밀림 | `HKI_OUTPUT_BATCH_SIZE=1`, release base 낮추기, 또는 빠른 연설 구간 **Pausar** |
| 설교인데 기도체 번역 | Iniciar 후 **▶ Sermón** 눌렀는지 확인 (`sermon_on`) |
| recombine «uno dos»만 | LLM reject/fallback — 운영자 경고 확인; Contextualizar `critical_sentences.es` 보강 |
| 자막이 다다다닥 | `HKI_OUTPUT_RELEASE_MIN_MS` 올리기 (예: 900) |
| 성경 자막·낭독 불일치 | **Contextualizar** 필수, NVI slug `nvies`, 참조 형식 `Mateo 1:1` |

**피하기:** 클래식 VAD ≤400, `FINAL_HISTORY_LINES` ≥15, 전사·번역 모델 **동시** 업그레이드. 오라시온 STT 250은 A/B 실험값(200–300).

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
HKI_SENTENCE_VAD_SILENCE_DURATION_MS=250
HKI_SENTENCE_INCOMPLETE_TIMEOUT_MS=4500
HKI_TTS_ENABLED=false
HKI_OUTPUT_BATCH_SIZE=2
HKI_OUTPUT_INCOMPLETE_TIMEOUT_MS=4500
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
| `hki/live/trace_schema.py` | 통일 릴리스 트레이스 (`build_release_trace` / `parse_release_trace`) |
| `hki/live/ko_sentence_translator.py` | por oración: KO buffer → Recombine → Translate |
| `hki/live/sentence_prompts.py` | recombine(KO 정리)·번역(ES+NVI) 프롬프트 |
| `hki/live/sentence_guard.py` | recombine unit mapping, KO vs source 가드 |
| `hki/live/context.py` | Contextualizar, recombine/translate 컨텍스트 뷰 |
| `hki/config.py` | env, `FINAL_HISTORY_LINES` |
| `.env.example` | env 템플릿 |

---

*2026-08-06 — 간결판: 현재 설정을 적정 구간으로 문서화*

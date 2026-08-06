# HKI 튜닝 가이드 — 빠르고 정확한 실시간 번역

이 문서는 HKI 실시간 설교 번역 시스템의 **조정 가능한 옵션**, **각 옵션이 품질·속도·비용에 미치는 영향**, 그리고 **「빠르고 정확하게」를 목표로 할 때의 추천 순서**를 정리합니다.

---

## 목차

1. [파이프라인 이해하기](#1-파이프라인-이해하기)
2. [번역 큐 (Translation Queue)](#2-번역-큐-translation-queue)
3. [현재 적용된 설정값](#3-현재-적용된-설정값)
4. [지금 바로 조정 가능한 옵션 (.env)](#4-지금-바로-조정-가능한-옵션-env)
5. [코드 수준 옵션](#5-코드-수준-옵션)
6. [구조·기능 개선 여지](#6-구조기능-개선-여지)
7. [빠르고 정확하게 — 추천 전략](#7-빠르고-정확하게--추천-전략)
8. [튜닝 실전 순서](#8-튜닝-실전-순서)
9. [피해야 할 설정](#9-피해야-할-설정)

---

## 1. 파이프라인 이해하기

청중이 체감하는 지연은 대략 다음 단계의 합입니다.

```
[오디오 입력] → [VAD: 말 끝 감지] → [전사 완료] → [번역 큐 대기] → [번역 GPT] → [자막 표시] → (선택) [TTS 큐] → [음성 재생]
```

| 단계 | 담당 | 설정 |
|------|------|------|
| 전사 | OpenAI Realtime API | `HKI_TRANSCRIPTION_MODEL` |
| 문장 분리 | 서버 VAD | `HKI_VAD_SILENCE_DURATION_MS`, `HKI_VAD_PREFIX_PADDING_MS` |
| 번역 대기·처리 | `Translator` 직렬 큐 | `hki/live/translate.py` |
| 번역 | Chat Completions | `HKI_FINAL_MODEL` + 성경/원고 system prompt |
| 맥락 유지 | 번역 히스토리 | `FINAL_HISTORY_LINES` |
| 음성 출력 | TTS 직렬 큐 | `HKI_TTS_*` |

**게이트:** `/captions` 청중이 `HKI_MIN_AUDIENCE_COUNT` 이상일 때만 전사·번역이 동작합니다. TTS는 청중이 스피커를 켰을 때만 생성됩니다.

---

## 2. 번역 큐 (Translation Queue)

번역은 문장이 완성될 때마다 **비동기 큐**에 쌓였다가, **한 번에 한 문장씩** GPT API를 호출합니다. 구현은 `hki/live/translate.py`의 `Translator` 클래스입니다.

### 2.1 동작 흐름

```
전사 완료 (pipeline)
    │
    ▼
on_transcript_completed(item_id, ko_text)   ← 즉시 반환 (블로킹 없음)
    │
    ▼
_final_queue.put((item_id, ko_text))        ← asyncio.Queue에 적재
    │
    ▼
_final_worker (별도 asyncio 태스크)           ← 방송 시작 시 translator.run()으로 기동
    │
    ├─ queue.get() 으로 FIFO 순서로 꺼냄
    ├─ await _translate()                     ← OpenAI Chat Completions 1회 (직렬)
    ├─ _history에 KO→ES 쌍 저장
    └─ on_translation 콜백 → 자막 브로드캐스트 + (TTS 큐에 적재)
```

**핵심 코드 경로**

| 단계 | 파일 | 설명 |
|------|------|------|
| 전사 완료 시 큐 적재 | `pipeline.py` → `translate.py` | `_on_transcript_completed`가 `on_transcript_completed` 호출 |
| 큐 적재 | `translate.py:57-58` | `await self._final_queue.put(...)` |
| 직렬 소비 | `translate.py:91-97` | `_final_worker`가 한 건씩 `_translate` 실행 |
| 결과 전달 | `pipeline.py` | `_on_translation` → WebSocket 자막 + TTS `speak()` |

### 2.2 직렬 처리인 이유

- **순서 보장:** 설교 자막은 문장 순서가 뒤바뀌면 안 됩니다. 병렬 번역은 빠를 수 있으나 완료 순서가 뒤섞일 수 있습니다.
- **히스토리 일관성:** `_history`에 이전 번역 결과를 넣고 다음 문장에 붙이므로, 앞 문장 번역이 끝나야 다음 문장 맥락이 정확합니다.
- **비용 예측:** 동시에 여러 GPT 호출을 열지 않아 rate limit·비용 급증을 줄입니다.

### 2.3 체감 지연에 미치는 영향

번역 큐가 **병목**이 되는 전형적인 상황:

| 상황 | 결과 |
|------|------|
| 목사님이 빠르게 연속으로 말함 | 큐에 문장이 쌓임 → 뒤쪽 자막이 점점 늦게 표시 |
| GPT 응답이 느린 문장 (긴 문장·복잡한 구절) | 그동안 다음 문장도 대기 |
| 성경+원고가 매우 김 | 문장당 입력 토큰↑ → 번역 1회 지연↑ → 큐 적체 가속 |

**체감 지연 공식 (단순화):**

```
자막 지연 ≈ VAD 대기 + 전사 처리 + (큐에 쌓인 문장 수 × 번역 1회 소요) + 네트워크
```

latency 리포트의 `final_after_utterance_ms`는 **큐 대기 없이** 문장 1개 기준입니다. 설교 중 연속 발화 시 실제 지연은 이보다 길어질 수 있습니다.

### 2.4 TTS 큐와의 관계

번역 큐와 **별도**로 `hki/live/tts.py`의 `TTSClient`도 직렬 큐를 사용합니다.

```
번역 완료 → tts.speak() → _queue.put → _synthesize (직렬) → WebSocket 오디오
```

- 자막은 번역 완료 즉시 표시됩니다.
- TTS는 번역 큐 **뒤**에 또 한 번 대기합니다.
- TTS 합성 + 재생 시간(`asyncio.sleep(duration)`) 동안 다음 TTS도 대기합니다.
- **자막만** 중요하면 TTS를 끄면(`HKI_TTS_ENABLED=false`) TTS 큐 병목이 사라집니다.

### 2.5 현재 구조의 한계 (알아두면 좋은 점)

| 한계 | 설명 |
|------|------|
| 큐 상한 없음 | `_final_queue`에 최대 길이 제한이 없음. 장시간 적체 시 메모리·지연 증가 |
| 스킵 없음 | 오래된 문장을 건너뛰는 로직 없음. 늦더라도 전부 번역 시도 |
| 방송 종료 시 | `stop()`은 worker만 멈추고, 큐에 남은 항목은 버려짐 |
| 병렬화 없음 | 속도 우선이면 구조 변경 필요 (순서·맥락 트레이드오프) |

### 2.6 큐 적체를 줄이는 방법

1. **VAD·전사 품질** — 문장 수 자체를 줄이거나 전사 지연을 줄임
2. **번역 입력 토큰 줄이기** — 원고를 오늘 분량만, `FINAL_HISTORY_LINES` 과도하게 올리지 않기
3. **빠른 번역 모델 유지** — `gpt-4o-mini` 유지
4. **TTS 끄기** — 자막 경로와 무관하지만 서버 부하·체감 “밀림” 완화
5. **(향후)** 큐 깊이 모니터링, N문장 이상 적체 시 알림 또는 오래된 항목 스킵

---

## 3. 현재 적용된 설정값

코드·운영 환경에 반영된 값과 평가입니다. (`.env`는 로컬마다 다를 수 있음)

| 항목 | 이전 | **현재** | 위치 | 평가 |
|------|------|----------|------|------|
| `FINAL_HISTORY_LINES` | 5 | **7** | `config.py` | ✅ 용어·담화 일관성 개선. 5 대비 토큰·지연 소폭↑이나 체감 품질 향상에 유리. **빠름+정확 목표에 적합한 구간.** |
| `temperature` | 0.2 | **0.1** | `translate.py` | ✅ 번역 표현 흔들림 감소, 신학 용어·인명 표기 안정화. 설교 통역에 **0.1이 더 적합**. 창의적 의역은 줄어듦. |
| `_history` 메모리 상한 | 10 | **14** | `translate.py` | ✅ `FINAL_HISTORY_LINES=7`의 2배 여유. 히스토리를 10~12로 올릴 때 코드 수정 없이 대응 가능. |
| `HKI_VAD_SILENCE_DURATION_MS` | 600 (기본) | **500** | `.env` | ✅ 자막 약 100ms 빨라짐. 450보다 문장 중간 쉼에서 덜 잘림. **600과 450의 균형점.** |
| `HKI_VAD_PREFIX_PADDING_MS` | 300 | **300** | `.env` | ✅ 유지. 시작 음절 보호에 무난. |
| `HKI_TTS_ENABLED` | false (기본) | **true** | `.env` | ⚠️ 청각 접근성↑, 비용·TTS 큐 병목 가능. 자막 속도만 중요한 날은 `false` 고려. |
| `HKI_TTS_INSTRUCTIONS` | (미설정) | **config.py 기본값 사용** | `config.py` | ✅ `.env`에 없어도 아르헨티나 voseo·교회 발음 지시가 API에 전달됨. |

**종합:** 현재 조합은 **「정확도·일관성 우선, 속도는 VAD 500으로 보완」** 방향입니다. 다음 점검 포인트는 **번역 큐 적체**(빠른 연설 시 자막 밀림)와 **TTS가 자막보다 늦게 따라오는지** 여부입니다.

---

## 4. 지금 바로 조정 가능한 옵션 (.env)

### 4.1 VAD (Voice Activity Detection)

| 변수 | 코드 기본값 | **현재 운영값** | 역할 |
|------|-------------|-----------------|------|
| `HKI_VAD_SILENCE_DURATION_MS` | `600` | **`500`** | 말 끝 침묵 대기 |
| `HKI_VAD_PREFIX_PADDING_MS` | `300` | **`300`** | 문장 앞 오디오 패딩 |

#### `HKI_VAD_SILENCE_DURATION_MS` — 왜 중요한가

- VAD는 번역이 시작되기 **전**에 반드시 거치는 **고정 대기**입니다.
- 값이 **크면** (700~800ms): 문장 덜 잘림, 자막 느림
- 값이 **작으면** (300~400ms): 자막 빠름, 문장 과분할·번역 큐 적체·오역 위험
- **현재 500ms:** 기본 600보다 약 100ms 빠르면서, 450보다 안정적. 빠름+정확 전략에 맞는 선택.

#### `HKI_VAD_PREFIX_PADDING_MS`

- 시작 음절 잘림 방지. `300ms` 유지 권장.

---

### 4.2 전사 모델

| 변수 | 현재값 |
|------|--------|
| `HKI_TRANSCRIPTION_MODEL` | `gpt-4o-mini-transcribe` |

번역 품질 상한은 전사 품질에 좌우됩니다. 한국어 오인식이 반복되면 `gpt-4o-transcribe`로 **전사만** 업그레이드하세요.

---

### 4.3 번역 모델

| 변수 | 현재값 |
|------|--------|
| `HKI_FINAL_MODEL` | `gpt-4o-mini` |

`temperature=0.1`과 함께 쓰면 mini 모델에서도 일관성이 좋아집니다. 스페인어 문맥이 여전히 아쉬울 때만 `gpt-4o` 검토.

---

### 4.4 TTS (음성 출력)

| 변수 | 코드 기본 | **현재 운영** |
|------|-----------|---------------|
| `HKI_TTS_ENABLED` | `false` | **`true`** |
| `HKI_TTS_MODEL` | `gpt-4o-mini-tts` | 동일 |
| `HKI_TTS_VOICE` | `onyx` | 동일 |
| `HKI_TTS_INSTRUCTIONS` | config.py 기본 문장 | (미오버라이드) |

TTS는 번역 큐 **이후** 또 직렬 대기합니다. 설교 중 자막이 밀리는 느낌이 없는데 음성만 늦다면 TTS 큐가 원인일 수 있습니다.

---

### 4.5 비용·활성화 게이트

| 변수 | 기본값 | 역할 |
|------|--------|------|
| `HKI_MIN_AUDIENCE_COUNT` | `1` | 청중 N명 이상일 때만 전사·번역 |

찬양·기도 시 **Pausar**로 API 비용 절약.

---

## 5. 코드 수준 옵션

### 5.1 번역 맥락 (히스토리)

| 항목 | 위치 | **현재값** | 역할 |
|------|------|------------|------|
| `FINAL_HISTORY_LINES` | `config.py` | **7** | API에 붙는 최근 KO→ES 쌍 수 |
| `_history` 상한 | `translate.py` | **14** | 메모리 보관 상한 |

- **7문장:** 5 대비 담화 연결·용어 통일 개선. 10 대비 토큰·번역 지연·큐 적체 위험 적음.
- **`_history` 14:** `FINAL_HISTORY_LINES`를 최대 12~14까지 올릴 여지. 지금은 7만 API에 사용.

### 5.2 번역 생성 파라미터

| 항목 | 위치 | **현재값** | 평가 |
|------|------|------------|------|
| `temperature` | `translate.py` | **0.1** | 설교 통역에 적합. 0.2보다 표기 일관성↑ |
| `max_tokens` | `translate.py` | `512` | 긴 문장 잘림 시에만 올리기 |

### 5.3 System prompt (성경·원고·규칙)

`translate.py`의 `ARGENTINE_RULES`, `PROMPT_TEMPLATE` — 모델 업그레이드 없이 품질을 올리는 가장 저렴한 방법. **오늘 본문만** 넣으면 번역 큐 지연도 소폭 줄어듭니다.

### 5.4 오디오 청크

`AUDIO_CHUNK_MS = 100` — 우선순위 낮음.

---

## 6. 구조·기능 개선 여지

| 영역 | 현재 | 개선 아이디어 |
|------|------|---------------|
| 번역 큐 | FIFO 직렬, 상한 없음 | 큐 깊이 모니터링, N초 이상 밀리면 스킵/알림 |
| 번역 큐 | 병렬 없음 | 제한적 병렬(2)~순서 재조합 (복잡·위험) |
| TTS | 전문 합성·순차 재생 | 문장 분할, 재생 중 새 TTS 중단 |
| 맥락 | 최근 7쌍 + 원고 | 용어집, rolling summary |
| 원고 | 방송 시작 시 스냅샷 | 중간 수정 반영 |

---

## 7. 빠르고 정확하게 — 추천 전략

### 7.1 이미 적용된 것 (유지 권장)

| 설정 | 값 | 이유 |
|------|-----|------|
| VAD 침묵 | **500ms** | 속도·안정성 균형 |
| 히스토리 | **7** | 맥락·비용 균형 |
| temperature | **0.1** | 용어·톤 일관성 |
| 번역 모델 | **gpt-4o-mini** | 큐 적체 최소화 |

### 7.2 다음에 조정할 것

| 증상 | 조치 |
|------|------|
| 자막이 점점 늦어짐 (큐 적체) | 원고 축소, VAD 500 유지(더 내리지 않기), TTS 끄기, 전사 품질 점검 |
| 한국어 오인식 | `gpt-4o-transcribe` |
| 스페인어만 아쉬움 | `gpt-4o` 또는 프롬프트 용어집 |
| 음성만 늦음 | TTS 큐 병목 — TTS 끄거나 문장 분할(향후) |

### 7.3 비용 여유 시

- `FINAL_HISTORY_LINES` → **8~10** (`_history` 상한 14 이내)
- 프롬프트 역본·고정 표기 추가
- `HKI_TTS_INSTRUCTIONS` 커스터마이즈

---

## 8. 튜닝 실전 순서

1. **baseline** — 파일 테스트 + latency 리포트
2. **큐 적체 확인** — 빠른 구간에서 자막이 전사보다 몇 초 늦는지 관찰
3. **VAD** — 현재 500 유지, 문제 시 550↔450 비교 (450 이하 비권장)
4. **원고** — 오늘 분량만
5. **히스토리** — 현재 7 유지, 품질 아쉬우면 8~10
6. **모델** — 전사 또는 번역 **한 단계만** 업그레이드
7. **라이브 전** — `/captions` 1대, 5분 리허설

---

## 9. 피해야 할 설정

| 설정 | 문제 |
|------|------|
| `HKI_VAD_SILENCE_DURATION_MS` ≤ 400 | 문장 과분할 → 번역 큐 호출 폭증·적체 |
| `FINAL_HISTORY_LINES` ≥ 15 | 문장당 토큰↑ → 번역 지연↑ → 큐 적체 악화 |
| 전사·번역 모델 동시 업그레이드 | 비용·지연 급증, 병목 파악 어려움 |
| TTS ON + 빠른 연설 | 자막은 버티는데 음성만 크게 밀릴 수 있음 |
| 성경+원고 전체 수만 자 | 토큰↑, 번역 느려짐, 맥락 노이즈 |

---

## 10. Contexto de traducción (Guardar)

### Flujo A / B / C

| Botón | Acción |
|-------|--------|
| **Guardar** | 성경+원문 → gpt-4o 참조 추출 → Bible API (NVI `nvies`) → gpt-4o 맥락(요약·outline·용어) |
| **Iniciar transmisión** | 전사·번역 ON (맥락 주입) |
| **Finalizar** | 방송만 종료 (맥락 유지) |

맥락 없이 Iniciar → **경고** 후 시작 (fallback 번역).

잠금 해제: **HKI 서버 재시작** 시.

### env

```env
HKI_CONTEXT_MODEL=gpt-4o
HKI_BIBLE_API_BASE=https://api.midvash.com/v1
HKI_BIBLE_VERSION=nvies
```

Midvash에서 스페인어 NVI slug는 `nvies` (Portuguese `nvi`와 다름).

### 성경 자막

실시간 번역 system에는 **NVI 구절 + 용어집**만. 참조는 `Mateo 1:1` 형식.

---

## 부록: 관련 파일

| 파일 | 내용 |
|------|------|
| `hki/live/translate.py` | **번역 큐**, prompt, 히스토리, temperature |
| `hki/live/tts.py` | TTS 직렬 큐 |
| `hki/live/pipeline.py` | 전사 완료 → 번역 큐 적재, 자막·TTS 브로드캐스트 |
| `hki/config.py` | env 변수, `FINAL_HISTORY_LINES` |
| `hki/live/transcribe.py` | Realtime 전사, VAD |
| `.env.example` | env 변수 예시 |
| `hki/live/context.py` | Guardar 3단계, `format_context_for_system` |
| `hki/live/bible_api.py` | Midvash NVI fetch |
| `hki/server/static/latency.html` | 파일 테스트 지연 분석 (큐 대기 미포함) |

---

*마지막 업데이트: 2026-08-06 — Guardar 맥락 + Bible API NVI*

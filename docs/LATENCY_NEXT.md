# TTS 지연 — 측정 후 할 일

이 문서는 **아직 구현하지 않는** 후속 작업이다. 파이프라인(번역 스트리밍, TTS 스트리밍, 병렬 워커, hold 정책)은 아래 로그를 본 뒤에만 손댄다.

관련 필드 정의는 [TUNING.md](TUNING.md) 릴리스 트레이스 표. `/log` JSON의 `legacy_traces`가 원천이다.

`speech→play`에서 `stt→rel`을 빼면 TTS만 남지 않는다. 발화 길이(`speech_ms`)가 섞인다. TTS 구간은 `rel→play`와 `tts_synth_ms`로 본다.

---

## 측정 방법

세션 한두 개를 스피커 ON으로 돌린 뒤 `action=release` 이고 `speed_trigger_reason`이 `tts_skipped` / `tts_error`가 아닌 줄만 쓴다.

파생값:

- `speech_ms` = `t_stt_final - t_audio_start` (발화 길이. TTS 아님)
- `rel→play` = `tts_play_start_ms - t_release`  
  ≈ `tts_queue_wait_ms + tts_synth_ms + tts_clock_wait_ms` (+ 자막 WS/numpy 수 ms)

유휴 줄(첫 소리·큐가 비어 있을 때)과 평균 줄을 나눠 본다.

| 보고 싶은 것 | 볼 필드 | 해석 |
|--------------|---------|------|
| 4–5초가 발화 길이인가 | `speech_ms` vs `rel→play` | `speech→play`만 보면 TTS로 오인하기 쉽다 |
| 합성 API 자체 | 유휴 줄 `tts_synth_ms` (`tts_queue_wait_ms≈0`, `tts_clock_wait_ms≈0`) | TTS 스트리밍 이득 상한 ≈ 이 값 − TTS TTFT |
| 합성 적체 | `tts_queue_wait_ms` 중앙/p90 | 병렬 워커는 여기만 줄임. 최소 지연과 무관 |
| 이전 클립 대기 | `tts_clock_wait_ms` | 재생 중 겹침. 줄이면 두 목소리가 겹침 |
| 열린 조각 대기 | `hold_ms` / `hold_reason` | 평균을 올리는 주범일 수 있음 |
| 페이서 | `pacer_wait_ms` | 첫 줄은 보통 0. 이후 줄 간격 |
| recombine LLM | `recombine_llm_ms` (`used_llm_recombine`) | 최소 경로에선 종종 0 |

### 산점도: 합성 시간이 길이에 비례하는가

`tts_input_chars`와 `tts_pcm_1x_ms`는 이 그림용이다. 유휴 줄을 분해한 **직후**, 스트리밍/병렬/파이프 중 무엇을 할지 고르기 **전에** 그린다. 안 그리면 길이 비례 여부를 놓치기 쉽다.

대상: `tts_synth_ms > 0` 인 릴리스. 가능하면 `tts_queue_wait_ms`가 작은 줄만 (대기와 합성을 섞지 않음).

그릴 것:

1. X = `tts_input_chars`, Y = `tts_synth_ms`
2. X = `tts_pcm_1x_ms`, Y = `tts_synth_ms` (재생 길이 vs 합성 벽시계)

읽는 법:

- **비례(기울기가 뚜렷)** → 긴 문장일수록 전체 PCM 대기가 큼. TTS 스트리밍(아래 B) 이득이 긴 줄에서 더 큼.
- **거의 수평(고정 오버헤드)** → 짧은 줄도 합성이 비슷. 스트리밍 TTFT가 핵심이고, 절 단위로 잘게 쪼개 TTS를 여러 번 치면 이득이 없거나 더 느릴 수 있음.
- **산포만 크고 관계 없음** → 길이보다 큐/모델 변동. 스트리밍 전에 `tts_queue_wait_ms`와 유휴 여부부터 재확인.

산점도는 구현이 아니다. `/log` JSON을 시트나 짧은 스크립트로 그려도 된다. 결론만 이 문서 체크리스트에 남긴다.

---

## 결정 기준 (로그 보기 전에는 코드 금지)

- 유휴 줄 `tts_synth_ms`가 크다 (대략 ≥1s) → **B TTS 스트리밍**이 최소 경로의 주력.
- `tts_queue_wait_ms` p90이 크다 → **병렬 워커** (백로그만. 첫 소리 최소 지연은 거의 안 줄어듦).
- `hold_ms` / `recombine_llm_ms`가 평균을 올린다 → **품질 정책** (A2 draft TTS, hold/배치). 이미 나온 음성은 취소 불가.
- 번역 생성 시간(전체 `translate_llm_ms` − TTFT)이 길 때만 → **C 절 단위 파이프**. Luna TTFT가 stt→rel 대부분이면 이득은 수백 ms.
- 산점도에서 길이 비례가 약하면 → C에서 절을 잘게 쪼개지 말 것.

하한: 닫힌 조각·유휴 큐에서 STT 확정 → 첫 소리 바닥 ≈ 번역 TTFT + TTS TTFT (**약 2초**). 번역 모델을 바꾸지 않으면 **1초는 비현실**. 평균 4–5초 → 1–2초는 hold/pacer/재생 큐 없이 스트리밍만으로는 불가.

---

## 할 일

측정 (코드 변경 없음):

- [ ] 유휴 줄 vs 평균 줄로 `rel→play` / `tts_synth_ms` / `hold_ms` / `pacer_wait_ms` / `recombine_llm_ms` 분해
- [ ] **산점도** `tts_synth_ms` vs `tts_input_chars`, `tts_synth_ms` vs `tts_pcm_1x_ms` (위 「산점도」절. 스트리밍 결정 전에)

구조 변경 (측정 후 하나만 또는 순서대로):

- [ ] **A1** 페이서 대기 중 TTS 합성 시작. 첫 줄 최소 지연은 거의 안 줄어듦
- [ ] **A2** `translation_draft` / recombine 전 TTS. 음성 철회 불가, 이음·구두점 어긋남 가능
- [ ] **B** TTS 스트리밍: `with_streaming_response` + pcm 청크 WS + `/captions` 스트리밍 재생 + 재생시계 재설계
- [ ] TTS 워커 병렬 (백로그 전용. 재생은 여전히 한 줄)
- [ ] **C** 번역 `stream=True` + 절 단위 TTS 이어붙임 (대변경. 1초 목표는 비현실)
- [ ] (비추천 1순위) STT delta로 선번역 — Realtime이 수정되고 `completed`만 번역함

배경: 번역은 이미 fragment마다 STT 직후 시작된다. 막힌 것은 확정 자막·TTS가 recombine + ReleasePacer 뒤에 직렬로 붙는 것. OpenAI Speech는 번역 토큰 스트림을 받아 이어서 말하지 않는다. C는 절을 모아 별도 `speech.create`를 여러 번 치는 방식이다.

(() => {
  const $ = (id) => document.getElementById(id);

  let ws = null;
  let state = "idle";
  let elapsedSec = 0;
  let testFileReady = false;
  let testDurationSec = 0;
  let testPlaying = false;
  let captionsUrl = "";
  let joinUrl = "";
  let contextReady = false;
  let hasLog = false;
  let hasLatencyReport = false;
  let ttsAvailable = false;
  let ttsActive = false;
  let audienceCount = 0;
  let audienceGateOpen = false;
  let speakerSubscribers = 0;
  let translationActive = false;
  let transcriptionActive = false;
  let sermonOn = false;
  let contextGeneratedAt = null;
  let wsReconnectTimer = null;
  let logCaptionIndex = 0;
  let lastWsMessageAt = 0;

  // Continuous input meter (gray track, green→yellow→red fill)
  let inputMeterPeak = -60;
  let inputMeterLevel = -60;
  let inputMeterPeakHold = -60;
  let inputMeterPeakHoldUntil = 0;
  let inputMeterClipping = false;
  let inputMeterLastAt = 0;
  let inputMeterLoopId = null;
  let levelFallbackPollId = null;
  const INPUT_PEAK_HOLD_MS = 500;
  // Hard-zeroing after 500ms made the meter "jump" whenever WS levels
  // paused and only the 3s REST poll refreshed the bar.
  const INPUT_METER_STALE_MS = 1200;
  const LEVEL_FALLBACK_POLL_MS = 150;

  const MAX_CAPTION_FINALS = 8;
  const captionArea = () => $("captionMonitor");
  let captionFinals = [];
  let captionKoEl = null;
  let captionDraftEl = null;

  function clearCaptions() {
    captionFinals = [];
    captionKoEl = null;
    captionDraftEl = null;
    logCaptionIndex = 0;
    const area = captionArea();
    area.innerHTML = "";
    const ph = document.createElement("div");
    ph.className = "placeholder";
    ph.id = "captionPlaceholder";
    ph.textContent = "Los subtítulos aparecerán al iniciar la transmisión";
    area.appendChild(ph);
  }

  function hideCaptionPlaceholder() {
    const ph = $("captionPlaceholder");
    if (ph) ph.remove();
  }

  function scrollCaptions() {
    pruneCaptions();
  }

  function pruneCaptions() {
    const area = captionArea();
    while (area.scrollHeight > area.clientHeight) {
      const firstLine = area.querySelector(".line");
      if (!firstLine) break;
      if (firstLine === captionKoEl) captionKoEl = null;
      if (firstLine === captionDraftEl) {
        captionDraftEl = null;
      }
      const idx = captionFinals.indexOf(firstLine);
      if (idx !== -1) captionFinals.splice(idx, 1);
      firstLine.remove();
    }
  }

  function showCaptionKo(text) {
    hideCaptionPlaceholder();
    if (!captionKoEl) {
      captionKoEl = document.createElement("div");
      captionKoEl.className = "line ko";
      captionArea().appendChild(captionKoEl);
    }
    captionKoEl.textContent = text;
    scrollCaptions();
  }

  function showCaptionDraft(text) {
    const t = (text || "").trim();
    if (!t || t === "—") return;
    hideCaptionPlaceholder();
    if (!captionDraftEl) {
      captionDraftEl = document.createElement("div");
      captionDraftEl.className = "line draft";
      captionArea().appendChild(captionDraftEl);
    }
    captionDraftEl.textContent = t;
    scrollCaptions();
  }

  function clearCaptionDraft() {
    if (captionDraftEl) {
      captionDraftEl.remove();
      captionDraftEl = null;
    }
  }

  function confirmCaptionFinal(itemId, text) {
    hideCaptionPlaceholder();
    clearCaptionDraft();
    if (captionKoEl) {
      captionKoEl.remove();
      captionKoEl = null;
    }

    const el = document.createElement("div");
    el.className = "line final";
    el.textContent = text;
    captionArea().appendChild(el);
    captionFinals.push(el);

    if (captionFinals.length > MAX_CAPTION_FINALS) {
      const old = captionFinals.shift();
      old.classList.add("fade-out");
      setTimeout(() => old.remove(), 300);
    }

    captionFinals.forEach((line, i) => {
      line.className = "line";
      if (i === captionFinals.length - 1) line.classList.add("final");
      else if (i === captionFinals.length - 2) line.classList.add("recent");
      else line.classList.add("old");
    });
    scrollCaptions();
  }

  function syncLiveStatusFromApi(data) {
    if (!data) return;
    if (data.has_log !== undefined) setHasLog(data.has_log);
    if (data.has_latency_report !== undefined) setHasLatencyReport(data.has_latency_report);
    applyStatusFields(data);
    if (data.test_mode !== undefined) {
      const live =
        data.state === "streaming" ||
        data.state === "paused" ||
        state === "streaming" ||
        state === "paused";
      testPlaying = data.test_mode && live;
      $("testPlayBtn").disabled = !testFileReady || testPlaying;
      $("testStopBtn").disabled = !testPlaying;
      if (data.test_duration_sec) testDurationSec = data.test_duration_sec;
      if (data.test_playback_sec !== undefined && data.test_duration_sec) {
        updateTestProgress(data.test_playback_sec, data.test_duration_sec);
      }
    }
    if (data.state !== undefined) {
      setState(data.state, data.elapsed_sec);
    }
  }

  function wsIsLive() {
    return ws && ws.readyState === WebSocket.OPEN;
  }

  function markWsActivity() {
    lastWsMessageAt = Date.now();
  }

  async function syncCaptionsFromLog() {
    if (wsIsLive() && Date.now() - lastWsMessageAt < 8000) return;
    try {
      const res = await fetch("/api/live/log");
      if (!res.ok) return;
      const data = await res.json();
      const translations = data.translations || [];
      while (logCaptionIndex < translations.length) {
        const text = translations[logCaptionIndex];
        logCaptionIndex++;
        confirmCaptionFinal(`log-${logCaptionIndex}`, text);
      }
    } catch (_) {}
  }

  function wsLiveUrl(role) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const port = location.port || "8765";
    const host = location.port ? location.host : `${location.hostname}:${port}`;
    return `${proto}://${host}/ws/live?role=${role}`;
  }

  function openQrGuide() {
    if (!joinUrl || joinUrl === "#") return;
    window.HKIQR.open(joinUrl, { mode: "guide" });
  }

  function openQrDirect() {
    if (!captionsUrl || captionsUrl === "#") return;
    window.HKIQR.open(captionsUrl, { mode: "direct" });
  }

  async function openQrPrintBoth() {
    const guide = joinUrl || $("joinUrl")?.href || "";
    const direct = captionsUrl || $("directCaptionsUrl")?.href || "";
    if (!guide || guide === "#" || !direct || direct === "#") {
      alert("Enlaces no cargados — recargue la página");
      return;
    }
    if (!window.HKIQR?.printBoth) {
      alert("QR no disponible — recargue la página");
      return;
    }
    const btn = $("qrPrintBothBtn");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Generando…";
    }
    try {
      await window.HKIQR.printBoth(guide, direct);
    } catch (err) {
      alert(err?.message || "Error al imprimir QR");
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = "🖨 Imprimir ambos QR";
      }
    }
  }

  function fmtTime(sec) {
    const s = Math.max(0, Math.floor(sec));
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}:${String(r).padStart(2, "0")}`;
  }

  function updateTestProgress(elapsed, duration) {
    $("testElapsed").textContent = fmtTime(elapsed);
    $("testRemaining").textContent = fmtTime(Math.max(0, duration - elapsed));
    const pct = duration > 0 ? Math.min(100, (elapsed / duration) * 100) : 0;
    $("testProgress").style.width = pct + "%";
  }

  function isBroadcastLive() {
    return state === "streaming" || state === "paused";
  }

  function updateLogButton() {
    const show = !isBroadcastLive();
    $("logBtn").classList.toggle("hidden", !(hasLog && show));
    $("latencyBtn").classList.toggle("hidden", !(hasLatencyReport && show));
  }

  function setHasLog(value) {
    hasLog = !!value;
    updateLogButton();
  }

  function setHasLatencyReport(value) {
    hasLatencyReport = !!value;
    updateLogButton();
  }

  function openLogWindow() {
    window.open("/log", "hkiLog", "width=820,height=720");
  }

  function openLatencyWindow() {
    window.open("/latency", "hkiLatency", "width=900,height=800");
  }

  function setContentFieldsLocked(locked) {
    contextReady = locked;
    $("bibleText").readOnly = locked;
    $("manuscriptText").readOnly = locked;
    $("bibleText").classList.toggle("locked", locked);
    $("manuscriptText").classList.toggle("locked", locked);
    $("bibleCard").classList.toggle("locked", locked);
    $("manuscriptCard").classList.toggle("locked", locked);
    $("contextualizarBtn").disabled = locked;
    $("contentInputCards").classList.toggle("collapsed", locked);
    $("contextOkCard").classList.toggle("hidden", !locked);
    $("passageCard").classList.toggle("hidden", !locked);
    $("contextSummaryCard").classList.toggle("hidden", !locked);
    updateContextualizarButton();
  }

  function updateContextualizarButton() {
    $("contextualizarBtn").disabled = contextReady;
  }

  function bindCollapsible(cardId, toggleId, chevronId) {
    const card = $(cardId);
    const btn = $(toggleId);
    const chevronBtn = chevronId ? $(chevronId) : null;
    if (!card || !btn) return;
    const body = card.querySelector(".card-collapsible-body");
    const setCollapsed = (collapsed) => {
      card.classList.toggle("collapsed", collapsed);
      if (body) body.hidden = collapsed;
      btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
      if (chevronBtn) {
        chevronBtn.setAttribute("aria-expanded", collapsed ? "false" : "true");
        chevronBtn.setAttribute(
          "aria-label",
          collapsed ? "Expandir sección" : "Contraer sección"
        );
      }
    };
    const toggle = (e) => {
      if (e) e.preventDefault();
      setCollapsed(!card.classList.contains("collapsed"));
    };
    setCollapsed(false);
    btn.addEventListener("click", toggle);
    if (chevronBtn) chevronBtn.addEventListener("click", toggle);
  }

  function sourceLabel(source) {
    if (source === "bible_api") return "NVI vía API";
    if (source === "bible_api_partial") return "NVI parcial (API + fallback)";
    if (source === "llm_fallback") return "NVI generado por modelo";
    return source || "";
  }

  function applyContextDisplay(display) {
    const card = $("contextSummaryCard");
    if (!display) {
      card.classList.add("hidden");
      return;
    }
    $("contextSummaryText").textContent = display.sermon_summary || "—";

    const outline = display.outline || [];
    $("contextOutlineSection").classList.toggle("hidden", !outline.length);
    const outlineList = $("contextOutlineList");
    outlineList.innerHTML = "";
    outline.forEach((item) => {
      const li = document.createElement("li");
      li.textContent = item;
      outlineList.appendChild(li);
    });

    const terms = display.terminology || [];
    $("contextTerminologySection").classList.toggle("hidden", !terms.length);
    const termList = $("contextTerminologyList");
    termList.innerHTML = "";
    terms.forEach((t) => {
      const row = document.createElement("div");
      row.className = "context-term-row";
      const ko = document.createElement("span");
      ko.className = "context-term-ko";
      ko.textContent = t.ko || "";
      const arrow = document.createElement("span");
      arrow.textContent = "→";
      const es = document.createElement("span");
      es.textContent = t.es || "";
      row.append(ko, arrow, es);
      if (t.note) {
        const note = document.createElement("span");
        note.style.color = "#666";
        note.textContent = ` (${t.note})`;
        row.appendChild(note);
      }
      termList.appendChild(row);
    });

    const books = display.bible_books || [];
    $("contextBooksSection").classList.toggle("hidden", !books.length);
    const booksList = $("contextBooksList");
    booksList.innerHTML = "";
    books.forEach((b) => {
      const row = document.createElement("div");
      row.className = "context-term-row";
      row.textContent = `${b.ko || ""} → ${b.es || ""}`;
      booksList.appendChild(row);
    });

    const style = display.style_notes || "";
    $("contextStyleSection").classList.toggle("hidden", !style);
    $("contextStyleNotes").textContent = style;

    const refs = (display.bible_references || []).join(", ");
    const src = sourceLabel(display.bible_es_source);
    const metaParts = [];
    if (refs) metaParts.push(`Referencias: ${refs}`);
    if (src) metaParts.push(`Fuente: ${src}`);
    const meta = $("contextMeta");
    if (metaParts.length) {
      meta.textContent = metaParts.join(" · ");
      meta.classList.remove("hidden");
    } else {
      meta.textContent = "";
      meta.classList.add("hidden");
    }
  }

  function formatGeneratedAt(iso) {
    if (!iso) return "";
    try {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return iso;
      return d.toLocaleString("es-AR", { dateStyle: "short", timeStyle: "short" });
    } catch {
      return iso;
    }
  }

  function applyContextGeneratedAt(generatedAt) {
    const el = $("contextOkTime");
    if (!generatedAt) {
      el.textContent = "";
      return;
    }
    const formatted = formatGeneratedAt(generatedAt);
    el.textContent = formatted ? ` · ${formatted}` : "";
  }

  function applyPassageDisplay(display) {
    if (!display) return;
    $("passageKo").textContent = display.ko || "";
    $("passageNvi").textContent = display.nvi || "";
  }

  function confirmNoContextStart() {
    return new Promise((resolve) => {
      const modal = $("noContextModal");
      const acceptBtn = $("noContextAcceptBtn");
      const cancelBtn = $("noContextCancelBtn");
      if (!modal || !acceptBtn || !cancelBtn) {
        resolve(true);
        return;
      }

      const cleanup = () => {
        modal.classList.add("hidden");
        acceptBtn.onclick = null;
        cancelBtn.onclick = null;
        modal.onclick = null;
      };

      acceptBtn.onclick = () => {
        cleanup();
        resolve(true);
      };
      cancelBtn.onclick = () => {
        cleanup();
        resolve(false);
      };
      modal.onclick = (e) => {
        if (e.target === modal) {
          cleanup();
          resolve(false);
        }
      };

      modal.classList.remove("hidden");
    });
  }

  function setDeviceLocked(locked) {
    $("deviceSelect").disabled = locked;
  }

  async function init() {
    bindEvents();
    startInputMeterLoop();
    try {
      const statusRes = await fetch("/api/live/status");
      const status = await statusRes.json();
      await loadDevices(status.device_index);
      applyStatus(status);
      connectWs();
      if (state !== "streaming" && state !== "paused") {
        await saveDeviceSettings();
      }
    } catch (err) {
      console.error("HKI control init failed:", err);
      alert("No se pudo conectar al servidor. Reiniciá HKI e recargá la página.");
    }
  }

  async function loadDevices(preferredIndex) {
    const res = await fetch("/api/audio-devices");
    const data = await res.json();
    const sel = $("deviceSelect");
    sel.innerHTML = "";
    if (!data.devices.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "No hay dispositivos de entrada";
      sel.appendChild(opt);
      return;
    }
    const pick =
      preferredIndex != null && data.devices.some((d) => d.index === preferredIndex)
        ? preferredIndex
        : data.scarlett_index;
    data.devices.forEach((d) => {
      const opt = document.createElement("option");
      opt.value = d.index;
      opt.textContent = `${d.name} (${d.sample_rate} Hz)`;
      if (d.index === pick) opt.selected = true;
      sel.appendChild(opt);
    });
  }

  function applyStatusFields(data) {
    if (data.audience_count !== undefined) audienceCount = data.audience_count;
    if (data.audience_gate_open !== undefined) audienceGateOpen = data.audience_gate_open;
    if (data.speaker_subscribers !== undefined) speakerSubscribers = data.speaker_subscribers;
    if (data.translation_active !== undefined) translationActive = data.translation_active;
    if (data.transcription_active !== undefined) transcriptionActive = data.transcription_active;
    if (data.sermon_on !== undefined) sermonOn = data.sermon_on;
    if (data.tts_available !== undefined) ttsAvailable = data.tts_available;
    if (data.tts_active !== undefined) ttsActive = data.tts_active;
    if (data.input_peak_db !== undefined) {
      updateLevel({
        peak_db: data.input_peak_db,
        rms_db: data.input_rms_db,
        clipping: data.input_clipping,
      });
    }
    updatePipelineStatus();
    updateTtsControls();
    updateSermonButton();
  }

  function applyStatus(data) {
    joinUrl = data.join_url || data.captions_url || "";
    captionsUrl = data.captions_direct_url || "";
    const joinEl = $("joinUrl");
    const directEl = $("directCaptionsUrl");
    if (joinEl) {
      joinEl.href = joinUrl;
      joinEl.textContent = joinUrl;
    }
    if (directEl) {
      directEl.href = captionsUrl;
      directEl.textContent = captionsUrl;
    }
    const directRow = $("directUrlRow");
    if (directRow) {
      directRow.classList.toggle("hidden", data.scheme !== "https");
    }
    const printBothBtn = $("qrPrintBothBtn");
    if (printBothBtn) {
      printBothBtn.classList.toggle("hidden", data.scheme !== "https");
    }
    if (data.scheme === "https" && location.protocol !== "https:") {
      console.warn(
        "HKI: operador en HTTP — use https://localhost:8765/ para WebSocket y estado en vivo"
      );
    }
    if (data.gain) {
      $("gainSlider").value = data.gain;
      $("gainValue").textContent = data.gain.toFixed(1);
    }
    setState(data.state, data.elapsed_sec);
    setHasLog(data.has_log);
    setHasLatencyReport(data.has_latency_report);
    if (data.passage_display && data.passage_display.ko) {
      $("bibleText").value = data.passage_display.ko;
    } else if (data.bible_text) {
      $("bibleText").value = data.bible_text;
    }
    if (data.manuscript) $("manuscriptText").value = data.manuscript;
    if (data.context_ready) {
      setContentFieldsLocked(true);
      applyPassageDisplay(data.passage_display);
      applyContextDisplay(data.context_display);
      applyContextGeneratedAt(data.context_generated_at);
      contextGeneratedAt = data.context_generated_at ?? null;
    } else {
      setContentFieldsLocked(false);
      applyContextDisplay(null);
      applyContextGeneratedAt(null);
    }
    applyStatusFields(data);
  }

  async function refreshLogState() {
    try {
      const res = await fetch("/api/live/status");
      if (!res.ok) return;
      syncLiveStatusFromApi(await res.json());
    } catch (_) {}
  }

  function connectWs() {
    if (wsReconnectTimer) {
      clearTimeout(wsReconnectTimer);
      wsReconnectTimer = null;
    }
    if (ws) {
      try {
        ws.onopen = null;
        ws.onmessage = null;
        ws.onclose = null;
        ws.onerror = null;
        if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
          ws.close();
        }
      } catch (_) {}
    }
    const url = wsLiveUrl("operator");
    try {
      ws = new WebSocket(url);
    } catch (err) {
      console.error("HKI WebSocket create failed:", url, err);
      wsReconnectTimer = setTimeout(connectWs, 3000);
      return;
    }
    ws.onopen = () => {
      markWsActivity();
      console.info("HKI WebSocket connected", url);
      pollAudienceStatus();
    };
    ws.onmessage = (e) => {
      markWsActivity();
      try {
        handleEvent(JSON.parse(e.data));
      } catch (err) {
        console.error("HKI WS parse/handle error:", err, e.data);
      }
    };
    ws.onerror = () => {
      console.warn(
        "HKI operator WebSocket error — revisá que la URL sea https://localhost:8765/",
        url
      );
    };
    ws.onclose = (ev) => {
      console.warn("HKI WebSocket closed", ev.code, ev.reason || "", url);
      wsReconnectTimer = setTimeout(connectWs, 3000);
    };
  }

  async function pollAudienceStatus() {
    try {
      const res = await fetch("/api/live/status");
      if (!res.ok) return;
      const data = await res.json();
      syncLiveStatusFromApi(data);
      if (state === "streaming" || state === "paused") {
        await syncCaptionsFromLog();
      }
    } catch (_) {}
  }

  function applyContextFromStatus(ev) {
    if (ev.context_ready) {
      const at = ev.context_generated_at ?? null;
      const shouldRefresh =
        !contextReady || at !== contextGeneratedAt;
      contextReady = true;
      if (shouldRefresh) {
        if (ev.passage_display && ev.passage_display.ko) {
          $("bibleText").value = ev.passage_display.ko;
        } else if (ev.bible_text) {
          $("bibleText").value = ev.bible_text;
        }
        if (ev.manuscript) $("manuscriptText").value = ev.manuscript;
        setContentFieldsLocked(true);
        applyPassageDisplay(ev.passage_display);
        applyContextDisplay(ev.context_display);
        applyContextGeneratedAt(at);
        contextGeneratedAt = at;
      }
    } else if (ev.context_ready === false) {
      contextReady = false;
      contextGeneratedAt = null;
      setContentFieldsLocked(false);
      applyContextDisplay(null);
      applyContextGeneratedAt(null);
    }
  }

  function handleEvent(ev) {
    if (ev.type === "translation_draft") {
      showCaptionDraft(ev.es);
      return;
    }
    if (ev.type === "translation") {
      confirmCaptionFinal(ev.item_id, ev.es);
      return;
    }
    if (ev.type === "status") {
      setState(ev.state, ev.elapsed_sec);
      if (ev.has_log !== undefined) setHasLog(ev.has_log);
      if (ev.has_latency_report !== undefined) setHasLatencyReport(ev.has_latency_report);
      applyContextFromStatus(ev);
      applyStatusFields(ev);
      if (ev.state === "idle") {
        testPlaying = false;
        $("testPlayBtn").disabled = !testFileReady;
        $("testStopBtn").disabled = true;
      }
    } else if (ev.type === "sermon_mode") {
      if (ev.sermon_on !== undefined) sermonOn = ev.sermon_on;
      applyStatusFields(ev);
      updateSermonButton();
      updatePipelineStatus();
    } else if (ev.type === "level") {
      updateLevel(ev);
    } else if (ev.type === "output_level") {
      updateOutputLevel(ev);
    } else if (ev.type === "transcript") {
      if (ev.final) showCaptionKo(ev.text);
    } else if (ev.type === "pausing") {
      $("pauseBtn").disabled = true;
      $("pauseBtn").textContent = "Pausando…";
    } else if (ev.type === "paused") {
      setState("paused", elapsedSec);
    } else if (ev.type === "resumed") {
      setState("streaming", elapsedSec);
      if (ev.test_mode !== undefined ? ev.test_mode : testPlaying) {
        testPlaying = true;
        $("testPlayBtn").disabled = true;
        $("testStopBtn").disabled = false;
      }
    } else if (ev.type === "test_progress") {
      testDurationSec = ev.duration_sec || testDurationSec;
      updateTestProgress(ev.elapsed_sec, testDurationSec);
      if (ev.elapsed_sec >= testDurationSec) {
        testPlaying = false;
        $("testPlayBtn").disabled = !testFileReady;
        $("testStopBtn").disabled = true;
        setTimeout(() => {
          refreshLogState();
          pollAudienceStatus();
        }, 3500);
      }
    }
  }

  function setState(s, elapsed) {
    state = s;
    elapsedSec = elapsed || 0;
    const isLive = isBroadcastLive();

    const badge = $("onAirBadge");
    if (badge) {
      if (s === "streaming") {
        badge.textContent = "ON AIR";
        badge.className = "on-air-badge live";
      } else if (s === "paused") {
        badge.textContent = "PAUSA";
        badge.className = "on-air-badge paused";
      } else {
        badge.textContent = "";
        badge.className = "on-air-badge";
      }
    }

    $("idleControls").classList.toggle("hidden", isLive);
    $("liveControls").classList.toggle("hidden", !isLive);
    $("liveStopRow").classList.toggle("hidden", !isLive);
    if (isLive) {
      $("pauseBtn").disabled = false;
      $("pauseBtn").textContent = s === "paused" ? "▶ Reanudar" : "⏸ Pausar";
      updateTimer();
    }
    updateSermonButton();

    $("bibleText").readOnly = contextReady;
    $("manuscriptText").readOnly = contextReady;
    setDeviceLocked(isLive);
    updateContextualizarButton();

    updateLogButton();
    updatePipelineStatus();
  }

  function updateTimer() {
    const h = String(Math.floor(elapsedSec / 3600)).padStart(2, "0");
    const m = String(Math.floor((elapsedSec % 3600) / 60)).padStart(2, "0");
    const s = String(elapsedSec % 60).padStart(2, "0");
    $("timerDisplay").textContent = `${h}:${m}:${s}`;
  }

  setInterval(() => {
    if (state === "streaming") {
      elapsedSec++;
      updateTimer();
    }
  }, 1000);

  setInterval(pollAudienceStatus, 3000);
  startLevelFallbackPoll();

  function startLevelFallbackPoll() {
    if (levelFallbackPollId != null) return;
    levelFallbackPollId = setInterval(async () => {
      // Prefer WS level stream; only hit REST when the live feed is stale.
      if (wsIsLive() && Date.now() - lastWsMessageAt < 800) return;
      try {
        const res = await fetch("/api/live/status");
        if (!res.ok) return;
        const data = await res.json();
        if (data.input_peak_db !== undefined) {
          updateLevel({
            peak_db: data.input_peak_db,
            rms_db: data.input_rms_db,
            clipping: data.input_clipping,
          });
        }
        if (data.state !== undefined && data.state !== state) {
          setState(data.state, data.elapsed_sec);
        }
      } catch (_) {}
    }, LEVEL_FALLBACK_POLL_MS);
  }

  function dbToMeterPercent(db) {
    const clamped = Math.max(-60, Math.min(0, db));
    return ((clamped + 60) / 60) * 100;
  }

  function pushInputMeterSample(ev) {
    const peak = ev.peak_db ?? -60;
    const now = performance.now();
    inputMeterPeak = peak;
    if (peak >= inputMeterPeakHold) {
      inputMeterPeakHold = peak;
      inputMeterPeakHoldUntil = now + INPUT_PEAK_HOLD_MS;
    }
    inputMeterClipping = Boolean(ev.clipping);
    inputMeterLastAt = now;
  }

  function paintInputMeter() {
    const fill = $("inputLevelFill");
    if (!fill) return;

    const now = performance.now();
    let target = inputMeterPeak;
    if (!inputMeterLastAt || now - inputMeterLastAt > INPUT_METER_STALE_MS) {
      target = -60;
      inputMeterClipping = false;
    }
    if (now < inputMeterPeakHoldUntil) {
      target = Math.max(target, inputMeterPeakHold);
    }

    if (target > inputMeterLevel) {
      inputMeterLevel = target;
    } else {
      inputMeterLevel += (target - inputMeterLevel) * 0.12;
    }

    const pct = dbToMeterPercent(inputMeterLevel);
    const hideRight = Math.max(0, Math.min(100, 100 - pct));
    fill.style.clipPath = `inset(0 ${hideRight.toFixed(1)}% 0 0)`;
    fill.classList.toggle("clip", inputMeterClipping);
  }

  function startInputMeterLoop() {
    if (inputMeterLoopId != null) return;
    const tick = () => {
      paintInputMeter();
      inputMeterLoopId = requestAnimationFrame(tick);
    };
    inputMeterLoopId = requestAnimationFrame(tick);
  }

  function updateLevel(ev) {
    pushInputMeterSample(ev);
  }

  function updateOutputLevel(ev) {
    if (!ttsAvailable) return;
    const fill = $("outputLevelFill");
    const label = $("outputLevelLabel");
    if (!fill || !label) return;
    if (!ev.active) {
      fill.style.width = "0%";
      label.textContent = ttsActive
        ? "Esperando voz..."
        : "Sin salida de voz activa";
      return;
    }
    const pct = Math.min(100, Math.max(0, ((ev.peak_db + 60) / 60) * 100));
    fill.style.width = pct + "%";
    fill.style.background = "#4a6cf7";
    const phrase = ev.phrase ? ` — ${ev.phrase}` : "";
    const phase =
      ev.phase === "playing"
        ? "Reproduciendo"
        : ev.phase === "synth"
          ? "Generando voz…"
          : "Activo";
    label.textContent = `${phase} · ${ev.peak_db.toFixed(1)} dB${phrase}`;
  }

  function setSvcState(dotEl, labelEl, active, text, unavailable) {
    const dotClass = "svc-dot " + (unavailable ? "na" : active ? "on" : "off");
    const labelClass =
      "svc-detail " + (unavailable ? "na" : active ? "on" : "off");
    if (dotEl.className !== dotClass) dotEl.className = dotClass;
    if (labelEl.className !== labelClass) labelEl.className = labelClass;
    if (labelEl.textContent !== text) labelEl.textContent = text;
  }

  function updatePipelineStatus() {
    const dot = $("pipelineDot");
    const label = $("pipelineStatusLabel");
    const countLabel = $("audienceCountLabel");
    const isLive = state === "streaming" || state === "paused";
    const pipelineOn = translationActive || transcriptionActive;

    if (countLabel) {
      countLabel.textContent = `Audiencia: ${audienceCount}`;
      countLabel.className = "svc-detail " + (audienceCount > 0 ? "on" : "off");
    }

    if (!isLive) {
      if (audienceGateOpen) {
        setSvcState(dot, label, true, "Listo — audiencia conectada");
      } else {
        setSvcState(dot, label, false, "Inactivo (Sin conexión)");
      }
      return;
    }

    if (state === "paused") {
      if (audienceGateOpen) {
        setSvcState(dot, label, false, "Pausa — audiencia conectada");
      } else {
        setSvcState(dot, label, false, "Pausa (Sin conexión)");
      }
      return;
    }

    if (pipelineOn) {
      const mode = sermonOn ? "sermón" : "servicio general";
      setSvcState(dot, label, true, `Activo — ${mode}`);
    } else {
      setSvcState(dot, label, false, "En espera de audiencia");
    }
  }

  function updateSermonButton() {
    const btn = $("sermonBtn");
    if (!btn) return;
    const isLive = state === "streaming" || state === "paused";
    btn.disabled = !isLive || state === "paused";
    btn.classList.toggle("sermon-active", sermonOn);
    btn.textContent = sermonOn ? "■ Fin sermón" : "▶ Sermón";
  }

  function updateTtsControls() {
    $("outputCard").style.opacity = ttsAvailable ? "1" : "0.5";
    const dot = $("ttsDot");
    const label = $("ttsStatusLabel");
    if (!ttsAvailable) {
      $("outputLevelFill").style.width = "0%";
      $("outputLevelLabel").textContent = "Bloqueado";
      setSvcState(dot, label, false, "Bloqueado", true);
    } else if (speakerSubscribers > 0) {
      setSvcState(dot, label, true, "Activo");
    } else {
      setSvcState(dot, label, false, "Inactivo (Sin solicitud)");
    }
  }

  function bindEvents() {
    bindCollapsible("passageCard", "passageToggle", "passageChevronBtn");
    bindCollapsible("contextSummaryCard", "contextSummaryToggle", "contextSummaryChevronBtn");
    $("qrGuideBtn").onclick = openQrGuide;
    $("qrDirectBtn").onclick = openQrDirect;
    $("qrPrintBothBtn").onclick = openQrPrintBoth;
    $("logBtn").onclick = openLogWindow;
    $("latencyBtn").onclick = openLatencyWindow;

    $("resetContextBtn").onclick = async () => {
      if (!contextReady) return;
      const liveNote =
        state === "streaming" || state === "paused"
          ? " La transmisión seguirá, pero las frases siguientes usarán traducción sin contexto."
          : "";
      const ok = confirm(
        "¿Liberar el contexto contextualizado? Podrás volver a contextualizar." + liveNote
      );
      if (!ok) return;
      try {
        const res = await fetch("/api/live/reset-context", { method: "POST" });
        let data = {};
        try {
          data = await res.json();
        } catch {
          data = {};
        }
        if (!res.ok || !data.ok) {
          alert(
            data.error ||
              (res.status === 404
                ? "Endpoint no encontrado — reiniciá el servidor HKI"
                : `Error al liberar contexto (${res.status})`)
          );
          return;
        }
        setContentFieldsLocked(false);
        applyContextDisplay(null);
        applyContextGeneratedAt(null);
        $("passageKo").textContent = "";
        $("passageNvi").textContent = "";
        $("contextualizarStatus").textContent = "";
      } catch {
        alert("Error al liberar contexto");
      }
    };

    $("deviceSelect").onchange = async () => {
      if (!contextReady && state !== "streaming" && state !== "paused") {
        await saveDeviceSettings();
      }
    };

    $("gainSlider").oninput = async (e) => {
      const gain = parseFloat(e.target.value);
      $("gainValue").textContent = gain.toFixed(1);
      await fetch("/api/live/gain", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ gain }),
      });
    };

    $("contextualizarBtn").onclick = async () => {
      if (contextReady) return;
      const bible = $("bibleText").value.trim();
      if (!bible) {
        alert("El texto bíblico es obligatorio");
        return;
      }
      const manuscript = $("manuscriptText").value.trim();
      if (!manuscript) {
        const proceed = confirm(
          "¿Quiere avanzar sin contextualizar el texto del sermón?"
        );
        if (!proceed) return;
      }
      $("contextualizarBtn").disabled = true;
      $("contextualizarStatus").textContent = "Extrayendo referencias…";
      try {
        const res = await fetch("/api/live/contextualizar", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            bible_text: bible,
            manuscript: manuscript,
          }),
        });
        const data = await res.json();
        if (!data.ok) {
          alert(data.error || "Error al contextualizar");
          $("contextualizarBtn").disabled = false;
          $("contextualizarStatus").textContent = "";
          return;
        }
        contextReady = true;
        contextGeneratedAt = data.generated_at ?? null;
        setContentFieldsLocked(true);
        applyPassageDisplay(data.passage_display);
        applyContextDisplay(data.context_display);
        applyContextGeneratedAt(data.generated_at);
        if (data.context_applied_live) {
          $("contextualizarStatus").textContent = "Contexto aplicado a la transmisión en curso";
          setTimeout(() => {
            if ($("contextualizarStatus").textContent === "Contexto aplicado a la transmisión en curso") {
              $("contextualizarStatus").textContent = "";
            }
          }, 4000);
        }
        if (data.warning) alert(data.warning);
        if (!data.context_applied_live) $("contextualizarStatus").textContent = "";
      } catch {
        alert("Error al contextualizar");
        $("contextualizarBtn").disabled = !contextReady;
        $("contextualizarStatus").textContent = "";
      }
    };

    $("startBtn").onclick = async () => {
      const devSaved = await saveDeviceSettings();
      if (!devSaved.ok) {
        alert(devSaved.error);
        return;
      }
      if (!contextReady) {
        const accepted = await confirmNoContextStart();
        if (!accepted) return;
      }
      try {
        const res = await fetch("/api/live/start", { method: "POST" });
        let data;
        try {
          data = await res.json();
        } catch {
          alert("Error al iniciar transmisión");
          return;
        }
        if (!res.ok || !data.ok) {
          alert(data.error || "Error al iniciar transmisión");
          return;
        }
        syncLiveStatusFromApi(data);
        if (data.warning) alert(data.warning);
      } catch {
        alert("Error al iniciar transmisión");
      }
    };

    $("pauseBtn").onclick = async () => {
      if (state === "paused") {
        await fetch("/api/live/resume", { method: "POST" });
        return;
      }
      const pauseBtn = $("pauseBtn");
      const prevLabel = pauseBtn.textContent;
      pauseBtn.disabled = true;
      pauseBtn.textContent = "Pausando…";
      try {
        const res = await fetch("/api/live/pause", { method: "POST" });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          alert(data.error || "Error al pausar");
        }
      } catch {
        alert("Error al pausar");
      } finally {
        if (state !== "paused") {
          pauseBtn.disabled = false;
          pauseBtn.textContent = prevLabel;
        }
      }
    };

    $("sermonBtn").onclick = async () => {
      const btn = $("sermonBtn");
      const endpoint = sermonOn ? "/api/live/sermon-off" : "/api/live/sermon-on";
      btn.disabled = true;
      try {
        const res = await fetch(endpoint, { method: "POST" });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          alert(data.error || "Error al cambiar modo sermón");
          return;
        }
        if (data.sermon_on !== undefined) {
          sermonOn = data.sermon_on;
          updateSermonButton();
          updatePipelineStatus();
        }
      } catch {
        alert("Error al cambiar modo sermón");
      } finally {
        updateSermonButton();
      }
    };

    $("stopBtn").onclick = async () => {
      try {
        const res = await fetch("/api/live/stop", { method: "POST" });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          alert(data.error || "Error al finalizar transmisión");
          return;
        }
        syncLiveStatusFromApi(data);
      } catch {
        alert("Error al finalizar transmisión");
        return;
      }
      clearCaptions();
      testPlaying = false;
      $("testPlayBtn").disabled = !testFileReady;
      $("testStopBtn").disabled = true;
      await saveDeviceSettings();
    };

    $("testBtn").onclick = () => $("testModal").classList.remove("hidden");
    $("testCloseBtn").onclick = () => $("testModal").classList.add("hidden");
    $("testModal").onclick = (e) => {
      if (e.target === $("testModal")) $("testModal").classList.add("hidden");
    };

    $("testFileInput").onchange = async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      $("testFileInfo").textContent = "Subiendo...";
      $("testPlayBtn").disabled = true;
      const form = new FormData();
      form.append("file", file);
      const res = await fetch("/api/live/test/upload", { method: "POST", body: form });
      const data = await res.json();
      if (!data.ok) {
        testFileReady = false;
        $("testFileInfo").textContent = `Error: ${data.error}`;
        return;
      }
      testFileReady = true;
      testDurationSec = data.duration_sec;
      $("testFileInfo").textContent = `${data.filename} (${fmtTime(data.duration_sec)})`;
      updateTestProgress(0, testDurationSec);
      $("testPlayBtn").disabled = testPlaying;
    };

    $("testPlayBtn").onclick = async () => {
      if (!testFileReady || testPlaying) return;
      await saveDeviceSettings();
      const res = await fetch("/api/live/test/play", { method: "POST" });
      const data = await res.json();
      if (!data.ok) {
        $("testFileInfo").textContent = `Error: ${data.error}`;
        return;
      }
      syncLiveStatusFromApi(data);
      testPlaying = true;
      testDurationSec = data.duration_sec;
      $("testPlayBtn").disabled = true;
      $("testStopBtn").disabled = false;
      updateTestProgress(0, testDurationSec);
    };

    $("testStopBtn").onclick = async () => {
      try {
        const res = await fetch("/api/live/test/stop", { method: "POST" });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          alert(data.error || "Error al detener prueba");
          return;
        }
        syncLiveStatusFromApi(data);
      } catch {
        alert("Error al detener prueba");
        return;
      }
      testPlaying = false;
      $("testPlayBtn").disabled = !testFileReady;
      $("testStopBtn").disabled = true;
      clearCaptions();
      await saveDeviceSettings();
    };
  }

  async function saveDeviceSettings() {
    const dev = $("deviceSelect").value;
    const res = await fetch("/api/live/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        device_index: dev !== "" ? Number(dev) : null,
        gain: parseFloat($("gainSlider").value),
      }),
    });
    const data = await res.json();
    if (!data.ok) {
      return { ok: false, error: data.error || "Error al iniciar entrada" };
    }
    return { ok: true };
  }

  init();
})();

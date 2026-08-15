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

  const STATUS_POLL_FAST_MS = 3000;
  const STATUS_POLL_SLOW_MS = 30000;
  const WS_FRESH_MS = 5000;
  const WELCOME_STORAGE_KEY = "hki_operator_welcome_shown";
  let lastStatusPollAt = 0;

  let maxCaptionFinals = 8;
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

  const MAX_RECOMBINE_WARNINGS = 8;
  let recombineWarnings = [];

  function pushRecombineWarning(text) {
    recombineWarnings.push(text);
    if (recombineWarnings.length > MAX_RECOMBINE_WARNINGS) {
      recombineWarnings = recombineWarnings.slice(-MAX_RECOMBINE_WARNINGS);
    }
    const box = $("recombineWarnings");
    if (!box) return;
    box.classList.remove("hidden");
    box.innerHTML = "";
    recombineWarnings.forEach((line) => {
      const div = document.createElement("div");
      div.className = "warn-line";
      div.textContent = line;
      box.appendChild(div);
    });
  }

  function captionIdsSeen(itemId, itemIds) {
    const ids = (itemIds && itemIds.length ? itemIds : [itemId]).filter(Boolean);
    for (const line of captionFinals) {
      const stored = (line.dataset.itemIds || line.dataset.itemId || "")
        .split(",")
        .filter(Boolean);
      for (const id of ids) {
        if (stored.includes(id)) return true;
      }
    }
    return false;
  }

  function confirmCaptionFinal(itemId, text, meta = {}) {
    hideCaptionPlaceholder();
    clearCaptionDraft();
    if (captionKoEl) {
      captionKoEl.remove();
      captionKoEl = null;
    }

    if (captionIdsSeen(itemId, meta.item_ids)) return;

    const el = document.createElement("div");
    el.className = "line final";
    if (meta.repair_rejected) el.classList.add("repair-rejected");
    if (meta.had_incierto) el.classList.add("had-incierto");
    const ids = meta.item_ids && meta.item_ids.length ? meta.item_ids : [itemId];
    el.dataset.itemId = ids[0] || itemId;
    el.dataset.itemIds = ids.join(",");
    let display = text;
    if (meta.had_incierto && !meta.repair_rejected) {
      display = `${text} ⚠`;
    }
    el.textContent = display;
    captionArea().appendChild(el);
    captionFinals.push(el);

    if (captionFinals.length > maxCaptionFinals) {
      const old = captionFinals.shift();
      old.classList.add("fade-out");
      setTimeout(() => old.remove(), 300);
    }

    captionFinals.forEach((line, i) => {
      const repair = line.classList.contains("repair-rejected");
      const incierto = line.classList.contains("had-incierto");
      line.className = "line";
      if (repair) line.classList.add("repair-rejected");
      if (incierto) line.classList.add("had-incierto");
      if (i === captionFinals.length - 1) line.classList.add("final");
      else if (i === captionFinals.length - 2) line.classList.add("recent");
      else line.classList.add("old");
    });
    scrollCaptions();

    if (meta.repair_rejected) {
      const koHint = (meta.ko || "").slice(0, 60);
      pushRecombineWarning(
        `⚠ Recombine repair rechazado · ${koHint}${koHint.length >= 60 ? "…" : ""}`
      );
    } else if (meta.had_incierto) {
      pushRecombineWarning(
        `⚠ Traducción con duda (INCIERTO) · ${(meta.ko || "").slice(0, 50)}`
      );
    }
    if (meta.recombine_flags && meta.recombine_flags.length) {
      pushRecombineWarning(`Recombine: ${meta.recombine_flags.join("; ")}`);
    }
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
    const keyCount = (display.key_names || []).length;
    const critCount = (display.critical_sentences || []).length;
    const recurCount = (display.recurring_phrases || []).length;
    if (keyCount) metaParts.push(`Nombres clave: ${keyCount}`);
    if (recurCount) metaParts.push(`Frases recurrentes: ${recurCount}`);
    if (critCount) metaParts.push(`Frases críticas: ${critCount}`);
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

  function confirmSermonContextStart() {
    return new Promise((resolve) => {
      const modal = $("sermonContextModal");
      const onBtn = $("sermonContextOnBtn");
      const laterBtn = $("sermonContextLaterBtn");
      if (!modal || !onBtn || !laterBtn) {
        resolve(false);
        return;
      }

      const cleanup = () => {
        modal.classList.add("hidden");
        onBtn.onclick = null;
        laterBtn.onclick = null;
        modal.onclick = null;
      };

      onBtn.onclick = () => {
        cleanup();
        resolve(true);
      };
      laterBtn.onclick = () => {
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

  async function init() {
    bindEvents();
    try {
      const statusRes = await fetch("/api/live/status");
      const status = await statusRes.json();
      applyStatus(status);
      connectWs();
      maybeShowOperatorWelcome(status.scheme || "http");
    } catch (err) {
      console.error("HKI control init failed:", err);
      alert("No se pudo conectar al servidor. Reiniciá HKI e recargá la página.");
    }
  }

  function maybeShowOperatorWelcome(scheme) {
    try {
      if (sessionStorage.getItem(WELCOME_STORAGE_KEY) === "1") return;
    } catch (_) {}
    const modal = $("operatorWelcomeModal");
    if (!modal) return;
    const certHint = $("welcomeCertHint");
    if (certHint) {
      certHint.classList.toggle("hidden", scheme !== "https");
    }
    modal.classList.remove("hidden");
  }

  function dismissOperatorWelcome() {
    const modal = $("operatorWelcomeModal");
    if (modal) modal.classList.add("hidden");
    try {
      sessionStorage.setItem(WELCOME_STORAGE_KEY, "1");
    } catch (_) {}
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
    if (data.caption_max_lines !== undefined) {
      maxCaptionFinals = Math.max(3, parseInt(data.caption_max_lines, 10) || 8);
    }
    updateInputIoStatus();
    updateOutputIoStatus();
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
    lastStatusPollAt = Date.now();
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
      // Draft display off — finals only (pipeline still emits translation_draft)
      return;
    }
    if (ev.type === "translation") {
      confirmCaptionFinal(ev.item_id, ev.es, {
        item_ids: ev.item_ids,
        ko: ev.ko,
        repair_rejected: ev.repair_rejected,
        had_incierto: ev.had_incierto,
        recombine_flags: ev.recombine_flags,
      });
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
    updateContextualizarButton();

    updateLogButton();
    updatePipelineStatus();
    updateInputIoStatus();
    updateOutputIoStatus();
  }

  function updateTimer() {
    const h = String(Math.floor(elapsedSec / 3600)).padStart(2, "0");
    const m = String(Math.floor((elapsedSec % 3600) / 60)).padStart(2, "0");
    const s = String(elapsedSec % 60).padStart(2, "0");
    $("timerDisplay").textContent = `${h}:${m}:${s}`;
  }

  function statusPollDelayMs() {
    return wsIsLive() && Date.now() - lastWsMessageAt < WS_FRESH_MS
      ? STATUS_POLL_SLOW_MS
      : STATUS_POLL_FAST_MS;
  }

  async function maybePollAudienceStatus() {
    const now = Date.now();
    if (now - lastStatusPollAt < statusPollDelayMs()) return;
    lastStatusPollAt = now;
    await pollAudienceStatus();
  }

  setInterval(() => {
    if (state === "streaming") {
      elapsedSec++;
      updateTimer();
    }
  }, 1000);

  setInterval(maybePollAudienceStatus, 1000);

  function setIoStatus(dotEl, labelEl, mode, text) {
    if (!dotEl || !labelEl) return;
    const dotClass = "svc-dot " + mode;
    const labelClass = "io-status-text " + mode;
    if (dotEl.className !== dotClass) dotEl.className = dotClass;
    if (labelEl.className !== labelClass) labelEl.className = labelClass;
    if (labelEl.textContent !== text) labelEl.textContent = text;
  }

  function inputConnected() {
    if (testPlaying) return false;
    return state === "monitoring" || state === "streaming" || state === "paused";
  }

  function updateInputIoStatus() {
    const dot = $("inputIoDot");
    const label = $("inputIoLabel");
    const connected = inputConnected();
    setIoStatus(
      dot,
      label,
      connected ? "on" : "off",
      connected ? "Conectado" : "Sin conexión"
    );
  }

  function updateOutputIoStatus() {
    const dot = $("outputIoDot");
    const label = $("outputIoLabel");
    setIoStatus(
      dot,
      label,
      ttsAvailable ? "on" : "off",
      ttsAvailable ? "Conectado" : "Sin conexión"
    );
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
      setSvcState(dot, label, false, "Bloqueado", true);
    } else if (speakerSubscribers > 0) {
      setSvcState(dot, label, true, "Activo");
    } else {
      setSvcState(dot, label, false, "Inactivo (Sin solicitud)");
    }
    updateOutputIoStatus();
  }

  function bindEvents() {
    const welcomeModal = $("operatorWelcomeModal");
    const welcomeDismissBtn = $("welcomeDismissBtn");
    if (welcomeDismissBtn) {
      welcomeDismissBtn.onclick = dismissOperatorWelcome;
    }
    if (welcomeModal) {
      welcomeModal.onclick = (e) => {
        if (e.target === welcomeModal) dismissOperatorWelcome();
      };
    }

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
        if (
          contextReady &&
          !sermonOn &&
          !data.auto_sermon_on
        ) {
          const activate = await confirmSermonContextStart();
          if (activate) {
            try {
              const sr = await fetch("/api/live/sermon-on", { method: "POST" });
              const sd = await sr.json().catch(() => ({}));
              if (sr.ok && sd.ok && sd.sermon_on !== undefined) {
                sermonOn = sd.sermon_on;
                updateSermonButton();
                updatePipelineStatus();
              }
            } catch {
              /* ignore */
            }
          }
        }
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
    };
  }

  init();
})();

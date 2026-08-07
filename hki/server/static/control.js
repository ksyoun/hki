(() => {
  const $ = (id) => document.getElementById(id);

  let ws = null;
  let state = "idle";
  let elapsedSec = 0;
  let testFileReady = false;
  let testDurationSec = 0;
  let testPlaying = false;
  let captionsUrl = "";
  let contextReady = false;
  let hasLog = false;
  let hasLatencyReport = false;
  let ttsAvailable = false;
  let ttsActive = false;
  let audienceCount = 0;
  let speakerSubscribers = 0;
  let translationActive = false;

  const MAX_CAPTION_FINALS = 3;
  const captionArea = () => $("captionMonitor");
  let captionFinals = [];
  let captionKoEl = null;

  function clearCaptions() {
    captionFinals = [];
    captionKoEl = null;
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

  function confirmCaptionFinal(itemId, text) {
    hideCaptionPlaceholder();
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

  function openQrModal() {
    const url = captionsUrl || $("captionsUrl").href;
    if (!url || url === "#") return;
    window.HKIQR.open(url);
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

  function updateLogButton() {
    $("logBtn").classList.toggle("hidden", !(hasLog && state === "idle"));
    $("latencyBtn").classList.toggle("hidden", !(hasLatencyReport && state === "idle"));
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
    const statusRes = await fetch("/api/live/status");
    const status = await statusRes.json();
    await loadDevices(status.device_index);
    applyStatus(status);
    connectWs();
    bindEvents();
    if (state !== "streaming" && state !== "paused") {
      await saveDeviceSettings();
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
    if (data.speaker_subscribers !== undefined) speakerSubscribers = data.speaker_subscribers;
    if (data.translation_active !== undefined) translationActive = data.translation_active;
    if (data.tts_available !== undefined) ttsAvailable = data.tts_available;
    if (data.tts_active !== undefined) ttsActive = data.tts_active;
    updatePipelineStatus();
    updateTtsControls();
  }

  function applyStatus(data) {
    $("captionsUrl").href = data.captions_url;
    $("captionsUrl").textContent = data.captions_url;
    captionsUrl = data.captions_url;
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
    } else {
      setContentFieldsLocked(false);
      applyContextDisplay(null);
      applyContextGeneratedAt(null);
    }
    applyStatusFields(data);
  }

  async function refreshLogState() {
    const res = await fetch("/api/live/status");
    const data = await res.json();
    setHasLog(data.has_log);
    setHasLatencyReport(data.has_latency_report);
  }

  function connectWs() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/live?role=operator`);
    ws.onmessage = (e) => handleEvent(JSON.parse(e.data));
    ws.onclose = () => setTimeout(connectWs, 3000);
  }

  function handleEvent(ev) {
    if (ev.type === "status") {
      setState(ev.state, ev.elapsed_sec);
      if (ev.has_log !== undefined) setHasLog(ev.has_log);
      if (ev.has_latency_report !== undefined) setHasLatencyReport(ev.has_latency_report);
      if (ev.context_ready) {
        if (ev.passage_display && ev.passage_display.ko) {
          $("bibleText").value = ev.passage_display.ko;
        } else if (ev.bible_text) {
          $("bibleText").value = ev.bible_text;
        }
        if (ev.manuscript) $("manuscriptText").value = ev.manuscript;
        setContentFieldsLocked(true);
        applyPassageDisplay(ev.passage_display);
        applyContextDisplay(ev.context_display);
        applyContextGeneratedAt(ev.context_generated_at);
      } else if (ev.context_ready === false) {
        setContentFieldsLocked(false);
        applyContextDisplay(null);
        applyContextGeneratedAt(null);
      }
      applyStatusFields(ev);
      if (ev.state === "idle") {
        testPlaying = false;
        $("testPlayBtn").disabled = !testFileReady;
        $("testStopBtn").disabled = true;
      }
    } else if (ev.type === "level") {
      updateLevel(ev);
    } else if (ev.type === "output_level") {
      updateOutputLevel(ev);
    } else if (ev.type === "transcript") {
      if (ev.final) showCaptionKo(ev.text);
    } else if (ev.type === "translation") {
      confirmCaptionFinal(ev.item_id, ev.es);
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
        setTimeout(refreshLogState, 3500);
      }
    }
  }

  function setState(s, elapsed) {
    state = s;
    elapsedSec = elapsed || 0;
    const isLive = s === "streaming" || s === "paused";

    $("statusDot").className =
      "status-dot " + (s === "streaming" ? "live" : s === "paused" ? "paused" : "idle");

    $("idleControls").classList.toggle("hidden", isLive);
    $("liveControls").classList.toggle("hidden", !isLive);
    $("liveStopRow").classList.toggle("hidden", !isLive);
    if (isLive) {
      $("pauseBtn").disabled = false;
      $("pauseBtn").textContent = s === "paused" ? "▶ Reanudar" : "⏸ Pausar";
      updateTimer();
    }

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

  function updateLevel(ev) {
    const pct = Math.min(100, Math.max(0, ((ev.peak_db + 60) / 60) * 100));
    const fill = $("levelFill");
    fill.style.width = pct + "%";
    fill.style.background = ev.clipping ? "#e74c3c" : ev.peak_db > -6 ? "#e67e22" : "#27ae60";
  }

  function updateOutputLevel(ev) {
    if (!ttsAvailable) return;
    const fill = $("outputLevelFill");
    const label = $("outputLevelLabel");
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
    label.textContent = `${ev.peak_db.toFixed(1)} dB pico${phrase}`;
  }

  function setSvcState(dotEl, labelEl, active, text, unavailable) {
    dotEl.className = "svc-dot " + (unavailable ? "na" : active ? "on" : "off");
    labelEl.className = "svc-detail " + (unavailable ? "na" : active ? "on" : "off");
    labelEl.textContent = text;
  }

  function updatePipelineStatus() {
    const dot = $("pipelineDot");
    const label = $("pipelineStatusLabel");
    const isLive = state === "streaming" || state === "paused";

    if (!isLive) {
      setSvcState(dot, label, false, "Inactivo");
      return;
    }

    if (translationActive) {
      setSvcState(dot, label, true, `Activo (${audienceCount} conectados)`);
    } else {
      setSvcState(dot, label, false, "En espera de conectados");
    }
  }

  function updateTtsControls() {
    $("outputCard").style.opacity = ttsAvailable ? "1" : "0.5";
    const dot = $("ttsDot");
    const label = $("ttsStatusLabel");
    if (!ttsAvailable) {
      $("outputLevelFill").style.width = "0%";
      $("outputLevelLabel").textContent = "Bloqueado";
      setSvcState(dot, label, false, "Bloqueado", true);
    } else if (ttsActive) {
      setSvcState(
        dot,
        label,
        true,
        `Activo (${speakerSubscribers} solicitud${speakerSubscribers === 1 ? "" : "es"})`
      );
    } else {
      setSvcState(dot, label, false, "Inactivo — Ninguna Solicitud de Voz");
    }
  }

  function bindEvents() {
    bindCollapsible("passageCard", "passageToggle", "passageChevronBtn");
    bindCollapsible("contextSummaryCard", "contextSummaryToggle", "contextSummaryChevronBtn");
    $("qrBtn").onclick = openQrModal;
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

    $("stopBtn").onclick = async () => {
      await fetch("/api/live/stop", { method: "POST" });
      clearCaptions();
      testPlaying = false;
      $("testPlayBtn").disabled = !testFileReady;
      $("testStopBtn").disabled = true;
      await saveDeviceSettings();
      await refreshLogState();
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
      testPlaying = true;
      testDurationSec = data.duration_sec;
      $("testPlayBtn").disabled = true;
      $("testStopBtn").disabled = false;
      updateTestProgress(0, testDurationSec);
    };

    $("testStopBtn").onclick = async () => {
      await fetch("/api/live/test/stop", { method: "POST" });
      testPlaying = false;
      $("testPlayBtn").disabled = !testFileReady;
      $("testStopBtn").disabled = true;
      clearCaptions();
      await saveDeviceSettings();
      await refreshLogState();
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

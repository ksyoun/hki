(() => {
  const $ = (id) => document.getElementById(id);

  let ws = null;
  let state = "idle";
  let elapsedSec = 0;
  let testFileReady = false;
  let testDurationSec = 0;
  let testPlaying = false;
  let captionsUrl = "";
  let contentLocked = false;
  let hasLog = false;
  let hasLatencyReport = false;
  let ttsAvailable = false;
  let ttsActive = false;
  let audienceCount = 0;
  let speakerSubscribers = 0;
  let translationActive = false;
  let minAudienceCount = 1;

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

  function setContentLocked(locked) {
    contentLocked = locked;
    $("bibleText").readOnly = locked;
    $("manuscriptText").readOnly = locked;
    $("bibleText").classList.toggle("locked", locked);
    $("manuscriptText").classList.toggle("locked", locked);
    $("bibleCard").classList.toggle("locked", locked);
    $("manuscriptCard").classList.toggle("locked", locked);
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
      await saveSession();
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
    if (data.min_audience_count !== undefined) minAudienceCount = data.min_audience_count;
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
    if (isLive) {
      $("pauseBtn").textContent = s === "paused" ? "▶ Reanudar" : "⏸ Pausar";
      updateTimer();
    }

    setContentLocked(isLive);
    setDeviceLocked(isLive);

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

    if (translationActive) {
      setSvcState(dot, label, true, `Activo (${audienceCount} conectados)`);
    } else if (audienceCount > 0) {
      setSvcState(
        dot,
        label,
        false,
        `Esperando (${audienceCount}/${minAudienceCount} conectados)`
      );
    } else {
      setSvcState(dot, label, false, "Sin conectados");
    }
  }

  function updateTtsControls() {
    $("outputCard").style.opacity = ttsAvailable ? "1" : "0.5";
    const dot = $("ttsDot");
    const label = $("ttsStatusLabel");
    if (!ttsAvailable) {
      $("outputLevelFill").style.width = "0%";
      $("outputLevelLabel").textContent = "Voz desactivada";
      setSvcState(dot, label, false, "No disponible (.env)", true);
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
    $("qrBtn").onclick = openQrModal;
    $("logBtn").onclick = openLogWindow;
    $("latencyBtn").onclick = openLatencyWindow;

    $("deviceSelect").onchange = async () => {
      if (!contentLocked) await saveSession();
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

    $("startBtn").onclick = async () => {
      const saved = await saveSession();
      if (!saved.ok) {
        alert(saved.error);
        return;
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
        }
      } catch {
        alert("Error al iniciar transmisión");
      }
    };

    $("pauseBtn").onclick = async () => {
      if (state === "paused") {
        await fetch("/api/live/resume", { method: "POST" });
      } else {
        await fetch("/api/live/pause", { method: "POST" });
      }
    };

    $("stopBtn").onclick = async () => {
      await fetch("/api/live/stop", { method: "POST" });
      clearCaptions();
      testPlaying = false;
      $("testPlayBtn").disabled = !testFileReady;
      $("testStopBtn").disabled = true;
      await saveSession();
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
      await saveSession();
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
      await saveSession();
      await refreshLogState();
    };
  }

  async function saveSession() {
    const dev = $("deviceSelect").value;
    const res = await fetch("/api/live/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        bible_text: $("bibleText").value,
        manuscript: $("manuscriptText").value,
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

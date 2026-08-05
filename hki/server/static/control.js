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

  const MAX_CAPTION_FINALS = 3;
  const captionArea = () => $("captionMonitor");
  let captionFinals = [];
  let captionDraftEl = null;
  let captionDraftItemId = null;
  let captionKoEl = null;

  function clearCaptions() {
    captionFinals = [];
    captionDraftEl = null;
    captionDraftItemId = null;
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
    const area = captionArea();
    area.scrollTop = area.scrollHeight;
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

  function showCaptionDraft(itemId, text) {
    hideCaptionPlaceholder();
    if (captionDraftItemId !== itemId) {
      if (captionDraftEl) captionDraftEl.remove();
      captionDraftEl = document.createElement("div");
      captionDraftEl.className = "line draft";
      captionArea().appendChild(captionDraftEl);
      captionDraftItemId = itemId;
    }
    captionDraftEl.textContent = text;
    scrollCaptions();
  }

  function confirmCaptionFinal(itemId, text) {
    hideCaptionPlaceholder();
    if (captionDraftEl && captionDraftItemId === itemId) {
      captionDraftEl.remove();
      captionDraftEl = null;
      captionDraftItemId = null;
    }
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
      old.classList.remove("final");
      old.classList.add("old");
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
    await loadDevices();
    await loadStatus();
    connectWs();
    bindEvents();
    if (state !== "streaming" && state !== "paused") {
      await saveSession();
    }
  }

  async function loadDevices() {
    const res = await fetch("/api/audio-devices");
    const data = await res.json();
    const sel = $("deviceSelect");
    sel.innerHTML = "";
    data.devices.forEach((d) => {
      const opt = document.createElement("option");
      opt.value = d.index;
      opt.textContent = `${d.name} (${d.sample_rate} Hz)`;
      if (d.index === data.scarlett_index) opt.selected = true;
      sel.appendChild(opt);
    });
  }

  async function loadStatus() {
    const res = await fetch("/api/live/status");
    const data = await res.json();
    $("captionsUrl").href = data.captions_url;
    $("captionsUrl").textContent = data.captions_url;
    captionsUrl = data.captions_url;
    if (data.gain) {
      $("gainSlider").value = data.gain;
      $("gainValue").textContent = data.gain.toFixed(1);
    }
    setState(data.state, data.elapsed_sec);
  }

  function connectWs() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/live`);
    ws.onmessage = (e) => handleEvent(JSON.parse(e.data));
    ws.onclose = () => setTimeout(connectWs, 3000);
  }

  function handleEvent(ev) {
    if (ev.type === "status") {
      setState(ev.state, ev.elapsed_sec);
      if (ev.state === "idle") {
        testPlaying = false;
        $("testPlayBtn").disabled = !testFileReady;
        $("testStopBtn").disabled = true;
      }
    } else if (ev.type === "level") {
      updateLevel(ev);
    } else if (ev.type === "transcript") {
      if (ev.final) showCaptionKo(ev.text);
    } else if (ev.type === "translation") {
      if (ev.tier === "draft") {
        showCaptionDraft(ev.item_id, ev.es);
      } else {
        confirmCaptionFinal(ev.item_id, ev.es);
      }
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

    const badge = $("monitorBadge");
    if (isLive) {
      badge.textContent = s === "paused" ? "● PAUSADO" : "● EN VIVO";
      badge.style.color = s === "paused" ? "#e67e22" : "#e74c3c";
    } else {
      badge.textContent = "● MONITOR";
      badge.style.color = "#27ae60";
    }
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
    $("levelLabel").textContent = `${ev.peak_db.toFixed(1)} dB pico`;
  }

  function bindEvents() {
    $("qrBtn").onclick = openQrModal;

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
      await saveSession();
      const res = await fetch("/api/live/start", { method: "POST" });
      const data = await res.json();
      if (!data.ok) alert(data.error || "Error al iniciar");
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
      $("levelLabel").textContent = data.error || "Error al iniciar entrada";
      $("monitorBadge").textContent = "● ERROR";
      $("monitorBadge").style.color = "#e74c3c";
      return false;
    }
    return true;
  }

  init();
})();

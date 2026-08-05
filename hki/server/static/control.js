(() => {
  const $ = (id) => document.getElementById(id);

  let ws = null;
  let state = "idle";
  let elapsedSec = 0;
  let monitoring = false;
  let testFileReady = false;
  let testDurationSec = 0;
  let testPlaying = false;
  let captionsUrl = "";

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

  // --- Init ---
  async function init() {
    await loadDevices();
    await loadStatus();
    connectWs();
    bindEvents();
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
    ws.onmessage = (e) => {
      const ev = JSON.parse(e.data);
      handleEvent(ev);
    };
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
      if (ev.final) $("previewKo").textContent = `KO: ${ev.text}`;
    } else if (ev.type === "translation") {
      if (ev.tier === "draft") {
        $("previewDraft").textContent = `ES (임시): ${ev.es}`;
      } else {
        $("previewFinal").textContent = `ES (확정): ${ev.es}`;
        $("previewDraft").textContent = "";
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
    const dot = $("statusDot");
    dot.className = "status-dot " + (s === "streaming" ? "live" : s === "paused" ? "paused" : "idle");

    if (s === "streaming" || s === "paused") {
      $("idleControls").classList.add("hidden");
      $("liveControls").classList.remove("hidden");
      $("pauseBtn").textContent = s === "paused" ? "▶ 재개" : "⏸ 일시정지";
      updateTimer();
    } else {
      $("idleControls").classList.remove("hidden");
      $("liveControls").classList.add("hidden");
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
    $("levelLabel").textContent = `${ev.peak_db.toFixed(1)} dB peak`;
  }

  function bindEvents() {
    $("settingsToggle").onclick = () => {
      $("settingsPanel").classList.toggle("hidden");
    };

    $("qrBtn").onclick = openQrModal;

    $("gainSlider").oninput = async (e) => {
      const gain = parseFloat(e.target.value);
      $("gainValue").textContent = gain.toFixed(1);
      await fetch("/api/live/gain", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ gain }),
      });
    };

    $("monitorBtn").onclick = async () => {
      if (monitoring) {
        await fetch("/api/live/monitor/stop", { method: "POST" });
        $("monitorBtn").textContent = "입력 테스트";
        monitoring = false;
      } else {
        await saveSession();
        await fetch("/api/live/monitor/start", { method: "POST" });
        $("monitorBtn").textContent = "입력 테스트 중지";
        monitoring = true;
      }
    };

    $("startBtn").onclick = async () => {
      await saveSession();
      if (monitoring) {
        await fetch("/api/live/monitor/stop", { method: "POST" });
        monitoring = false;
        $("monitorBtn").textContent = "입력 테스트";
      }
      await fetch("/api/live/start", { method: "POST" });
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
      $("previewKo").textContent = "";
      $("previewDraft").textContent = "";
      $("previewFinal").textContent = "";
      testPlaying = false;
      $("testPlayBtn").disabled = !testFileReady;
      $("testStopBtn").disabled = true;
    };

    $("testBtn").onclick = () => {
      $("testModal").classList.remove("hidden");
    };

    $("testCloseBtn").onclick = () => {
      $("testModal").classList.add("hidden");
    };

    $("testModal").onclick = (e) => {
      if (e.target === $("testModal")) $("testModal").classList.add("hidden");
    };

    $("testFileInput").onchange = async (e) => {
      const file = e.target.files[0];
      if (!file) return;

      $("testFileInfo").textContent = "업로드 중...";
      $("testPlayBtn").disabled = true;

      const form = new FormData();
      form.append("file", file);
      const res = await fetch("/api/live/test/upload", { method: "POST", body: form });
      const data = await res.json();

      if (!data.ok) {
        testFileReady = false;
        $("testFileInfo").textContent = `오류: ${data.error}`;
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
      if (monitoring) {
        await fetch("/api/live/monitor/stop", { method: "POST" });
        monitoring = false;
        $("monitorBtn").textContent = "입력 테스트";
      }
      const res = await fetch("/api/live/test/play", { method: "POST" });
      const data = await res.json();
      if (!data.ok) {
        $("testFileInfo").textContent = `오류: ${data.error}`;
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
      $("previewKo").textContent = "";
      $("previewDraft").textContent = "";
      $("previewFinal").textContent = "";
    };
  }

  async function saveSession() {
    await fetch("/api/live/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        bible_text: $("bibleText").value,
        manuscript: $("manuscriptText").value,
        device_index: parseInt($("deviceSelect").value),
        gain: parseFloat($("gainSlider").value),
      }),
    });
  }

  init();
})();

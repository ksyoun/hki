(() => {
  const $ = (id) => document.getElementById(id);

  let ws = null;
  let state = "idle";
  let elapsedSec = 0;
  let monitoring = false;

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

(() => {
  const $ = (id) => document.getElementById(id);

  let ws = null;
  let state = "idle";
  let elapsedSec = 0;
  let contextReady = false;
  let ttsAvailable = false;
  let ttsActive = false;
  let audienceCount = 0;
  let audienceGateOpen = false;
  let speakerSubscribers = 0;
  let translationActive = false;
  let transcriptionActive = false;
  let sermonOn = false;
  let joinUrl = "";
  let captionsDirectUrl = "";
  let maxCaptionFinals = 8;
  let captionFinals = [];
  let slides = [];
  let cursor = -1;
  let nviPreview = "";
  let saveTimer = null;
  let insertAt = null;
  let dragFrom = null;

  const captionArea = () => $("captionMonitor");

  function wsLiveUrl(role) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const port = location.port || "8765";
    const host = location.port ? location.host : `${location.hostname}:${port}`;
    return `${proto}://${host}/ws/live?role=${role}`;
  }

  function isLive() {
    return state === "streaming" || state === "paused";
  }

  function currentCue() {
    if (cursor >= 0 && slides[cursor]) return slides[cursor].cue;
    if (state === "streaming") return "speak";
    if (state === "paused") return "caption";
    return null;
  }

  function hidePlaceholder() {
    const el = $("captionPlaceholder");
    if (el) el.classList.add("hidden");
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
    hidePlaceholder();
    if (captionIdsSeen(itemId, meta.item_ids)) return;
    const el = document.createElement("div");
    el.className = "line final";
    if (meta.lyrics) el.classList.add("lyrics");
    const ids = meta.item_ids && meta.item_ids.length ? meta.item_ids : [itemId];
    el.dataset.itemId = ids[0] || itemId;
    el.dataset.itemIds = ids.join(",");
    el.textContent = text;
    captionArea().appendChild(el);
    captionFinals.push(el);
    if (captionFinals.length > maxCaptionFinals) {
      const old = captionFinals.shift();
      old.remove();
    }
    captionFinals.forEach((line, i) => {
      const lyrics = line.classList.contains("lyrics");
      line.className = "line" + (lyrics ? " lyrics" : "");
      if (i === captionFinals.length - 1) line.classList.add("final");
      else if (i === captionFinals.length - 2) line.classList.add("recent");
      else line.classList.add("old");
    });
    captionArea().scrollTop = captionArea().scrollHeight;
  }

  function setIo(dot, label, on, text, na) {
    dot.className = "svc-dot " + (na ? "na" : on ? "on" : "off");
    label.className = "io-status-text " + (na ? "na" : on ? "on" : "off");
    label.textContent = text;
  }

  function setSvc(el, on, text, na) {
    el.className = "svc-detail " + (na ? "na" : on ? "on" : "off");
    el.textContent = text;
  }

  function updateIo() {
    const inputOn = state === "monitoring" || isLive();
    setIo($("inputIoDot"), $("inputIoLabel"), inputOn, inputOn ? "Conectado" : "Sin conexión");
    setIo(
      $("outputIoDot"),
      $("outputIoLabel"),
      ttsAvailable,
      ttsAvailable ? "Conectado" : "Sin conexión",
      !ttsAvailable
    );
    $("outputCard").style.opacity = ttsAvailable ? "1" : "0.55";
  }

  function updateDelivery() {
    $("audienceCountLabel").textContent = `Audiencia: ${audienceCount}`;
    $("audienceCountLabel").className = "svc-detail " + (audienceCount > 0 ? "on" : "off");

    const pipeEl = $("pipelineStatusLabel");
    if (!isLive()) {
      setSvc(pipeEl, audienceGateOpen, audienceGateOpen ? "Listo — audiencia conectada" : "Inactivo (Sin conexión)");
    } else if (state === "paused") {
      setSvc(pipeEl, false, audienceGateOpen ? "Pausa — audiencia conectada" : "Pausa (Sin conexión)");
    } else if (translationActive || transcriptionActive) {
      setSvc(pipeEl, true, sermonOn ? "Activo — sermón" : "Activo — servicio general");
    } else {
      setSvc(pipeEl, false, "En espera de audiencia");
    }

    const ttsEl = $("ttsStatusLabel");
    if (!ttsAvailable) setSvc(ttsEl, false, "Bloqueado", true);
    else if (speakerSubscribers > 0) setSvc(ttsEl, true, `Activo (${speakerSubscribers})`);
    else setSvc(ttsEl, false, "Inactivo (Sin solicitud)");

    const line = $("deliveryLine");
    const cue = currentCue();
    const captionsOut = audienceCount > 0;
    const audioOut =
      captionsOut &&
      ttsAvailable &&
      speakerSubscribers > 0 &&
      cue !== "caption" &&
      cue !== "bible" &&
      state === "streaming";
    if (!captionsOut) {
      line.className = "delivery-line wait";
      line.textContent = "Sin salida — sin audiencia";
    } else if (audioOut) {
      line.className = "delivery-line audio";
      line.textContent = "Subtítulos + audio";
    } else {
      line.className = "delivery-line caps";
      line.textContent = "Solo subtítulos";
    }
  }

  function formatTime(sec) {
    const s = Math.max(0, Number(sec) || 0);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const r = s % 60;
    return [h, m, r].map((n) => String(n).padStart(2, "0")).join(":");
  }

  function setState(next, elapsed) {
    const wasLive = isLive();
    const changed = next !== state;
    state = next;
    if (elapsed !== undefined) elapsedSec = elapsed;
    const live = isLive();
    $("idleControls").classList.toggle("hidden", live);
    $("liveControls").classList.toggle("hidden", !live);
    const badge = $("onAirBadge");
    if (state === "paused") {
      badge.textContent = "PAUSA";
      badge.className = "on-air-badge paused";
    } else if (state === "streaming") {
      badge.textContent = "ON AIR";
      badge.className = "on-air-badge live";
    } else {
      badge.textContent = "IDLE";
      badge.className = "on-air-badge idle";
    }
    $("timerDisplay").textContent = formatTime(elapsedSec);
    if (wasLive && !isLive()) cursor = -1;
    updateIo();
    updateDelivery();
    updatePresPos();
    if (changed) renderRundown();
  }

  function updatePresPos() {
    const n = slides.length;
    const cur = cursor < 0 ? "—" : String(cursor + 1);
    $("presPos").textContent = n ? `${cur} / ${n}` : "— / —";
    $("prevBtn").disabled = !isLive() || cursor <= 0;
    $("nextBtn").disabled = !isLive() || !n || cursor >= n - 1;
  }

  function applyRundown(data) {
    if (!data) return;
    if (Array.isArray(data.slides)) slides = data.slides;
    if (data.cursor !== undefined) cursor = data.cursor;
    updatePresPos();
    updateDelivery();
    renderRundown();
  }

  function collectSlides() {
    return slides.map((s) => ({
      id: s.id,
      cue: s.cue,
      ko: s.ko || "",
      es: s.es || "",
      label: s.label || "",
    }));
  }

  function scheduleSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveRundown, 400);
  }

  async function saveRundown() {
    const res = await fetch("/api/v2/rundown", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slides: collectSlides(), cursor }),
    });
    const data = await res.json().catch(() => ({}));
    if (data.ok) applyRundown(data);
  }

  function cueLabel(cue) {
    if (cue === "caption") return "♪ subtítulo";
    if (cue === "sermon") return "✝ sermón";
    if (cue === "bible") return "📖 lectura";
    return "🎤 hablar";
  }

  function closeInsertPop() {
    insertAt = null;
    renderRundown();
  }

  function renderRundown() {
    const table = $("rundownTable");
    table.innerHTML = "";
    const addGap = (index) => {
      const gap = document.createElement("div");
      gap.className = "rd-gap" + (insertAt === index ? " show" : "");
      const plus = document.createElement("button");
      plus.type = "button";
      plus.className = "rd-plus";
      plus.textContent = "+";
      plus.onclick = (e) => {
        e.stopPropagation();
        insertAt = insertAt === index ? null : index;
        renderRundown();
      };
      gap.appendChild(plus);
      if (insertAt === index) {
        const pop = document.createElement("div");
        pop.className = "cue-pop";
        [
          ["speak", "🎤 hablar"],
          ["caption", "♪ subtítulo"],
          ["bible", "📖 lectura"],
          ["sermon", "✝ sermón"],
        ].forEach(([cue, label]) => {
          const b = document.createElement("button");
          b.type = "button";
          b.textContent = label;
          b.onclick = (ev) => {
            ev.stopPropagation();
            slides.splice(index, 0, { id: "", cue, ko: "", es: "", label: "" });
            if (cursor >= index) cursor += 1;
            insertAt = null;
            renderRundown();
            scheduleSave();
          };
          pop.appendChild(b);
        });
        gap.appendChild(pop);
      }
      gap.addEventListener("dragover", (e) => {
        e.preventDefault();
        gap.classList.add("show");
      });
      gap.addEventListener("drop", (e) => {
        e.preventDefault();
        if (dragFrom == null) return;
        moveSlide(dragFrom, index);
      });
      table.appendChild(gap);
    };

    if (!slides.length) {
      addGap(0);
      const empty = document.createElement("div");
      empty.className = "rd-empty";
      empty.textContent = "Pase el cursor entre filas y pulse + para agregar una hoja";
      table.appendChild(empty);
      return;
    }

    addGap(0);
    slides.forEach((slide, i) => {
      const row = document.createElement("div");
      row.className = "rd-row" + (i === cursor && isLive() ? " current" : "");
      row.draggable = true;
      row.ondragstart = () => {
        dragFrom = i;
      };
      row.ondragend = () => {
        dragFrom = null;
      };

      const num = document.createElement("div");
      num.className = "rd-num";
      num.textContent = String(i + 1);
      num.title = "Arrastrar para mover";
      num.onclick = () => {
        if (isLive()) goto({ index: i });
      };

      const sel = document.createElement("select");
      ["speak", "caption", "bible", "sermon"].forEach((c) => {
        const o = document.createElement("option");
        o.value = c;
        o.textContent = cueLabel(c);
        if (slide.cue === c) o.selected = true;
        sel.appendChild(o);
      });
      sel.onchange = () => {
        slides[i].cue = sel.value;
        if (sel.value !== "caption") slides[i].es = "";
        renderRundown();
        scheduleSave();
      };

      const body = document.createElement("div");
      if (slide.cue === "caption") {
        const ko = document.createElement("textarea");
        ko.placeholder = "Coreano (una hoja)";
        ko.value = slide.ko || "";
        ko.oninput = () => {
          slides[i].ko = ko.value;
          scheduleSave();
        };
        const es = document.createElement("textarea");
        es.placeholder = "Español (subtítulo)";
        es.value = slide.es || "";
        es.oninput = () => {
          slides[i].es = es.value;
          scheduleSave();
        };
        body.appendChild(ko);
        body.appendChild(es);
      } else if (slide.cue === "bible") {
        const inp = document.createElement("input");
        inp.type = "text";
        inp.placeholder = "Lectura bíblica (título)";
        inp.value = slide.label || slide.ko || "";
        inp.oninput = () => {
          slides[i].label = inp.value;
          slides[i].ko = inp.value;
          scheduleSave();
        };
        const nvi = document.createElement("div");
        nvi.style.cssText = "font-size:0.75rem;color:#9db4ff;margin-top:0.25rem;white-space:pre-wrap;max-height:6rem;overflow:auto";
        nvi.textContent = nviPreview
          ? nviPreview
          : "NVI al ir a esta hoja (Contextualizar primero)";
        body.appendChild(inp);
        body.appendChild(nvi);
      } else {
        const inp = document.createElement("input");
        inp.type = "text";
        inp.placeholder = slide.cue === "sermon"
          ? "Nota (en subtítulos: ✝ Sermón ✝)"
          : "Título (🎤 en subtítulos al entrar)";
        inp.value = slide.label || slide.ko || "";
        inp.oninput = () => {
          slides[i].label = inp.value;
          slides[i].ko = inp.value;
          scheduleSave();
        };
        body.appendChild(inp);
      }

      const actions = document.createElement("div");
      actions.className = "rd-actions";
      if (slide.cue === "caption") {
        const tr = document.createElement("button");
        tr.type = "button";
        tr.textContent = "ES";
        tr.onclick = () => translateRow(i);
        actions.appendChild(tr);
      }
      const del = document.createElement("button");
      del.type = "button";
      del.textContent = "×";
      del.onclick = () => {
        slides.splice(i, 1);
        if (cursor === i) cursor = -1;
        else if (cursor > i) cursor -= 1;
        renderRundown();
        scheduleSave();
      };
      actions.appendChild(del);

      row.appendChild(num);
      row.appendChild(sel);
      row.appendChild(body);
      row.appendChild(actions);
      table.appendChild(row);
      addGap(i + 1);
    });
  }

  function moveSlide(from, to) {
    if (from === to || from === to - 1) {
      dragFrom = null;
      return;
    }
    const item = slides.splice(from, 1)[0];
    let dest = to;
    if (to > from) dest -= 1;
    slides.splice(dest, 0, item);
    if (cursor === from) cursor = dest;
    else if (from < cursor && dest >= cursor) cursor -= 1;
    else if (from > cursor && dest <= cursor) cursor += 1;
    dragFrom = null;
    insertAt = null;
    renderRundown();
    scheduleSave();
  }

  async function goto(body) {
    if (!isLive()) return;
    clearTimeout(saveTimer);
    await saveRundown();
    const res = await fetch("/api/v2/rundown/goto", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!data.ok && data.error) alert(data.error);
    if (data.slides) applyRundown(data);
  }

  async function translateRow(index) {
    const res = await fetch("/api/v2/rundown/translate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index }),
    });
    const data = await res.json().catch(() => ({}));
    if (!data.ok) {
      alert(data.error || "Error al traducir");
      return;
    }
    if (data.warning) alert(data.warning);
    applyRundown(data);
  }

  function applyStatus(data) {
    if (!data) return;
    if (data.state) setState(data.state, data.elapsed_sec);
    else if (data.elapsed_sec !== undefined) {
      elapsedSec = data.elapsed_sec;
      $("timerDisplay").textContent = formatTime(elapsedSec);
    }
    if (data.tts_available !== undefined) ttsAvailable = !!data.tts_available;
    if (data.tts_active !== undefined) ttsActive = !!data.tts_active;
    if (data.audience_count !== undefined) audienceCount = data.audience_count;
    if (data.audience_gate_open !== undefined) audienceGateOpen = !!data.audience_gate_open;
    if (data.speaker_subscribers !== undefined) speakerSubscribers = data.speaker_subscribers;
    if (data.translation_active !== undefined) translationActive = !!data.translation_active;
    if (data.transcription_active !== undefined) transcriptionActive = !!data.transcription_active;
    if (data.sermon_on !== undefined) sermonOn = !!data.sermon_on;
    if (data.caption_max_lines) maxCaptionFinals = data.caption_max_lines;
    if (data.join_url) {
      joinUrl = data.join_url;
      $("joinUrl").textContent = joinUrl;
      $("joinUrl").href = joinUrl;
    }
    if (data.captions_direct_url) {
      captionsDirectUrl = data.captions_direct_url;
      $("directCaptionsUrl").textContent = captionsDirectUrl;
      $("directCaptionsUrl").href = captionsDirectUrl;
    }
    if (data.context_ready !== undefined) {
      setContentLocked(!!data.context_ready);
      if (data.bible_text) $("bibleText").value = data.bible_text;
      if (data.manuscript) $("manuscriptText").value = data.manuscript;
      if (data.passage_display) applyPassage(data.passage_display);
      if (data.context_display) applyContext(data.context_display);
      applyContextTime(data.context_generated_at);
    }
    updateIo();
    updateDelivery();
  }

  function setContentLocked(locked) {
    contextReady = locked;
    $("bibleText").readOnly = locked;
    $("manuscriptText").readOnly = locked;
    $("bibleText").classList.toggle("locked", locked);
    $("manuscriptText").classList.toggle("locked", locked);
    $("contextualizarBtn").disabled = locked;
    $("contextOkCard").classList.toggle("hidden", !locked);
    $("passageCard").classList.toggle("hidden", !locked);
    $("contextCards").classList.toggle("hidden", !locked);
  }

  function applyPassage(display) {
    if (!display) {
      nviPreview = "";
      return;
    }
    $("passageKo").textContent = display.ko || "";
    $("passageNvi").textContent = display.nvi || "";
    nviPreview = (display.nvi || "").trim();
    if (slides.some((s) => s.cue === "bible")) renderRundown();
  }

  function applyContext(display) {
    if (!display) {
      $("contextCards").classList.add("hidden");
      return;
    }
    const ko = (display.ko || {}).sermon_summary || "";
    const es = (display.es || {}).sermon_summary || "";
    $("contextKoSummarySection").classList.toggle("hidden", !ko);
    $("contextKoSummaryText").textContent = ko;
    $("contextEsSummarySection").classList.toggle("hidden", !es);
    $("contextEsSummaryText").textContent = es;
  }

  function applyContextTime(generatedAt) {
    const el = $("contextOkTime");
    if (!generatedAt) {
      el.textContent = "";
      return;
    }
    const d = new Date(generatedAt);
    el.textContent = Number.isNaN(d.getTime()) ? "" : " · " + d.toLocaleTimeString();
  }

  function handleEvent(ev) {
    if (ev.type === "translation") {
      confirmCaptionFinal(ev.item_id, ev.es, { item_ids: ev.item_ids });
    } else if (ev.type === "lyrics") {
      confirmCaptionFinal(ev.item_id, ev.text, { item_ids: ev.item_ids, lyrics: true });
    } else if (ev.type === "status") {
      applyStatus(ev);
    } else if (ev.type === "v2_rundown") {
      applyRundown(ev);
    } else if (ev.type === "sermon_mode") {
      if (ev.sermon_on !== undefined) sermonOn = ev.sermon_on;
      updateDelivery();
    } else if (ev.type === "paused") {
      setState("paused", elapsedSec);
    } else if (ev.type === "resumed") {
      setState("streaming", elapsedSec);
    }
  }

  function connect() {
    if (ws) {
      try {
        ws.close();
      } catch (_) {}
    }
    ws = new WebSocket(wsLiveUrl("operator"));
    ws.onmessage = (e) => {
      try {
        handleEvent(JSON.parse(e.data));
      } catch (_) {}
    };
    ws.onclose = () => setTimeout(connect, 3000);
  }

  async function loadStatus() {
    const res = await fetch("/api/live/status");
    const data = await res.json().catch(() => ({}));
    applyStatus(data);
  }

  async function loadRundown() {
    const res = await fetch("/api/v2/rundown");
    const data = await res.json().catch(() => ({}));
    if (data.ok) applyRundown(data);
  }

  $("startBtn").onclick = async () => {
    const res = await fetch("/api/live/start", { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!data.ok) {
      alert(data.error || "No se pudo iniciar");
      return;
    }
    if (data.warning) alert(data.warning);
    applyStatus(data);
    await fetch("/api/v2/rundown", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slides: collectSlides(), cursor: -1 }),
    });
    cursor = -1;
    updatePresPos();
    renderRundown();
  };

  $("stopBtn").onclick = async () => {
    const res = await fetch("/api/live/stop", { method: "POST" });
    const data = await res.json().catch(() => ({}));
    applyStatus(data);
    cursor = -1;
    updatePresPos();
    renderRundown();
  };
  $("prevBtn").onclick = () => goto({ action: "prev" });
  $("nextBtn").onclick = () => goto({ action: "next" });

  document.addEventListener("keydown", (e) => {
    if (!isLive()) return;
    const tag = (e.target && e.target.tagName) || "";
    if (tag === "TEXTAREA" || tag === "INPUT" || tag === "SELECT") return;
    if (e.key === "ArrowRight" || e.key === " " || e.key === "Spacebar") {
      e.preventDefault();
      goto({ action: "next" });
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      goto({ action: "prev" });
    }
  });

  $("contextualizarBtn").onclick = async () => {
    const bible = $("bibleText").value.trim();
    if (!bible) {
      alert("El texto bíblico es obligatorio");
      return;
    }
    $("contextualizarBtn").disabled = true;
    $("contextualizarStatus").textContent = "Extrayendo referencias…";
    try {
      const res = await fetch("/api/live/contextualizar", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          bible_text: bible,
          manuscript: $("manuscriptText").value.trim(),
        }),
      });
      const data = await res.json();
      if (!data.ok) {
        alert(data.error || "Error al contextualizar");
        $("contextualizarBtn").disabled = false;
        $("contextualizarStatus").textContent = "";
        return;
      }
      setContentLocked(true);
      applyPassage(data.passage_display);
      applyContext(data.context_display);
      applyContextTime(data.generated_at);
      $("contextualizarStatus").textContent = "";
      if (data.warning) alert(data.warning);
    } catch {
      alert("Error al contextualizar");
      $("contextualizarBtn").disabled = false;
      $("contextualizarStatus").textContent = "";
    }
  };

  $("resetContextBtn").onclick = async () => {
    if (!confirm("¿Liberar el contexto contextualizado?")) return;
    await fetch("/api/live/reset-context", { method: "POST" });
    setContentLocked(false);
    $("bibleText").value = "";
    $("manuscriptText").value = "";
    applyContext(null);
  };

  function openQr(kind) {
    if (!window.HKIQR) return;
    if (kind === "guide") window.HKIQR.open(joinUrl, { mode: "guide" });
    else if (kind === "direct") window.HKIQR.open(captionsDirectUrl, { mode: "direct" });
    else if (window.HKIQR.printBoth) window.HKIQR.printBoth(joinUrl, captionsDirectUrl);
  }
  $("qrGuideBtn").onclick = () => openQr("guide");
  $("qrDirectBtn").onclick = () => openQr("direct");
  $("qrPrintBothBtn").onclick = () => openQr("both");

  setInterval(() => {
    if (isLive()) {
      elapsedSec += 1;
      $("timerDisplay").textContent = formatTime(elapsedSec);
    }
  }, 1000);

  loadStatus();
  loadRundown();
  connect();
})();

(() => {
  const WEBM_URL = "/static/keep-awake.webm";
  const MP4_URL = "/static/keep-awake.mp4";
  const IS_ANDROID = /Android/i.test(navigator.userAgent);

  let wakeLock = null;
  let video = null;
  let sharedAudio = null;
  let audioCtx = null;
  let audioOsc = null;
  let watchdog = null;
  let active = false;
  let mode = "off"; // off | wakelock | fallback | background
  let enteredFullscreen = false;
  let onFullscreenExit = null;
  let ttsActive = false;

  function getSharedAudio() {
    if (!sharedAudio) {
      sharedAudio = document.createElement("audio");
      sharedAudio.setAttribute("playsinline", "");
      sharedAudio.setAttribute("webkit-playsinline", "");
      sharedAudio.preload = "auto";
      sharedAudio.style.cssText =
        "position:fixed;width:0;height:0;opacity:0;pointer-events:none;";
      document.body.appendChild(sharedAudio);
    }
    return sharedAudio;
  }

  function buildVideo() {
    if (video) return video;
    video = document.createElement("video");
    video.setAttribute("playsinline", "");
    video.setAttribute("webkit-playsinline", "");
    video.setAttribute("disablePictureInPicture", "");
    video.setAttribute("disableRemotePlayback", "");
    video.muted = true;
    video.volume = 0;
    video.style.cssText =
      "position:fixed;width:4px;height:4px;bottom:0;left:0;opacity:0.04;pointer-events:none;z-index:0;";
    const webm = document.createElement("source");
    webm.src = WEBM_URL;
    webm.type = "video/webm";
    const mp4 = document.createElement("source");
    mp4.src = MP4_URL;
    mp4.type = "video/mp4";
    video.appendChild(webm);
    video.appendChild(mp4);
    video.addEventListener("loadedmetadata", () => {
      if (video.duration <= 1) {
        video.loop = true;
      } else {
        video.loop = false;
        video.addEventListener("timeupdate", () => {
          if (video.currentTime > 0.5) {
            video.currentTime = Math.random();
          }
        });
      }
    });
    document.body.appendChild(video);
    return video;
  }

  function buildSilentAudio() {
    const audio = getSharedAudio();
    if (!audio.src || audio.src.indexOf("keep-awake") === -1) {
      audio.src = MP4_URL;
    }
    return audio;
  }

  async function tryFullscreen() {
    const el = document.documentElement;
    try {
      if (document.fullscreenElement || document.webkitFullscreenElement) {
        enteredFullscreen = true;
        return true;
      }
      if (el.requestFullscreen) {
        await el.requestFullscreen({ navigationUI: "hide" });
      } else if (el.webkitRequestFullscreen) {
        await el.webkitRequestFullscreen();
      }
      enteredFullscreen = !!(
        document.fullscreenElement || document.webkitFullscreenElement
      );
      return enteredFullscreen;
    } catch (_) {
      return false;
    }
  }

  function setMediaSessionPlaying(title) {
    if (!("mediaSession" in navigator)) return;
    try {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: title || "HKI Traducción en vivo",
        artist: "Traducción en vivo",
      });
      navigator.mediaSession.playbackState = "playing";
    } catch (_) {}
  }

  async function requestWakeLock() {
    if (!active || !window.isSecureContext || !("wakeLock" in navigator)) {
      return false;
    }
    if (document.visibilityState !== "visible") return false;
    try {
      if (wakeLock && !wakeLock.released) return true;
      wakeLock = await navigator.wakeLock.request("screen");
      wakeLock.addEventListener("release", () => {
        wakeLock = null;
        if (active && mode === "wakelock" && document.visibilityState === "visible") {
          requestWakeLock();
        }
      });
      return true;
    } catch (_) {
      wakeLock = null;
      return false;
    }
  }

  function startSilentOscillator() {
    if (audioOsc) return true;
    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      audioOsc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      gain.gain.value = 0.001;
      audioOsc.connect(gain);
      gain.connect(audioCtx.destination);
      audioOsc.start();
      return true;
    } catch (_) {
      return false;
    }
  }

  async function resumeAudioCtx() {
    if (audioCtx && audioCtx.state === "suspended") {
      try {
        await audioCtx.resume();
      } catch (_) {}
    }
  }

  async function playSilentKeepalive() {
    if (ttsActive) return false;
    const audio = buildSilentAudio();
    audio.loop = true;
    audio.muted = true;
    audio.volume = 0;
    try {
      if (audio.paused) await audio.play();
      return !audio.paused;
    } catch (_) {
      return false;
    }
  }

  async function playKeepAwakeMedia() {
    buildVideo();
    const audio = buildSilentAudio();
    video.muted = true;
    audio.muted = true;
    audio.volume = 0;
    let videoOk = false;
    let audioOk = false;
    try {
      await video.play();
      videoOk = !video.paused;
    } catch (_) {}
    try {
      await audio.play();
      audioOk = !audio.paused;
    } catch (_) {}
    return videoOk || audioOk;
  }

  function startWatchdog() {
    if (watchdog) return;
    watchdog = setInterval(() => {
      if (!active) return;
      if (mode === "wakelock") {
        requestWakeLock();
        return;
      }
      if (mode === "background") {
        if (!ttsActive) {
          playSilentKeepalive();
        }
        setMediaSessionPlaying();
        return;
      }
      if (mode !== "fallback") return;
      requestWakeLock();
      if (video && video.paused) playKeepAwakeMedia();
      if (!ttsActive) playSilentKeepalive();
      resumeAudioCtx();
      setMediaSessionPlaying();
    }, 1500);
  }

  function releaseWakeLockOnly() {
    if (wakeLock) {
      try {
        wakeLock.release();
      } catch (_) {}
      wakeLock = null;
    }
  }

  function stopFallbackMedia() {
    if (video) video.pause();
    if (audioOsc) {
      try {
        audioOsc.stop();
      } catch (_) {}
      audioOsc = null;
    }
    if (audioCtx) {
      try {
        audioCtx.close();
      } catch (_) {}
      audioCtx = null;
    }
    if (enteredFullscreen && document.fullscreenElement) {
      try {
        document.exitFullscreen();
      } catch (_) {}
    }
    enteredFullscreen = false;
  }

  async function enableBackgroundAudio() {
    if (mode === "wakelock" || mode === "fallback") {
      releaseWakeLockOnly();
      stopFallbackMedia();
    }
    active = true;
    mode = "background";
    ttsActive = false;
    const media = await playSilentKeepalive();
    setMediaSessionPlaying("HKI Traducción");
    startWatchdog();
    return {
      wakeLock: false,
      fullscreen: false,
      media,
      audio: media,
      android: IS_ANDROID,
      mode,
      secure: window.isSecureContext,
    };
  }

  async function enableFallback() {
    mode = "fallback";
    const fullscreen = await tryFullscreen();
    buildVideo();
    const osc = startSilentOscillator();
    await resumeAudioCtx();
    const media = await playKeepAwakeMedia();
    setMediaSessionPlaying();
    startWatchdog();
    return {
      wakeLock: !!wakeLock,
      fullscreen,
      media,
      audio: osc,
      android: IS_ANDROID,
      mode,
      secure: window.isSecureContext,
    };
  }

  async function enable() {
    if (mode === "background") {
      if (watchdog) {
        clearInterval(watchdog);
        watchdog = null;
      }
      ttsActive = false;
      const audio = getSharedAudio();
      audio.pause();
      audio.removeAttribute("src");
    }
    active = true;
    enteredFullscreen = false;
    ttsActive = false;

    const wl = await requestWakeLock();
    if (wl && window.isSecureContext) {
      mode = "wakelock";
      startWatchdog();
      return {
        wakeLock: true,
        fullscreen: false,
        media: false,
        audio: false,
        android: IS_ANDROID,
        mode,
        secure: true,
      };
    }

    return enableFallback();
  }

  function disable() {
    active = false;
    mode = "off";
    ttsActive = false;
    if (watchdog) {
      clearInterval(watchdog);
      watchdog = null;
    }
    releaseWakeLockOnly();
    stopFallbackMedia();
    const audio = sharedAudio;
    if (audio) {
      audio.pause();
      audio.removeAttribute("src");
      audio.muted = true;
      audio.volume = 0;
      audio.loop = false;
    }
  }

  function setTtsActive(playing) {
    ttsActive = playing;
    if (!playing && active && mode === "background") {
      playSilentKeepalive();
      setMediaSessionPlaying();
    }
  }

  function resumeSilentKeepalive() {
    if (!active || mode !== "background" || ttsActive) return;
    playSilentKeepalive();
    setMediaSessionPlaying();
  }

  function handleFullscreenExit() {
    if (!active || mode !== "fallback") return;
    if (document.fullscreenElement || document.webkitFullscreenElement) return;
    if (onFullscreenExit) onFullscreenExit();
  }

  document.addEventListener("fullscreenchange", handleFullscreenExit);
  document.addEventListener("webkitfullscreenchange", handleFullscreenExit);

  document.addEventListener("visibilitychange", () => {
    if (!active) return;
    if (mode === "background") {
      if (!ttsActive) {
        playSilentKeepalive();
      } else {
        const audio = getSharedAudio();
        if (audio.paused) {
          audio.play().catch(() => {});
        }
      }
      setMediaSessionPlaying();
      return;
    }
    if (document.visibilityState !== "visible") return;
    if (mode === "wakelock") {
      requestWakeLock();
    }
    if (mode === "fallback") {
      playKeepAwakeMedia();
      resumeAudioCtx();
    }
  });
  window.addEventListener("pageshow", () => {
    if (!active) return;
    if (mode === "background") {
      if (!ttsActive) {
        playSilentKeepalive();
      } else {
        const audio = getSharedAudio();
        if (audio.paused) {
          audio.play().catch(() => {});
        }
      }
      setMediaSessionPlaying();
      return;
    }
    if (mode === "wakelock") {
      requestWakeLock();
    }
    if (mode === "fallback") {
      playKeepAwakeMedia();
      resumeAudioCtx();
    }
  });

  window.HKIKeepAwake = {
    enable,
    enableBackgroundAudio,
    disable,
    isActive: () => active,
    getMode: () => mode,
    isSecureContext: () => window.isSecureContext,
    getSharedAudio,
    setTtsActive,
    resumeSilentKeepalive,
    setMediaSession: (title) => setMediaSessionPlaying(title),
    setOnFullscreenExit: (fn) => {
      onFullscreenExit = fn;
    },
  };
})();

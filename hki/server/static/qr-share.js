/** QR share modal — used by control.html and captions.html */
(() => {
  const QR_LOCAL = "/static/qrcode.min.js";

  let modalEl = null;
  let printSheetEl = null;
  let qrLoading = null;

  function loadQrLib() {
    if (window.QRCode) return Promise.resolve();
    if (qrLoading) return qrLoading;
    qrLoading = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = QR_LOCAL;
      s.onload = () => {
        if (window.QRCode) resolve();
        else reject(new Error("QR 라이브러리 초기화 실패"));
      };
      s.onerror = () => reject(new Error("QR 라이브러리 로드 실패"));
      document.head.appendChild(s);
    });
    return qrLoading;
  }

  function injectStyles() {
    if (document.getElementById("hki-qr-styles")) return;
    const style = document.createElement("style");
    style.id = "hki-qr-styles";
    style.textContent = `
      .hki-qr-backdrop {
        position: fixed; inset: 0; background: rgba(0,0,0,0.65);
        display: flex; align-items: center; justify-content: center;
        z-index: 200; padding: 1rem;
      }
      .hki-qr-modal {
        background: #1a1a2e; border: 1px solid #2a2a3e; border-radius: 12px;
        padding: 1.25rem; width: 100%; max-width: 360px; text-align: center;
      }
      .hki-qr-modal h2 { font-size: 1.05rem; margin-bottom: 0.5rem; color: #e8e8f0; }
      .hki-qr-modal .hki-qr-hint {
        font-size: 0.8rem; color: #8888aa; margin-bottom: 1rem; line-height: 1.4;
      }
      .hki-qr-canvas-wrap {
        display: flex; justify-content: center; margin: 0.75rem 0;
        min-height: 200px; align-items: center;
      }
      .hki-qr-url {
        font-size: 0.8rem; color: #4a6cf7; word-break: break-all;
        margin: 0.5rem 0 1rem;
      }
      .hki-qr-actions { display: flex; gap: 0.5rem; }
      .hki-qr-actions button {
        flex: 1; padding: 0.65rem; font-size: 0.9rem;
        border: none; border-radius: 8px; cursor: pointer; color: #fff;
      }
      .hki-qr-actions .btn-primary { background: #4a6cf7; }
      .hki-qr-actions .btn-secondary { background: #2a2a3e; color: #e8e8f0; }
      .hki-qr-btn {
        background: none; border: 1px solid #2a2a3e; color: #8888aa;
        padding: 0.35rem 0.65rem; font-size: 0.8rem; border-radius: 6px;
        cursor: pointer;
      }
      .hki-qr-btn:hover { color: #e8e8f0; border-color: #4a6cf7; }
      .hki-qr-fab {
        position: fixed; top: 0.75rem; right: 0.75rem; z-index: 20;
        background: rgba(0,0,0,0.55); border: 1px solid #444;
        color: #ccc; padding: 0.4rem 0.7rem; border-radius: 8px;
        font-size: 0.8rem; cursor: pointer;
      }
      .hki-qr-fab:active { transform: scale(0.97); }
      #hki-qr-print-sheet {
        display: none;
      }
      @media print {
        body > *:not(#hki-qr-print-sheet) { display: none !important; }
        #hki-qr-print-sheet {
          display: flex !important;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          min-height: 100vh;
          padding: 2rem;
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          text-align: center;
        }
        #hki-qr-print-sheet h1 { font-size: 1.75rem; margin-bottom: 0.5rem; }
        #hki-qr-print-sheet p { font-size: 1rem; color: #333; margin: 0.25rem 0; }
        #hki-qr-print-sheet .print-url {
          font-size: 1.1rem; font-weight: 600; margin-top: 1rem;
          word-break: break-all;
        }
        #hki-qr-print-sheet img { width: 280px; height: 280px; margin: 1.5rem 0; }
      }
    `;
    document.head.appendChild(style);
  }

  function ensureModal() {
    injectStyles();
    if (modalEl) return modalEl;

    modalEl = document.createElement("div");
    modalEl.className = "hki-qr-backdrop hidden";
    modalEl.id = "hkiQrModal";
    modalEl.innerHTML = `
      <div class="hki-qr-modal">
        <h2>자막 페이지 공유</h2>
        <p class="hki-qr-hint">교회 Wi-Fi에 연결한 뒤<br>카메라로 QR을 스캔하세요</p>
        <div class="hki-qr-canvas-wrap" id="hkiQrCanvas"></div>
        <div class="hki-qr-url" id="hkiQrUrl"></div>
        <div class="hki-qr-actions">
          <button class="btn-primary" id="hkiQrPrintBtn">🖨 인쇄</button>
          <button class="btn-secondary" id="hkiQrCopyBtn">링크 복사</button>
          <button class="btn-secondary" id="hkiQrCloseBtn">닫기</button>
        </div>
      </div>
    `;
    document.body.appendChild(modalEl);

    printSheetEl = document.createElement("div");
    printSheetEl.id = "hki-qr-print-sheet";
    document.body.appendChild(printSheetEl);

    modalEl.onclick = (e) => {
      if (e.target === modalEl) close();
    };
    modalEl.querySelector("#hkiQrCloseBtn").onclick = close;
    modalEl.querySelector("#hkiQrPrintBtn").onclick = () => print();
    modalEl.querySelector("#hkiQrCopyBtn").onclick = copyLink;

    return modalEl;
  }

  async function renderQr(url) {
    const wrap = document.getElementById("hkiQrCanvas");
    const urlEl = document.getElementById("hkiQrUrl");
    urlEl.textContent = url;
    wrap.innerHTML = '<span style="color:#888">QR 생성 중...</span>';

    try {
      await loadQrLib();
      wrap.innerHTML = "";
      const canvas = document.createElement("canvas");
      await QRCode.toCanvas(canvas, url, {
        width: 260,
        margin: 2,
        color: { dark: "#000000", light: "#ffffff" },
      });
      wrap.appendChild(canvas);
      return canvas.toDataURL("image/png");
    } catch (err) {
      wrap.innerHTML = `<span style="color:#e74c3c;font-size:0.85rem">${err.message}</span>`;
      return null;
    }
  }

  async function open(url) {
    ensureModal();
    modalEl.classList.remove("hidden");
    const dataUrl = await renderQr(url);
    if (dataUrl && printSheetEl) {
      printSheetEl.innerHTML = `
        <h1>HKI 실시간 자막</h1>
        <p>교회 Wi-Fi 연결 후 QR 스캔</p>
        <img src="${dataUrl}" alt="QR">
        <p class="print-url">${url}</p>
        <p style="margin-top:1rem;font-size:0.9rem;color:#666">Scan para ver subtítulos en vivo</p>
      `;
    }
  }

  function close() {
    if (modalEl) modalEl.classList.add("hidden");
  }

  function print() {
    window.print();
  }

  async function copyLink() {
    const url = document.getElementById("hkiQrUrl")?.textContent;
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      const btn = document.getElementById("hkiQrCopyBtn");
      const prev = btn.textContent;
      btn.textContent = "복사됨!";
      setTimeout(() => { btn.textContent = prev; }, 1500);
    } catch {
      alert(url);
    }
  }

  window.HKIQR = { open, close, print };
  injectStyles();
})();

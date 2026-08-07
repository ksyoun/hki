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
        else reject(new Error("Error al inicializar la biblioteca QR"));
      };
      s.onerror = () => reject(new Error("Error al cargar la biblioteca QR"));
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
      .hki-qr-cert-hint {
        font-size: 0.78rem; color: #e8d48b; background: #3a2a1a;
        border: 1px solid #5a4a2a; border-radius: 8px;
        padding: 0.65rem 0.75rem; margin-bottom: 0.85rem; line-height: 1.4;
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
        #hki-qr-print-sheet .print-dual {
          display: flex; gap: 2rem; flex-wrap: wrap;
          justify-content: center; margin: 1rem 0;
        }
        #hki-qr-print-sheet .print-dual-item {
          flex: 0 1 280px; text-align: center;
        }
        #hki-qr-print-sheet .print-dual-item img {
          width: 220px; height: 220px; margin: 0.5rem 0;
        }
        #hki-qr-print-sheet .print-dual-item h2 {
          font-size: 1.1rem; margin-bottom: 0.25rem;
        }
      }
    `;
    document.head.appendChild(style);
  }

  function ensurePrintSheet() {
    injectStyles();
    if (!printSheetEl) {
      printSheetEl = document.getElementById("hki-qr-print-sheet");
    }
    if (!printSheetEl) {
      printSheetEl = document.createElement("div");
      printSheetEl.id = "hki-qr-print-sheet";
      document.body.appendChild(printSheetEl);
    }
    return printSheetEl;
  }

  function waitForPrintImages(root) {
    const imgs = root.querySelectorAll("img");
    return Promise.all(
      Array.from(imgs).map((img) => {
        if (img.complete) return Promise.resolve();
        return new Promise((resolve) => {
          img.onload = () => resolve();
          img.onerror = () => resolve();
        });
      })
    );
  }

  async function runPrint() {
    const sheet = ensurePrintSheet();
    await waitForPrintImages(sheet);
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    window.print();
  }

  function ensureModal() {
    injectStyles();
    ensurePrintSheet();
    if (modalEl) return modalEl;

    modalEl = document.createElement("div");
    modalEl.className = "hki-qr-backdrop hidden";
    modalEl.id = "hkiQrModal";
    modalEl.innerHTML = `
      <div class="hki-qr-modal">
        <h2>Compartir subtítulos</h2>
        <p class="hki-qr-hint">Conéctese al Wi-Fi de la iglesia<br>y escanee el QR con la cámara</p>
        <div class="hki-qr-cert-hint hidden" id="hkiQrCertHint">
          <strong>Primera vez en cada teléfono</strong> (al tocar Continuar en la guía HTTP):
          <br>• <strong>Android Chrome:</strong> Avanzado → Acceder (no seguro)
          <br>• <strong>iPhone Safari:</strong> Mostrar detalles → visitar este sitio web
        </div>
        <div class="hki-qr-canvas-wrap" id="hkiQrCanvas"></div>
        <div class="hki-qr-url" id="hkiQrUrl"></div>
        <div class="hki-qr-actions">
          <button class="btn-primary" id="hkiQrPrintBtn">🖨 Imprimir</button>
          <button class="btn-secondary" id="hkiQrCopyBtn">Copiar enlace</button>
          <button class="btn-secondary" id="hkiQrCloseBtn">Cerrar</button>
        </div>
      </div>
    `;
    document.body.appendChild(modalEl);

    modalEl.onclick = (e) => {
      if (e.target === modalEl) close();
    };
    modalEl.querySelector("#hkiQrCloseBtn").onclick = close;
    modalEl.querySelector("#hkiQrPrintBtn").onclick = () => runPrint();
    modalEl.querySelector("#hkiQrCopyBtn").onclick = copyLink;

    return modalEl;
  }

  async function qrToDataUrl(url, width = 260) {
    await loadQrLib();
    const canvas = document.createElement("canvas");
    await QRCode.toCanvas(canvas, url, {
      width,
      margin: 2,
      color: { dark: "#000000", light: "#ffffff" },
    });
    return canvas.toDataURL("image/png");
  }

  async function renderQr(url) {
    const wrap = document.getElementById("hkiQrCanvas");
    const urlEl = document.getElementById("hkiQrUrl");
    urlEl.textContent = url;
    wrap.innerHTML = '<span style="color:#888">Generando QR...</span>';

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

  const CERT_PRINT = `<p style="margin-top:0.75rem;font-size:0.85rem;color:#666;line-height:1.4;text-align:left">
          <strong>Al tocar Continuar (HTTPS):</strong><br>
          Android Chrome: Avanzado → Acceder al sitio (no seguro)<br>
          iPhone Safari: Mostrar detalles → visitar este sitio web / Visit Website
        </p>`;

  function applyModalMode(mode) {
    const title = modalEl.querySelector("h2");
    const hint = modalEl.querySelector(".hki-qr-hint");
    const certHint = document.getElementById("hkiQrCertHint");
    if (mode === "direct") {
      title.textContent = "QR directo (subtítulos)";
      hint.innerHTML =
        "Para quien <strong>ya aceptó</strong> el certificado<br>Abre subtítulos sin guía HTTP";
      if (certHint) certHint.classList.add("hidden");
    } else {
      title.textContent = "QR primera vez (guía)";
      hint.innerHTML =
        "Conéctese al Wi-Fi y escanee el QR<br>Guía HTTP sin advertencia → luego certificado";
      if (certHint) certHint.classList.remove("hidden");
    }
  }

  async function open(url, opts = {}) {
    const mode = opts.mode || "guide";
    ensureModal();
    applyModalMode(mode);
    modalEl.classList.remove("hidden");
    const dataUrl = await renderQr(url);
    if (dataUrl && printSheetEl) {
      ensurePrintSheet().innerHTML = `
        <h1>HKI Subtítulos en Vivo</h1>
        <p>${mode === "direct" ? "QR directo — ya aceptó certificado" : "QR primera vez — guía de conexión"}</p>
        <img src="${dataUrl}" alt="QR">
        <p class="print-url">${url}</p>
        ${mode === "guide" ? CERT_PRINT : ""}
        <p style="margin-top:1rem;font-size:0.9rem;color:#666">Escanee para ver subtítulos en vivo</p>
      `;
    }
  }

  async function printBoth(guideUrl, directUrl) {
    const sheet = ensurePrintSheet();
    try {
      const guideImg = await qrToDataUrl(guideUrl, 220);
      const directImg = await qrToDataUrl(directUrl, 220);
      sheet.innerHTML = `
        <h1>HKI Subtítulos en Vivo</h1>
        <p>Conéctese al Wi-Fi de la iglesia</p>
        <div class="print-dual">
          <div class="print-dual-item">
            <h2>Primera vez</h2>
            <p style="font-size:0.85rem;color:#666">Guía + certificado</p>
            <img src="${guideImg}" alt="QR guía">
            <p class="print-url" style="font-size:0.9rem">${guideUrl}</p>
          </div>
          <div class="print-dual-item">
            <h2>Directo</h2>
            <p style="font-size:0.85rem;color:#666">Ya aceptó certificado</p>
            <img src="${directImg}" alt="QR directo">
            <p class="print-url" style="font-size:0.9rem">${directUrl}</p>
          </div>
        </div>
        ${CERT_PRINT}
      `;
      await runPrint();
    } catch (err) {
      alert(err.message || "Error al generar QR");
    }
  }

  function close() {
    if (modalEl) modalEl.classList.add("hidden");
  }

  function print() {
    runPrint();
  }

  async function copyLink() {
    const url = document.getElementById("hkiQrUrl")?.textContent;
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      const btn = document.getElementById("hkiQrCopyBtn");
      const prev = btn.textContent;
      btn.textContent = "¡Copiado!";
      setTimeout(() => { btn.textContent = prev; }, 1500);
    } catch {
      alert(url);
    }
  }

  window.HKIQR = { open, close, print, printBoth };
  injectStyles();
})();

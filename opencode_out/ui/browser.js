/**
 * browser.js – PiP browser panel UI logic
 * =========================================
 * Handles the floating overlay, drag, close, URL updates,
 * and forwarding page-load events back to Flask.
 *
 * Expects these elements to exist in the DOM (added to index.html):
 *   #browser-panel       – outer container (hidden until opened)
 *   #browser-handle      – drag strip across the top
 *   #browser-url-bar     – <span> showing current URL
 *   #browser-close-btn   – close button
 */

(function () {
  'use strict';

  // ── Element refs ──────────────────────────────────────────────────────
  const panel    = document.getElementById('browser-panel');
  const handle   = document.getElementById('browser-handle');
  const urlBar   = document.getElementById('browser-url-bar');
  const closeBtn = document.getElementById('browser-close-btn');

  if (!panel) return; // not in DOM yet – bail cleanly

  // ── Drag state ────────────────────────────────────────────────────────
  let dragging  = false;
  let startTX   = 0, startTY  = 0;  // touch start coords
  let startLeft = 0, startTop = 0;  // panel position at drag start

  // ── Helpers ───────────────────────────────────────────────────────────

  /** Read current pixel position of the panel. */
  function panelRect () {
    return panel.getBoundingClientRect();
  }

  /** Clamp panel inside the viewport after any move. */
  function clamp (x, y) {
    const pw = panel.offsetWidth;
    const ph = panel.offsetHeight;
    return {
      x: Math.max(0, Math.min(x, window.innerWidth  - pw)),
      y: Math.max(0, Math.min(y, window.innerHeight - ph)),
    };
  }

  /** Push the panel's pixel position down to Android so the native
   *  WebView stays aligned with the CSS overlay chrome. */
  function syncNativePosition () {
    if (typeof Android === 'undefined') return;
    const r   = panelRect();
    const dpr = window.devicePixelRatio || 1;
    Android.browserMove(Math.round(r.left * dpr), Math.round(r.top * dpr));
  }

  /** Sync panel dimensions (called on resize handle release). */
  function syncNativeSize () {
    if (typeof Android === 'undefined') return;
    const dpr = window.devicePixelRatio || 1;
    Android.browserResize(
      Math.round(panel.offsetWidth  * dpr),
      Math.round(panel.offsetHeight * dpr)
    );
  }

  // ── Open / close (called from app.js or the AI tool result) ──────────

  /**
   * Show the panel and tell Android to open a URL.
   * @param {string} url
   */
  window.browserPanelOpen = function (url) {
    panel.classList.add('open');
    if (urlBar) urlBar.textContent = url || '';
    // Small delay so the CSS transition fires before we measure
    requestAnimationFrame(syncNativePosition);
  };

  /** Hide the panel. Does NOT close the Android WebView – use
   *  browserClose() for that (or the close button below). */
  window.browserPanelClose = function () {
    panel.classList.remove('open');
  };

  /** Update the URL bar text (called after navigation). */
  window.browserPanelSetUrl = function (url) {
    if (urlBar) urlBar.textContent = url || '';
  };

  // ── Drag logic ────────────────────────────────────────────────────────

  if (handle) {
    handle.addEventListener('touchstart', function (e) {
      if (e.touches.length !== 1) return;
      dragging  = true;
      const t   = e.touches[0];
      startTX   = t.clientX;
      startTY   = t.clientY;
      const r   = panelRect();
      startLeft = r.left;
      startTop  = r.top;
      e.preventDefault();
    }, { passive: false });

    document.addEventListener('touchmove', function (e) {
      if (!dragging || e.touches.length !== 1) return;
      const t  = e.touches[0];
      const dx = t.clientX - startTX;
      const dy = t.clientY - startTY;
      const p  = clamp(startLeft + dx, startTop + dy);
      panel.style.left   = p.x + 'px';
      panel.style.top    = p.y + 'px';
      panel.style.right  = 'auto';
      panel.style.bottom = 'auto';
      e.preventDefault();
    }, { passive: false });

    document.addEventListener('touchend', function () {
      if (!dragging) return;
      dragging = false;
      syncNativePosition();
    });

    // Mouse drag (desktop / emulator)
    handle.addEventListener('mousedown', function (e) {
      dragging  = true;
      startTX   = e.clientX;
      startTY   = e.clientY;
      const r   = panelRect();
      startLeft = r.left;
      startTop  = r.top;
      e.preventDefault();
    });

    document.addEventListener('mousemove', function (e) {
      if (!dragging) return;
      const p = clamp(startLeft + e.clientX - startTX, startTop + e.clientY - startTY);
      panel.style.left   = p.x + 'px';
      panel.style.top    = p.y + 'px';
      panel.style.right  = 'auto';
      panel.style.bottom = 'auto';
    });

    document.addEventListener('mouseup', function () {
      if (!dragging) return;
      dragging = false;
      syncNativePosition();
    });
  }

  // ── Resize handle ─────────────────────────────────────────────────────
  // A small grip in the bottom-right corner lets the user resize the panel.

  const resizeHandle = document.getElementById('browser-resize-handle');
  if (resizeHandle) {
    let resizing = false, rStartX = 0, rStartY = 0, rStartW = 0, rStartH = 0;
    const MIN_W = 200, MIN_H = 160;

    resizeHandle.addEventListener('touchstart', function (e) {
      if (e.touches.length !== 1) return;
      resizing = true;
      rStartX  = e.touches[0].clientX;
      rStartY  = e.touches[0].clientY;
      rStartW  = panel.offsetWidth;
      rStartH  = panel.offsetHeight;
      e.preventDefault();
    }, { passive: false });

    document.addEventListener('touchmove', function (e) {
      if (!resizing || e.touches.length !== 1) return;
      const dx = e.touches[0].clientX - rStartX;
      const dy = e.touches[0].clientY - rStartY;
      panel.style.width  = Math.max(MIN_W, rStartW + dx) + 'px';
      panel.style.height = Math.max(MIN_H, rStartH + dy) + 'px';
      e.preventDefault();
    }, { passive: false });

    document.addEventListener('touchend', function () {
      if (!resizing) return;
      resizing = false;
      syncNativeSize();
    });
  }

  // ── Close button ──────────────────────────────────────────────────────

  if (closeBtn) {
    closeBtn.addEventListener('click', function () {
      // Tell Flask so the AI state updates
      fetch('/browser_event', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'user_close', ts: Date.now() }),
      }).catch(function () {});

      panel.classList.remove('open');

      if (typeof Android !== 'undefined') {
        Android.browserClose();
      }
    });
  }

  // ── Poll browser_state for URL / open changes ─────────────────────────
  // Keeps the URL bar in sync when the AI navigates programmatically.

  setInterval(function () {
    if (!panel.classList.contains('open')) return;
    fetch('/browser_state')
      .then(function (r) { return r.json(); })
      .then(function (s) {
        if (!s.open && panel.classList.contains('open')) {
          panel.classList.remove('open');
        }
        if (s.url && urlBar && urlBar.textContent !== s.url) {
          urlBar.textContent = s.url;
        }
      })
      .catch(function () {});
  }, 1500);

}());

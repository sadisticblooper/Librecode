/**
 * oc_webview.js  —  $oc helper library, injected into the Android WebView.
 *
 * Exposes window.$oc with:
 *   Selectors:  waitFor, waitWhile, waitNew, waitStable
 *   Actions:    click, type, clear, pressKey, select, hover, scroll, navigate
 *   Read:       extractLast, extractFirst, extractUrl, extractTitle
 *   Stream:     _getText(css)  — Python calls this to poll live text
 *   Phase mgr:  _runPhase(phase, input), _pollDone()  — Python drives phases
 *   Util:       sleep, eval (raw JS)
 *
 * Selector prefixes (same as the old DSL):
 *   aria:Label        →  [aria-label*="Label"]
 *   placeholder:Text  →  :is([placeholder*="Text"],[data-placeholder*="Text"],[aria-placeholder*="Text"])
 *   role:Value        →  [role="Value"]
 *   id:myId           →  #myId
 *   css:.class        →  .class  (raw passthrough)
 *   text:Stop         →  BFS innerText match
 *   anything else     →  treated as raw CSS
 */
(function () {
  'use strict';

  // ── Selector resolution ────────────────────────────────────────────────────

  function resolveCss(sel) {
    sel = (sel || '').trim();
    if (sel.startsWith('aria:'))        return '[aria-label*="' + sel.slice(5).trim() + '"]';
    if (sel.startsWith('placeholder:')) {
      const n = sel.slice(12).trim();
      return ':is([placeholder*="' + n + '"],[data-placeholder*="' + n + '"],[aria-placeholder*="' + n + '"])';
    }
    if (sel.startsWith('role:'))        return '[role="' + sel.slice(5).trim() + '"]';
    if (sel.startsWith('id:'))          return '#' + sel.slice(3).trim();
    if (sel.startsWith('css:'))         return sel.slice(4).trim();
    if (sel.startsWith('text:'))        return null;  // handled by text search
    return sel;  // raw CSS
  }

  function findOne(sel) {
    sel = (sel || '').trim();
    if (sel.startsWith('text:')) {
      const needle = sel.slice(5).trim().toLowerCase();
      function walkText(root) {
        const tw = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
        let n;
        while ((n = tw.nextNode())) {
          if (n.shadowRoot) { const r = walkText(n.shadowRoot); if (r) return r; }
          if ((n.innerText || '').toLowerCase().includes(needle)) return n;
        }
        return null;
      }
      return walkText(document.body || document.documentElement);
    }
    const css = resolveCss(sel);
    if (!css) return null;
    function searchOne(root) {
      const el = root.querySelector(css);
      if (el) return el;
      const all = root.querySelectorAll('*');
      for (let i = 0; i < all.length; i++) {
        if (all[i].shadowRoot) { const r = searchOne(all[i].shadowRoot); if (r) return r; }
      }
      return null;
    }
    return searchOne(document.body || document.documentElement);
  }

  function findAll(sel) {
    sel = (sel || '').trim();
    if (sel.startsWith('text:')) {
      const needle = sel.slice(5).trim().toLowerCase();
      const results = [];
      function walkAll(root) {
        const tw = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
        let n;
        while ((n = tw.nextNode())) {
          if (n.shadowRoot) walkAll(n.shadowRoot);
          if ((n.innerText || '').toLowerCase().includes(needle)) results.push(n);
        }
      }
      walkAll(document.body || document.documentElement);
      return results;
    }
    const css = resolveCss(sel);
    if (!css) return [];
    const results = [];
    function searchAll(root) {
      root.querySelectorAll(css).forEach(e => results.push(e));
      root.querySelectorAll('*').forEach(e => { if (e.shadowRoot) searchAll(e.shadowRoot); });
    }
    searchAll(document.body || document.documentElement);
    return results;
  }

  function isVisible(el) {
    if (!el) return false;
    const s = window.getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
  }

  // ── Phase state machine ────────────────────────────────────────────────────
  //  Python polls _pollDone() after calling _runPhase().

  const _phase = { running: false, done: false, result: null, error: null };

  // ── Main $oc object ────────────────────────────────────────────────────────

  const $oc = window.$oc = {

    // -- Waits -----------------------------------------------------------------

    sleep(ms) {
      return new Promise(r => setTimeout(r, ms));
    },

    async waitFor(sel, timeoutMs = 20000) {
      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline) {
        const el = findOne(sel);
        if (el && isVisible(el)) return el;
        await this.sleep(150);
      }
      throw new Error('$oc.waitFor timeout: ' + sel);
    },

    async waitWhile(sel, timeoutMs = 120000) {
      // First wait for it to appear (up to 8s), then wait for it to disappear.
      const appearDeadline = Date.now() + 8000;
      while (Date.now() < appearDeadline) {
        const el = findOne(sel);
        if (el && isVisible(el)) break;
        await this.sleep(150);
      }
      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline) {
        const el = findOne(sel);
        if (!el || !isVisible(el)) return;
        await this.sleep(150);
      }
      throw new Error('$oc.waitWhile timeout: ' + sel);
    },

    async waitNew(sel, timeoutMs = 120000) {
      // Resolve selector to CSS for querySelectorAll count check.
      const css = resolveCss(sel) || sel;
      const before = document.querySelectorAll(css).length;
      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline) {
        if (document.querySelectorAll(css).length > before) return;
        await this.sleep(150);
      }
      throw new Error('$oc.waitNew timeout: ' + sel);
    },

    async waitStable(sel, stableMs = 1500, timeoutMs = 120000) {
      const deadline = Date.now() + timeoutMs;
      let lastText = null;
      let stableSince = null;
      while (Date.now() < deadline) {
        const items = findAll(sel);
        const text = items.length ? (items[items.length - 1].innerText || '') : null;
        if (text !== lastText) {
          lastText = text;
          stableSince = Date.now();
        } else if (stableSince !== null && (Date.now() - stableSince) >= stableMs) {
          return lastText;
        }
        await this.sleep(150);
      }
      return lastText;
    },

    // -- Actions ---------------------------------------------------------------

    click(sel) {
      let el = findOne(sel);
      if (!el) throw new Error('$oc.click: not found: ' + sel);
      // Walk up to nearest clickable ancestor.
      let t = el;
      while (t && t !== document.body &&
             t.tagName !== 'BUTTON' && t.tagName !== 'A' &&
             t.getAttribute('role') !== 'button') {
        t = t.parentElement;
      }
      if (t && t !== document.body) el = t;
      el.scrollIntoView({ block: 'center' });
      el.focus();
      ['mousedown', 'mouseup', 'click'].forEach(evt =>
        el.dispatchEvent(new MouseEvent(evt, { bubbles: true, cancelable: true }))
      );
      if (el.tagName === 'BUTTON' || el.getAttribute('role') === 'button') el.click();
    },

    type(sel, value) {
      let el = findOne(sel);
      if (!el) throw new Error('$oc.type: not found: ' + sel);
      // Walk up to nearest typeable ancestor.
      let t = el;
      while (t && t !== document.body &&
             t.tagName !== 'INPUT' && t.tagName !== 'TEXTAREA' &&
             t.contentEditable !== 'true') {
        t = t.parentElement;
      }
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.contentEditable === 'true')) el = t;
      el.focus();
      if (el.contentEditable === 'true') {
        el.innerHTML = '';
        const ok = document.execCommand('insertText', false, value);
        if (!ok) el.textContent = value;
        el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value }));
      } else {
        const proto = el.tagName === 'TEXTAREA'
          ? window.HTMLTextAreaElement.prototype
          : window.HTMLInputElement.prototype;
        const niv = Object.getOwnPropertyDescriptor(proto, 'value');
        if (niv) niv.set.call(el, value); else el.value = value;
        el.dispatchEvent(new Event('input',  { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      }
    },

    clear(sel) {
      const el = findOne(sel);
      if (!el) throw new Error('$oc.clear: not found: ' + sel);
      el.focus();
      const proto = el.tagName === 'TEXTAREA'
        ? window.HTMLTextAreaElement.prototype
        : window.HTMLInputElement.prototype;
      const niv = Object.getOwnPropertyDescriptor(proto, 'value');
      if (niv) niv.set.call(el, ''); else el.value = '';
      el.dispatchEvent(new Event('input',  { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    },

    pressKey(sel, key) {
      const el = findOne(sel);
      if (!el) throw new Error('$oc.pressKey: not found: ' + sel);
      el.focus();
      const codeMap = {
        Enter: 'Enter', Tab: 'Tab', Escape: 'Escape', Backspace: 'Backspace',
        Delete: 'Delete', ArrowUp: 'ArrowUp', ArrowDown: 'ArrowDown',
        ArrowLeft: 'ArrowLeft', ArrowRight: 'ArrowRight', ' ': 'Space',
      };
      const code = codeMap[key] || key;
      ['keydown', 'keypress', 'keyup'].forEach(evt =>
        el.dispatchEvent(new KeyboardEvent(evt, { key, code, bubbles: true, cancelable: true }))
      );
    },

    hover(sel) {
      const el = findOne(sel);
      if (!el) return;  // non-fatal
      el.scrollIntoView({ block: 'center' });
      ['mouseenter', 'mouseover'].forEach(evt =>
        el.dispatchEvent(new MouseEvent(evt, { bubbles: true, cancelable: true }))
      );
    },

    select(sel, value) {
      const el = findOne(sel);
      if (!el) throw new Error('$oc.select: not found: ' + sel);
      const match = Array.from(el.options).find(o => o.value === value || o.text === value);
      if (!match) throw new Error('$oc.select: option not found: ' + value);
      el.value = match.value;
      el.dispatchEvent(new Event('change', { bubbles: true }));
    },

    scroll(direction, amount = 300) {
      if (direction === 'top')    window.scrollTo(0, 0);
      else if (direction === 'bottom') window.scrollTo(0, document.body.scrollHeight);
      else if (direction === 'up')     window.scrollBy(0, -amount);
      else                             window.scrollBy(0, amount);
    },

    scrollTo(sel) {
      const el = findOne(sel);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    },

    navigate(url) { window.location.href = url; },

    // -- Extraction ------------------------------------------------------------

    extractLast(sel) {
      const items = findAll(sel);
      if (!items.length) return null;
      return items[items.length - 1].innerText || null;
    },

    extractFirst(sel) {
      const items = findAll(sel);
      if (!items.length) return null;
      return items[0].innerText || null;
    },

    extractUrl()   { return window.location.href; },
    extractTitle() { return document.title; },

    attr(sel, attrName) {
      const el = findOne(sel);
      return el ? el.getAttribute(attrName) : null;
    },

    isVisible(sel) {
      return isVisible(findOne(sel));
    },

    // -- Stream poll (Python calls this directly while response is growing) ----

    _getText(css) {
      const all = document.querySelectorAll(css);
      if (!all.length) return null;
      return all[all.length - 1].innerText || null;
    },

    // -- Phase runner ----------------------------------------------------------
    //  Python calls: $oc._runPhase("load"|"send"|"read", inputOrNull)
    //  Then polls:   $oc._pollDone()  →  JSON string

    async _runPhase(phase, input) {
      _phase.running = true;
      _phase.done    = false;
      _phase.result  = null;
      _phase.error   = null;
      try {
        let result;
        if      (phase === 'load') result = await window.onLoad();
        else if (phase === 'send') result = await window.onSend(input);
        else if (phase === 'read') result = await window.onRead();
        _phase.result  = (result !== undefined && result !== null) ? String(result) : null;
        _phase.error   = null;
      } catch (e) {
        _phase.result = null;
        _phase.error  = e && e.message ? e.message : String(e);
      } finally {
        _phase.running = false;
        _phase.done    = true;
      }
    },

    _pollDone() {
      return JSON.stringify({
        running: _phase.running,
        done:    _phase.done,
        result:  _phase.result,
        error:   _phase.error,
      });
    },
  };

  // Mark library as loaded so Python can verify injection succeeded.
  window.__ocLoaded = true;

})();

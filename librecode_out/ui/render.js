// ── Rendering helpers ─────────────────────────────────────────────────

import { chatEl } from './state.js';

// ── HTML utils ─────────────────────────────────────────────────────────

export function escHtml(s) {
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

function buildCodeBlock(lang, code) {
    return '<div class="code-block">' +
        '<div class="code-block-header">' +
        '<span class="code-lang">' + escHtml(lang || 'code') + '</span>' +
        '<button class="copy-btn" onclick="copyCode(this)">copy</button>' +
        '</div>' +
        '<pre><code class="' + escHtml(lang) + '">' + escHtml(code.trimEnd()) + '</code></pre>' +
        '</div>';
}

export function highlightCodeBlocks(container) {
    if (typeof hljs === 'undefined') return;
    const blocks = container.querySelectorAll('pre code');
    blocks.forEach(block => {
        if (block.dataset.highlighted) return;
        hljs.highlightElement(block);
        block.dataset.highlighted = 'true';
    });
}

export function parseMarkdown(text) {
    if (!text) return '';
    const segments = [];
    const fence    = /```(\w*)\n?([\s\S]*?)```/g;
    let last = 0, m;
    while ((m = fence.exec(text)) !== null) {
        if (m.index > last) segments.push({ type: 'text', content: text.slice(last, m.index) });
        segments.push({ type: 'code', lang: m[1] || '', content: m[2] });
        last = m.index + m[0].length;
    }
    if (last < text.length) segments.push({ type: 'text', content: text.slice(last) });
    return segments.map(seg => {
        if (seg.type === 'code') return buildCodeBlock(seg.lang, seg.content);
        let s = escHtml(seg.content);
        s = s.replace(/`([^`\n]+)`/g,           '<code>$1</code>');
        s = s.replace(/\*\*([^*\n]+)\*\*/g,     '<strong>$1</strong>');
        s = s.replace(/__([^_\n]+)__/g,          '<strong>$1</strong>');
        s = s.replace(/\*([^*\n]+)\*/g,          '<em>$1</em>');
        s = s.replace(/(^|[\s>])_([^_\n]+)_(?=[\s<,\.!?;:]|$)/gm, '$1<em>$2</em>');
        s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
        s = s.replace(/^### (.+)$/gm, '<h3>$1</h3>');
        s = s.replace(/^## (.+)$/gm,  '<h2>$1</h2>');
        s = s.replace(/^# (.+)$/gm,   '<h1>$1</h1>');
        s = s.replace(/^[\*\-] (.+)$/gm, '<li>$1</li>');
        s = s.replace(/(<li>.*<\/li>\n?)+/g, mm => '<ul>' + mm + '</ul>');
        s = s.replace(/^---$/gm, '<hr>');
        return s.split(/\n\n+/).map(b => {
            b = b.trim();
            if (!b) return '';
            if (/^<(div|ul|ol|h[1-6]|hr|blockquote)/.test(b)) return b;
            return b.replace(/\n/g, '<br>');
        }).join('<br>');
    }).join('');
}

window.copyCode = function(btn) {
    const code = btn.closest('.code-block').querySelector('code');
    navigator.clipboard.writeText(code.textContent).then(() => {
        btn.textContent = 'copied!';
        btn.classList.add('copied');
        setTimeout(() => { btn.textContent = 'copy'; btn.classList.remove('copied'); }, 1800);
    });
};

// ── Scroll ─────────────────────────────────────────────────────────────

function isNearBottom() {
    const threshold = 180;
    return chatEl.scrollHeight - chatEl.scrollTop - chatEl.clientHeight < threshold;
}

export function scrollBottom() {
    if (isNearBottom()) {
        chatEl.scrollTop = chatEl.scrollHeight;
    }
}

export function forceScrollBottom() {
    chatEl.scrollTop = chatEl.scrollHeight;
}

// ── Turn wrapper — one per assistant reply ─────────────────────────────

export function createTurnWrapper() {
    const div = document.createElement('div');
    div.className = 'msg assistant streaming';
    const label = document.createElement('div');
    label.className = 'agent-label';
    label.textContent = 'Agent';
    div.appendChild(label);
    chatEl.appendChild(div);
    scrollBottom();
    return div;
}

export function sealTurn(div) {
    div.classList.remove('streaming');
}

// ── Message DOM builders ───────────────────────────────────────────────

export function addUserMsgStatic(content) {
    const div   = document.createElement('div');
    div.className = 'msg user';
    const inner = document.createElement('div');
    inner.className = 'user-inner';
    inner.textContent = content;
    div.appendChild(inner);
    chatEl.appendChild(div);
}

export function addUserMsg(content) {
    addUserMsgStatic(content);
    forceScrollBottom();
}

export function addAssistantMsgStatic(content, reasoning) {
    const div    = document.createElement('div');
    div.className = 'msg assistant';
    const label = document.createElement('div');
    label.className = 'agent-label';
    label.textContent = 'Agent';
    div.appendChild(label);
    if (reasoning) {
        const wrapper = document.createElement('div');
        wrapper.className = 'thinking-wrapper';
        wrapper.innerHTML = `
            <div class="thinking-header">
                <span class="thinking-header-icon">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>
                    </svg>
                </span>
                <span class="thinking-header-label">Thought</span>
                <svg class="thinking-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                    <polyline points="6 9 12 15 18 9"/>
                </svg>
            </div>
            <div class="thinking-collapse">
                <div class="thinking-body"></div>
            </div>`;
        wrapper.querySelector('.thinking-body').textContent = reasoning;
        wrapper.querySelector('.thinking-header').addEventListener('click', () => {
            wrapper.classList.toggle('open');
        });
        div.appendChild(wrapper);
    }
    if (content) {
        const contentDiv = document.createElement('div');
        contentDiv.innerHTML = parseMarkdown(content);
        div.appendChild(contentDiv);
    }
    chatEl.appendChild(div);
    highlightCodeBlocks(div);
}

export function createAssistantShell(container) {
    const target = container || chatEl;
    const div = document.createElement('div');
    div.className = 'assistant-text-block';
    div.innerHTML = '<span class="cursor"></span>';
    target.appendChild(div);
    scrollBottom();
    return div;
}

export function sealAssistant(div, text) {
    div.classList.remove('streaming');
    div.removeAttribute('data-live');
    div.innerHTML = parseMarkdown(text);
    highlightCodeBlocks(div);
}

// ── Activity bar (unified thinking + tools, Claude-style) ─────────────
//
//  createActivityBar(container)  → bar object
//  bar.addThought(text)          → append/update thinking text
//  bar.addTool(name, args)       → add tool step, returns step obj
//  bar.setToolResult(step, res)  → attach result to step
//  bar.seal()                    → freeze, show "N steps"
//  addActivityBarStatic(steps)   → replay from history

function _actLabel(name, args) {
    if (name === '__thought__')  return 'Thinking\u2026';
    if (name === 'web_search')   return 'Searching \u201c' + (args.query   || '') + '\u201d';
    if (name === 'glob')         return 'Finding '   + (args.pattern || '');
    if (name === 'grep')         return 'Searching ' + (args.pattern || '');
    if (name === 'read')         return 'Reading '   + (args.filePath || '');
    if (name === 'write')        return 'Writing '   + (args.filePath || '');
    if (name === 'edit')         return 'Editing '   + (args.filePath || '');
    if (name === 'diff')         return 'Diffing '   + (args.fileA    || '');
    if (name === 'shell')        return 'Running '   + (args.command  || '');
    if (name === 'web_fetch')    return 'Fetching '  + (args.url      || '');
    if (name === 'spawn_agent')  return 'Spawning agent ' + (args.agent_id || '');
    return 'Running ' + name;
}

function _actStepIcon(name) {
    if (name === '__thought__')
        return '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="flex-shrink:0;opacity:.6"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>';
    if (name === 'web_search' || name === 'grep')
        return '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="flex-shrink:0;opacity:.6"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>';
    if (name === 'read')
        return '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="flex-shrink:0;opacity:.6"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';
    if (name === 'write' || name === 'edit')
        return '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="flex-shrink:0;opacity:.6"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>';
    if (name === 'shell')
        return '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="flex-shrink:0;opacity:.6"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>';
    return '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="flex-shrink:0;opacity:.6"><circle cx="12" cy="12" r="10"/></svg>';
}

function _actInputSummary(name, args) {
    if (name === 'web_search')  return args.query    || '';
    if (name === 'glob')        return (args.pattern || '') + (args.path ? '\nin: ' + args.path : '');
    if (name === 'grep')        return (args.pattern || '') + (args.path ? '\nin: ' + args.path : '');
    if (name === 'read')        return args.filePath || '';
    if (name === 'write')       return (args.filePath || '') + '\n\n' + (args.content || '');
    if (name === 'edit')        return (args.filePath || '') + '\n\n--- old ---\n' + (args.oldString || '') + '\n\n--- new ---\n' + (args.newString || '');
    if (name === 'shell')       return (args.command || '') + (args.cwd ? '\ncwd: ' + args.cwd : '');
    if (name === 'web_fetch')   return args.url || '';
    if (name === 'diff')        return (args.fileA || '') + '\n' + (args.fileB || '');
    return JSON.stringify(args, null, 2);
}

function _renderDiff(diffText) {
    if (!diffText) return '<span class="diff-empty">no changes</span>';
    return diffText.split('\n').map(line => {
        if (line.startsWith('+++') || line.startsWith('---')) return '<div class="diff-line diff-meta">' + escHtml(line) + '</div>';
        if (line.startsWith('@@'))  return '<div class="diff-line diff-hunk">' + escHtml(line) + '</div>';
        if (line.startsWith('+'))   return '<div class="diff-line diff-add">'  + escHtml(line) + '</div>';
        if (line.startsWith('-'))   return '<div class="diff-line diff-del">'  + escHtml(line) + '</div>';
        return '<div class="diff-line diff-ctx">' + escHtml(line) + '</div>';
    }).join('');
}

// ── Steps bottom sheet (singleton) ────────────────────────────────────

let _sheetEl = null;

function _ensureSheet() {
    if (_sheetEl) return;
    const overlay = document.createElement('div');
    overlay.className = 'steps-sheet-overlay';
    overlay.innerHTML =
        '<div class="steps-sheet">' +
            '<div class="steps-sheet-drag"></div>' +
            '<div class="steps-sheet-header">' +
                '<span class="steps-sheet-title"></span>' +
                '<button class="steps-sheet-close">' +
                    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">' +
                        '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>' +
                    '</svg>' +
                '</button>' +
            '</div>' +
            '<div class="steps-sheet-body"></div>' +
        '</div>';
    overlay.addEventListener('click', e => { if (e.target === overlay) _closeSheet(); });
    overlay.querySelector('.steps-sheet-close').addEventListener('click', _closeSheet);

    // ── Drag-to-resize / drag-to-dismiss ─────────────────────────────────
    const sheet     = overlay.querySelector('.steps-sheet');
    const dragPill  = overlay.querySelector('.steps-sheet-drag');
    const header    = overlay.querySelector('.steps-sheet-header');
    const closeBtn  = overlay.querySelector('.steps-sheet-close');
    let _dragStartY = 0, _dragStartH = 0, _dragging = false;
    let _lastY = 0, _lastT = 0, _velocity = 0;

    const SNAP_MIN     = () => window.innerHeight * 0.25;
    const SNAP_DEFAULT = () => window.innerHeight * 0.65;
    const SNAP_MAX     = () => window.innerHeight * 0.90;

    function _snapTo(targetH) {
        sheet.style.transition = 'height .35s cubic-bezier(.25,.46,.45,.94), max-height .35s cubic-bezier(.25,.46,.45,.94)';
        sheet.style.height     = targetH + 'px';
        sheet.style.maxHeight  = targetH + 'px';
    }

    function _onDragStart(clientY) {
        _dragging    = true;
        _dragStartY  = clientY;
        _dragStartH  = sheet.getBoundingClientRect().height;
        _lastY       = clientY;
        _lastT       = Date.now();
        _velocity    = 0;
        sheet.style.transition = 'none';
        document.body.style.userSelect = 'none';
    }
    function _onDragMove(clientY) {
        if (!_dragging) return;
        const now   = Date.now();
        const dt    = now - _lastT || 1;
        _velocity   = (_lastY - clientY) / dt;
        _lastY      = clientY;
        _lastT      = now;
        const delta = _dragStartY - clientY;
        const newH  = Math.min(Math.max(_dragStartH + delta, 40), SNAP_MAX());
        sheet.style.height    = newH + 'px';
        sheet.style.maxHeight = newH + 'px';
    }
    function _onDragEnd() {
        if (!_dragging) return;
        _dragging = false;
        document.body.style.userSelect = '';
        const curH = sheet.getBoundingClientRect().height;

        // Only close if user physically dragged it all the way to the bottom
        if (curH < 60) { _closeSheet(); return; }

        // Fast flick up → max; no momentum-close
        if (_velocity > 0.5) { _snapTo(SNAP_MAX()); return; }

        // Snap to nearest of min / default / max
        const positions = [SNAP_MIN(), SNAP_DEFAULT(), SNAP_MAX()];
        const nearest   = positions.reduce((a, b) => Math.abs(b - curH) < Math.abs(a - curH) ? b : a);
        _snapTo(nearest);
    }

    // Attach to full header area (pill + title row), but not the close button
    function _headerDragStart(e) {
        if (e.target === closeBtn || closeBtn.contains(e.target)) return;
        e.preventDefault();
        _onDragStart(e.touches ? e.touches[0].clientY : e.clientY);
    }

    dragPill.addEventListener('mousedown',   _headerDragStart);
    header.addEventListener('mousedown',     _headerDragStart);
    window.addEventListener('mousemove',     e => _onDragMove(e.clientY));
    window.addEventListener('mouseup',       _onDragEnd);

    dragPill.addEventListener('touchstart',  _headerDragStart, { passive: false });
    header.addEventListener('touchstart',    _headerDragStart, { passive: false });
    window.addEventListener('touchmove',     e => { if (_dragging) { e.preventDefault(); _onDragMove(e.touches[0].clientY); } }, { passive: false });
    window.addEventListener('touchend',      _onDragEnd);

    // Expose drag start so detail screen headers can forward to it
    overlay._sheetDragStart = _headerDragStart;

    document.body.appendChild(overlay);
    _sheetEl = overlay;
}

function _renderStepDetail(step, el) {
    if (el._rendered) return;
    el._rendered = true;
    if (step.name === '__thought__') {
        const pre = document.createElement('pre');
        pre.className = 'act-detail-pre';
        el.appendChild(pre);
        const lines = (step.thoughtText || step.text || '').split('\n');
        let i = 0;
        const batch = Math.max(1, Math.ceil(lines.length / 80));
        function tick() {
            for (let b = 0; b < batch && i < lines.length; b++, i++) {
                pre.textContent += (i > 0 ? '\n' : '') + lines[i];
            }
            // auto-scroll detail area as lines come in
            el.scrollTop = el.scrollHeight;
            if (i < lines.length) requestAnimationFrame(tick);
        }
        requestAnimationFrame(tick);
    } else if (step.name === 'edit' || step.name === 'diff') {
        const result = step.result || '';
        const sepIdx = result.indexOf('\n\n<<<DIFF>>>\n');
        const status  = sepIdx !== -1 ? result.slice(0, sepIdx) : result;
        const rawDiff = sepIdx !== -1 ? result.slice(sepIdx + '\n\n<<<DIFF>>>\n'.length) : '';
        el.innerHTML =
            '<div class="act-detail-section"><span class="act-detail-label">file</span><pre class="act-detail-pre">' + escHtml(step.args.filePath || step.args.fileA || '') + '</pre></div>' +
            (status ? '<div class="act-detail-section"><span class="act-detail-label">status</span><pre class="act-detail-pre">' + escHtml(status) + '</pre></div>' : '') +
            '<div class="act-detail-section act-detail-diff"><div class="diff-view">' + _renderDiff(rawDiff) + '</div></div>';
    } else {
        const input = _actInputSummary(step.name, step.args || {});
        el.innerHTML =
            '<div class="act-detail-section"><span class="act-detail-label">input</span><pre class="act-detail-pre">' + escHtml(input) + '</pre></div>' +
            (step.result != null ? '<div class="act-detail-section"><span class="act-detail-label">output</span><pre class="act-detail-pre">' + escHtml(String(step.result)) + '</pre></div>' : '');
    }
}

function _openSheet(steps, title) {
    _ensureSheet();
    _sheetEl.querySelector('.steps-sheet-title').textContent = title;
    const body = _sheetEl.querySelector('.steps-sheet-body');
    body.innerHTML = '';
    body.scrollTop = 0;

    // Hide any leftover detail screen
    const existingDetail = _sheetEl.querySelector('.step-detail-screen');
    if (existingDetail) existingDetail.remove();

    for (const step of steps) {
        const row     = document.createElement('div');
        row.className = 'act-step-row';

        const rowMain = document.createElement('div');
        rowMain.className = 'act-step-main';
        rowMain.innerHTML =
            _actStepIcon(step.name) +
            '<span class="act-step-label">' + escHtml(_actLabel(step.name, step.args || {})) + '</span>' +
            '<svg class="act-step-chevron" width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="9 6 15 12 9 18"/></svg>';
        row.appendChild(rowMain);

        rowMain.addEventListener('click', () => {
            _openDetailScreen(step);
        });

        body.appendChild(row);
    }

    _sheetEl.classList.remove('closing');
    _sheetEl.classList.add('open');
}

function _openDetailScreen(step) {
    const sheet = _sheetEl.querySelector('.steps-sheet');

    // Remove any existing detail screen
    const old = sheet.querySelector('.step-detail-screen');
    if (old) old.remove();

    const screen = document.createElement('div');
    screen.className = 'step-detail-screen';

    const label = _actLabel(step.name, step.args || {});
    screen.innerHTML =
        '<div class="step-detail-header">' +
            '<button class="step-detail-back" aria-label="Back">' +
                '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="15 18 9 12 15 6"/></svg>' +
            '</button>' +
            '<span class="step-detail-title">' + escHtml(label) + '</span>' +
        '</div>' +
        '<div class="step-detail-body"></div>';

    const detailBody = screen.querySelector('.step-detail-body');
    screen.querySelector('.step-detail-back').addEventListener('click', () => _closeDetailScreen(screen));

    const detailHeader = screen.querySelector('.step-detail-header');
    // Forward drag events on the detail header to the sheet's drag handler
    const sheetDrag = _sheetEl._sheetDragStart;
    if (sheetDrag) {
        detailHeader.addEventListener('mousedown', sheetDrag);
        detailHeader.addEventListener('touchstart', sheetDrag, { passive: false });
    }

    sheet.appendChild(screen);
    // Trigger animation
    requestAnimationFrame(() => {
        requestAnimationFrame(() => screen.classList.add('visible'));
    });

    // Render content into a plain div (reuse _renderStepDetail)
    const el = document.createElement('div');
    el.style.cssText = 'padding: 4px 0;';
    detailBody.appendChild(el);
    _renderStepDetail(step, el);
}

function _closeDetailScreen(screen) {
    screen.classList.remove('visible');
    screen.classList.add('hiding');
    screen.addEventListener('transitionend', () => screen.remove(), { once: true });
}

function _closeSheet() {
    if (!_sheetEl) return;
    _sheetEl.classList.add('closing');
    _sheetEl.classList.remove('open');
    const sheet = _sheetEl.querySelector('.steps-sheet');
    // reset explicit height after animation
    setTimeout(() => {
        if (!_sheetEl.classList.contains('open')) {
            sheet.style.height = '';
            sheet.style.maxHeight = '';
            _sheetEl.classList.remove('closing');
            // Remove any open detail screen
            const ds = sheet.querySelector('.step-detail-screen');
            if (ds) ds.remove();
        }
    }, 300);
}

// ── Activity bar ───────────────────────────────────────────────────────

export function createActivityBar(container) {
    const target = container || chatEl;
    const steps  = [];
    let sealed   = false;

    const wrap = document.createElement('div');
    wrap.className = 'act-wrap';

    const bar = document.createElement('div');
    bar.className = 'act-bar';
    bar.innerHTML =
        '<span class="act-spinner"></span>' +
        '<span class="act-label">Thinking\u2026</span>' +
        '<svg class="act-chevron" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="display:none"><polyline points="6 9 12 15 18 9"/></svg>';
    wrap.appendChild(bar);
    target.appendChild(wrap);
    scrollBottom();

    const labelEl   = bar.querySelector('.act-label');
    const spinnerEl = bar.querySelector('.act-spinner');
    const chevronEl = bar.querySelector('.act-chevron');

    function _setBarLabel(text) {
        const n = steps.length;
        const full = n > 0 ? text + '  \u00b7  step\u00a0' + n : text;
        labelEl.textContent = full.length > 70 ? full.slice(0, 68) + '\u2026' : full;
    }

    bar.addEventListener('click', () => {
        if (steps.length) _openSheet(steps, labelEl.textContent);
    });

    const obj = {
        wrap,

        addThought(text) {
            let step = steps.length && steps[steps.length - 1].name === '__thought__' ? steps[steps.length - 1] : null;
            if (!step) {
                step = { name: '__thought__', args: {}, thoughtText: '', result: null };
                steps.push(step);
            }
            step.thoughtText += text;
            _setBarLabel('Thinking\u2026');
            scrollBottom();
        },

        addTool(name, args) {
            const step = { name, args: args || {}, thoughtText: '', result: null };
            steps.push(step);
            _setBarLabel(_actLabel(name, args || {}));
            scrollBottom();
            return step;
        },

        setToolResult(step, result) {
            step.result = result;
        },

        seal() {
            sealed = true;
            spinnerEl.className = '';
            // freeze whatever label was last shown, step count appended
            chevronEl.style.display = '';
            wrap.classList.add('act-sealed');
            scrollBottom();
        },
    };

    return obj;
}

// ── Static replay ───────────────────────────────────────────────────────

export function addActivityBarStatic(steps, container) {
    const target = container || chatEl;
    if (!steps || !steps.length) return;

    const wrap = document.createElement('div');
    wrap.className = 'act-wrap act-sealed';

    const n     = steps.length;
    const last  = steps[n - 1];
    const title = last ? _actLabel(last.name, last.args || {}) + '  \u00b7  step\u00a0' + n : n + ' steps';
    const bar   = document.createElement('div');
    bar.className = 'act-bar';
    bar.innerHTML =
        '<span class="act-label">' + title + '</span>' +
        '<svg class="act-chevron" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"/></svg>';
    wrap.appendChild(bar);

    bar.addEventListener('click', () => _openSheet(steps, title));

    target.appendChild(wrap);
}

// Legacy shims — keep app.js callers working ────────────────────────────

export function addThinkingStatic(text) {
    addActivityBarStatic([{ name: '__thought__', args: {}, thoughtText: text }]);
}

export function addToolGroupStatic(tools, container) {
    addActivityBarStatic(tools.map(t => ({ name: t.name, args: t.args || {}, result: t.result ?? null })), container);
}

export function addSubagentStatic(agentId, task, context, result) {
    addActivityBarStatic([{ name: 'spawn_agent', args: { agent_id: agentId, task, context }, result }]);
}


// ── Status banner ──────────────────────────────────────────────────────

export function showStatusBanner(text, kind = 'info') {
    const old = document.getElementById('status-banner');
    if (old) old.remove();
    const el = document.createElement('div');
    el.id        = 'status-banner';
    el.className = 'status-banner status-' + kind;
    el.textContent = text;
    chatEl.appendChild(el);
    scrollBottom();
    setTimeout(() => el.remove(), 4000);
}

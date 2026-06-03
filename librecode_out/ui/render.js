// ── Rendering helpers ─────────────────────────────────────────────────

import { chatEl } from './state.js';

// ── HTML utils ─────────────────────────────────────────────────────────

export function escHtml(s) {
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

let _mermaidCounter = 0;
let _smilesCounter = 0;
let _plotlyCounter = 0;
let _fnplotCounter = 0;

function buildCodeBlock(lang, code) {
    if (lang === 'mermaid') {
        const id = 'mermaid-' + (++_mermaidCounter);
        return '<div class="mermaid-block"><div class="mermaid-diagram" id="' + id + '" data-src="' + encodeURIComponent(code.trim()) + '"></div></div>';
    }
    if (lang === 'smiles') {
        const id = 'smiles-' + (++_smilesCounter);
        return '<div class="smiles-block"><canvas class="smiles-canvas" id="' + id + '" data-src="' + encodeURIComponent(code.trim()) + '" width="400" height="300"></canvas></div>';
    }
    if (lang === 'plotly') {
        const id = 'plotly-' + (++_plotlyCounter);
        return '<div class="plotly-block"><div class="plotly-chart" id="' + id + '" data-src="' + encodeURIComponent(code.trim()) + '"></div></div>';
    }
    if (lang === 'functionplot') {
        const id = 'fnplot-' + (++_fnplotCounter);
        return '<div class="fnplot-block"><div class="fnplot-chart" id="' + id + '" data-src="' + encodeURIComponent(code.trim()) + '"></div></div>';
    }
    return '<div class="code-block">' +
        '<div class="code-block-header">' +
        '<span class="code-lang">' + escHtml(lang || 'code') + '</span>' +
        '<button class="copy-btn" onclick="copyCode(this)">copy</button>' +
        '</div>' +
        '<pre><code class="' + escHtml(lang) + '">' + escHtml(code.trimEnd()) + '</code></pre>' +
        '</div>';
}

export function highlightCodeBlocks(container) {
    if (typeof hljs !== 'undefined') {
        const blocks = container.querySelectorAll('pre code');
        blocks.forEach(block => {
            if (block.dataset.highlighted) return;
            hljs.highlightElement(block);
            block.dataset.highlighted = 'true';
        });
    }
    if (typeof window._mermaid !== 'undefined') {
        const diagrams = container.querySelectorAll('.mermaid-diagram:not([data-rendered])');
        diagrams.forEach(async (el) => {
            el.dataset.rendered = 'true';
            const src = decodeURIComponent(el.dataset.src || '');
            try {
                const id = (el.id || ('mermaid-' + (++_mermaidCounter))) + '-svg';
                const { svg } = await window._mermaid.render(id, src);
                el.innerHTML = svg;
            } catch (e) {
                el.innerHTML = '<pre class="mermaid-error">' + escHtml(src) + '</pre>';
            }
        });
    }
    if (typeof SmilesDrawer !== 'undefined') {
        const canvases = container.querySelectorAll('.smiles-canvas:not([data-rendered])');
        canvases.forEach((el) => {
            el.dataset.rendered = 'true';
            const src = decodeURIComponent(el.dataset.src || '');
            try {
                // v1 API: SmilesDrawer.Drawer takes options, draw() takes (tree, canvasId, theme)
                const drawerOpts = {
                    width: el.width || 400,
                    height: el.height || 300,
                    themes: {
                        dark: {
                            C: '#e8e8e8', O: '#e06c75', N: '#61afef',
                            S: '#e5c07b', P: '#c678dd', F: '#56b6c2',
                            CL: '#56b6c2', BR: '#be5046', I: '#be5046',
                            BACKGROUND: 'transparent'
                        }
                    }
                };
                const drawer = new SmilesDrawer.Drawer(drawerOpts);
                SmilesDrawer.parse(src,
                    (tree) => {
                        try {
                            drawer.draw(tree, el.id, 'dark', false);
                        } catch (drawErr) {
                            el.parentElement.innerHTML = '<pre class="mermaid-error">' + escHtml(String(drawErr)) + '</pre>';
                        }
                    },
                    (err) => {
                        el.parentElement.innerHTML = '<pre class="mermaid-error">' + escHtml(String(err)) + '</pre>';
                    }
                );
            } catch (e) {
                el.parentElement.innerHTML = '<pre class="mermaid-error">' + escHtml(String(e)) + '</pre>';
            }
        });
    }
    if (typeof Plotly !== 'undefined') {
        const charts = container.querySelectorAll('.plotly-chart:not([data-rendered])');
        charts.forEach((el) => {
            el.dataset.rendered = 'true';
            const src = decodeURIComponent(el.dataset.src || '');
            try {
                const spec = JSON.parse(src);
                const layout = Object.assign({ paper_bgcolor: 'transparent', plot_bgcolor: 'transparent', font: { color: '#cdd6f4' }, margin: { t: 40, r: 20, b: 40, l: 50 } }, spec.layout || {});
                Plotly.newPlot(el, spec.data || spec, layout, { responsive: true, displayModeBar: false });
            } catch (e) {
                el.innerHTML = '<pre class="mermaid-error">' + escHtml(String(e)) + '</pre>';
            }
        });
    }
    if (typeof functionPlot !== 'undefined') {
        const fncharts = container.querySelectorAll('.fnplot-chart:not([data-rendered])');
        fncharts.forEach((el) => {
            el.dataset.rendered = 'true';
            const src = decodeURIComponent(el.dataset.src || '');
            try {
                const opts = JSON.parse(src);
                functionPlot(Object.assign({ target: '#' + el.id }, opts));
            } catch (e) {
                el.innerHTML = '<pre class="mermaid-error">' + escHtml(String(e)) + '</pre>';
            }
        });
    }
}

// ── Table parser ────────────────────────────────────────────────────────
//
// Tables are extracted BEFORE escHtml runs, stored in a sentinel, then
// re-injected AFTER all other escaping. This mirrors how inline math
// is handled and prevents the generated <table> HTML from being escaped
// into literal text.

const _tableStore = [];

function parseTableBlock(text) {
    // Reset for each top-level parseMarkdown call (caller does this)
    return text.replace(
        /^(\|.+\|\n)([ \t]*\|[ \t]*[-:]+[-| \t:]*\|\n)((?:\|.+\|\n?)*)/gm,
        (match) => {
            const idx = _tableStore.length;
            _tableStore.push(match);
            return '\x00T' + idx + '\x00';
        }
    );
}

function buildTableHtml(tableText) {
    const lines = tableText.split('\n').filter(Boolean);
    if (lines.length < 3) return tableText;
    const parseRow = row =>
        row.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
    const headers = parseRow(lines[0]);
    const rows    = lines.slice(2).map(parseRow);
    const th = headers.map(h => '<th>' + escHtml(h) + '</th>').join('');
    const trs = rows.map(r =>
        '<tr>' + r.map(c => '<td>' + escHtml(c) + '</td>').join('') + '</tr>'
    ).join('');
    return '<div class="md-table-wrap"><table class="md-table"><thead><tr>' + th + '</tr></thead><tbody>' + trs + '</tbody></table></div>';
}

// ── KaTeX renderer ──────────────────────────────────────────────────────

function renderMath(tex, display) {
    if (typeof katex === 'undefined') {
        return display ? '<div class="math-block">$$' + escHtml(tex) + '$$</div>'
                       : '<span class="math-inline">$' + escHtml(tex) + '$</span>';
    }
    try {
        return katex.renderToString(tex, { throwOnError: false, displayMode: display });
    } catch {
        return escHtml(tex);
    }
}

export function parseMarkdown(text) {
    if (!text) return '';
    // Reset the per-call table store so sentinels are unique to this parse
    _tableStore.length = 0;
    const segments = [];
    // Split by fenced code blocks AND display math $$...$$
    const fence = /```(\w*)\n?([\s\S]*?)```|\$\$([\s\S]*?)\$\$/g;
    let last = 0, m;
    while ((m = fence.exec(text)) !== null) {
        if (m.index > last) segments.push({ type: 'text', content: text.slice(last, m.index) });
        if (m[3] !== undefined) {
            segments.push({ type: 'math_block', content: m[3] });
        } else {
            segments.push({ type: 'code', lang: m[1] || '', content: m[2] });
        }
        last = m.index + m[0].length;
    }
    if (last < text.length) segments.push({ type: 'text', content: text.slice(last) });

    return segments.map(seg => {
        if (seg.type === 'code')       return buildCodeBlock(seg.lang, seg.content);
        if (seg.type === 'math_block') return '<div class="math-block">' + renderMath(seg.content.trim(), true) + '</div>';

        // ── text segment ──
        let raw = seg.content;

        // Tables (before escaping, operating on raw text)
        raw = parseTableBlock(raw);

        // Inline math placeholders — extract before escaping
        const mathStore = [];
        raw = raw.replace(/\$([^$\n]+?)\$/g, (_, tex) => {
            mathStore.push(tex);
            return '\x00M' + (mathStore.length - 1) + '\x00';
        });

        let s = escHtml(raw);

        s = s.replace(/`([^`\n]+)`/g,            '<code>$1</code>');
        s = s.replace(/\*\*([^*\n]+)\*\*/g,      '<strong>$1</strong>');
        s = s.replace(/__([^_\n]+)__/g,           '<strong>$1</strong>');
        s = s.replace(/\*([^*\n]+)\*/g,           '<em>$1</em>');
        s = s.replace(/(^|[\s>])_([^_\n]+)_(?=[\s<,\.!?;:]|$)/gm, '$1<em>$2</em>');
        s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
        s = s.replace(/^### (.+)$/gm, '<h3>$1</h3>');
        s = s.replace(/^## (.+)$/gm,  '<h2>$1</h2>');
        s = s.replace(/^# (.+)$/gm,   '<h1>$1</h1>');
        s = s.replace(/^[\*\-] (.+)$/gm, '<li>$1</li>');
        s = s.replace(/(<li>.*<\/li>\n?)+/g, mm => '<ul>' + mm + '</ul>');
        s = s.replace(/^---$/gm, '<hr>');

        // Restore inline math
        if (mathStore.length) {
            s = s.replace(/\x00M(\d+)\x00/g, (_, i) =>
                '<span class="math-inline">' + renderMath(mathStore[+i], false) + '</span>'
            );
        }

        // Restore tables (cells already escaped during buildTableHtml)
        if (_tableStore.length) {
            s = s.replace(/\x00T(\d+)\x00/g, (_, i) => buildTableHtml(_tableStore[+i]));
        }

        return s.split(/\n\n+/).map(b => {
            b = b.trim();
            if (!b) return '';
            if (/^<(div|ul|ol|h[1-6]|hr|blockquote|table)/.test(b)) return b;
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

let _userScrolled = false;

function isNearBottom() {
    const threshold = 120;
    return chatEl.scrollHeight - chatEl.scrollTop - chatEl.clientHeight < threshold;
}

// Detect intentional upward scroll → stop auto-following
chatEl.addEventListener('wheel', (e) => {
    if (e.deltaY < 0) _userScrolled = true;
}, { passive: true });

// Touch: finger swipes down (content moves up) → stop following
let _touchStartY = 0;
chatEl.addEventListener('touchstart', (e) => { _touchStartY = e.touches[0].clientY; }, { passive: true });
chatEl.addEventListener('touchmove',  (e) => { if (e.touches[0].clientY > _touchStartY) _userScrolled = true; }, { passive: true });

// Resume following once user scrolls back near bottom
chatEl.addEventListener('scroll', () => { if (_userScrolled && isNearBottom()) _userScrolled = false; }, { passive: true });

export function scrollBottom() {
    if (_userScrolled) return;
    if (isNearBottom()) chatEl.scrollTop = chatEl.scrollHeight;
}

export function forceScrollBottom() {
    chatEl.scrollTop = chatEl.scrollHeight;
}

// ── Turn wrapper — one per assistant reply ─────────────────────────────

export function createTurnWrapper() {
    const div = document.createElement('div');
    div.className = 'msg assistant streaming';
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
    if (name === 'spawn_agent') return (args.task || '') + (args.context ? '\n\nContext:\n' + args.context : '');
    return JSON.stringify(args, null, 2);
}

function _langFromPath(path) {
    if (!path) return '';
    const ext = (path.split('.').pop() || '').toLowerCase();
    const map = {
        js: 'javascript', jsx: 'javascript', ts: 'typescript', tsx: 'typescript',
        py: 'python', rb: 'ruby', go: 'go', rs: 'rust', java: 'java',
        cpp: 'cpp', cc: 'cpp', c: 'c', h: 'c', cs: 'csharp',
        sh: 'bash', bash: 'bash', zsh: 'bash',
        css: 'css', scss: 'css', html: 'html', xml: 'xml',
        json: 'json', yaml: 'yaml', yml: 'yaml', toml: 'toml',
        md: 'markdown', sql: 'sql', kt: 'kotlin', swift: 'swift',
    };
    return map[ext] || '';
}

function _buildHighlightedPre(code, lang) {
    const codeEl = document.createElement('code');
    if (lang) codeEl.className = lang;
    codeEl.textContent = code;
    const pre = document.createElement('pre');
    pre.className = 'act-detail-pre act-detail-code';
    pre.appendChild(codeEl);
    if (lang && typeof hljs !== 'undefined') {
        try { hljs.highlightElement(codeEl); } catch {}
    }
    return pre;
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

        // Tap with no meaningful drag — do nothing
        if (Math.abs(_dragStartH - curH) < 6) return;

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

function _renderStepDetail(step, container) {
    // For thought steps that are still active, we need live updating.
    // We store state on the container element itself.
    if (step.name === '__thought__') {
        const isFirst = !container._thoughtPre;
        if (isFirst) {
            container.style.cssText = 'padding: 10px 14px;';
            const pre = document.createElement('pre');
            pre.className = 'act-detail-pre';
            pre.style.cssText = 'max-height: none; padding: 0;';
            container.appendChild(pre);
            container._thoughtPre = pre;
        }
        const pre = container._thoughtPre;
        const fullText = step.thoughtText || step.text || '';
        pre.textContent = fullText;
        // First open → top; live streaming update → follow bottom
        if (isFirst) {
            container.scrollTop = 0;
        } else if (step.result == null) {
            container.scrollTop = container.scrollHeight;
        }
    } else if (step.name === 'edit' || step.name === 'diff') {
        if (container._rendered) return;
        container._rendered = true;
        const result = step.result || '';
        const sepIdx = result.indexOf('\n\n<<<DIFF>>>\n');
        const status  = sepIdx !== -1 ? result.slice(0, sepIdx) : result;
        const rawDiff = sepIdx !== -1 ? result.slice(sepIdx + '\n\n<<<DIFF>>>\n'.length) : '';
        container.innerHTML = '';
        const fileSection = document.createElement('div');
        fileSection.className = 'act-detail-section';
        fileSection.innerHTML = '<span class="act-detail-label">file</span>';
        fileSection.appendChild(_buildHighlightedPre(step.args.filePath || step.args.fileA || '', ''));
        container.appendChild(fileSection);
        if (status) {
            const statusSection = document.createElement('div');
            statusSection.className = 'act-detail-section';
            statusSection.innerHTML = '<span class="act-detail-label">status</span>';
            statusSection.appendChild(_buildHighlightedPre(status, ''));
            container.appendChild(statusSection);
        }
        const diffSection = document.createElement('div');
        diffSection.className = 'act-detail-section act-detail-diff';
        diffSection.innerHTML = '<div class="diff-view">' + _renderDiff(rawDiff) + '</div>';
        container.appendChild(diffSection);
    } else if (step.name === 'spawn_agent') {
        if (!container._headerRendered) {
            container._headerRendered = true;
            const agentSection = document.createElement('div');
            agentSection.className = 'act-detail-section';
            agentSection.innerHTML = '<span class="act-detail-label">agent</span>';
            agentSection.appendChild(_buildHighlightedPre(step.args.agent_id || '', ''));
            container.appendChild(agentSection);
            const taskSection = document.createElement('div');
            taskSection.className = 'act-detail-section';
            taskSection.innerHTML = '<span class="act-detail-label">task</span>';
            taskSection.appendChild(_buildHighlightedPre(step.args.task || '', ''));
            container.appendChild(taskSection);
            const liveSection = document.createElement('div');
            liveSection.className = 'act-detail-section act-detail-subagent-live';
            container.appendChild(liveSection);
            container._liveSection = liveSection;
        }
        const live = container._liveSection;
        if (step.result != null) {
            live.innerHTML = '<span class="act-detail-label">result</span><div class="act-detail-md-body">' + parseMarkdown(String(step.result)) + '</div>';
            highlightCodeBlocks(live);
        } else if (step.streamText) {
            live.innerHTML = '<span class="act-detail-label">responding\u2026</span><div class="act-detail-md-body act-detail-md-streaming">' + parseMarkdown(step.streamText) + '<span class="cursor"></span></div>';
            highlightCodeBlocks(live);
            live.scrollTop = live.scrollHeight;
        }
    } else if (step.name === 'shell') {
        if (container._rendered && step.result == null) return;
        container._rendered = true;
        container.innerHTML = '';
        const cmdSection = document.createElement('div');
        cmdSection.className = 'act-detail-section';
        cmdSection.innerHTML = '<span class="act-detail-label">command</span>';
        cmdSection.appendChild(_buildHighlightedPre(step.args.command || '', 'bash'));
        container.appendChild(cmdSection);
        if (step.args.cwd) {
            const cwdSection = document.createElement('div');
            cwdSection.className = 'act-detail-section';
            cwdSection.innerHTML = '<span class="act-detail-label">cwd</span>';
            cwdSection.appendChild(_buildHighlightedPre(step.args.cwd, ''));
            container.appendChild(cwdSection);
        }
        if (step.result != null) {
            const outSection = document.createElement('div');
            outSection.className = 'act-detail-section';
            outSection.innerHTML = '<span class="act-detail-label">output</span>';
            outSection.appendChild(_buildHighlightedPre(String(step.result), ''));
            container.appendChild(outSection);
        }
    } else if (step.name === 'write' || step.name === 'read') {
        if (container._rendered && step.result == null) return;
        container._rendered = true;
        const filePath = step.args.filePath || '';
        const lang = _langFromPath(filePath);
        container.innerHTML = '';
        const fileSection = document.createElement('div');
        fileSection.className = 'act-detail-section';
        fileSection.innerHTML = '<span class="act-detail-label">file</span>';
        fileSection.appendChild(_buildHighlightedPre(filePath, ''));
        container.appendChild(fileSection);
        const content = step.name === 'write' ? (step.args.content || '') : (step.result != null ? String(step.result) : null);
        if (content != null) {
            const contentSection = document.createElement('div');
            contentSection.className = 'act-detail-section';
            contentSection.innerHTML = '<span class="act-detail-label">' + (step.name === 'write' ? 'content' : 'output') + '</span>';
            contentSection.appendChild(_buildHighlightedPre(content, lang));
            container.appendChild(contentSection);
        }
        if (step.name === 'write' && step.result != null) {
            const statusSection = document.createElement('div');
            statusSection.className = 'act-detail-section';
            statusSection.innerHTML = '<span class="act-detail-label">status</span>';
            statusSection.appendChild(_buildHighlightedPre(String(step.result), ''));
            container.appendChild(statusSection);
        }
    } else {
        if (container._rendered && step.result == null) return;
        container._rendered = true;
        const input = _actInputSummary(step.name, step.args || {});
        container.innerHTML = '';
        const inputSection = document.createElement('div');
        inputSection.className = 'act-detail-section';
        inputSection.innerHTML = '<span class="act-detail-label">input</span>';
        inputSection.appendChild(_buildHighlightedPre(input, ''));
        container.appendChild(inputSection);
        if (step.result != null) {
            const outputSection = document.createElement('div');
            outputSection.className = 'act-detail-section';
            outputSection.innerHTML = '<span class="act-detail-label">output</span>';
            outputSection.appendChild(_buildHighlightedPre(String(step.result), ''));
            container.appendChild(outputSection);
        }
    }
}

function _refreshOpenDetail(step) {
    if (!_sheetEl) return;
    const screen = _sheetEl.querySelector('.step-detail-screen');
    if (!screen || screen._step !== step) return;
    _renderStepDetail(step, screen.querySelector('.step-detail-body'));
}

function _refreshSheetList(steps) {
    if (!_sheetEl || !_sheetEl.classList.contains('open')) return;
    // only add rows for steps that don't have a row yet
    const body = _sheetEl.querySelector('.steps-sheet-body');
    const existingRows = body.querySelectorAll('.act-step-row');
    for (let i = existingRows.length; i < steps.length; i++) {
        const step = steps[i];
        const row = document.createElement('div');
        row.className = 'act-step-row';
        const rowMain = document.createElement('div');
        rowMain.className = 'act-step-main';
        rowMain.innerHTML =
            _actStepIcon(step.name) +
            '<span class="act-step-label">' + escHtml(_actLabel(step.name, step.args || {})) + '</span>' +
            '<svg class="act-step-chevron" width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="9 6 15 12 9 18"/></svg>';
        rowMain.addEventListener('click', () => _openDetailScreen(step));
        row.appendChild(rowMain);
        body.appendChild(row);
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

function _openDetailScreen(step, closeOnBack = false) {
    const sheet = _sheetEl.querySelector('.steps-sheet');

    // Remove any existing detail screen instantly
    const old = sheet.querySelector('.step-detail-screen');
    if (old) old.remove();

    const screen = document.createElement('div');
    screen.className = 'step-detail-screen';
    screen._step = step; // store ref for live updates

    const label = _actLabel(step.name, step.args || {});
    screen.innerHTML =
        '<div class="step-detail-header">' +
            '<button class="step-detail-back" aria-label="Back">' +
                '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="15 18 9 12 15 6"/></svg>' +
            '</button>' +
            '<span class="step-detail-title">' + escHtml(label) + '</span>' +
        '</div>' +
        '<div class="step-detail-body"></div>';

    const backBtn = screen.querySelector('.step-detail-back');
    backBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (closeOnBack) {
            _closeSheet();
        } else {
            _closeDetailScreen(screen);
        }
    });

    const detailHeader = screen.querySelector('.step-detail-header');
    const sheetDrag = _sheetEl._sheetDragStart;
    if (sheetDrag) {
        const detailDragStart = (e) => {
            if (e.target === backBtn || backBtn.contains(e.target)) return;
            sheetDrag(e);
        };
        detailHeader.addEventListener('mousedown', detailDragStart);
        detailHeader.addEventListener('touchstart', detailDragStart, { passive: false });
    }

    sheet.appendChild(screen);
    requestAnimationFrame(() => requestAnimationFrame(() => screen.classList.add('visible')));

    _renderStepDetail(step, screen.querySelector('.step-detail-body'));
}

function _closeDetailScreen(screen) {
    screen.classList.remove('visible');
    screen.classList.add('hiding');
    // fallback timeout in case transitionend doesn't fire
    const cleanup = () => { if (screen.parentNode) screen.remove(); };
    screen.addEventListener('transitionend', cleanup, { once: true });
    setTimeout(cleanup, 350);
}

function _closeSheet() {
    if (!_sheetEl) return;
    // if detail screen open, go back to list instead of closing
    const detail = _sheetEl.querySelector('.step-detail-screen');
    if (detail) { _closeDetailScreen(detail); return; }
    const sheet = _sheetEl.querySelector('.steps-sheet');
    sheet.style.transition = ''; // clear inline transition from drag so CSS closing animation plays
    _sheetEl.classList.add('closing');
    _sheetEl.classList.remove('open');
    setTimeout(() => {
        if (!_sheetEl.classList.contains('open')) {
            sheet.style.height = '';
            sheet.style.maxHeight = '';
            _sheetEl.classList.remove('closing');
        }
    }, 300);
}

// ── Activity bar ───────────────────────────────────────────────────────

export function createActivityBar(container) {
    const target = container || chatEl;
    const steps  = [];
    let sealed    = false;
    let collapsed = false;

    const wrap = document.createElement('div');
    wrap.className = 'act-wrap';

    const card = document.createElement('div');
    card.className = 'act-live-card';

    const cardHeader = document.createElement('div');
    cardHeader.className = 'act-live-header';
    cardHeader.innerHTML =
        '<svg class="act-live-chevron" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"/></svg>' +
        '<span class="act-spinner"></span>' +
        '<span class="act-live-count">Thinking\u2026</span>';

    const stepsList = document.createElement('div');
    stepsList.className = 'act-live-steps';

    card.appendChild(cardHeader);
    card.appendChild(stepsList);
    wrap.appendChild(card);
    target.appendChild(wrap);
    scrollBottom();

    const countEl     = cardHeader.querySelector('.act-live-count');
    const liveChevron = cardHeader.querySelector('.act-live-chevron');

    cardHeader.addEventListener('click', () => {
        if (sealed) return;
        collapsed = !collapsed;
        stepsList.classList.toggle('act-live-steps-hidden', collapsed);
        liveChevron.style.transform = collapsed ? 'rotate(-90deg)' : 'rotate(0deg)';
    });

    function _updateCount() {
        const n = steps.length;
        countEl.textContent = n <= 1
            ? 'Thinking\u2026'
            : n + '\u00a0steps';
    }

    function _addStepItem(step) {
        const item = document.createElement('div');
        item.className = 'act-live-step-item';

        const main = document.createElement('div');
        main.className = 'act-live-step-main';
        main.innerHTML =
            _actStepIcon(step.name) +
            '<span class="act-live-step-lbl">' + escHtml(_actLabel(step.name, step.args || {})) + '</span>';
        item.appendChild(main);

        if (step.name === '__thought__') {
            const body = document.createElement('div');
            body.className = 'act-live-thought-body';
            item.appendChild(body);
            item._thoughtBody = body;
        }

        // tap any row → open detail screen
        item.addEventListener('click', (e) => {
            e.stopPropagation();
            _openSheet(steps, countEl.textContent);
            _openDetailScreen(step, true);
        });

        item._step = step;
        stepsList.appendChild(item);
        // keep newest step in view
        stepsList.scrollTop = stepsList.scrollHeight;
        return item;
    }

    function _activeThoughtItem() {
        const items = stepsList.querySelectorAll('.act-live-step-item');
        const last  = items.length ? items[items.length - 1] : null;
        return (last && last._step && last._step.name === '__thought__') ? last : null;
    }

    const obj = {
        wrap,

        addThought(text) {
            let step = steps.length && steps[steps.length - 1].name === '__thought__'
                ? steps[steps.length - 1] : null;
            if (!step) {
                step = { name: '__thought__', args: {}, thoughtText: '', result: null };
                steps.push(step);
                _addStepItem(step);
                _updateCount();
            }
            step.thoughtText += text;
            const item = _activeThoughtItem();
            if (item && item._thoughtBody) {
                item._thoughtBody.textContent = step.thoughtText;
                stepsList.scrollTop = stepsList.scrollHeight;
            }
            _refreshOpenDetail(step);
            scrollBottom();
        },

        addTool(name, args) {
            const step = { name, args: args || {}, thoughtText: '', result: null };
            steps.push(step);
            _addStepItem(step);
            _updateCount();
            _refreshSheetList(steps);
            scrollBottom();
            return step;
        },

        setToolResult(step, result) {
            step.result = result;
            _refreshOpenDetail(step);
        },

        updateSubagentStream(step, text) {
            if (!step.streamText) step.streamText = '';
            step.streamText += text;
            _refreshOpenDetail(step);
        },

        seal() {
            sealed = true;
            const n     = steps.length;
            const last  = steps[n - 1];
            const title = last
                ? _actLabel(last.name, last.args || {}) + '  \u00b7  step\u00a0' + n
                : n + '\u00a0steps';

            card.classList.add('act-sealing');
            setTimeout(() => {
                if (!wrap.isConnected) return;
                wrap.innerHTML = '';
                const bar = document.createElement('div');
                bar.className = 'act-bar act-bar-fadein';
                bar.innerHTML =
                    '<span class="act-label">' + escHtml(title) + '</span>' +
                    '<svg class="act-chevron" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"/></svg>';
                bar.addEventListener('click', () => _openSheet(steps, title));
                wrap.appendChild(bar);
                wrap.classList.add('act-sealed');
                scrollBottom();
            }, 260);
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


// ── Subagent live stream block ─────────────────────────────────────────

export function createSubagentStreamBlock(container) {
    const target = container || chatEl;
    const wrap = document.createElement('div');
    wrap.className = 'subagent-stream-block';

    const header = document.createElement('div');
    header.className = 'subagent-stream-header';
    header.innerHTML =
        '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" style="flex-shrink:0;opacity:.5"><circle cx="12" cy="12" r="10"/><polyline points="8 12 12 8 16 12"/><line x1="12" y1="8" x2="12" y2="16"/></svg>' +
        '<span class="subagent-stream-label">Subagent</span>' +
        '<span class="subagent-stream-cursor"></span>';

    const content = document.createElement('div');
    content.className = 'subagent-stream-content';

    wrap.appendChild(header);
    wrap.appendChild(content);
    target.appendChild(wrap);
    scrollBottom();
    return { wrap, content };
}



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

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
    document.body.appendChild(overlay);
    _sheetEl = overlay;
}

function _renderStepDetail(step, el) {
    if (el._rendered) return;
    el._rendered = true;
    if (step.name === '__thought__') {
        el.innerHTML = '<pre class="act-detail-pre">' + escHtml(step.thoughtText || step.text || '') + '</pre>';
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

    for (const step of steps) {
        const row     = document.createElement('div');
        row.className = 'act-step-row';

        const rowMain = document.createElement('div');
        rowMain.className = 'act-step-main';
        rowMain.innerHTML =
            _actStepIcon(step.name) +
            '<span class="act-step-label">' + escHtml(_actLabel(step.name, step.args || {})) + '</span>' +
            '<svg class="act-step-chevron" width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"/></svg>';
        row.appendChild(rowMain);

        const detail  = document.createElement('div');
        detail.className = 'act-step-detail';
        row.appendChild(detail);

        rowMain.addEventListener('click', () => {
            const open = !detail.classList.contains('open');
            detail.classList.toggle('open', open);
            rowMain.classList.toggle('act-step-open', open);
            _renderStepDetail(step, detail);
        });

        body.appendChild(row);
    }

    _sheetEl.classList.add('open');
}

function _closeSheet() {
    if (_sheetEl) _sheetEl.classList.remove('open');
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
        const short = text.length > 60 ? text.slice(0, 58) + '\u2026' : text;
        labelEl.textContent = short;
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
            const n = steps.length;
            labelEl.textContent = n + (n === 1 ? ' step' : ' steps');
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
    const title = n + (n === 1 ? ' step' : ' steps');
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

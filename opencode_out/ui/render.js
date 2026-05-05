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
            return '<p>' + b.replace(/\n/g, '<br>') + '</p>';
        }).join('\n');
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
        const body = document.createElement('div');
        body.className = 'thinking-body';
        body.textContent = reasoning;
        wrapper.appendChild(body);
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

export function createAssistantShell() {
    const div = document.createElement('div');
    div.className = 'msg assistant streaming';
    div.innerHTML = '<div class="agent-label">Agent</div><span class="cursor"></span>';
    chatEl.appendChild(div);
    scrollBottom();
    return div;
}

export function sealAssistant(div, text) {
    div.classList.remove('streaming');
    div.removeAttribute('data-live');
    div.innerHTML = parseMarkdown(text);
    highlightCodeBlocks(div);
}

// ── Thinking block ─────────────────────────────────────────────────────

export function createThinkingBlock() {
    const wrapper = document.createElement('div');
    wrapper.className = 'thinking-wrapper';
    wrapper.innerHTML = '<div class="thinking-body"></div>';
    chatEl.appendChild(wrapper);
    scrollBottom();
    const body = wrapper.querySelector('.thinking-body');
    return { wrapper, body };
}

export function sealThinking(block) {
}

// ── Tool pills ─────────────────────────────────────────────────────────

export function createToolGroup() {
    const group = document.createElement('div');
    group.className = 'tool-group';
    chatEl.appendChild(group);
    scrollBottom();
    return group;
}

function _toolPillIcon(name) {
    const s = 'width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" style="flex-shrink:0"';
    if (name === 'web_search' || name === 'grep')
        return `<svg ${s} stroke-width="2.5"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>`;
    if (name === 'glob')
        return `<svg ${s} stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>`;
    if (name === 'read')
        return `<svg ${s} stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`;
    if (name === 'write' || name === 'edit')
        return `<svg ${s} stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>`;
    if (name === 'shell')
        return `<svg ${s} stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>`;
    if (name === 'web_fetch')
        return `<svg ${s} stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>`;
    if (name === 'diff')
        return `<svg ${s} stroke-width="2"><polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/><line x1="4" y1="4" x2="9" y2="9"/></svg>`;
    return `<svg ${s} stroke-width="2"><circle cx="12" cy="12" r="10"/></svg>`;
}

function _toolPillLabel(name, args) {
    if (name === 'web_search')  return 'searching&nbsp;<em>' + escHtml(args.query   || '') + '</em>';
    if (name === 'glob')        return 'finding&nbsp;<em>'   + escHtml(args.pattern || '') + '</em>';
    if (name === 'grep')        return 'searching&nbsp;<em>' + escHtml(args.pattern || '') + '</em>';
    if (name === 'read')        return 'reading&nbsp;<em>'   + escHtml(args.filePath || '') + '</em>';
    if (name === 'write')       return 'writing&nbsp;<em>'   + escHtml(args.filePath || '') + '</em>';
    if (name === 'edit')        return 'editing&nbsp;<em>'   + escHtml(args.filePath || '') + '</em>';
    if (name === 'shell')       return 'running&nbsp;<em>'   + escHtml(args.command  || '') + '</em>';
    if (name === 'web_fetch')   return 'fetching&nbsp;<em>'  + escHtml(args.url      || '') + '</em>';
    if (name === 'github_walk') return 'github&nbsp;<em>'    + escHtml(args.repo     || '') + '</em>';
    if (name === 'spawn_agent') return 'spawning&nbsp;<em>'  + escHtml(args.agent_id || 'agent') + '</em>';
    if (name === 'diff') {
        const f1 = escHtml(args.filePath || '');
        const f2 = args.filePath2 ? escHtml(args.filePath2) : 'proposed';
        return 'diffing&nbsp;<em>' + f1 + '</em>&nbsp;vs&nbsp;<em>' + f2 + '</em>';
    }
    return 'running&nbsp;<em>' + escHtml(name) + '</em>';
}

function _toolInputSummary(name, args) {
    if (name === 'web_search')  return args.query || '';
    if (name === 'glob')        return (args.pattern || '') + (args.path    ? '\nin: '      + args.path    : '');
    if (name === 'grep')        return (args.pattern || '') + (args.path    ? '\nin: '      + args.path    : '') + (args.include ? '\ninclude: ' + args.include : '');
    if (name === 'read')        return (args.filePath || '') + (args.offset != null ? '\noffset: ' + args.offset : '') + (args.limit != null ? '  limit: ' + args.limit : '');
    if (name === 'write')       return (args.filePath || '') + '\n\n' + (args.content  || '');
    if (name === 'edit')        return (args.filePath || '') + '\n\n--- old ---\n' + (args.oldString || '') + '\n\n--- new ---\n' + (args.newString || '');
    if (name === 'shell')       return (args.command  || '') + (args.cwd    ? '\ncwd: '     + args.cwd     : '');
    if (name === 'web_fetch')   return args.url || '';
    if (name === 'github_walk') return (args.action || 'tree') + '  ' + (args.repo || '') + (args.file_path ? '\n' + args.file_path : '');
    if (name === 'diff') {
        let s = 'file: ' + (args.filePath || '');
        if (args.filePath2) s += '\nvs file: ' + args.filePath2;
        else if (args.newContent) s += '\nvs proposed content:\n' + args.newContent;
        return s;
    }
    return JSON.stringify(args, null, 2);
}

function _makeExpandPanel(inputText, outputText) {
    const panel = document.createElement('div');
    panel.className = 'tool-expand-panel';
    let html = '<div class="tool-expand-section">'
             + '<div class="tool-expand-label">input</div>'
             + '<pre class="tool-expand-pre">' + escHtml(inputText) + '</pre>'
             + '</div>';
    if (outputText != null) {
        html += '<div class="tool-expand-section">'
              + '<div class="tool-expand-label">output</div>'
              + '<pre class="tool-expand-pre">' + escHtml(String(outputText)) + '</pre>'
              + '</div>';
    }
    panel.innerHTML = html;
    return panel;
}

export function createToolPill(name, args, group) {
    const container = group || chatEl;
    const wrapper   = document.createElement('div');
    wrapper.className = 'tool-pill-wrapper';

    const div = document.createElement('div');
    div.className  = 'tool-pill';
    div.style.cursor = 'pointer';
    div.innerHTML  = '<span class="tool-spinner"></span>' + _toolPillIcon(name) + '<span>' + _toolPillLabel(name, args) + '</span>' +
        '<svg class="tool-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"/></svg>';
    wrapper.appendChild(div);

    let expanded = false;
    let panel    = null;

    div.onclick = () => {
        if (!panel) return;
        expanded = !expanded;
        panel.classList.toggle('open', expanded);
        div.classList.toggle('tool-pill-open', expanded);
    };

    div._setResult = (result) => {
        panel = _makeExpandPanel(_toolInputSummary(name, args), result);
        wrapper.appendChild(panel);
    };

    container.appendChild(wrapper);
    scrollBottom();
    return div;
}

export function createSubagentPill(agentId, task, context, group) {
    const container = group || chatEl;
    const wrapper   = document.createElement('div');
    wrapper.className = 'tool-pill-wrapper';

    const s    = 'width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="flex-shrink:0"';
    const icon = `<svg ${s}><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`;
    const div  = document.createElement('div');
    div.className  = 'tool-pill subagent-pill';
    div.style.cursor = 'pointer';
    div.innerHTML  = '<span class="tool-spinner"></span>' + icon + '<span>\u26a1&nbsp;<em>' + escHtml(agentId) + '</em>&nbsp;subagent</span>' +
        '<svg class="tool-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"/></svg>';
    wrapper.appendChild(div);

    let expanded = false;
    let panel    = null;
    let liveBody = null;

    const _ensurePanel = () => {
        if (panel) return;
        panel    = document.createElement('div');
        panel.className = 'tool-expand-panel';
        liveBody = document.createElement('div');
        liveBody.className = 'subagent-live-body';
        panel.appendChild(liveBody);
        wrapper.appendChild(panel);
    };

    div.onclick = () => {
        _ensurePanel();
        expanded = !expanded;
        panel.classList.toggle('open', expanded);
        div.classList.toggle('tool-pill-open', expanded);
    };

    div._liveEvent = (ev) => {
        _ensurePanel();
        const sub = ev.subtype;
        if (sub === 'text' && ev.data) {
            let textNode = liveBody._lastText;
            if (!textNode) {
                textNode = document.createElement('div');
                textNode.className = 'subagent-live-text';
                liveBody.appendChild(textNode);
                liveBody._lastText = textNode;
            }
            textNode.textContent += ev.data;
        } else if (sub === 'thinking' && ev.data) {
            liveBody._lastText = null;
            let thinkNode = liveBody._lastThink;
            if (!thinkNode) {
                thinkNode = document.createElement('div');
                thinkNode.className = 'subagent-live-think';
                liveBody.appendChild(thinkNode);
                liveBody._lastThink = thinkNode;
            }
            thinkNode.textContent += ev.data;
        } else if (sub === 'tool_use') {
            liveBody._lastText  = null;
            liveBody._lastThink = null;
            const pill = document.createElement('div');
            pill.className = 'subagent-live-tool';
            pill.textContent = '\u2022 ' + (ev.name || '?');
            pill.dataset.tcId = ev.tc_id || '';
            liveBody.appendChild(pill);
            liveBody._lastToolPill = pill;
        } else if (sub === 'tool_done') {
            const existing = ev.tc_id && liveBody.querySelector(`[data-tc-id="${ev.tc_id}"]`);
            const p = existing || liveBody._lastToolPill;
            if (p) p.classList.add('done');
        }
        if (expanded) scrollBottom();
    };

    div._setResult = (result) => {
        _ensurePanel();
        panel.innerHTML = '';
        const inputText = (context ? 'context:\n' + context + '\n\n---\n\ntask:\n' : 'task:\n') + task;
        const sec1 = document.createElement('div');
        sec1.className = 'tool-expand-section';
        sec1.innerHTML = '<div class="tool-expand-label">input</div><pre class="tool-expand-pre">' + escHtml(inputText) + '</pre>';
        const sec2 = document.createElement('div');
        sec2.className = 'tool-expand-section';
        sec2.innerHTML = '<div class="tool-expand-label">output</div><pre class="tool-expand-pre">' + escHtml(result) + '</pre>';
        panel.appendChild(sec1);
        panel.appendChild(sec2);
    };

    container.appendChild(wrapper);
    scrollBottom();
    return div;
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

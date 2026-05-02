// ── State ─────────────────────────────────────────────────────────────
// Per-chat sending state — replaces the old global `sending` bool.
// Each chat can be independently streaming without blocking others.
const sendingChats = new Set();   // set of chat IDs currently awaiting a response

// Per-chat partial stream state so we can re-render when switching back
// to a chat that is still responding.
// chatId -> { assistantText, hasContent }
const chatStreamState = {};

let selectedModel = 'minimax-m2.5-free';
let selectedModelCtx = 1000000;
let selectedAgent = 'build';  // build | plan | explore | ask

function formatCtx(n) {
    if (n >= 1000000) return (n / 1000000).toFixed(0) + 'M';
    if (n >= 1000) return Math.round(n / 1000) + 'k';
    return String(n);
}

// Each chat: { id, title, workingDirs: [], history: [], createdAt }
let chats = [];
let activeChatId = null;

// ── DOM refs ──────────────────────────────────────────────────────────
const chatEl       = document.getElementById('chat');
const input        = document.getElementById('input');
const sendBtn      = document.getElementById('send');
const modelBtn     = document.getElementById('model-btn');
const modelLabel   = document.getElementById('model-label');
const modelDropdown= document.getElementById('model-dropdown');
const sidebar      = document.getElementById('sidebar');
const menuBtn      = document.getElementById('menu-btn');
const folderBtn    = document.getElementById('folder-btn');
const folderBar    = document.getElementById('folder-bar');
const chatList     = document.getElementById('chat-list');
const chatTitle    = document.getElementById('chat-title');
const newChatBtn   = document.getElementById('new-chat-btn');
const chatMenuBtn  = document.getElementById('chat-menu-btn');
const chatMenu     = document.getElementById('chat-menu');
const renameChatBtn= document.getElementById('rename-chat-btn');
const deleteChatBtn= document.getElementById('delete-chat-btn');
const compressChatBtn = document.getElementById('compress-chat-btn');
const renameModal  = document.getElementById('rename-modal');
const renameInput  = document.getElementById('rename-input');
const renameCancel = document.getElementById('rename-cancel');
const renameConfirm= document.getElementById('rename-confirm');

// ── Android bridge ────────────────────────────────────────────────────
function androidBridge() { return window.Android; }

// ── Storage dir path ──────────────────────────────────────────────────
let storageDir = '';

async function getStorageDir() {
    try {
        const r = await fetch('/storage_dir');
        const d = await r.json();
        storageDir = d.path || '';
    } catch {}
}

// ── Chat persistence ──────────────────────────────────────────────────
async function saveChats() {
    try {
        await fetch('/save_chats', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ chats, activeChatId })
        });
    } catch {}
}

// Auto-save every 500ms — cheap op, just write JSON
setInterval(saveChats, 500);

async function loadChats() {
    try {
        const r = await fetch('/load_chats');
        const d = await r.json();
        chats = d.chats || [];
        activeChatId = d.activeChatId || null;
    } catch {
        chats = [];
        activeChatId = null;
    }
}

// ── Chat helpers ──────────────────────────────────────────────────────
function activeChat() {
    return chats.find(c => c.id === activeChatId) || null;
}

function createChat() {
    const id = 'chat_' + Date.now();
    const chat = { id, title: 'new chat', workingDirs: [], history: [], createdAt: Date.now() };
    chats.unshift(chat);
    return chat;
}

function truncatePath(p) {
    const parts = p.replace(/\\/g, '/').split('/').filter(Boolean);
    if (parts.length <= 3) return p;
    return '.../' + parts.slice(-3).join('/');
}

// ── Send button state ─────────────────────────────────────────────────
// Only disable the send button if the *currently active* chat is sending.
// Other chats streaming in the background do not affect this chat's button.
let currentReader = null;

function stopGeneration() {
    if (currentReader) {
        try { currentReader.cancel(); } catch {}
        currentReader = null;
    }
    if (activeChatId) {
        sendingChats.delete(activeChatId);
        delete chatStreamState[activeChatId];
    }
    renderChatList();
    updateSendButton();
    input.focus();
}

function updateSendButton() {
    const busy = sendingChats.has(activeChatId);
    sendBtn.disabled = false;
    if (busy) {
        sendBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>';
        sendBtn.title = 'Stop';
        sendBtn.onclick = stopGeneration;
        sendBtn.classList.add('stop-mode');
    } else {
        sendBtn.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>';
        sendBtn.title = 'Send';
        sendBtn.onclick = send;
        sendBtn.classList.remove('stop-mode');
    }
}

// ── Folder bar ────────────────────────────────────────────────────────
function renderFolderBar() {
    const chat = activeChat();
    const dirs = chat ? chat.workingDirs : [];
    if (!dirs.length) {
        folderBar.classList.add('hidden');
        return;
    }
    folderBar.classList.remove('hidden');
    folderBar.innerHTML = dirs.map((d) =>
        '<span class="folder-chip-tag" title="' + escHtml(d) + '">' +
            '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>' +
            escHtml(truncatePath(d)) +
            '<button class="folder-remove" data-path="' + escHtml(d) + '" title="Remove folder">x</button>' +
        '</span>'
    ).join('');
    folderBar.querySelectorAll('.folder-remove').forEach(btn => {
        btn.onclick = async (e) => {
            e.stopPropagation();
            const chat = activeChat();
            if (!chat) return;
            const pathToRemove = btn.dataset.path;
            chat.workingDirs = chat.workingDirs.filter(d => d !== pathToRemove);
            await syncWorkingDirs();
            renderFolderBar();
            saveChats();
        };
    });
}

async function syncWorkingDirs() {
    const chat = activeChat();
    const dirs = chat ? chat.workingDirs : [];
    try {
        const resp = await fetch('/working_dirs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ working_dirs: dirs })
        });
        const data = await resp.json();
        if (data.invalid_dirs && data.invalid_dirs.length && chat) {
            const removed = new Set(data.invalid_dirs);
            const before = chat.workingDirs.length;
            chat.workingDirs = chat.workingDirs.filter(d => !removed.has(d));
            if (chat.workingDirs.length !== before) {
                renderFolderBar();
                renderChatList();
                saveChats();
                const notice = document.createElement('div');
                notice.className = 'folder-removed-notice';
                notice.textContent = removed.size + ' folder(s) no longer accessible and were removed.';
                document.getElementById('chat').appendChild(notice);
                setTimeout(() => notice.remove(), 4000);
            }
        }
    } catch {}
}

// ── Chat list sidebar ─────────────────────────────────────────────────
function renderChatList() {
    if (!chats.length) {
        chatList.innerHTML = '<div class="chat-list-empty">no chats yet</div>';
        return;
    }
    chatList.innerHTML = chats.map(c => {
        const isSending = sendingChats.has(c.id);
        return '<div class="chat-item ' + (c.id === activeChatId ? 'active' : '') + '" data-id="' + c.id + '">' +
            '<div class="chat-item-inner">' +
                '<span class="chat-item-title">' + escHtml(c.title) + '</span>' +
                (isSending ? '<span class="chat-item-responding">responding\u2026</span>' : '') +
                (!isSending && c.workingDirs.length ? '<span class="chat-item-dir" title="' + escHtml(c.workingDirs[0]) + '">' + escHtml(truncatePath(c.workingDirs[0])) + '</span>' : '') +
            '</div>' +
        '</div>';
    }).join('');
    chatList.querySelectorAll('.chat-item').forEach(el => {
        el.onclick = () => switchChat(el.dataset.id);
    });
}

async function switchChat(id) {
    if (id === activeChatId) {
        sidebar.classList.add('collapsed');
        return;
    }
    activeChatId = id;
    const chat = activeChat();
    chatTitle.textContent = chat ? chat.title : 'new chat';

    // Tell backend which chat is now active
    await fetch('/switch_chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: id, history: chat ? chat.history : [] })
    });
    await syncWorkingDirs();

    renderChatList();
    renderFolderBar();

    // Render saved history first
    renderHistory();

    // If this chat is still streaming in the background, show the partial response
    if (sendingChats.has(id)) {
        const state = chatStreamState[id];
        const div = createAssistantShell();
        div.dataset.live = id;
        if (state && state.hasContent) {
            div.innerHTML = '<span class="msg-prefix">assistant</span>' + parseMarkdown(state.assistantText) + '<span class="cursor"></span>';
        }
        scrollBottom();
    }

    updateContextBadge();
    updateSendButton();
    saveChats();
    sidebar.classList.add('collapsed');
}

function renderHistory() {
    const chat = activeChat();
    chatEl.innerHTML = '';
    if (!chat || !chat.history.length) {
        updateContextBadge();
        return;
    }
    for (const msg of chat.history) {
        if (msg.role === 'user') {
            addUserMsgStatic(msg.content);
        } else if (msg.role === 'assistant' && (msg.content || msg.reasoning_content)) {
            addAssistantMsgStatic(msg.content, msg.reasoning_content);
        }
    }
    updateContextBadge();
    scrollBottom();
}

// ── Folder picker ─────────────────────────────────────────────────────
folderBtn.onclick = () => {
    const android = androidBridge();
    if (android && android.openFolderPicker) {
        android.openFolderPicker();
    } else {
        const path = prompt('Enter absolute folder path:');
        if (path && path.trim()) addFolder(path.trim());
    }
};

async function addFolder(path) {
    let chat = activeChat();
    if (!chat) {
        chat = createChat();
        activeChatId = chat.id;
    }
    if (!chat.workingDirs.includes(path)) {
        chat.workingDirs.push(path);
    }
    await syncWorkingDirs();
    renderFolderBar();
    renderChatList();
    saveChats();
}

setInterval(async () => {
    const android = androidBridge();
    if (!android || !android.getWorkingDir) return;
    const newPath = android.getWorkingDir();
    if (!newPath) return;
    const chat = activeChat();
    if (chat && !chat.workingDirs.includes(newPath)) {
        await addFolder(newPath);
        if (android.clearWorkingDir) android.clearWorkingDir();
    }
}, 1000);

// ── New chat ──────────────────────────────────────────────────────────
newChatBtn.onclick = async () => {
    const chat = createChat();
    activeChatId = chat.id;
    chatTitle.textContent = chat.title;
    chatEl.innerHTML = '';
    await fetch('/switch_chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: chat.id, history: [] })
    });
    await syncWorkingDirs();
    renderChatList();
    renderFolderBar();
    updateSendButton();
    saveChats();
    input.focus();
    sidebar.classList.add('collapsed');
};

// ── Context badge (in header) ─────────────────────────────────────────
const contextBadge = document.getElementById('context-badge');

function updateContextBadge() {
    if (!contextBadge) return;
    const chat = activeChat();
    const charCount = chat ? chat.history.reduce((n, m) => n + String(m.content || '').length, 0) : 0;
    const tokEst = Math.round(charCount / 4);
    const total = selectedModelCtx;
    contextBadge.textContent = formatCtx(tokEst) + '/' + formatCtx(total);
    const pct = tokEst / total;
    contextBadge.style.color = pct > 0.9 ? 'var(--err, #e05)' : pct > 0.7 ? 'var(--warn, #f90)' : '';
}

// ── Chat menu (three dots) ─────────────────────────────────────────────────────
chatMenuBtn.onclick = (e) => {
    e.stopPropagation();
    chatMenu.classList.toggle('hidden');
};
document.addEventListener('click', () => {
    chatMenu.classList.add('hidden');
    modelDropdown.classList.add('hidden');
    modelBtn.classList.remove('open');
    if (agentDropdown) { agentDropdown.classList.add('hidden'); agentBtn.classList.remove('open'); }
});

renameChatBtn.onclick = (e) => {
    e.stopPropagation();
    chatMenu.classList.add('hidden');
    const chat = activeChat();
    if (!chat) return;
    renameInput.value = chat.title === 'new chat' ? '' : chat.title;
    renameModal.classList.remove('hidden');
    renameInput.focus();
};

renameCancel.onclick = () => renameModal.classList.add('hidden');
renameConfirm.onclick = doRename;
renameInput.onkeydown = (e) => { if (e.key === 'Enter') doRename(); if (e.key === 'Escape') renameModal.classList.add('hidden'); };
renameModal.onclick = (e) => { if (e.target === renameModal) renameModal.classList.add('hidden'); };

function doRename() {
    const val = renameInput.value.trim();
    if (!val) return;
    const chat = activeChat();
    if (!chat) return;
    chat.title = val;
    chatTitle.textContent = val;
    renderChatList();
    saveChats();
    renameModal.classList.add('hidden');
}

deleteChatBtn.onclick = async (e) => {
    e.stopPropagation();
    chatMenu.classList.add('hidden');
    const chat = activeChat();
    if (!chat) return;

    await fetch('/delete_chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: chat.id })
    });

    chats = chats.filter(c => c.id !== chat.id);
    activeChatId = chats.length ? chats[0].id : null;
    await saveChats();
    location.reload();
};

compressChatBtn.onclick = async (e) => {
    e.stopPropagation();
    chatMenu.classList.add('hidden');
    const chat = activeChat();
    if (!chat) return;

    showStatusBanner('✦ Compressing chat…', 'info');

    try {
        const resp = await fetch('/compact', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ chat_id: chat.id, model: selectedModel })
        });
        const data = await resp.json();

        if (data.history) {
            chat.history = data.history;
            await saveChats();
            renderHistory();
            updateContextBadge();
        }

        const parts = [];
        if (data.compacted)
            parts.push('history summarised');

        if (parts.length) {
            showStatusBanner('✓ ' + parts.join(' · '), 'ok');
        } else {
            showStatusBanner('✓ Not enough history to summarise yet', 'info');
        }
    } catch (err) {
        showStatusBanner('⚠ Compression failed: ' + err.message, 'error');
    }
};

// ── Status banner (pencil / compact notifications) ─────────────────────
function showStatusBanner(text, kind = 'info') {
    // Remove any existing banner
    const old = document.getElementById('status-banner');
    if (old) old.remove();

    const el = document.createElement('div');
    el.id = 'status-banner';
    el.className = 'status-banner status-' + kind;
    el.textContent = text;
    chatEl.appendChild(el);
    scrollBottom();
    setTimeout(() => el.remove(), 4000);
}

// ── Sidebar toggle ────────────────────────────────────────────────────
menuBtn.onclick = () => sidebar.classList.toggle('collapsed');

// ── Model selector ────────────────────────────────────────────────────
modelBtn.onclick = (e) => {
    e.stopPropagation();
    const isHidden = modelDropdown.classList.toggle('hidden');
    modelBtn.classList.toggle('open', !isHidden);
};
modelDropdown.querySelectorAll('.model-option').forEach(btn => {
    btn.onclick = (e) => {
        e.stopPropagation();
        selectedModel = btn.dataset.model;
        selectedModelCtx = parseInt(btn.dataset.ctx || '128000', 10);
        modelLabel.textContent = btn.dataset.label;
        modelDropdown.querySelectorAll('.model-option').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        modelDropdown.classList.add('hidden');
        modelBtn.classList.remove('open');
        updateContextBadge();
    };
});

// ── Agent selector ────────────────────────────────────────────────────
const agentBtn      = document.getElementById('agent-btn');
const agentLabel    = document.getElementById('agent-label');
const agentDropdown = document.getElementById('agent-dropdown');

function bindAgentOptions() {
    agentDropdown.querySelectorAll('.agent-option').forEach(btn => {
        btn.onclick = (e) => {
            e.stopPropagation();
            selectedAgent = btn.dataset.agent;
            agentLabel.textContent = selectedAgent;
            agentDropdown.querySelectorAll('.agent-option').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            agentDropdown.classList.add('hidden');
            agentBtn.classList.remove('open');
            showStatusBanner('agent: ' + selectedAgent, 'info');
        };
    });
}

async function loadAgents() {
    try {
        const r = await fetch('/agents');
        const d = await r.json();
        const agents = d.agents || [];

        const labelEl = agentDropdown.querySelector('.model-dropdown-label');
        const labelText = labelEl ? labelEl.textContent : 'Agent Mode';
        agentDropdown.innerHTML = '<div class="model-dropdown-label">' + escHtml(labelText) + '</div>';

        if (!agents.length) {
            // Profiles not loaded yet — show a reload option
            const btn = document.createElement('button');
            btn.className = 'agent-option';
            btn.innerHTML = '<span class="agent-name">No profiles</span><span class="agent-desc">Tap to reload</span>';
            btn.onclick = () => { agentDropdown.classList.add('hidden'); loadAgents(); };
            agentDropdown.appendChild(btn);
            bindAgentOptions();
            return;
        }

        agents.forEach((agent, i) => {
            const btn = document.createElement('button');
            btn.className = 'agent-option' + (agent.id === selectedAgent ? ' active' : '');
            btn.dataset.agent = agent.id;
            btn.innerHTML =
                '<span class="agent-name">' + escHtml(agent.name) + '</span>' +
                '<span class="agent-desc">' + escHtml(agent.description) + '</span>';
            agentDropdown.appendChild(btn);
        });

        const firstMatch = agents.find(a => a.id === selectedAgent) || agents[0];
        selectedAgent = firstMatch.id;
        agentLabel.textContent = firstMatch.name;

        bindAgentOptions();
    } catch {
        bindAgentOptions();
    }
}

agentBtn.onclick = (e) => {
    e.stopPropagation();
    const isHidden = agentDropdown.classList.toggle('hidden');
    agentBtn.classList.toggle('open', !isHidden);
    modelDropdown.classList.add('hidden');
    modelBtn.classList.remove('open');
};

// ── Markdown / HTML helpers ───────────────────────────────────────────
function escHtml(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function buildCodeBlock(lang, code) {
    return '<div class="code-block">' +
        '<div class="code-block-header">' +
        '<span class="code-lang">' + escHtml(lang||'code') + '</span>' +
        '<button class="copy-btn" onclick="copyCode(this)">copy</button>' +
        '</div>' +
        '<pre><code class="lang-' + escHtml(lang) + '">' + escHtml(code.trimEnd()) + '</code></pre>' +
        '</div>';
}

function parseMarkdown(text) {
    if (!text) return '';
    const segments = [];
    const fence = /```(\w*)\n?([\s\S]*?)```/g;
    let last = 0, m;
    while ((m = fence.exec(text)) !== null) {
        if (m.index > last) segments.push({ type:'text', content: text.slice(last, m.index) });
        segments.push({ type:'code', lang: m[1]||'', content: m[2] });
        last = m.index + m[0].length;
    }
    if (last < text.length) segments.push({ type:'text', content: text.slice(last) });
    return segments.map(seg => {
        if (seg.type === 'code') return buildCodeBlock(seg.lang, seg.content);
        let s = escHtml(seg.content);
        s = s.replace(/`([^`\n]+)`/g, '<code>$1</code>');
        s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
        s = s.replace(/__([^_\n]+)__/g, '<strong>$1</strong>');
        s = s.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
        s = s.replace(/(^|[\s>])_([^_\n]+)_(?=[\s<,\.!?;:]|$)/gm, '$1<em>$2</em>');
        s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
        s = s.replace(/^### (.+)$/gm, '<h3>$1</h3>');
        s = s.replace(/^## (.+)$/gm,  '<h2>$1</h2>');
        s = s.replace(/^# (.+)$/gm,   '<h1>$1</h1>');
        s = s.replace(/^[\*\-] (.+)$/gm, '<li>$1</li>');
        s = s.replace(/(<li>.*<\/li>\n?)+/g, function(mm) { return '<ul>' + mm + '</ul>'; });
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

// ── DOM helpers ───────────────────────────────────────────────────────
function scrollBottom() { chatEl.scrollTop = chatEl.scrollHeight; }

function addUserMsgStatic(content) {
    const div = document.createElement('div');
    div.className = 'msg user';
    const inner = document.createElement('div');
    inner.className = 'user-inner';
    inner.textContent = content;
    div.appendChild(inner);
    chatEl.appendChild(div);
}

function addUserMsg(content) {
    addUserMsgStatic(content);
    scrollBottom();
}

function addAssistantMsgStatic(content, reasoning) {
    const div = document.createElement('div');
    div.className = 'msg assistant';
    const prefix = document.createElement('span');
    prefix.className = 'msg-prefix';
    prefix.textContent = 'assistant';
    div.appendChild(prefix);
    if (reasoning) {
        const wrapper = document.createElement('div');
        wrapper.className = 'thinking-wrapper';
        const header = document.createElement('button');
        header.className = 'thinking-header';
        header.innerHTML =
            '<span class="thinking-label">thought process</span>' +
            '<svg class="thinking-chevron" width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"/></svg>';
        const body = document.createElement('div');
        body.className = 'thinking-body open';
        body.textContent = reasoning;
        header.addEventListener('click', () => body.classList.toggle('open'));
        wrapper.appendChild(header);
        wrapper.appendChild(body);
        div.appendChild(wrapper);
    }
    if (content) {
        const contentDiv = document.createElement('div');
        contentDiv.innerHTML = parseMarkdown(content);
        div.appendChild(contentDiv);
    }
    chatEl.appendChild(div);
}

function createAssistantShell() {
    const div = document.createElement('div');
    div.className = 'msg assistant streaming';
    div.innerHTML = '<span class="msg-prefix">assistant</span><span class="cursor"></span>';
    chatEl.appendChild(div);
    scrollBottom();
    return div;
}

function sealAssistant(div, text) {
    div.classList.remove('streaming');
    div.removeAttribute('data-live');
    div.innerHTML = '<span class="msg-prefix">assistant</span>' + parseMarkdown(text);
}

function createThinkingBlock() {
    const wrapper = document.createElement('div');
    wrapper.className = 'thinking-wrapper';
    wrapper.innerHTML =
        '<button class="thinking-header">' +
            '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><circle cx="12" cy="12" r="10"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>' +
            '<span class="thinking-label">thinking\u2026</span>' +
            '<svg class="thinking-chevron" width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"/></svg>' +
        '</button>' +
        '<div class="thinking-body open"></div>';
    chatEl.appendChild(wrapper);
    scrollBottom();
    const header = wrapper.querySelector('.thinking-header');
    const body   = wrapper.querySelector('.thinking-body');
    let open = true;
    header.onclick = () => {
        open = !open;
        body.classList.toggle('open', open);
        wrapper.classList.toggle('collapsed', !open);
    };
    return { wrapper, body, header };
}

function sealThinking(block) {
    block.header.querySelector('.thinking-label').textContent = 'thought process';
}

function createToolGroup() {
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
    return `<svg ${s} stroke-width="2"><circle cx="12" cy="12" r="10"/></svg>`;
}

function _toolPillLabel(name, args) {
    if (name === 'web_search')  return 'searching&nbsp;<em>' + escHtml(args.query||'') + '</em>';
    if (name === 'glob')        return 'finding&nbsp;<em>' + escHtml(args.pattern||'') + '</em>';
    if (name === 'grep')        return 'searching&nbsp;<em>' + escHtml(args.pattern||'') + '</em>';
    if (name === 'read')        return 'reading&nbsp;<em>' + escHtml(args.filePath||'') + '</em>';
    if (name === 'write')       return 'writing&nbsp;<em>' + escHtml(args.filePath||'') + '</em>';
    if (name === 'edit')        return 'editing&nbsp;<em>' + escHtml(args.filePath||'') + '</em>';
    if (name === 'shell')       return 'running&nbsp;<em>' + escHtml(args.command||'') + '</em>';
    if (name === 'web_fetch')   return 'fetching&nbsp;<em>' + escHtml(args.url||'') + '</em>';
    if (name === 'github_walk') return 'github&nbsp;<em>' + escHtml(args.repo||'') + '</em>';
    if (name === 'spawn_agent') return 'spawning&nbsp;<em>' + escHtml(args.agent_id||'agent') + '</em>';
    return 'running&nbsp;<em>' + escHtml(name) + '</em>';
}

function _toolInputSummary(name, args) {
    if (name === 'web_search')  return args.query || '';
    if (name === 'glob')        return (args.pattern||'') + (args.path ? '\nin: ' + args.path : '');
    if (name === 'grep')        return (args.pattern||'') + (args.path ? '\nin: ' + args.path : '') + (args.include ? '\ninclude: ' + args.include : '');
    if (name === 'read')        return (args.filePath||'') + (args.offset != null ? '\noffset: ' + args.offset : '') + (args.limit != null ? '  limit: ' + args.limit : '');
    if (name === 'write')       return (args.filePath||'') + '\n\n' + (args.content||'');
    if (name === 'edit')        return (args.filePath||'') + '\n\n--- old ---\n' + (args.oldString||'') + '\n\n--- new ---\n' + (args.newString||'');
    if (name === 'shell')       return (args.command||'') + (args.cwd ? '\ncwd: ' + args.cwd : '');
    if (name === 'web_fetch')   return args.url || '';
    if (name === 'github_walk') return (args.action||'tree') + '  ' + (args.repo||'') + (args.file_path ? '\n' + args.file_path : '');
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

function createToolPill(name, args, group) {
    const container = group || chatEl;
    const wrapper = document.createElement('div');
    wrapper.className = 'tool-pill-wrapper';

    const div = document.createElement('div');
    div.className = 'tool-pill';
    div.style.cursor = 'pointer';
    div.innerHTML = '<span class="tool-spinner"></span>' + _toolPillIcon(name) + '<span>' + _toolPillLabel(name, args) + '</span>';
    wrapper.appendChild(div);

    let expanded = false;
    let panel = null;

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

function createSubagentPill(agentId, task, context, group) {
    const container = group || chatEl;
    const wrapper = document.createElement('div');
    wrapper.className = 'tool-pill-wrapper';

    const s = 'width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="flex-shrink:0"';
    const icon = `<svg ${s}><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`;
    const div = document.createElement('div');
    div.className = 'tool-pill subagent-pill';
    div.style.cursor = 'pointer';
    div.innerHTML = '<span class="tool-spinner"></span>' + icon + '<span>\u26a1&nbsp;<em>' + escHtml(agentId) + '</em>&nbsp;subagent</span>';
    wrapper.appendChild(div);

    let expanded = false;
    let panel = null;
    let liveBody = null;

    const _ensurePanel = () => {
        if (panel) return;
        panel = document.createElement('div');
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
            liveBody._lastText = null;
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

// ── Auto-title ────────────────────────────────────────────────────────
async function autoTitle(chatId, userMsg) {
    const chat = chats.find(c => c.id === chatId);
    if (!chat || chat.title !== 'new chat') return;
    const words = userMsg.trim().split(/\s+/).slice(0, 6).join(' ');
    chat.title = words.length > 40 ? words.slice(0, 40) + '\u2026' : words;
    if (chatId === activeChatId) chatTitle.textContent = chat.title;
    renderChatList();
    saveChats();
}

// ── Send ──────────────────────────────────────────────────────────────
// Each call to send() spawns an independent async stream for the current
// chat. Multiple chats can stream simultaneously without blocking each other.
async function send() {
    const userMsg = input.value.trim();
    if (!userMsg) return;

    // Prevent double-sending the SAME chat, but allow other chats to send freely
    if (sendingChats.has(activeChatId)) return;

    if (!activeChatId || !activeChat()) {
        const chat = createChat();
        activeChatId = chat.id;
        chatTitle.textContent = chat.title;
        await fetch('/switch_chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ chat_id: chat.id, history: [] })
        });
        await syncWorkingDirs();
        renderChatList();
    }

    // Snapshot the chat ID for this request's entire lifetime.
    // If the user switches chats, sendingChatId stays correct.
    const sendingChatId = activeChatId;
    const isActive = () => activeChatId === sendingChatId;

    input.value = '';
    input.style.height = 'auto';

    // Show user message only if we are still looking at this chat
    if (isActive()) addUserMsg(userMsg);

    autoTitle(sendingChatId, userMsg);

    // Mark this chat as busy
    sendingChats.add(sendingChatId);
    chatStreamState[sendingChatId] = { assistantText: '', hasContent: false };
    updateSendButton();
    renderChatList(); // show "responding..." badge in sidebar

    const chat = chats.find(c => c.id === sendingChatId);

    // Immediately persist user turn so closing the app mid-stream doesn't lose it.
    // history_update at stream end will overwrite this with the authoritative history.
    if (chat) {
        const snapshot = [...(chat.history || []), { id: 'u_pending_' + Date.now(), role: 'user', content: userMsg, _pending: true }];
        chat.history = snapshot;
        saveChats();
        // Keep user message in history during streaming (without _pending flag)
        // so autosave always has at minimum the user turn.
        // history_update at stream end overwrites with the authoritative version.
        chat.history = [...(chat.history || []).filter(t => !t._pending),
                        { id: 'u_' + Date.now(), role: 'user', content: userMsg }];
    }

    let thinkingBlock = null;
    let assistantDiv  = null;
    let toolPill      = null;
    let toolGroup     = null;
    let assistantText = '';
    const activePills = {};
    let loadingDiv    = null;

    if (isActive()) {
        loadingDiv = document.createElement('div');
        loadingDiv.className = 'msg-loading';
        loadingDiv.innerHTML = '<span class="loading-ring"></span>';
        chatEl.appendChild(loadingDiv);
        scrollBottom();
    }

    let keepAliveTimer = setInterval(async () => {
        try { await fetch('/ping', { method: 'GET' }); } catch {}
    }, 20000);

    try {
        const resp = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: userMsg,
                model: selectedModel,
                agent: selectedAgent,
                chat_id: sendingChatId          // tell backend which chat this belongs to
            })
        });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);

        const reader  = resp.body.getReader();
        currentReader = reader;
        const decoder = new TextDecoder();
        let buf = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            const lines = buf.split('\n');
            buf = lines.pop();

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const raw = line.slice(6).trim();
                if (raw === '[DONE]') continue;
                let ev;
                try { ev = JSON.parse(raw); } catch { continue; }

                switch (ev.type) {
                    case 'thinking': {
                        if (!isActive()) break;
                        if (loadingDiv) { loadingDiv.remove(); loadingDiv = null; }
                        if (!thinkingBlock) thinkingBlock = createThinkingBlock();
                        if (!thinkingBlock._indicator) {
                            thinkingBlock._indicator = true;
                            thinkingBlock.header.querySelector('.thinking-label').innerHTML =
                                'thinking <span class="thinking-dots"><span></span><span></span><span></span></span>';
                        }
                        thinkingBlock.body.textContent += ev.text;
                        scrollBottom();
                        break;
                    }
                    case 'text': {
                        assistantText += ev.text;
                        chatStreamState[sendingChatId] = { assistantText, hasContent: true };

                        // Bruteforce: keep chat.history current during streaming.
                        // 500ms autosave will pick this up so partial response is never lost.
                        // history_update at stream end replaces with the authoritative version.
                        if (chat) {
                            const base = (chat.history || []).filter(t => !t._partial);
                            chat.history = [...base, { id: 'a_partial', role: 'assistant', content: assistantText, _partial: true }];
                        }

                        if (!isActive()) break;

                        if (loadingDiv) { loadingDiv.remove(); loadingDiv = null; }
                        if (thinkingBlock) { sealThinking(thinkingBlock); thinkingBlock = null; }
                        if (toolPill) { toolPill.classList.add('done'); toolPill = null; toolGroup = null; }

                        if (!assistantDiv) {
                            // Check if switchChat already planted a live shell for this chat
                            assistantDiv = chatEl.querySelector('[data-live="' + sendingChatId + '"]');
                            if (!assistantDiv) {
                                assistantDiv = createAssistantShell();
                                assistantDiv.dataset.live = sendingChatId;
                            }
                        }
                        assistantDiv.innerHTML = '<span class="msg-prefix">assistant</span>' + parseMarkdown(assistantText) + '<span class="cursor"></span>';
                        scrollBottom();
                        break;
                    }
                    case 'tool_use': {
                        if (!isActive()) break;
                        if (loadingDiv) { loadingDiv.remove(); loadingDiv = null; }
                        if (thinkingBlock) { sealThinking(thinkingBlock); thinkingBlock = null; }
                        if (assistantDiv) { sealAssistant(assistantDiv, assistantText); assistantDiv = null; assistantText = ''; }
                        if (!toolGroup) toolGroup = createToolGroup();
                        const pill = createToolPill(ev.name, ev.args, toolGroup);
                        if (ev.tc_id) activePills[ev.tc_id] = pill;
                        toolPill = pill;
                        break;
                    }
                    case 'subagent_start': {
                        if (!isActive()) break;
                        if (!toolGroup) toolGroup = createToolGroup();
                        const spawnPill = (ev.key && activePills[ev.key]) || toolPill;
                        if (spawnPill) {
                            const sp = spawnPill.querySelector('.tool-spinner');
                            if (sp) sp.outerHTML = '<span class="tool-check">\u2713</span>';
                        }
                        const sPill = createSubagentPill(ev.agent, ev.task||'', ev.context||'', toolGroup);
                        if (ev.key) activePills[ev.key] = sPill;
                        toolPill = sPill;
                        break;
                    }
                    case 'subagent_stream': {
                        if (!isActive()) break;
                        const target = ev.key && activePills[ev.key];
                        if (target && typeof target._liveEvent === 'function') target._liveEvent(ev);
                        break;
                    }
                    case 'subagent_done': {
                        if (!isActive()) break;
                        const dPill = (ev.key && activePills[ev.key]) || toolPill;
                        if (dPill) {
                            if (typeof dPill._setResult === 'function') dPill._setResult(ev.result||'');
                            const sp = dPill.querySelector('.tool-spinner');
                            if (sp) sp.outerHTML = '<span class="tool-check">\u2713</span>';
                            dPill.classList.add('done');
                        }
                        if (ev.key) delete activePills[ev.key];
                        break;
                    }
                    case 'tool_done': {
                        if (!isActive()) break;
                        const tPill = (ev.tc_id && activePills[ev.tc_id]) || toolPill;
                        if (tPill) {
                            if (typeof tPill._setResult === 'function') tPill._setResult(ev.result||'');
                            const spinner = tPill.querySelector('.tool-spinner');
                            if (spinner) spinner.outerHTML = '<span class="tool-check">\u2713</span>';
                        }
                        if (ev.tc_id) delete activePills[ev.tc_id];
                        break;
                    }
                    case 'heartbeat': {
                        break;
                    }

                    case 'history_update': {
                        if (chat) {
                            chat.history = ev.history;
                            saveChats();
                            if (isActive()) updateContextBadge();
                        }
                        break;
                    }
                    case 'error': {
                        if (!isActive()) break;
                        if (thinkingBlock) { sealThinking(thinkingBlock); thinkingBlock = null; }
                        if (!assistantDiv) { assistantDiv = createAssistantShell(); }
                        assistantDiv.classList.remove('streaming');
                        assistantDiv.innerHTML = '<span class="msg-prefix">assistant</span><span class="error-msg">\u26a0 ' + escHtml(ev.text) + '</span>';
                        assistantDiv = null;
                        break;
                    }
                    case 'done': {
                        if (isActive()) {
                            if (thinkingBlock) { sealThinking(thinkingBlock); thinkingBlock = null; }
                            if (assistantDiv)  { sealAssistant(assistantDiv, assistantText); assistantDiv = null; }
                            if (toolPill)      { toolPill.classList.add('done'); toolPill = null; toolGroup = null; }
                            saveChats();
                        }
                        break;
                    }
                }
            }
        }

        // Final seal in case stream ended without a 'done' event
        if (isActive()) {
            if (thinkingBlock) sealThinking(thinkingBlock);
            if (assistantDiv)  sealAssistant(assistantDiv, assistantText);
            if (toolPill)      toolPill.classList.add('done');
        }

    } catch (e) {
        if (isActive()) {
            const d = assistantDiv || createAssistantShell();
            d.classList.remove('streaming');
            d.innerHTML = '<span class="msg-prefix">assistant</span><span class="error-msg">\u26a0 ' + escHtml(e.message) + '</span>';
        }
    }

    clearInterval(keepAliveTimer);
    currentReader = null;

    sendingChats.delete(sendingChatId);
    delete chatStreamState[sendingChatId];

    // Re-render sidebar badge and send button for whatever chat is active now
    renderChatList();
    updateSendButton();

    // If the user is still on this chat, re-focus input
    if (isActive()) input.focus();
}

sendBtn.onclick = send;
input.onkeydown = e => {
    if (e.key === 'Enter') {
        if (e.shiftKey || e.ctrlKey) {
            e.preventDefault();
            send();
        }
    }
};
input.oninput = () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 120) + 'px';
};

// ── Init ──────────────────────────────────────────────────────────────
async function init() {
    await getStorageDir();
    await loadChats();
    await loadAgents();

    if (chats.length && activeChatId) {
        const chat = activeChat();
        if (chat) {
            chatTitle.textContent = chat.title;
            await fetch('/switch_chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ chat_id: chat.id, history: chat.history })
            });
            await syncWorkingDirs();
            renderHistory();
            updateContextBadge();
        }
    } else if (!chats.length) {
        const chat = createChat();
        activeChatId = chat.id;
        saveChats();
    }

    renderChatList();
    renderFolderBar();
    updateSendButton();
    input.focus();
}

init();

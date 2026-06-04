/**
 * app.js — main orchestrator
 *
 * Handles:
 *   - chat lifecycle (create, switch, rename, delete, compress)
 *   - folder picker / working-dir sync
 *   - model & agent selectors
 *   - send / stream loop
 *   - init
 *
 * Pure rendering helpers  → render.js
 * API calls               → api.js
 * Mutable state / DOM refs→ state.js
 */

import {
    sendingChats, chatStreamState,
    selectedModel, selectedModelCtx, selectedAgent,
    setSelectedModel, setSelectedModelCtx, setSelectedAgent,
    formatCtx,
    chats, activeChatId, setChats, setActiveChatId,
    currentReader, setCurrentReader,
    chatEl, input, sendBtn, modelBtn, modelLabel, modelDropdown,
    sidebar, menuBtn, folderBtn, folderBar,
    chatList, chatTitle, newChatBtn,
    chatMenuBtn, chatMenu, renameChatBtn, deleteChatBtn, compressChatBtn,
    renameModal, renameInput, renameCancel, renameConfirm,
    contextBadge, agentBtn, agentLabel, agentDropdown,
} from './state.js';

import {
    getStorageDir, saveChats, loadChats,
    switchChatApi, syncWorkingDirs as _syncWorkingDirs,
    deleteChatApi, compactChatApi, loadAgentsApi, loadModelsApi, pingKeepalive, chatStream,
} from './api.js';

import {
    escHtml, parseMarkdown, scrollBottom, forceScrollBottom,
    addUserMsgStatic, addUserMsg, addAssistantMsgStatic,
    createTurnWrapper, sealTurn,
    createAssistantShell, sealAssistant,
    createActivityBar, addActivityBarStatic,
    addThinkingStatic, addToolGroupStatic, addSubagentStatic,
    showStatusBanner, highlightCodeBlocks,
} from './render.js';


// Auto-save every 500 ms — guarded so it never fires before loadChats() completes,
// which would overwrite the real index with an empty one.
let _appInitialized = false;
setInterval(() => { if (_appInitialized) saveChats(); }, 500);

// Poll for providers every 3 s — only when the model dropdown is closed,
// to avoid visual glitches while the user is browsing it.
setInterval(() => {
    if (modelDropdown.classList.contains('hidden')) {
        const inner = modelDropdown.querySelector('.model-dropdown-inner');
        if (!inner || !inner.querySelector('.model-section')) {
            loadModels();
        }
    }
}, 3000);

// ── Message rail (right-side user message position indicator) ──────────
const _rail = document.getElementById('msg-rail');

function updateMsgRail() {
    if (!_rail) return;
    const msgs = Array.from(chatEl.querySelectorAll('.msg.user'));
    const existing = _rail.querySelectorAll('.rail-pip');
    const delta = msgs.length - existing.length;
    if (delta > 0) {
        for (let i = 0; i < delta; i++) {
            const pip = document.createElement('div');
            pip.className = 'rail-pip';
            _rail.appendChild(pip);
        }
    } else if (delta < 0) {
        for (let i = 0; i < -delta; i++) _rail.firstChild && _rail.removeChild(_rail.firstChild);
    }
    // Wire click targets
    const pips = _rail.querySelectorAll('.rail-pip');
    pips.forEach((pip, i) => {
        pip.onclick = () => msgs[i] && msgs[i].scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
    _updateRailVisibility();
    _updateRailActive();
}

function _updateRailVisibility() {
    if (!_rail) return;
    // Only show the rail when there's actually something to scroll
    const scrollable = chatEl.scrollHeight > chatEl.clientHeight + 20;
    _rail.classList.toggle('visible', scrollable);
}

function _updateRailActive() {
    if (!_rail) return;
    const pips = Array.from(_rail.querySelectorAll('.rail-pip'));
    const msgs = Array.from(chatEl.querySelectorAll('.msg.user'));
    if (!msgs.length || !pips.length) return;

    const scrollTop    = chatEl.scrollTop;
    const scrollHeight = chatEl.scrollHeight;
    const clientHeight = chatEl.clientHeight;
    const atTop        = scrollTop < 8;
    const atBottom     = scrollHeight - scrollTop - clientHeight < 8;

    let best = 0;
    if (atTop) {
        best = 0;
    } else if (atBottom) {
        best = msgs.length - 1;
    } else {
        // Find the last user message whose top edge is above the upper third of the viewport
        const threshold = scrollTop + clientHeight * 0.35;
        for (let i = 0; i < msgs.length; i++) {
            if (msgs[i].offsetTop <= threshold) best = i;
            else break;
        }
    }
    pips.forEach((pip, i) => pip.classList.toggle('active', i === best));

    // Scroll the rail so the active pip stays visible.
    // offsetTop is unreliable inside position:fixed, so calculate manually:
    // each pip is 14px tall + 5px gap = 19px per slot.
    const PIP_SLOT = 19;
    const targetTop = best * PIP_SLOT;
    _rail.scrollTop = targetTop - (_rail.clientHeight / 2) + 7;
}

// Touch-drag on rail to scrub between messages
let _railDrag = false;
let _railLastIdx = -1;
let _railArrowsVisible = false;

// Inject the arrow buttons element next to the rail
const _railArrows = document.createElement('div');
_railArrows.id = 'rail-arrows';
_railArrows.innerHTML =
    '<button class="rail-arrow" id="rail-arrow-up"  aria-label="Previous message">&#8593;</button>' +
    '<button class="rail-arrow" id="rail-arrow-dn"  aria-label="Next message">&#8595;</button>';
document.body.appendChild(_railArrows);

const _railArrowUp = document.getElementById('rail-arrow-up');
const _railArrowDn = document.getElementById('rail-arrow-dn');

const _showRailArrows = () => {
    _railArrowsVisible = true;
    _railArrows.classList.add('visible');
};
const _hideRailArrows = () => {
    _railArrowsVisible = false;
    _railArrows.classList.remove('visible');
};

const _railStepMsg = (dir) => {
    // dir: -1 = up (earlier), +1 = down (later)
    const msgs = Array.from(chatEl.querySelectorAll('.msg.user'));
    if (!msgs.length) return;
    const pips = Array.from(_rail.querySelectorAll('.rail-pip'));
    const activeIdx = pips.findIndex(p => p.classList.contains('active'));
    const next = Math.max(0, Math.min(msgs.length - 1, (activeIdx < 0 ? 0 : activeIdx) + dir));
    const target = msgs[next];
    if (!target) return;
    // Mirror _scrubToIdx: set scrollTop directly — scrollIntoView doesn't
    // work reliably inside a custom overflow container on Android WebView.
    const desired = target.offsetTop - Math.round(chatEl.clientHeight * 0.35);
    chatEl.scrollTop = Math.max(0, desired);
};

_railArrowUp.addEventListener('pointerdown', e => e.stopPropagation());
_railArrowDn.addEventListener('pointerdown', e => e.stopPropagation());
_railArrowUp.addEventListener('click', e => { e.stopPropagation(); _railStepMsg(-1); });
_railArrowDn.addEventListener('click', e => { e.stopPropagation(); _railStepMsg(1); });

// Dismiss when tapping anywhere outside the arrows or rail
document.addEventListener('pointerdown', e => {
    if (!_railArrowsVisible) return;
    if (_railArrows.contains(e.target) || _rail.contains(e.target)) return;
    _hideRailArrows();
}, { capture: true });

if (_rail) {
    const PIP_SLOT = 19; // 14px pip + 5px gap
    let _longPressTimer = null;
    let _downY = 0;
    let _moved = false;

    const _idxFromY = (clientY) => {
        const pips = _rail.querySelectorAll('.rail-pip');
        if (!pips.length) return -1;
        const railRect = _rail.getBoundingClientRect();
        const offsetInRail = (clientY - railRect.top) + _rail.scrollTop;
        return Math.max(0, Math.min(pips.length - 1, Math.round(offsetInRail / PIP_SLOT)));
    };

    const _scrubToIdx = (idx) => {
        const msgs = Array.from(chatEl.querySelectorAll('.msg.user'));
        const target = msgs[idx];
        if (!target) return;
        const desired = target.offsetTop - Math.round(chatEl.clientHeight * 0.35);
        chatEl.scrollTop = Math.max(0, desired);
    };

    _rail.addEventListener('pointerdown', e => {
        if (_railArrowsVisible) { _hideRailArrows(); return; }
        _moved = false;
        _downY = e.clientY;
        _rail.setPointerCapture(e.pointerId);

        // Long-press: hold without moving → show arrows
        _longPressTimer = setTimeout(() => {
            if (!_moved) {
                _railDrag = false;
                _showRailArrows();
            }
        }, 420);
    });

    _rail.addEventListener('pointermove', e => {
        if (Math.abs(e.clientY - _downY) > 6) {
            // Intentional drag — cancel long-press, enter scrub mode
            if (_longPressTimer) { clearTimeout(_longPressTimer); _longPressTimer = null; }
            if (_railArrowsVisible) { _hideRailArrows(); return; }
            _moved = true;
            _railDrag = true;
        }
        if (!_railDrag) return;
        const idx = _idxFromY(e.clientY);
        if (idx === _railLastIdx) return;
        _railLastIdx = idx;
        _scrubToIdx(idx);
    });

    _rail.addEventListener('pointerup', e => {
        if (_longPressTimer) { clearTimeout(_longPressTimer); _longPressTimer = null; }
        if (_railDrag) {
            const idx = _idxFromY(e.clientY);
            const msgs = Array.from(chatEl.querySelectorAll('.msg.user'));
            msgs[idx] && msgs[idx].scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
        _railDrag = false;
        _railLastIdx = -1;
    });

    _rail.addEventListener('pointercancel', () => {
        if (_longPressTimer) { clearTimeout(_longPressTimer); _longPressTimer = null; }
        _railDrag = false;
        _railLastIdx = -1;
    });
}

// Update on scroll
chatEl.addEventListener('scroll', () => { _updateRailActive(); _updateRailVisibility(); }, { passive: true });



function activeChat() {
    return chats.find(c => c.id === activeChatId) || null;
}

function createChat() {
    const id   = 'chat_' + Date.now();
    const chat = { id, title: 'new chat', workingDirs: [], history: [], createdAt: Date.now() };
    chats.unshift(chat);
    return chat;
}

function truncatePath(p) {
    const parts = p.replace(/\\/g, '/').split('/').filter(Boolean);
    if (parts.length <= 3) return p;
    return '.../' + parts.slice(-3).join('/');
}

// ── Send button ────────────────────────────────────────────────────────

function stopGeneration() {
    if (currentReader) {
        try { currentReader.cancel(); } catch {}
        setCurrentReader(null);
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
        sendBtn.title   = 'Stop';
        sendBtn.onclick = stopGeneration;
        sendBtn.classList.add('stop-mode');
    } else {
        sendBtn.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>';
        sendBtn.title   = 'Send';
        sendBtn.onclick = send;
        sendBtn.classList.remove('stop-mode');
    }
}

// ── Folder bar ─────────────────────────────────────────────────────────

const FOLDER_BTN_DEFAULT =
    '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/></svg>' +
    '<span>add folder</span>';

function renderFolderBar() {
    const chat = activeChat();
    const dirs = chat ? chat.workingDirs : [];

    // The old bar is never shown — everything lives in the header button now.
    folderBar.classList.add('hidden');

    if (!dirs.length) {
        folderBtn.innerHTML = FOLDER_BTN_DEFAULT;
        folderBtn.title = 'Add folder';
        folderBtn.style.pointerEvents = '';
        return;
    }

    // Show the first dir inline; ✕ removes it.
    const d = dirs[0];
    folderBtn.innerHTML =
        '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>' +
        '<span class="folder-chip-path" title="' + escHtml(d) + '">' + escHtml(truncatePath(d)) + '</span>' +
        '<span class="folder-chip-remove" title="Remove folder">✕</span>';

    // Clicking the button itself does nothing while a folder is active.
    folderBtn.style.pointerEvents = 'none';

    const removeBtn = folderBtn.querySelector('.folder-chip-remove');
    removeBtn.style.pointerEvents = 'auto';
    removeBtn.onclick = async (e) => {
        e.stopPropagation();
        const c = activeChat();
        if (!c) return;
        c.workingDirs = c.workingDirs.filter(x => x !== d);
        // Tell Android to forget the path so the 1-second poll doesn't re-add it.
        const android = window.Android;
        if (android && android.clearWorkingDir) android.clearWorkingDir();
        await syncWorkingDirs();
        renderFolderBar();
        saveChats();
    };
}

async function syncWorkingDirs() {
    const chat = activeChat();
    await _syncWorkingDirs(chat, (removedCount) => {
        renderFolderBar();
        renderChatList();
        saveChats();
        const notice = document.createElement('div');
        notice.className   = 'folder-removed-notice';
        notice.textContent = removedCount + ' folder(s) no longer accessible and were removed.';
        chatEl.appendChild(notice);
        setTimeout(() => notice.remove(), 4000);
    });
}

// ── Chat list sidebar ──────────────────────────────────────────────────

function renderChatList() {
    if (!chats.length) {
        chatList.innerHTML = '<div class="chat-list-empty">No chats yet.<br>Start one below.</div>';
        return;
    }
    const now  = Date.now();
    const day  = 86400000;
    const groups = { today: [], week: [], older: [] };
    chats.forEach(c => {
        const age = now - (c.updatedAt || c.createdAt || 0);
        if (age < day)          groups.today.push(c);
        else if (age < day * 7) groups.week.push(c);
        else                    groups.older.push(c);
    });

    const makeItem = c => {
        const isSending = sendingChats.has(c.id);
        const active    = c.id === activeChatId ? ' active' : '';
        return '<div class="chat-item' + active + '" data-id="' + c.id + '">' +
            '<div class="chat-item-inner">' +
                '<span class="chat-item-title">' + escHtml(c.title) + '</span>' +
                (isSending ? '<span class="chat-item-responding">responding\u2026</span>' :
                    (c.workingDirs && c.workingDirs.length
                        ? '<span class="chat-item-dir" title="' + escHtml(c.workingDirs[0]) + '">' + escHtml(truncatePath(c.workingDirs[0])) + '</span>'
                        : '')) +
            '</div>' +
        '</div>';
    };

    let html = '';
    if (groups.today.length)  html += '<div class="chat-list-section-label">Today</div>'    + groups.today.map(makeItem).join('');
    if (groups.week.length)   html += '<div class="chat-list-section-label">This week</div>' + groups.week.map(makeItem).join('');
    if (groups.older.length)  html += '<div class="chat-list-section-label">Older</div>'     + groups.older.map(makeItem).join('');

    chatList.innerHTML = html;
    chatList.querySelectorAll('.chat-item').forEach(el => {
        el.onclick = () => switchChat(el.dataset.id);
    });
}

async function switchChat(id) {
    if (id === activeChatId) { sidebar.classList.add('collapsed'); return; }
    setActiveChatId(id);
    const chat = activeChat();
    chatTitle.textContent = chat ? chat.title : 'new chat';

    await switchChatApi(id, chat ? chat.history : [], chat ? chat.apiHistory : null, chat ? chat.compactionSummary : null);
    await syncWorkingDirs();

    renderChatList();
    renderFolderBar();
    renderHistory();

    if (sendingChats.has(id)) {
        const state = chatStreamState[id];
        if (state && state.hasContent && state.segmentText) {
            // uiEvents is fully live so renderHistory() already drew the in-progress
            // assistant text. Find that last assistant div and mark it as the live
            // target so the stream loop keeps updating it in place -- no duplicate shell.
            const allAssistants = chatEl.querySelectorAll('.msg.assistant');
            const lastDiv = allAssistants[allAssistants.length - 1];
            if (lastDiv) {
                lastDiv.dataset.live = id;
                lastDiv.innerHTML = '<span class="reply-marker">&gt;</span>' +
                    parseMarkdown(state.segmentText) + '<span class="cursor"></span>';
                highlightCodeBlocks(lastDiv);
            }
            // If no assistant div yet (only tools running), stream loop creates shell naturally.
        }
        forceScrollBottom();
    }

    updateContextBadge();
    updateSendButton();
    saveChats();
    sidebar.classList.add('collapsed');
}

function renderHistory() {
    const chat = activeChat();
    chatEl.innerHTML = '';
    if (!chat) { updateContextBadge(); return; }
    const events = chat.uiEvents && chat.uiEvents.length ? chat.uiEvents : null;
    if (events) {
        // During live streaming, thinking + tools share one activity bar (one pill).
        // On replay we must batch consecutive thinking/tool_group/subagent events
        // into a single addActivityBarStatic call so they render as one pill, not many.
        let pendingSteps = [];

        const flushActivity = () => {
            if (pendingSteps.length > 0) {
                addActivityBarStatic(pendingSteps);
                pendingSteps = [];
            }
        };

        for (const ev of events) {
            if (ev.type === 'user') {
                flushActivity();
                addUserMsgStatic(ev.content);
            } else if (ev.type === 'thinking') {
                pendingSteps.push({ name: '__thought__', args: {}, thoughtText: ev.text });
            } else if (ev.type === 'tool_group') {
                (ev.tools || []).forEach(t =>
                    pendingSteps.push({ name: t.name, args: t.args || {}, result: t.result ?? null })
                );
            } else if (ev.type === 'subagent') {
                pendingSteps.push({ name: 'spawn_agent', args: { agent_id: ev.agentId, task: ev.task || '', context: ev.context || '' }, result: ev.result ?? null });
            } else if (ev.type === 'assistant') {
                flushActivity();
                addAssistantMsgStatic(ev.content, ev.reasoning || null);
            } else if (ev.type === 'error') {
                flushActivity();
                const errDiv = document.createElement('div');
                errDiv.className = 'msg assistant';
                errDiv.innerHTML = '<span class="error-msg">⚠ ' + escHtml(ev.text) + '</span>';
                chatEl.appendChild(errDiv);
            }
        }
        flushActivity(); // flush any trailing activity (e.g. response still in progress)
    } else if (chat.history && chat.history.length) {
        for (const msg of chat.history) {
            if (msg.role === 'user') {
                addUserMsgStatic(msg.content);
            } else if (msg.role === 'assistant' && (msg.content || msg.reasoning_content)) {
                addAssistantMsgStatic(msg.content, msg.reasoning_content);
            }
        }
    }
    updateContextBadge();
    forceScrollBottom();
    updateMsgRail();
}

folderBtn.onclick = () => {
    const android = window.Android;
    if (android && android.openFolderPicker) {
        android.openFolderPicker();
    } else {
        const path = prompt('Enter absolute folder path:');
        if (path && path.trim()) addFolder(path.trim());
    }
};

async function addFolder(path) {
    let chat = activeChat();
    if (!chat) { chat = createChat(); setActiveChatId(chat.id); }
    if (!chat.workingDirs.includes(path)) chat.workingDirs.push(path);
    await syncWorkingDirs();
    renderFolderBar();
    renderChatList();
    saveChats();
}

// Android calls this after folder picker resolves.
// Must be on window so evaluateJavascript("setWorkingDir(...)") finds it.
window.setWorkingDir = (path) => {
    if (path && path.trim()) addFolder(path.trim());
};

setInterval(async () => {
    const android = window.Android;
    if (!android || !android.getWorkingDir) return;
    const newPath = android.getWorkingDir();
    if (!newPath) return;
    // Clear immediately so the next tick doesn't re-add after removal.
    if (android.clearWorkingDir) android.clearWorkingDir();
    const chat = activeChat();
    if (chat && !chat.workingDirs.includes(newPath)) {
        await addFolder(newPath);
    }
}, 1000);

// ── New chat ───────────────────────────────────────────────────────────

newChatBtn.onclick = async () => {
    const chat = createChat();
    setActiveChatId(chat.id);
    chatTitle.textContent = chat.title;
    chatEl.innerHTML = '';
    if (_rail) _rail.innerHTML = '';
    await switchChatApi(chat.id, [], null, null);
    await syncWorkingDirs();
    renderChatList();
    renderFolderBar();
    updateContextBadge();
    updateSendButton();
    saveChats();
    input.focus();
    sidebar.classList.add('collapsed');
};

// ── Context badge ──────────────────────────────────────────────────────

function updateContextBadge() {
    if (!contextBadge) return;
    const chat = activeChat();
    // Use apiHistory (actual payload sent to model) when available — it reflects
    // compaction and is the true measure of context consumption. Fall back to the
    // display history for new/uncompacted chats.
    const source = (chat && chat.apiHistory && chat.apiHistory.length)
        ? chat.apiHistory
        : (chat ? chat.history : []);
    const charCount = source.reduce((n, m) => {
        let len = String(m.content || '').length;
        // Count tool_calls argument strings too
        if (m.tool_calls) {
            for (const tc of m.tool_calls) {
                len += String(tc?.function?.arguments || '').length;
            }
        }
        return n + len;
    }, 0);
    const tokEst = Math.round(charCount / 4);
    const total  = selectedModelCtx;
    contextBadge.textContent = formatCtx(tokEst) + '/' + formatCtx(total);
    const pct = tokEst / total;
    contextBadge.style.color = pct > 0.9 ? 'var(--err, #e05)' : pct > 0.7 ? 'var(--warn, #f90)' : '';
}

// ── Chat menu ──────────────────────────────────────────────────────────

chatMenuBtn.onclick = (e) => { e.stopPropagation(); chatMenu.classList.toggle('hidden'); };

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

renameCancel.onclick  = () => renameModal.classList.add('hidden');
renameConfirm.onclick = doRename;
renameInput.onkeydown = (e) => {
    if (e.key === 'Enter')  doRename();
    if (e.key === 'Escape') renameModal.classList.add('hidden');
};
renameModal.onclick = (e) => { if (e.target === renameModal) renameModal.classList.add('hidden'); };

function doRename() {
    const val  = renameInput.value.trim();
    if (!val)  return;
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
    await deleteChatApi(chat.id);
    setChats(chats.filter(c => c.id !== chat.id));
    setActiveChatId(chats.length ? chats[0].id : null);
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
        const data = await compactChatApi(chat.id, selectedModel);
        if (data.history) {
            chat.history    = data.history;      // display history (full)
            if (data.api_history)        chat.apiHistory          = data.api_history;
            if (data.summary)            chat.compactionSummary   = data.summary;
            chat.uiEvents = [];
            await saveChats();
            renderHistory();
            updateContextBadge();
        }
        if (data.compacted) {
            showStatusBanner('✓ Context compacted', 'ok');
        } else if (data.status === 'error') {
            showStatusBanner('⚠ Compaction failed: ' + (data.message || 'unknown error'), 'error');
        } else {
            showStatusBanner('✓ Context compacted', 'ok');
        }
    } catch (err) {
        showStatusBanner('⚠ Compression failed: ' + err.message, 'error');
    }
};

// ── Sidebar toggle ─────────────────────────────────────────────────────

menuBtn.onclick = () => sidebar.classList.toggle('collapsed');

// ── Model selector ─────────────────────────────────────────────────────

function bindModelSectionToggle() {
    document.querySelectorAll('.model-section-header').forEach(header => {
        header.onclick = () => {
            const section = header.parentElement;
            const body = section.querySelector('.model-section-body');
            const chevron = header.querySelector('.section-chevron');
            const isOpen = body.classList.toggle('open');
            chevron.classList.toggle('closed', !isOpen);
        };
    });
}

function bindModelOptions() {
    modelDropdown.querySelectorAll('.model-option').forEach(btn => {
        btn.onclick = (e) => {
            e.stopPropagation();
            setSelectedModel(btn.dataset.model);
            setSelectedModelCtx(parseInt(btn.dataset.ctx || '128000', 10));
            modelLabel.textContent = btn.dataset.label;
            modelDropdown.querySelectorAll('.model-option').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            modelDropdown.classList.add('hidden');
            modelBtn.classList.remove('open');
            updateContextBadge();
        };
    });
}

const _PROVIDER_META = {
    free:   { label: 'Free',   icon: '<circle cx="12" cy="12" r="10"/><path d="M12 8v4l3 3"/>',                         open: true  },
    ollama: { label: 'Ollama', icon: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',    open: false },
    gemini: { label: 'Gemini', icon: '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>', open: false },
    qwen:   { label: 'Qwen',   icon: '<circle cx="12" cy="12" r="10"/><path d="M8 12h8M12 8v8"/>',                      open: false },
};
const _PROVIDER_META_DEFAULT = { label: null, icon: '<circle cx="12" cy="12" r="5"/>', open: false };

function _buildProviderSection(provider, models) {
    const meta   = _PROVIDER_META[provider] || _PROVIDER_META_DEFAULT;
    const label  = meta.label || (provider.charAt(0).toUpperCase() + provider.slice(1));
    const isOpen = meta.open;
    const chevronClass = isOpen ? 'section-chevron' : 'section-chevron closed';
    const bodyClass    = isOpen ? 'model-section-body open' : 'model-section-body';

    const section = document.createElement('div');
    section.className = 'model-section';
    section.dataset.provider = provider;

    const header = document.createElement('div');
    header.className = 'model-section-header';
    header.innerHTML =
        `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">${meta.icon}</svg>` +
        `<span>${escHtml(label)}</span>` +
        `<svg class="${chevronClass}" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"/></svg>`;

    const body = document.createElement('div');
    body.className = bodyClass;

    if (!models.length) {
        body.innerHTML = '<div class="model-section-empty">No models</div>';
    } else {
        models.forEach(model => {
            const btn = document.createElement('button');
            btn.className = 'model-option' + (model.id === selectedModel ? ' active' : '');
            btn.dataset.model    = model.id;
            btn.dataset.label    = model.label;
            btn.dataset.ctx      = model.ctx;
            if (model.script_id) btn.dataset.scriptId = model.script_id;
            btn.textContent    = model.label;
            body.appendChild(btn);
        });
    }

    section.appendChild(header);
    section.appendChild(body);
    return section;
}

async function loadModels() {
    try {
        const data  = await loadModelsApi();
        const inner = modelDropdown.querySelector('.model-dropdown-inner');

        inner.querySelectorAll('.model-section').forEach(s => s.remove());

        const order    = Object.keys(_PROVIDER_META);
        const returned = Object.keys(data);
        const sorted   = [
            ...order.filter(p => returned.includes(p)),
            ...returned.filter(p => !order.includes(p)),
        ];

        sorted.forEach(provider => {
            inner.appendChild(_buildProviderSection(provider, data[provider] || []));
        });

        const freeModels   = data.free || [];
        const bigPickle    = freeModels.find(m => m.id === 'big-pickle');
        const defaultModel = bigPickle || freeModels[0] || null;
        if (defaultModel && !selectedModel) {
            setSelectedModel(defaultModel.id);
            setSelectedModelCtx(defaultModel.ctx);
            modelLabel.textContent = defaultModel.label;
        }

        bindModelSectionToggle();
        bindModelOptions();
    } catch {
        bindModelSectionToggle();
        bindModelOptions();
    }
}

modelBtn.onclick = (e) => {
    e.stopPropagation();
    const isHidden = modelDropdown.classList.toggle('hidden');
    modelBtn.classList.toggle('open', !isHidden);
};

// Clicking inside dropdown should not close it
modelDropdown.onclick = (e) => e.stopPropagation();

document.addEventListener('click', (e) => {
    // Don't close if click is inside model dropdown or agent dropdown
    if (modelDropdown.contains(e.target) || (agentDropdown && agentDropdown.contains(e.target))) return;
    chatMenu.classList.add('hidden');
    modelDropdown.classList.add('hidden');
    modelBtn.classList.remove('open');
    if (agentDropdown) { agentDropdown.classList.add('hidden'); agentBtn.classList.remove('open'); }
});

// ── Agent selector ─────────────────────────────────────────────────────

function bindAgentOptions() {
    agentDropdown.querySelectorAll('.agent-option').forEach(btn => {
        btn.onclick = (e) => {
            e.stopPropagation();
            setSelectedAgent(btn.dataset.agent);
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
        const agents = await loadAgentsApi();
        const labelEl   = agentDropdown.querySelector('.model-dropdown-label');
        const labelText = labelEl ? labelEl.textContent : 'Agent Mode';
        agentDropdown.innerHTML = '<div class="model-dropdown-label">' + escHtml(labelText) + '</div>';

        if (!agents.length) {
            const btn = document.createElement('button');
            btn.className = 'agent-option';
            btn.innerHTML = '<span class="agent-name">No profiles</span><span class="agent-desc">Tap to reload</span>';
            btn.onclick = () => { agentDropdown.classList.add('hidden'); loadAgents(); };
            agentDropdown.appendChild(btn);
            bindAgentOptions();
            return;
        }

        agents.forEach(agent => {
            const btn = document.createElement('button');
            btn.className    = 'agent-option' + (agent.id === selectedAgent ? ' active' : '');
            btn.dataset.agent = agent.id;
            btn.innerHTML    =
                '<span class="agent-name">' + escHtml(agent.name) + '</span>' +
                '<span class="agent-desc">' + escHtml(agent.description) + '</span>';
            agentDropdown.appendChild(btn);
        });

        const firstMatch = agents.find(a => a.id === selectedAgent) || agents[0];
        setSelectedAgent(firstMatch.id);
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

// ── Auto-title ─────────────────────────────────────────────────────────

async function autoTitle(chatId, userMsg) {
    const chat = chats.find(c => c.id === chatId);
    if (!chat || chat.title !== 'new chat') return;
    const words = userMsg.trim().split(/\s+/).slice(0, 6).join(' ');
    chat.title  = words.length > 40 ? words.slice(0, 40) + '\u2026' : words;
    if (chatId === activeChatId) chatTitle.textContent = chat.title;
    renderChatList();
    saveChats();
}

// ── Send / stream ──────────────────────────────────────────────────────

async function send() {
    const userMsg = input.value.trim();
    if (!userMsg) return;
    if (sendingChats.has(activeChatId)) return;

    if (!activeChatId || !activeChat()) {
        const chat = createChat();
        setActiveChatId(chat.id);
        chatTitle.textContent = chat.title;
        await switchChatApi(chat.id, []);
        await syncWorkingDirs();
        renderChatList();
    } else {
        const chat = activeChat();
        if (chat) await switchChatApi(chat.id, chat.history || []);
    }

    const sendingChatId = activeChatId;
    const isActive      = () => activeChatId === sendingChatId;

    input.value = '';
    input.style.height = 'auto';
    if (isActive()) addUserMsg(userMsg);
    if (isActive()) updateMsgRail();
    autoTitle(sendingChatId, userMsg);

    sendingChats.add(sendingChatId);
    chatStreamState[sendingChatId] = { assistantText: '', hasContent: false };
    updateSendButton();
    renderChatList();

    const chat = chats.find(c => c.id === sendingChatId);
    if (chat) {
        const snapshot = [...(chat.history || []), { id: 'u_pending_' + Date.now(), role: 'user', content: userMsg, _pending: true }];
        chat.history   = snapshot;
        saveChats();
        chat.history   = [...(chat.history || []).filter(t => !t._pending),
                          { id: 'u_' + Date.now(), role: 'user', content: userMsg }];
    }

    // ── uiEvents: initialise and record user turn ──────────────────────
    if (chat) {
        if (!chat.uiEvents) chat.uiEvents = [];
        chat.uiEvents.push({ type: 'user', content: userMsg });
    }
    // Live event objects — pushed to uiEvents immediately and mutated in place.
    // This keeps uiEvents authoritative at all times so renderHistory() is always
    // the source of truth when switching chats mid-stream.
    let _uiLiveThink = null, _uiLiveText = null, _uiGroup = null, _uiSub = null;
    const _uiToolMap = {}, _uiSubMap = {};
    const _flushThink = () => { _uiLiveThink = null; };
    const _flushText  = () => { _uiLiveText  = null; };
    const _flushGroup = () => { _uiGroup = null; };
    const _flushSub   = () => { _uiSub   = null; };
    // ──────────────────────────────────────────────────────────────────

    let turnDiv       = null;
    let actBar        = null;   // unified activity bar
    let assistantDiv  = null;
    let assistantText = '';
    let segmentText   = '';
    const activePills = {};
    let loadingDiv    = null;

    if (isActive()) {
        loadingDiv = document.createElement('div');
        loadingDiv.className = 'msg-loading';
        loadingDiv.innerHTML = '<span class="loading-ring"></span>';
        chatEl.appendChild(loadingDiv);
        forceScrollBottom();
    }

    const keepAliveTimer = setInterval(() => pingKeepalive(), 20000);

    try {
        const resp = await chatStream(userMsg, selectedModel, selectedAgent, sendingChatId);
        if (!resp.ok) throw new Error('HTTP ' + resp.status);

        const reader  = resp.body.getReader();
        setCurrentReader(reader);
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

                const getTurn   = () => { if (!turnDiv) turnDiv = createTurnWrapper(); return turnDiv; };
                const getActBar = () => { if (!actBar)  actBar  = createActivityBar(getTurn());  return actBar;  };
                switch (ev.type) {
                    case 'compaction': {
                        // Auto-compaction fired — save the summary and compacted api history
                        if (chat) {
                            if (ev.summary)     chat.compactionSummary = ev.summary;
                            if (ev.api_history) chat.apiHistory        = ev.api_history;
                            saveChats();
                        }
                        break;
                    }
                    case 'thinking': {
                        if (!_uiLiveThink) { _uiLiveThink = { type: 'thinking', text: '' }; if (chat) chat.uiEvents.push(_uiLiveThink); }
                        _uiLiveThink.text += ev.text;
                        if (!isActive()) break;
                        if (loadingDiv) { loadingDiv.remove(); loadingDiv = null; }
                        if (assistantDiv) { sealAssistant(assistantDiv, segmentText); assistantDiv = null; segmentText = ''; }
                        getActBar().addThought(ev.text);
                        scrollBottom();
                        break;
                    }
                    case 'text': {
                        _flushThink(); _flushGroup(); _flushSub();
                        if (!_uiLiveText) { _uiLiveText = { type: 'assistant', content: '' }; if (chat) chat.uiEvents.push(_uiLiveText); }
                        _uiLiveText.content += ev.text;
                        assistantText += ev.text;
                        segmentText   += ev.text;
                        chatStreamState[sendingChatId] = { assistantText, hasContent: true, segmentText };
                        if (chat) {
                            const base = (chat.history || []).filter(t => !t._partial);
                            chat.history = [...base, { id: 'a_partial', role: 'assistant', content: assistantText, _partial: true }];
                        }
                        if (!isActive()) break;
                        if (loadingDiv) { loadingDiv.remove(); loadingDiv = null; }
                        if (actBar) { actBar.seal(); actBar = null; }   // freeze activity bar when text starts
                        if (!assistantDiv) {
                            assistantDiv = chatEl.querySelector('[data-live="' + sendingChatId + '"]');
                            if (!assistantDiv) {
                                assistantDiv = createAssistantShell(getTurn());
                                assistantDiv.dataset.live = sendingChatId;
                            }
                        }
                        assistantDiv.innerHTML = '<span class="reply-marker">&gt;</span>' + parseMarkdown(segmentText) + '<span class="cursor"></span>';
                        highlightCodeBlocks(assistantDiv);
                        scrollBottom();
                        break;
                    }
                    case 'tool_use': {
                        _flushThink(); _flushText(); _flushSub();
                        if (!_uiGroup) { _uiGroup = { type: 'tool_group', tools: [] }; if (chat) chat.uiEvents.push(_uiGroup); }
                        const _toolEntry = { name: ev.name, args: ev.args || {}, result: null };
                        _uiGroup.tools.push(_toolEntry);
                        if (ev.tc_id) _uiToolMap[ev.tc_id] = _toolEntry;
                        if (!isActive()) break;
                        if (loadingDiv) { loadingDiv.remove(); loadingDiv = null; }
                        if (assistantDiv) { sealAssistant(assistantDiv, segmentText); assistantDiv = null; segmentText = ''; }
                        if (ev.name !== 'spawn_agent') {
                            const _toolStep = getActBar().addTool(ev.name, ev.args || {});
                            if (ev.tc_id) _uiToolMap[ev.tc_id]._actStep = _toolStep;
                        }
                        break;
                    }
                    case 'subagent_start': {
                        _flushThink(); _flushText(); _flushGroup();
                        const _subEntry = { type: 'subagent', agentId: ev.agent, task: ev.task || '', context: ev.context || '', result: '' };
                        _uiSub = _subEntry;
                        if (chat) chat.uiEvents.push(_uiSub);
                        if (ev.key) _uiSubMap[ev.key] = _subEntry;
                        if (!isActive()) break;
                        if (loadingDiv) { loadingDiv.remove(); loadingDiv = null; }
                        const _subStep = getActBar().addTool('spawn_agent', { agent_id: ev.agent, task: ev.task || '', context: ev.context || '' });
                        if (ev.key) _uiSubMap[ev.key]._actStep = _subStep;
                        if (ev.key && _uiToolMap[ev.key]) _uiToolMap[ev.key]._actStep = _subStep;
                        break;
                    }
                    case 'subagent_stream': {
                        if (ev.subtype === 'text' && ev.data) {
                            const _sr = ev.key && _uiSubMap[ev.key];
                            if (_sr && _sr._actStep && actBar) {
                                actBar.updateSubagentStream(_sr._actStep, ev.data);
                            }
                        }
                        break;
                    }
                    case 'subagent_done': {
                        const _sr = (ev.key && _uiSubMap[ev.key]) || _uiSub;
                        if (_sr) {
                            _sr.result = ev.result || '';
                            if (_sr._actStep && actBar) actBar.setToolResult(_sr._actStep, ev.result || '');
                        }
                        _flushSub();
                        if (ev.key) delete _uiSubMap[ev.key];
                        break;
                    }
                    case 'tool_done': {
                        const _tr = ev.tc_id && _uiToolMap[ev.tc_id];
                        if (_tr) {
                            _tr.result = ev.result || '';
                            if (_tr._actStep && actBar) actBar.setToolResult(_tr._actStep, ev.result || '');
                        }
                        if (ev.tc_id) delete _uiToolMap[ev.tc_id];
                        break;
                    }
                    case 'heartbeat': break;
                    case 'history_update': {
                        if (chat) {
                            // ev.history = full display history (never compacted)
                            // ev.api_history = compacted version for the API
                            chat.history    = ev.history;
                            if (ev.api_history !== undefined) chat.apiHistory = ev.api_history;
                            saveChats();
                            if (isActive()) updateContextBadge();
                        }
                        break;
                    }
                    case 'error': {
                        _flushThink(); _flushText(); _flushGroup(); _flushSub();
                        if (chat) chat.uiEvents.push({ type: 'error', text: ev.text });
                        if (!isActive()) break;
                        if (actBar) { actBar.seal(); actBar = null; }
                        if (!assistantDiv) assistantDiv = createAssistantShell(getTurn());
                        assistantDiv.classList.remove('streaming');
                        assistantDiv.innerHTML = '<span class="error-msg">\u26a0 ' + escHtml(ev.text) + '</span>';
                        assistantDiv = null;
                        break;
                    }
                    case 'done': {
                        _flushThink(); _flushText(); _flushGroup(); _flushSub();
                        if (chat) saveChats();
                        if (isActive()) {
                            if (actBar)       { actBar.seal(); actBar = null; }
                            if (assistantDiv) { sealAssistant(assistantDiv, segmentText); assistantDiv = null; }
                            if (turnDiv)      { sealTurn(turnDiv); turnDiv = null; }
                        }
                        break;
                    }
                }
            }
        }

        if (isActive()) {
            if (actBar)       { actBar.seal(); actBar = null; }
            if (assistantDiv) sealAssistant(assistantDiv, segmentText);
        }

    } catch (e) {
        if (isActive()) {
            const d = assistantDiv || createAssistantShell();
            d.classList.remove('streaming');
            d.innerHTML = '<span class="error-msg">\u26a0 ' + escHtml(e.message) + '</span>';
        }
    }

    clearInterval(keepAliveTimer);
    setCurrentReader(null);
    sendingChats.delete(sendingChatId);
    delete chatStreamState[sendingChatId];
    renderChatList();
    updateSendButton();
    if (isActive()) input.focus();
}

// ── Settings / Themes ──────────────────────────────────────────────────

const THEMES = [
    { id: 'default', label: 'Default',  colors: ['#0a0a0a', '#8b4fe0', '#4a9eff', '#1e1e1e'] },
    { id: 'midnight', label: 'Midnight', colors: ['#080810', '#7c6af4', '#9888ff', '#222244'] },
    { id: 'mocha',   label: 'Mocha',    colors: ['#0f0c09', '#d4845a', '#e8a07a', '#2e2520'] },
    { id: 'forest',  label: 'Forest',   colors: ['#080e09', '#4caf6a', '#6bc882', '#1e2a20'] },
    { id: 'ocean',   label: 'Ocean',    colors: ['#080c10', '#22c2d4', '#44d8e8', '#1c2836'] },
    { id: 'rose',    label: 'Rose',     colors: ['#100a0c', '#f06080', '#f888a0', '#2c1820'] },
    { id: 'dusk',    label: 'Dusk',     colors: ['#120e18', '#b06af4', '#cc88ff', '#282040'] },
    { id: 'slate',   label: 'Slate',    colors: ['#0d1117', '#58a6ff', '#79c0ff', '#2d333b'] },
    { id: 'nord',    label: 'Nord',     colors: ['#2e3440', '#88c0d0', '#8fbcbb', '#434c5e'] },
    { id: 'latte',   label: 'Latte',    colors: ['#e8e0d4', '#b06030', '#d07840', '#bfb4a4'] },
    { id: 'stone',   label: 'Stone',    colors: ['#d8dde4', '#2860b0', '#3878d0', '#b0bac8'] },
    { id: 'clay',    label: 'Clay',     colors: ['#ddd0c8', '#a03820', '#c05030', '#b2a098'] },
    { id: 'light',   label: 'Light',    colors: ['#f5f5f7', '#4a6ef0', '#6888ff', '#d0d0d8'] },
    { id: 'mint',    label: 'Mint',     colors: ['#f2faf6', '#18a86a', '#30c880', '#b8dacc'] },
    { id: 'cashew',  label: 'Cashew',   colors: ['#faf7f0', '#c87820', '#e89030', '#d4cab0'] },
];

// Light-background themes that need inverted rendering
const LIGHT_THEMES = new Set(['light', 'mint', 'cashew', 'latte', 'stone', 'clay']);

let _activeTheme = localStorage.getItem('lc_theme') || 'default';
let _previewTheme = null;

function applyTheme(id) {
    if (id === 'default') {
        document.documentElement.removeAttribute('data-theme');
    } else {
        document.documentElement.setAttribute('data-theme', id);
    }
}

function setTheme(id) {
    _activeTheme = id;
    _previewTheme = null;
    localStorage.setItem('lc_theme', id);
    applyTheme(id);
    renderThemeCards();
    // Re-init mermaid so next render uses correct light/dark theme
    if (typeof window._mermaidReinit === 'function') window._mermaidReinit();
}

function previewTheme(id) {
    _previewTheme = id;
    applyTheme(id);
}

function endPreview() {
    _previewTheme = null;
    applyTheme(_activeTheme);
}

function renderThemeCards() {
    const grid = document.getElementById('themes-grid');
    if (!grid) return;
    grid.innerHTML = '';
    for (const t of THEMES) {
        const card = document.createElement('div');
        card.className = 'theme-card' + (t.id === _activeTheme ? ' active' : '');
        card.innerHTML = `
            <div class="theme-swatch" style="background:${t.colors[0]}">
                <div class="swatch-bar" style="background:${t.colors[1]}"></div>
                <div class="swatch-bar" style="background:${t.colors[2]}"></div>
                <div class="swatch-bar" style="background:${t.colors[3]}"></div>
            </div>
            <div class="theme-label">
                <span>${t.label}</span>
                <button class="theme-eye" title="Preview">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                </button>
            </div>`;
        card.querySelector('.theme-eye').addEventListener('mouseenter', () => previewTheme(t.id));
        card.querySelector('.theme-eye').addEventListener('mouseleave', () => endPreview());
        card.querySelector('.theme-eye').addEventListener('click', e => { e.stopPropagation(); setTheme(t.id); });
        card.addEventListener('click', () => setTheme(t.id));
        grid.appendChild(card);
    }
}

const _settingsClose = document.getElementById('settings-close');
const _userSettingsBtn = document.getElementById('user-settings-btn');

const _settingsOverlay = document.getElementById('settings-sheet-overlay');

const _themesToggle = document.getElementById('themes-toggle');
const _themesGrid   = document.getElementById('themes-grid');

_themesToggle.addEventListener('click', () => {
    const isOpen = _themesToggle.classList.contains('open');
    if (isOpen) {
        _themesToggle.classList.remove('open');
        _themesGrid.classList.add('hidden');
    } else {
        renderThemeCards();
        _themesToggle.classList.add('open');
        _themesGrid.classList.remove('hidden');
    }
});

function openSettings() {
    _settingsOverlay.classList.remove('hidden');
    requestAnimationFrame(() => _settingsOverlay.classList.add('open'));
}

function closeSettings() {
    endPreview();
    _settingsOverlay.classList.add('closing');
    _settingsOverlay.classList.remove('open');
    setTimeout(() => {
        _settingsOverlay.classList.remove('closing');
        _settingsOverlay.classList.add('hidden');
    }, 300);
}

_userSettingsBtn.addEventListener('click', openSettings);
_settingsClose.addEventListener('click', closeSettings);
_settingsOverlay.addEventListener('click', e => { if (e.target === _settingsOverlay) closeSettings(); });

// ── UI Scale ────────────────────────────────────────────────────────────

const _scaleSlider = document.getElementById('scale-slider');
const _scaleValue  = document.getElementById('scale-value');

function applyScale(v) {
    document.documentElement.style.zoom = v + '%';
}

let _uiScale = parseInt(localStorage.getItem('lc_scale') || '100', 10);
_uiScale = Math.min(150, Math.max(50, _uiScale));
_scaleSlider.value = _uiScale;
_scaleValue.textContent = _uiScale + '%';
applyScale(_uiScale);

_scaleSlider.addEventListener('input', () => {
    _uiScale = parseInt(_scaleSlider.value, 10);
    _scaleValue.textContent = _uiScale + '%';
    localStorage.setItem('lc_scale', _uiScale);
    applyScale(_uiScale);
});

// Apply saved theme on load
applyTheme(_activeTheme);

sendBtn.onclick = send;
input.onkeydown = e => {
    if (e.key === 'Enter' && (e.shiftKey || e.ctrlKey)) {
        e.preventDefault();
        send();
    }
};
input.oninput = () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 120) + 'px';
};

// ── Init ───────────────────────────────────────────────────────────────

async function init() {
    await getStorageDir();
    await loadChats();

    // On a fresh install (or reinstall), storage_dir.txt is wiped and the
    // Android side may not have rewritten it before the webview fires this
    // init.  If loadChats returned nothing, wait briefly and retry — giving
    // the native layer time to recreate storage_dir.txt and expose the real
    // chat directory.  We try up to 3 times with increasing back-off so
    // genuine "first ever launch" (truly no chats) still resolves quickly.
    if (!chats.length) {
        const delays = [400, 800, 1500];
        for (const ms of delays) {
            await new Promise(r => setTimeout(r, ms));
            await getStorageDir();
            await loadChats();
            if (chats.length) break;
        }
    }

    _appInitialized = true; // ungate auto-save — index is now populated
    // Ensure every loaded chat has its own workingDirs array (backend may omit it).
    chats.forEach(c => { if (!Array.isArray(c.workingDirs)) c.workingDirs = []; });
    await loadAgents();
    await loadModels();

    if (chats.length && activeChatId) {
        const chat = activeChat();
        if (chat) {
            chatTitle.textContent = chat.title;
            await switchChatApi(chat.id, chat.history, chat.apiHistory || null, chat.compactionSummary || null);
            await syncWorkingDirs();
            renderHistory();
            updateContextBadge();
        }
    } else if (!chats.length) {
        const chat = createChat();
        setActiveChatId(chat.id);
        saveChats();
    }

    renderChatList();
    renderFolderBar();
    updateSendButton();
    input.focus();
}

init();

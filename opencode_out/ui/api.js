// ── API helpers — every fetch call in one place ────────────────────────

import { chats, activeChatId, setChats, setActiveChatId } from './state.js';

export let storageDir = '';

export async function getStorageDir() {
    try {
        const r   = await fetch('/storage_dir');
        const d   = await r.json();
        storageDir = d.path || '';
    } catch {}
}

export async function saveChats() {
    try {
        await fetch('/save_chats', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ chats, activeChatId }),
        });
    } catch {}
}

export async function loadChats() {
    try {
        const r = await fetch('/load_chats');
        const d = await r.json();
        setChats(d.chats || []);
        setActiveChatId(d.activeChatId || null);
    } catch {
        setChats([]);
        setActiveChatId(null);
    }
}

export async function switchChatApi(chatId, history) {
    await fetch('/switch_chat', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ chat_id: chatId, history: history || [] }),
    });
}

export async function syncWorkingDirs(chat, onRemoved) {
    const dirs = chat ? chat.workingDirs : [];
    try {
        const resp = await fetch('/working_dirs', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ working_dirs: dirs }),
        });
        const data = await resp.json();
        if (data.invalid_dirs && data.invalid_dirs.length && chat) {
            const removed = new Set(data.invalid_dirs);
            const before  = chat.workingDirs.length;
            chat.workingDirs = chat.workingDirs.filter(d => !removed.has(d));
            if (chat.workingDirs.length !== before && onRemoved) {
                onRemoved(removed.size);
            }
        }
    } catch {}
}

export async function deleteChatApi(chatId) {
    await fetch('/delete_chat', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ chat_id: chatId }),
    });
}

export async function compactChatApi(chatId, model) {
    const resp = await fetch('/compact', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ chat_id: chatId, model }),
    });
    return resp.json();
}

export async function loadAgentsApi() {
    const r = await fetch('/agents');
    const d = await r.json();
    return d.agents || [];
}

export function pingKeepalive() {
    return fetch('/ping', { method: 'GET' }).catch(() => {});
}

export function chatStream(userMsg, model, agent, chatId) {
    return fetch('/chat', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ message: userMsg, model, agent, chat_id: chatId }),
    });
}

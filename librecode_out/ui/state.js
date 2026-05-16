// ── Per-chat sending state ─────────────────────────────────────────────
// Each chat can be independently streaming without blocking others.
export const sendingChats   = new Set();   // set of chat IDs currently awaiting a response
export const chatStreamState = {};          // chatId -> { assistantText, hasContent }

export let selectedModel    = 'minimax-m2.5-free';
export let selectedModelCtx = 1000000;
export let selectedAgent    = 'build';  // build | plan | explore | ask

export function setSelectedModel(m)    { selectedModel    = m; }
export function setSelectedModelCtx(c) { selectedModelCtx = c; }
export function setSelectedAgent(a)    { selectedAgent    = a; }

export function formatCtx(n) {
    if (n >= 1000000) return (n / 1000000).toFixed(0) + 'M';
    if (n >= 1000)    return Math.round(n / 1000) + 'k';
    return String(n);
}

// Each chat: { id, title, workingDirs: [], history: [], createdAt }
export let chats        = [];
export let activeChatId = null;

export function setChats(c)        { chats        = c; }
export function setActiveChatId(id) { activeChatId = id; }

export let currentReader = null;
export function setCurrentReader(r) { currentReader = r; }

// ── DOM refs ───────────────────────────────────────────────────────────
export const chatEl          = document.getElementById('chat');
export const input           = document.getElementById('input');
export const sendBtn         = document.getElementById('send');
export const modelBtn        = document.getElementById('model-btn');
export const modelLabel      = document.getElementById('model-label');
export const modelDropdown   = document.getElementById('model-dropdown');
export const sidebar         = document.getElementById('sidebar');
export const menuBtn         = document.getElementById('menu-btn');
export const folderBtn       = document.getElementById('folder-btn');
export const folderBar       = document.getElementById('folder-bar');
export const chatList        = document.getElementById('chat-list');
export const chatTitle       = document.getElementById('chat-title');
export const newChatBtn      = document.getElementById('new-chat-btn');
export const chatMenuBtn     = document.getElementById('chat-menu-btn');
export const chatMenu        = document.getElementById('chat-menu');
export const renameChatBtn   = document.getElementById('rename-chat-btn');
export const deleteChatBtn   = document.getElementById('delete-chat-btn');
export const compressChatBtn = document.getElementById('compress-chat-btn');
export const renameModal     = document.getElementById('rename-modal');
export const renameInput     = document.getElementById('rename-input');
export const renameCancel    = document.getElementById('rename-cancel');
export const renameConfirm   = document.getElementById('rename-confirm');
export const contextBadge    = document.getElementById('context-badge');
export const agentBtn        = document.getElementById('agent-btn');
export const agentLabel      = document.getElementById('agent-label');
export const agentDropdown   = document.getElementById('agent-dropdown');

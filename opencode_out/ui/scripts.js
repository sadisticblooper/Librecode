/**
 * scripts.js — DSL Scripts manager UI
 *
 * Provides:
 *   initScripts()  — call once on app start
 */

let _currentScriptId = null;
let _sessions = {};

// ── API helpers ───────────────────────────────────────────────────────────────

async function fetchScripts() {
    const r = await fetch("/mediator/scripts");
    return (await r.json()).scripts || [];
}

async function fetchScript(id) {
    const r = await fetch(`/mediator/scripts/${id}`);
    return r.json();
}

async function saveScript(id, content) {
    await fetch(`/mediator/scripts/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
    });
}

async function fetchSessions() {
    const r = await fetch("/mediator/sessions");
    return (await r.json()).sessions || [];
}

async function startSession(id) {
    await fetch(`/mediator/start/${id}`, { method: "POST" });
}

async function stopSession(id) {
    await fetch(`/mediator/stop/${id}`, { method: "POST" });
}

async function createScript(id, label, url) {
    const r = await fetch(`/mediator/scripts/${id}/create`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label, url, port: 11440 }),
    });
    return r.json();
}

// ── Render ─────────────────────────────────────────────────────────────────────

function renderList(scripts) {
    const list = document.getElementById("scripts-list");
    list.innerHTML = "";
    scripts.forEach(s => {
        const item = document.createElement("div");
        item.className = "scripts-list-item" + (s.id === _currentScriptId ? " active" : "");
        item.innerHTML = `<div class="item-label">${s.label}</div><div class="item-id">${s.id}</div>`;
        item.addEventListener("click", () => selectScript(s.id, scripts));
        list.appendChild(item);
    });
}

async function selectScript(id, scripts) {
    _currentScriptId = id;

    // Update active state in list
    document.querySelectorAll(".scripts-list-item").forEach(el => {
        el.classList.toggle("active", el.querySelector(".item-id")?.textContent === id);
    });

    const [scriptData, sessions] = await Promise.all([
        fetchScript(id),
        fetchSessions(),
    ]);

    const session = sessions.find(s => s.id === id);
    const isLoaded = session?.loaded;

    document.getElementById("scripts-editor-empty").classList.add("hidden");
    const form = document.getElementById("scripts-editor-form");
    form.classList.remove("hidden");

    const manifest = scriptData.manifest || {};
    document.getElementById("script-label-display").textContent = manifest.label || id;
    document.getElementById("script-url-display").textContent = manifest.url || "";
    document.getElementById("script-content").value = scriptData.content || "";

    const statusEl = document.getElementById("script-session-status");
    statusEl.textContent = isLoaded ? "● active" : "○ inactive";
    statusEl.className = "session-status " + (isLoaded ? "active" : "inactive");

    document.getElementById("script-save-status").textContent = "";
}

// ── Init ───────────────────────────────────────────────────────────────────────

export function initScripts() {
    const btn       = document.getElementById("scripts-btn");
    const modal     = document.getElementById("scripts-modal");
    const closeBtn  = document.getElementById("scripts-close-btn");
    const saveBtn   = document.getElementById("script-save-btn");
    const startBtn  = document.getElementById("script-start-btn");
    const stopBtn   = document.getElementById("script-stop-btn");
    const newBtn    = document.getElementById("new-script-btn");
    const newModal  = document.getElementById("new-script-modal");
    const newCancel = document.getElementById("new-script-cancel");
    const newConfirm= document.getElementById("new-script-confirm");

    let _scripts = [];

    async function openModal() {
        modal.classList.remove("hidden");
        _scripts = await fetchScripts();
        renderList(_scripts);

        // Show scripts dir hint
        try {
            const r = await fetch("/mediator/scripts");
            const data = await r.json();
            const hint = document.getElementById("scripts-dir-hint");
            if (hint && data.scripts_dir) hint.textContent = data.scripts_dir;
        } catch {}
    }

    btn.addEventListener("click", openModal);
    closeBtn.addEventListener("click", () => modal.classList.add("hidden"));
    modal.addEventListener("click", e => { if (e.target === modal) modal.classList.add("hidden"); });

    // Save script
    saveBtn.addEventListener("click", async () => {
        if (!_currentScriptId) return;
        const content = document.getElementById("script-content").value;
        await saveScript(_currentScriptId, content);
        const status = document.getElementById("script-save-status");
        status.textContent = "saved ✓";
        setTimeout(() => status.textContent = "", 2000);
    });

    // Start / stop session
    startBtn.addEventListener("click", async () => {
        if (!_currentScriptId) return;
        await startSession(_currentScriptId);
        await selectScript(_currentScriptId, _scripts);
    });

    stopBtn.addEventListener("click", async () => {
        if (!_currentScriptId) return;
        await stopSession(_currentScriptId);
        await selectScript(_currentScriptId, _scripts);
    });

    // New script
    newBtn.addEventListener("click", () => {
        document.getElementById("new-script-id").value    = "";
        document.getElementById("new-script-label").value = "";
        document.getElementById("new-script-url").value   = "";
        newModal.classList.remove("hidden");
    });
    newCancel.addEventListener("click", () => newModal.classList.add("hidden"));
    newModal.addEventListener("click", e => { if (e.target === newModal) newModal.classList.add("hidden"); });

    newConfirm.addEventListener("click", async () => {
        const id    = document.getElementById("new-script-id").value.trim().replace(/\s+/g, "_");
        const label = document.getElementById("new-script-label").value.trim();
        const url   = document.getElementById("new-script-url").value.trim();
        if (!id) return;
        await createScript(id, label || id, url);
        newModal.classList.add("hidden");
        _scripts = await fetchScripts();
        renderList(_scripts);
        selectScript(id, _scripts);
    });
}

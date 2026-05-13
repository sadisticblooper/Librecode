const LOAD_URL = "https://chatgpt.com";

// ── Persistent state ──────────────────────────────────────────────────────────
window._ocApiState = {
  conduitToken:       null,
  requirementsToken:  null,
  tokenExpiry:        0,      // ms epoch; refresh when past this
  deviceId:           null,
  sessionId:          null,
  conversationId:     null,
  parentMessageId:    null,
};

// Written by the async stream loop; read by _ocGetSnapshot
window._ocApiResponse = {
  reply:      "",
  generating: false,
  error:      null,
};

// ── Tiny helpers ──────────────────────────────────────────────────────────────
function _genUUID() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0;
    return (c === "x" ? r : (r & 0x3 | 0x8)).toString(16);
  });
}

function _getCookie(name) {
  const m = document.cookie.match(new RegExp("(?:^| )" + name + "=([^;]+)"));
  return m ? decodeURIComponent(m[1]) : null;
}

// Minimal proof token — base64 bundle of timing + UA + screen info.
// ChatGPT validates shape, not exact values, for anon sessions.
function _makeProofToken() {
  const payload = JSON.stringify([
    Math.floor(Date.now() / 1000).toString(),
    navigator.userAgent,
    screen.width + "," + screen.height,
    (Math.random() * 1e15).toString(36),
    performance.now().toFixed(3),
  ]);
  return btoa(payload);
}

// Base headers shared across all auth + conversation requests
function _baseHeaders() {
  const s = window._ocApiState;
  return {
    "OAI-Device-Id":           s.deviceId,
    "OAI-Session-Id":          s.sessionId,
    "OAI-Client-Version":      "prod-767c16cfce2fbcbdd1ae079fcf0b43838ff1b3ed",
    "OAI-Client-Build-Number": "6549031",
    "OAI-Language":            navigator.language || "en-US",
  };
}

// ── Auth flow ─────────────────────────────────────────────────────────────────
// 3-step: prepare (conduit) → sentinel prepare → sentinel finalize (token)
// Cookies from the loaded WebView are sent automatically via credentials:'include'
async function _refreshAuth() {
  const s = window._ocApiState;

  // Grab device ID from cookie / localStorage, or generate once per session
  if (!s.deviceId) {
    s.deviceId = _getCookie("oai-did")
      || localStorage.getItem("oai-did")
      || _genUUID();
  }
  s.sessionId = _genUUID(); // fresh each auth round

  const hdrs = _baseHeaders();

  // Step 1 — conduit token
  const r1 = await fetch("https://chatgpt.com/backend-anon/f/conversation/prepare", {
    headers: hdrs,
    credentials: "include",
  });
  if (!r1.ok) throw new Error("conduit prepare failed " + r1.status);
  const d1 = await r1.json();
  s.conduitToken = d1.conduit_token;
  if (!s.conduitToken) throw new Error("conduit_token missing in prepare response");

  // Step 2 — sentinel prepare (must be called; response not needed)
  const r2 = await fetch("https://chatgpt.com/backend-anon/sentinel/chat-requirements/prepare", {
    headers: hdrs,
    credentials: "include",
  });
  if (!r2.ok) throw new Error("sentinel prepare failed " + r2.status);

  // Step 3 — sentinel finalize → requirements token
  const r3 = await fetch("https://chatgpt.com/backend-anon/sentinel/chat-requirements/finalize", {
    headers: hdrs,
    credentials: "include",
  });
  if (!r3.ok) throw new Error("sentinel finalize failed " + r3.status);
  const d3 = await r3.json();
  s.requirementsToken = d3.token;
  if (!s.requirementsToken) throw new Error("token missing in finalize response");

  // expire_after is in seconds; back off 30s to be safe
  const ttl = (d3.expire_after || 480) - 30;
  s.tokenExpiry = Date.now() + ttl * 1000;
}

async function _ensureAuth() {
  if (!window._ocApiState.requirementsToken || Date.now() >= window._ocApiState.tokenExpiry) {
    await _refreshAuth();
  }
}

// ── onLoad ────────────────────────────────────────────────────────────────────
// Wait for the page to fully load (so cookies/Cloudflare are set), dismiss
// any modal, then warm up auth tokens for the first send.
async function onLoad() {
  // Wait for the page to be interactive (prompt textarea appears)
  await $oc.waitFor("#prompt-textarea", 30000);
  await $oc.sleep(800); // let Cloudflare + cookie writes settle

  // Dismiss cookie/terms modal if present
  var modal = document.querySelector('div[role="dialog"]');
  if (modal) {
    var btns = modal.querySelectorAll("button");
    for (var i = 0; i < btns.length; i++) {
      if (/accept|agree/i.test(btns[i].innerText)) { btns[i].click(); break; }
    }
    await $oc.sleep(300);
  }

  // Pre-fetch auth tokens so the first send() has zero auth delay
  try {
    await _refreshAuth();
  } catch (e) {
    // Non-fatal here; _ensureAuth() will retry on first send
    console.warn("[chatgpt-api] onLoad auth warm-up failed:", e.message);
  }
}

// ── onSend ────────────────────────────────────────────────────────────────────
// Sends the message via direct API call. Returns immediately after firing the
// fetch — Python polls _ocGetSnapshot() for streaming deltas, same as before.
async function onSend(input) {
  const resp = window._ocApiResponse;
  resp.reply      = "";
  resp.generating = true;
  resp.error      = null;

  // Ensure valid tokens (throws on hard failure)
  await _ensureAuth();

  const s = window._ocApiState;

  const requestHeaders = Object.assign(_baseHeaders(), {
    "Content-Type":    "application/json",
    "Accept":          "text/event-stream",
    "x-conduit-token": s.conduitToken,
    "x-oai-turn-trace-id":                       _genUUID(),
    "OpenAI-Sentinel-Chat-Requirements-Token":    s.requirementsToken,
    "OpenAI-Sentinel-Proof-Token":                _makeProofToken(),
  });

  const body = {
    action: "next",
    messages: [{
      id:          _genUUID(),
      author:      { role: "user" },
      create_time: Date.now() / 1000,
      content:     { content_type: "text", parts: [input] },
      metadata: {
        selected_github_repos:      [],
        selected_all_github_repos:  false,
        serialization_metadata:     { custom_symbol_offsets: [] },
      },
    }],
    conversation_id:    s.conversationId  || null,
    parent_message_id:  s.parentMessageId || null,
    model:              "auto",
    timezone_offset_min: -new Date().getTimezoneOffset(),
    timezone:           Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    conversation_mode:  { kind: "primary_assistant" },
    enable_message_followups: true,
    system_hints:       [],
    supports_buffering: true,
    supported_encodings: ["v1"],
    client_contextual_info: {
      is_dark_mode:   window.matchMedia("(prefers-color-scheme: dark)").matches,
      time_since_loaded: Math.round(performance.now()),
      page_height:    window.innerHeight,
      page_width:     window.innerWidth,
      pixel_ratio:    window.devicePixelRatio || 1,
      screen_height:  screen.height,
      screen_width:   screen.width,
      app_name:       "chatgpt.com",
    },
    no_auth_ad_preferences: {
      personalization_enabled: true,
      history_enabled:         true,
      bazaar_consent_set:      false,
    },
    paragen_cot_summary_display_override: "allow",
    force_parallel_switch: "auto",
  };

  // ── Fire-and-forget stream loop ─────────────────────────────────────────────
  // Runs asynchronously so onSend() returns immediately.
  // Python polls _ocGetSnapshot() for deltas, exactly as with DOM streaming.
  (async () => {
    try {
      const res = await fetch("https://chatgpt.com/backend-anon/f/conversation", {
        method:      "POST",
        headers:     requestHeaders,
        body:        JSON.stringify(body),
        credentials: "include",
      });

      if (!res.ok) {
        const errText = await res.text().catch(() => "");
        throw new Error("HTTP " + res.status + ": " + errText.slice(0, 300));
      }

      const reader  = res.body.getReader();
      const decoder = new TextDecoder();
      let   buf     = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buf += decoder.decode(value, { stream: true });

        // Process all complete SSE lines; keep trailing partial line in buf
        const lines = buf.split("\n");
        buf = lines.pop();

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6).trim();
          if (!raw || raw === "[DONE]") continue;

          let evt;
          try { evt = JSON.parse(raw); } catch (_) { continue; }

          // Track conversation ID for multi-turn continuity
          if (evt.conversation_id) s.conversationId = evt.conversation_id;

          const msg = evt.message;
          if (!msg || msg.author?.role !== "assistant") continue;

          // Accumulate text parts into resp.reply on every delta
          const parts = msg.content?.parts;
          if (parts) {
            resp.reply = parts.filter(p => typeof p === "string").join("");
          }

          // On final message, capture the parent ID for the next turn
          if (msg.status === "finished_successfully" || msg.end_turn === true) {
            if (msg.id) s.parentMessageId = msg.id;
          }
        }
      }

    } catch (e) {
      resp.error = e.message;
    } finally {
      resp.generating = false;
      // Invalidate tokens after each turn — they're single-use effectively
      s.tokenExpiry = 0;
    }
  })();

  // Return immediately; Python will stream via _ocGetSnapshot
}

// ── _ocGetSnapshot ────────────────────────────────────────────────────────────
// Called repeatedly by Python during streaming. Same shape as before.
window._ocGetSnapshot = function () {
  const r = window._ocApiResponse;
  return JSON.stringify({
    thinking:   null,
    reply:      r.reply     || null,
    generating: r.generating,
    error:      r.error     || null,
  });
};

// ── onRead ────────────────────────────────────────────────────────────────────
// Non-streaming fallback: block until done, then return full reply.
async function onRead() {
  var deadline = Date.now() + 120000;
  while (Date.now() < deadline) {
    if (!window._ocApiResponse.generating) break;
    await $oc.sleep(200);
  }
  var snap = JSON.parse(window._ocGetSnapshot());
  if (snap.error) throw new Error(snap.error);
  return snap.reply || "";
}

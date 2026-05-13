const LOAD_URL = "https://chatgpt.com";

var _preSendCount = 0;

async function onLoad() {
  await $oc.waitFor("id:prompt-textarea", 30000);
  await $oc.sleep(500);
  var modal = document.querySelector('div[role="dialog"]');
  if (modal) {
    var btns = modal.querySelectorAll("button");
    for (var i = 0; i < btns.length; i++) {
      if (/accept|agree/i.test(btns[i].innerText)) { btns[i].click(); break; }
    }
  }
}

async function onSend(input) {
  // ── Snapshot count BEFORE send ────────────────────────────────────────────
  _preSendCount = document.querySelectorAll('[data-message-author-role="assistant"]').length;

  // ── Fill textarea ─────────────────────────────────────────────────────────
  var textarea = (function findTextarea() {
    var el = document.querySelector('#prompt-textarea');
    if (el) return el;
    var hosts = document.querySelectorAll('*');
    for (var i = 0; i < hosts.length; i++) {
      var sr = hosts[i].shadowRoot;
      if (sr) { el = sr.querySelector('#prompt-textarea'); if (el) return el; }
    }
    return null;
  })();
  if (!textarea) throw new Error("Textarea not found");
  textarea.focus();
  document.execCommand('insertText', false, input);

  // ── Wait for send button, click it ───────────────────────────────────────
  var sendBtn = null;
  var btnDeadline = Date.now() + 3000;
  while (Date.now() < btnDeadline) {
    var candidate = document.querySelector('#composer-submit-button')
      || document.querySelector('button[data-testid="send-button"]')
      || document.querySelector('button[aria-label="Submit"]')
      || document.querySelector('button[aria-label="Send"]')
      || document.querySelector('button[aria-label="Send prompt"]')
      || document.querySelector('button[aria-label="Send message"]');
    if (candidate && !candidate.disabled) { sendBtn = candidate; break; }
    await $oc.sleep(80);
  }
  if (!sendBtn) throw new Error("Send button not found or still disabled after 3s");
  sendBtn.click();

  // ── RETURN IMMEDIATELY after click ───────────────────────────────────────
  // DO NOT wait for the response here. Python will stream via _ocGetSnapshot().
  // Just close any modal that might have appeared.
  var closeBtn = document.querySelector("[aria-label='Close']");
  if (closeBtn) closeBtn.click();
}

// ── Snapshot helper — called repeatedly by Python during streaming ────────────
// Returns JSON string: { thinking, reply, generating }
window._ocGetSnapshot = function() {
  // --- Thinking block ---
  var thinkingEl = null;
  var thinkingSelectors = [
    '[data-testid="thinking-block"]',
    '[data-testid="reasoning-block"]',
    '[class*="thinking-block"]',
  ];
  for (var i = 0; i < thinkingSelectors.length; i++) {
    thinkingEl = document.querySelector(thinkingSelectors[i]);
    if (thinkingEl) break;
  }
  if (!thinkingEl) {
    // Text-based: <details> summary saying "Thought for..."
    var summaries = document.querySelectorAll('details > summary');
    for (var j = 0; j < summaries.length; j++) {
      if (/thought for|thinking|reasoning/i.test(summaries[j].innerText)) {
        thinkingEl = summaries[j].closest('details');
        break;
      }
    }
  }

  var thinkingText = null;
  if (thinkingEl) {
    if (thinkingEl.tagName === 'DETAILS' && !thinkingEl.open) thinkingEl.open = true;
    var content = thinkingEl.querySelector('[data-testid="thinking-block-content"]')
      || thinkingEl.querySelector('[class*="thinking-content"]')
      || thinkingEl;
    thinkingText = (content.innerText || '').trim() || null;
  }

  // --- Reply text ---
  var assistants = document.querySelectorAll('[data-message-author-role="assistant"]');
  var replyText = null;
  if (assistants.length > _preSendCount) {
    replyText = (assistants[assistants.length - 1].innerText || '').trim() || null;
  }

  // --- Still generating? (stop button visible) ---
  var stopSelectors = [
    'button[aria-label="Stop streaming"]',
    'button[data-testid="stop-button"]',
    'button[aria-label="Stop generating"]',
    '[aria-label="Stop streaming"]',
  ];
  var generating = false;
  for (var k = 0; k < stopSelectors.length; k++) {
    if (document.querySelector(stopSelectors[k])) { generating = true; break; }
  }

  return JSON.stringify({ thinking: thinkingText, reply: replyText, generating: generating });
};

async function onRead() {
  // Non-streaming fallback only. Wait for generation then return combined text.
  function findStopBtn() {
    var ss = ['button[aria-label="Stop streaming"]','button[data-testid="stop-button"]',
              'button[aria-label="Stop generating"]','[aria-label="Stop streaming"]'];
    for (var i = 0; i < ss.length; i++) { var el = document.querySelector(ss[i]); if (el) return el; }
    return null;
  }
  var appeared = false;
  var d1 = Date.now() + 8000;
  while (Date.now() < d1) { if (findStopBtn()) { appeared = true; break; } await $oc.sleep(150); }
  if (appeared) {
    var d2 = Date.now() + 120000;
    while (Date.now() < d2) { if (!findStopBtn()) break; await $oc.sleep(200); }
    await $oc.sleep(300);
  } else {
    await $oc.waitStable('[data-message-author-role="assistant"]', 1500, 30000);
  }
  var snap = JSON.parse(window._ocGetSnapshot());
  var parts = [];
  if (snap.thinking) parts.push("[Thinking]\n" + snap.thinking);
  if (snap.reply)    parts.push(snap.reply);
  return parts.join("\n\n---\n\n") || $oc.extractLast('[data-message-author-role="assistant"]');
}

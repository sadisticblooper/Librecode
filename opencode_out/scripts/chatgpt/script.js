const LOAD_URL = "https://chatgpt.com";

// Snapshot taken BEFORE inserting text — clean baseline for onSend/onRead.
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
  // ── 1. Snapshot BEFORE anything touches the DOM ──────────────────────────
  _preSendCount = document.querySelectorAll('[data-message-author-role="assistant"]').length;

  // ── 2. Find & fill textarea ───────────────────────────────────────────────
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

  // ── 3. Poll for send button enabled ──────────────────────────────────────
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

  // ── 4. Return as soon as NEW assistant element OR thinking block appears ──
  //    KEY CHANGE: return early so Python stream_read() can poll live tokens.
  var waitDeadline = Date.now() + 60000;
  while (Date.now() < waitDeadline) {
    var newAssistant = document.querySelectorAll('[data-message-author-role="assistant"]').length > _preSendCount;
    var thinkingVisible = !!_findThinkingBlock();
    if (newAssistant || thinkingVisible) break;
    await $oc.sleep(100);
  }

  var closeBtn = document.querySelector("[aria-label='Close']");
  if (closeBtn) closeBtn.click();
}

// ── Thinking block detection ─────────────────────────────────────────────────
// ChatGPT renders reasoning in a collapsible block. Selectors checked in order.
function _findThinkingBlock() {
  var selectors = [
    '[data-testid="thinking-block"]',
    '[data-testid="reasoning-block"]',
    '.group\\/thinking',
    '[class*="thinking-block"]',
  ];
  for (var i = 0; i < selectors.length; i++) {
    var el = document.querySelector(selectors[i]);
    if (el) return el;
  }
  // Text-based fallback: <details> whose summary says "Thought for N seconds"
  var summaries = document.querySelectorAll('details > summary');
  for (var j = 0; j < summaries.length; j++) {
    if (/thought for|thinking|reasoning/i.test(summaries[j].innerText)) {
      return summaries[j].closest('details');
    }
  }
  return null;
}

// ── Snapshot helper called by Python during stream polling ───────────────────
// Returns JSON: { thinking: string|null, reply: string|null, generating: bool }
window._ocGetSnapshot = function() {
  var thinkingEl = _findThinkingBlock();
  var thinkingText = null;
  if (thinkingEl) {
    if (thinkingEl.tagName === 'DETAILS' && !thinkingEl.open) {
      thinkingEl.open = true;  // expand so innerText is populated
    }
    // Prefer a dedicated content child; fall back to the whole block
    var content = thinkingEl.querySelector('[data-testid="thinking-block-content"]')
      || thinkingEl.querySelector('[class*="thinking-content"]')
      || thinkingEl;
    thinkingText = (content.innerText || "").trim() || null;
  }

  var assistants = document.querySelectorAll('[data-message-author-role="assistant"]');
  var replyText = null;
  if (assistants.length > _preSendCount) {
    replyText = (assistants[assistants.length - 1].innerText || "").trim() || null;
  }

  // Check stop-button to know if still generating
  var stopSelectors = [
    'button[aria-label="Stop streaming"]',
    'button[data-testid="stop-button"]',
    'button[aria-label="Stop generating"]',
    '[aria-label="Stop streaming"]',
  ];
  var generating = false;
  for (var i = 0; i < stopSelectors.length; i++) {
    if (document.querySelector(stopSelectors[i])) { generating = true; break; }
  }

  return JSON.stringify({ thinking: thinkingText, reply: replyText, generating: generating });
};

async function onRead() {
  // Non-streaming fallback: wait for generation to finish, return combined text.
  var stopSelectors = [
    'button[aria-label="Stop streaming"]',
    'button[data-testid="stop-button"]',
    'button[aria-label="Stop generating"]',
    '[aria-label="Stop streaming"]',
  ];
  function findStopBtn() {
    for (var i = 0; i < stopSelectors.length; i++) {
      var el = document.querySelector(stopSelectors[i]);
      if (el) return el;
    }
    return null;
  }

  var appeared = false;
  var stopAppearDeadline = Date.now() + 8000;
  while (Date.now() < stopAppearDeadline) {
    if (findStopBtn()) { appeared = true; break; }
    await $oc.sleep(150);
  }
  if (appeared) {
    var stopGoneDeadline = Date.now() + 120000;
    while (Date.now() < stopGoneDeadline) {
      if (!findStopBtn()) break;
      await $oc.sleep(200);
    }
    await $oc.sleep(300);
  } else {
    await $oc.waitStable('[data-message-author-role="assistant"]', 1500, 30000);
  }

  var snapshot = JSON.parse(window._ocGetSnapshot());
  var parts = [];
  if (snapshot.thinking) parts.push("[Thinking]\n" + snapshot.thinking);
  if (snapshot.reply)    parts.push(snapshot.reply);
  return parts.join("\n\n---\n\n") || $oc.extractLast('[data-message-author-role="assistant"]');
}

const LOAD_URL = "https://chatgpt.com";

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
  // Find textarea — walk shadow DOM
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

  // Insert text with React-compatible input event
  if (textarea.tagName === 'TEXTAREA') {
    var setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
    setter.call(textarea, input);
  } else {
    textarea.textContent = input;
  }
  textarea.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));

  await $oc.sleep(300); // Let React enable the send button

  // Find and click send button — walk shadow DOM
  var sendBtn = (function findBtn() {
    var btn = document.querySelector('#composer-submit-button')
      || document.querySelector('button[data-testid="send-button"]')
      || document.querySelector('button[aria-label="Submit"]')
      || document.querySelector('button[aria-label="Send"]')
      || document.querySelector('button[aria-label="Send prompt"]')
      || document.querySelector('button[aria-label="Send message"]');
    if (btn && !btn.disabled) return btn;
    var hosts = document.querySelectorAll('*');
    for (var i = 0; i < hosts.length; i++) {
      var sr = hosts[i].shadowRoot;
      if (sr) {
        btn = sr.querySelector('#composer-submit-button')
          || sr.querySelector('button[data-testid="send-button"]')
          || sr.querySelector('button[aria-label="Submit"]')
          || sr.querySelector('button[aria-label="Send"]')
          || sr.querySelector('button[aria-label="Send prompt"]')
          || sr.querySelector('button[aria-label="Send message"]');
        if (btn && !btn.disabled) return btn;
      }
    }
    return null;
  })();
  if (!sendBtn) throw new Error("Send button not found");
  sendBtn.click();

  await $oc.waitNew('[data-message-author-role="assistant"]');
  var closeBtn = document.querySelector("[aria-label='Close']");
  if (closeBtn) closeBtn.click();
}

async function onRead() {
  await $oc.waitStable('[data-message-author-role="assistant"]', 1500, 120000);
  return $oc.extractLast('[data-message-author-role="assistant"]');
}
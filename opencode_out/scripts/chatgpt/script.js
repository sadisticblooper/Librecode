const LOAD_URL = "https://chatgpt.com";

async function onLoad() {
  await $oc.waitFor("id:prompt-textarea");
  await $oc.sleep(2500);
  (function () {
    var d = document.querySelector('div[role="dialog"]');
    if (d) {
      d.querySelectorAll("button").forEach(function (b) {
        if ((b.innerText || "").toLowerCase().includes("accept")) b.click();
      });
    }
  })();
}

async function onSend(input) {
  await $oc.waitFor("id:prompt-textarea");
  await $oc.type("id:prompt-textarea", input);
  // ChatGPT changes aria labels often — try all known variants.
  var sendBtn = document.querySelector('button[data-testid="send-button"]')
    || document.querySelector('button[aria-label="Send prompt"]')
    || document.querySelector('button[aria-label="Send message"]')
    || document.querySelector('#composer-submit-button');
  if (!sendBtn) throw new Error("Send button not found");
  sendBtn.click();
  await $oc.waitNew("[role='assistant']");
  var closeBtn = document.querySelector("[aria-label='Close']");
  if (closeBtn) closeBtn.click();
}

async function onRead() {
  await $oc.waitStable("[role='assistant']", 1500, 120000);
  return $oc.extractLast("[role='assistant']");
}

// ChatGPT Web script
// Streaming: onSend returns once the new response element appears.
// Python polls the last [role="assistant"] element and streams chunks live.

const LOAD_URL = "https://chatgpt.com";

async function onLoad() {
  await $oc.waitFor("id:prompt-textarea");
  await $oc.sleep(2500);
  // Dismiss accept/onboarding dialog if present.
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
  await $oc.click("aria:Send prompt");
  // Wait for new response element to appear — Python streams from here.
  await $oc.waitNew("[role='assistant']");
  // Dismiss any modal that popped up.
  var closeBtn = document.querySelector("[aria-label='Close']");
  if (closeBtn) closeBtn.click();
}

async function onRead() {
  // Fallback for non-streaming usage.
  await $oc.waitStable("[role='assistant']", 1500, 120000);
  return $oc.extractLast("[role='assistant']");
}

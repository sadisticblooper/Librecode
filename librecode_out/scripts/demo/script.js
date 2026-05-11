// Demo site script
// Deploy demo.html (librecode_out/index.html) anywhere, update LOAD_URL + manifest.url.
// The demo site is a simple non-streaming chat UI.

const LOAD_URL = "https://sadisticblooper.github.io/Sjdjd/";

async function onLoad() {
  await $oc.waitFor("placeholder:Type your message");
}

async function onSend(input) {
  await $oc.waitFor("placeholder:Type your message");
  await $oc.type("placeholder:Type your message", input);
  await $oc.click("aria:Send message");
  // Non-streaming: wait for the stop indicator to appear then disappear.
  await $oc.waitWhile("aria:Stop generating");
}

async function onRead() {
  return $oc.extractLast("role:assistant");
}

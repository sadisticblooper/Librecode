# DSL Builder Skill

Use this skill to generate a complete `script.oc` + `manifest.json` for any website using the OpenCode DSL. The agent browses the live site, inspects the DOM, validates selectors, then writes the files.

---

## When to use

Any time the user says:
- "add support for X"
- "make a script for X"
- "create a provider for X"
- "build a DSL script for X"

---

## Agent Workflow

### Step 1 — Navigate to the site

Use `spawn_browser` to open the target URL. Always start logged-in if credentials are available in context (the WebView shares cookies with the browser tool).

```
spawn_browser: https://chatgpt.com
```

### Step 2 — Discover selectors

For each required interaction point, run JS in the browser to find the exact selector. Test the **same JS the DSL will generate** — not hand-rolled equivalents.

Required interaction points for a chat site:
| Purpose | What to find |
|---------|-------------|
| Type message | `input` or `textarea` with placeholder, or aria-label |
| Send message | Button with aria-label |
| Stop generating | Button/element that appears while streaming |
| Response container | Element with role or data attribute containing reply text |

**Test each selector with:**
```js
// Test visibility-aware find (same as WAIT_FOR/WAIT_WHILE):
const el = document.querySelector('YOUR_CSS');
if (!el) return 'NOT_FOUND';
const s = window.getComputedStyle(el);
console.log(s.display, s.visibility, s.opacity, el.innerText?.slice(0, 100));
```

**Test WAIT_WHILE candidate (must be in DOM but hidden when done):**
```js
// Check if stop button is display:none or removed from DOM when idle
const btn = document.querySelector('[aria-label*="Stop"]');
console.log(btn ? window.getComputedStyle(btn).display : 'NOT IN DOM');
// If NOT IN DOM when idle -> use WAIT_FOR/WAIT_WHILE
// If display:none when idle -> WAIT_WHILE works (visibility-based)
// If removed from DOM -> WAIT_WHILE works (null = GONE)
```

**Test EXTRACT candidate:**
```js
// Find all role=assistant elements, check innerText of last
const els = document.querySelectorAll('[data-message-author-role="assistant"]');
console.log(els.length, els[els.length-1]?.innerText?.slice(0, 200));
```

### Step 3 — Send a test message

Type a short message and click send. Observe:
1. What changes in the DOM when sending (stop button appears? input clears?)
2. What the streaming state looks like (stop button visible, response container updating)
3. When streaming ends (stop button gone/hidden, text stable)
4. Where the final response text lives

### Step 4 — Validate edge cases

Check each of these in the browser:

**Cookie/consent banners:**
```js
document.querySelectorAll('[id*="cookie"],[class*="cookie"],[id*="consent"],[class*="banner"]')
```
If found → add `IF_VISIBLE css:.banner-class THEN CLICK css:.accept-btn` to `ON LOAD`.

**Login wall:**
```js
// Is the send button present?
document.querySelector('[aria-label*="Send"]')
// If null → site needs login; note WAIT_URL for post-login redirect
```

**Model picker / settings that need selecting:**
```js
document.querySelectorAll('select, [role="listbox"], [role="combobox"]')
```

**Shadow DOM:**
```js
// Check if important elements are inside shadow roots
document.querySelectorAll('*').forEach(el => {
  if (el.shadowRoot) console.log(el.tagName, el.id, el.className);
});
```
If shadow DOM is present → selectors still work (DSL uses BFS shadow traversal).

### Step 5 — Write script.oc

Use the discovered selectors. Follow these rules:

**Always use the most stable selector** in this priority order:
1. `aria:` — most stable, semantic
2. `placeholder:` — stable for inputs
3. `role:` — stable
4. `data-*` attribute → use `css:[data-attr='value']`
5. `id:` — stable if not generated
6. `css:` — last resort, avoid generated class names

**ON LOAD** — wait for the page to be interactive:
```
ON LOAD
  WAIT_FOR  aria:Send message
  # Add any cookie/consent dismissal here
  IF_VISIBLE css:.cookie-accept  THEN CLICK css:.cookie-accept
END
```

**ON SEND** — clear, type, submit:
```
ON SEND
  CLEAR      <input-selector>
  TYPE       <input-selector>  WITH $INPUT
  CLICK      aria:Send message
  # OR: PRESS_KEY <input-selector> Enter
END
```

**ON READ** — wait for streaming to end, extract:
```
ON READ
  WAIT_WHILE   <stop-indicator-selector>
  WAIT_STABLE  <response-container-selector>
  EXTRACT      last <response-container-selector>
END
```

**Common patterns:**

ChatGPT-style (stop button in DOM, hidden when idle):
```
ON READ
  WAIT_WHILE   aria:Stop streaming
  WAIT_STABLE  css:[data-message-author-role=assistant]
  EXTRACT      last css:[data-message-author-role=assistant]
END
```

Sites that remove the stop button from DOM entirely:
```
ON READ
  WAIT_WHILE   aria:Stop generating
  WAIT_STABLE  role:assistant
  EXTRACT      last role:assistant
END
```

Sites with a loading spinner instead of stop button:
```
ON READ
  WAIT_WHILE   css:.loading-indicator
  WAIT_STABLE  css:.response-text
  EXTRACT      last css:.response-text
END
```

Sites needing login redirect:
```
ON LOAD
  IF_URL /login  THEN WAIT_URL /chat
  WAIT_FOR aria:Send message
END
```

### Step 6 — Write manifest.json

```json
{
  "id": "<site-id>",
  "label": "<Human Readable Name>",
  "url": "https://example.com/chat",
  "port": 11441,
  "supports_tools": false,
  "timeouts": {
    "ready_ms": 20000,
    "send_ms": 8000,
    "response_ms": 120000,
    "stable_ms": 1500,
    "poll_interval_ms": 150
  },
  "script": "script.oc"
}
```

**Port assignment** — each script needs a unique port. Check existing manifests and increment:
```
demo      → 11440
chatgpt   → 11441
claude    → 11442
gemini    → 11443
...
```

**Timeout tuning:**
- `send_ms`: increase to 15000 for sites with slow input handling
- `response_ms`: increase to 300000 for sites that can take very long
- `stable_ms`: increase to 2500 for sites with chunky streaming
- `ready_ms`: increase to 40000 for SPAs with slow initial load

### Step 7 — Validate end-to-end

Send a real test message through the browser, running each DSL command's JS manually:

```js
// Simulate WAIT_WHILE aria:Stop generating
const el = document.querySelector("[aria-label*='Stop']");
const s = window.getComputedStyle(el);
console.log('visible?', s.display !== 'none' && s.visibility !== 'hidden');

// Simulate EXTRACT last role:assistant
const items = document.querySelectorAll("[role='assistant']");
console.log(items[items.length-1]?.innerText?.slice(0, 300));
```

If any step fails, go back and fix the selector.

---

## Output Files

### `scripts/<site-id>/script.oc`
### `scripts/<site-id>/manifest.json`

Place both in the same directory. The manifest's `"script"` field is relative to that directory.

---

## Full Example — ChatGPT

**scripts/chatgpt/manifest.json:**
```json
{
  "id": "chatgpt",
  "label": "ChatGPT",
  "url": "https://chatgpt.com",
  "port": 11441,
  "supports_tools": false,
  "timeouts": {
    "ready_ms": 25000,
    "send_ms": 8000,
    "response_ms": 180000,
    "stable_ms": 2000,
    "poll_interval_ms": 150
  },
  "script": "script.oc"
}
```

**scripts/chatgpt/script.oc:**
```
LOAD https://chatgpt.com

ON LOAD
  WAIT_FOR  aria:Send message
END

ON SEND
  IF_VISIBLE css:[data-radix-scroll-area-viewport]  THEN SCROLL bottom
  CLEAR      aria:Message ChatGPT
  TYPE       aria:Message ChatGPT  WITH $INPUT
  PRESS_KEY  aria:Message ChatGPT  Enter
END

ON READ
  WAIT_WHILE   aria:Stop streaming
  WAIT_STABLE  css:[data-message-author-role=assistant]
  EXTRACT      last css:[data-message-author-role=assistant]
END
```

---

## DSL Quick Reference (for builder use)

### Waiting
| Command | Resolves when |
|---------|--------------|
| `WAIT_FOR <sel>` | Element visible |
| `WAIT_WHILE <sel>` | Element not visible |
| `WAIT_STABLE <sel>` | innerText unchanged for stable_ms |
| `WAIT_URL <pattern>` | URL contains pattern |
| `WAIT_COUNT <sel> <op> <n>` | Element count satisfies condition (gt/gte/lt/lte/eq) |
| `SLEEP <ms>` | Hard pause |

### Interaction
| Command | Action |
|---------|--------|
| `TYPE <sel> WITH <val>` | Fill input (React-safe) |
| `CLEAR <sel>` | Clear input (React-safe) |
| `CLICK <sel>` | Click element |
| `PRESS_KEY <sel> <key>` | Keyboard event (Enter/Tab/Escape/etc) |
| `HOVER <sel>` | mouseenter/mouseover |
| `SELECT <sel> WITH <option>` | Set dropdown value |

### Navigation
| Command | Action |
|---------|--------|
| `NAVIGATE <url>` | Go to URL |
| `NAVIGATE_BACK` | history.back() |
| `NAVIGATE_FORWARD` | history.forward() |
| `RELOAD` | Reload page |
| `SCROLL_TO <sel>` | Scroll element into view |
| `SCROLL up/down/top/bottom [px]` | Scroll page |

### Extraction
| Command | Returns |
|---------|---------|
| `EXTRACT last <sel>` | innerText of last match |
| `EXTRACT first <sel>` | innerText of first match |
| `EXTRACT url` | window.location.href |
| `EXTRACT title` | document.title |
| `EXTRACT count <sel>` | Number of matches |
| `EXTRACT attr <sel> <attr>` | Attribute value |

### Control Flow
| Command | Behaviour |
|---------|-----------|
| `IF_VISIBLE <sel> THEN <cmd>` | Run cmd if element in DOM |
| `IF_URL <pattern> THEN <cmd>` | Run cmd if URL matches |
| `EVAL <js>` | Raw JS, returns result |
| `RETURN ERROR:<msg>` | Raise DslError immediately |

### Selector Prefixes
| Prefix | CSS equivalent |
|--------|---------------|
| `aria:Label` | `[aria-label*='Label']` |
| `placeholder:Text` | `[placeholder*='Text']` |
| `role:Value` | `[role='Value']` |
| `id:myId` | `#myId` |
| `css:.class` | `.class` |
| `text:Stop` | BFS innerText match |

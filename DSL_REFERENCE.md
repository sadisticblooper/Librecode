# OpenCode DSL Reference

Scripts are `.oc` files with three lifecycle blocks. Commands are executed top-to-bottom within each block.

---

## Script Structure

```
LOAD <url>

ON LOAD
  # Runs once when the page first loads
END

ON SEND
  # Runs every time a message is sent; $INPUT = user message
END

ON READ
  # Runs to extract the response; first EXTRACT result is returned
END
```

---

## Selector Syntax

All commands that target elements use a selector prefix:

| Prefix | Matches |
|--------|---------|
| `aria:Label` | `[aria-label*='Label']` |
| `placeholder:Text` | `[placeholder*='Text']` |
| `role:Value` | `[role='Value']` |
| `id:myId` | `#myId` |
| `css:.my-class` | Raw CSS selector |
| `text:Stop` | BFS innerText match (shadow-DOM aware) |

All finders are shadow-DOM aware — they traverse shadow roots automatically.

---

## Commands

### Waiting

#### `WAIT_FOR <selector>`
Polls until element is **visible** (not `display:none`, `visibility:hidden`, or `opacity:0`).

```
WAIT_FOR aria:Send message
WAIT_FOR placeholder:Type your message
```

**Fails with:** `WAIT_FOR '<sel>' — element never became visible after Nms.`

---

#### `WAIT_WHILE <selector>`
Polls until element is **no longer visible**. Use after clicking send to wait for a loading/generating indicator to disappear.

```
WAIT_WHILE aria:Stop generating
WAIT_WHILE css:.loading-spinner
```

**Fails with:** `WAIT_WHILE '<sel>' — element still visible after Nms. Page may still be loading or streaming.`

---

#### `WAIT_STABLE <selector>`
Polls until element's `innerText` stops changing for `stable_ms` (default 1500ms). Use after `WAIT_WHILE` to confirm streaming is truly done.

```
WAIT_STABLE role:assistant
```

**Fails with:** `WAIT_STABLE '<sel>' — content never stabilised after Nms. Response may still be streaming.`

---

#### `WAIT_URL <pattern>`
Polls until `window.location.href` contains pattern. Use after navigation/login redirects.

```
WAIT_URL /dashboard
WAIT_URL chatgpt.com/c/
```

**Fails with:** `WAIT_URL '<pattern>' — URL never matched after Nms. Current: <url>`

---

#### `WAIT_COUNT <selector> <op> <n>`
Polls until the number of matching elements satisfies the condition.
`op`: `gt` `gte` `lt` `lte` `eq`

```
WAIT_COUNT css:[data-message-author-role=assistant] gte 2
WAIT_COUNT css:.message gt 0
```

**Fails with:** `WAIT_COUNT '<sel>' <op> <n> — not met after Nms. Current count: X`

---

#### `SLEEP <ms>`
Hard pause. Avoid if possible — prefer `WAIT_FOR`/`WAIT_STABLE`.

```
SLEEP 500
```

---

### Interaction

#### `TYPE <selector> WITH <value>`
Fills an input or textarea (React-safe native setter + fires `input`/`change` events).

```
TYPE placeholder:Type your message  WITH $INPUT
TYPE aria:Message ChatGPT           WITH $INPUT
```

**Fails with:** `TYPE '<sel>' — input not found. Placeholders: [...]. URL: <url>`

---

#### `CLEAR <selector>`
Clears a field (React-safe). Use before `TYPE` if field may already have content.

```
CLEAR placeholder:Search
```

**Fails with:** `CLEAR '<sel>' — element not found. URL: <url>`

---

#### `CLICK <selector>`
Scrolls element into view and dispatches `mousedown` / `mouseup` / `click`.

```
CLICK aria:Send message
CLICK aria:New chat
CLICK css:#submit-btn
```

**Fails with:** `CLICK '<sel>' — element not found. Aria-labels: [...]. URL: <url>`

---

#### `PRESS_KEY <selector> <key>`
Dispatches `keydown` / `keypress` / `keyup` on the element. Useful when sites listen for keyboard events instead of button clicks.

Supported keys: `Enter` `Tab` `Escape` `Backspace` `Delete` `ArrowUp` `ArrowDown` `ArrowLeft` `ArrowRight`

```
PRESS_KEY aria:Message ChatGPT  Enter
PRESS_KEY css:#search-input     Enter
```

**Fails with:** `PRESS_KEY '<sel>' <key> — element not found.`

---

#### `HOVER <selector>`
Dispatches `mouseenter` / `mouseover`. Opens hover menus that reveal hidden buttons.

```
HOVER css:.message:last-child
```

Non-fatal if element not found (logs warning).

---

#### `SELECT <selector> WITH <option>`
Sets a `<select>` dropdown by option value or visible text.

```
SELECT css:#model-select  WITH gpt-4o
SELECT aria:Model picker   WITH Claude 3.5 Sonnet
```

**Fails with:** `SELECT '<sel>' — option '<val>' not in dropdown.`

---

### Navigation

#### `NAVIGATE <url>`
Navigates the WebView to a URL.

```
NAVIGATE https://chatgpt.com
```

---

#### `NAVIGATE_BACK`
Equivalent to `window.history.back()`.

---

#### `NAVIGATE_FORWARD`
Equivalent to `window.history.forward()`.

---

#### `RELOAD`
Reloads the current page.

---

#### `SCROLL_TO <selector>`
Smoothly scrolls element into view. Non-fatal if not found.

```
SCROLL_TO css:.latest-message
```

---

#### `SCROLL <direction> [amount]`
Scrolls the page. Direction: `up` `down` `top` `bottom`. Amount in px (default 300).

```
SCROLL down
SCROLL down 600
SCROLL top
```

---

### Extraction

#### `EXTRACT last <selector>`
Returns `innerText` of the **last** matching element. Typical use: get latest AI response.

```
EXTRACT last role:assistant
EXTRACT last css:[data-message-author-role=assistant]
```

---

#### `EXTRACT first <selector>`
Returns `innerText` of the **first** matching element.

```
EXTRACT first css:.response-text
```

---

#### `EXTRACT url`
Returns current `window.location.href`.

```
EXTRACT url
```

---

#### `EXTRACT title`
Returns `document.title`.

```
EXTRACT title
```

---

#### `EXTRACT count <selector>`
Returns number of matching elements as a string.

```
EXTRACT count css:[data-message-author-role=assistant]
```

---

#### `EXTRACT attr <selector> <attribute>`
Returns an attribute value from the first matching element.

```
EXTRACT attr css:#session-token  data-value
EXTRACT attr aria:Profile photo  src
```

---

### Control Flow

#### `IF_VISIBLE <selector> THEN <command>`
Runs command only if element is currently in the DOM.

```
IF_VISIBLE aria:Stop generating  THEN WAIT_WHILE aria:Stop generating
IF_VISIBLE css:.cookie-banner    THEN CLICK css:.accept-btn
```

---

#### `IF_URL <pattern> THEN <command>`
Runs command only if current URL contains pattern.

```
IF_URL /login  THEN WAIT_URL /dashboard
```

---

#### `EVAL <js>`
Runs arbitrary JS and returns the result. Use for one-off logic not covered by other commands.

```
EVAL document.querySelector('.token-count').innerText
```

---

#### `RETURN ERROR:<message>`
Immediately raises a `DslError` with the given message. Use for explicit failure conditions.

```
IF_URL /error  THEN RETURN ERROR:Site returned error page
```

---

## Example — ChatGPT

```
LOAD https://chatgpt.com

ON LOAD
  WAIT_FOR  aria:Send message
END

ON SEND
  IF_VISIBLE css:.cookie-banner  THEN CLICK css:#accept-all
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

## Manifest Timeouts (`manifest.json`)

```json
"timeouts": {
  "ready_ms":        20000,
  "send_ms":          8000,
  "response_ms":    120000,
  "stable_ms":        1500,
  "poll_interval_ms":  150
}
```

| Key | Used by | Description |
|-----|---------|-------------|
| `ready_ms` | `ON LOAD` | Max time to wait for page ready |
| `send_ms` | `ON SEND` | Max time for send flow to complete |
| `response_ms` | `ON READ` | Max time to wait for response |
| `stable_ms` | `WAIT_STABLE` | How long content must be unchanged |
| `poll_interval_ms` | All waits | How often to re-evaluate JS |

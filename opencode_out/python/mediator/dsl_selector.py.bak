"""
dsl_selector.py — Converts DSL selector strings into JavaScript expressions.

Selector prefixes:
  aria:Label        →  [aria-label*='Label']
  placeholder:Text  →  [placeholder*='Text']
  role:assistant    →  [role='assistant']
  id:myId           →  #myId
  css:.class        →  .class  (raw)
  text:Stop         →  BFS innerText match
"""

import json


def selector_to_js_finder(selector: str, return_all: bool = False) -> str:
    """
    Returns a self-executing JS snippet that finds element(s) on the page,
    including shadow DOM traversal.

    If return_all=False  → returns first matching element (or null).
    If return_all=True   → returns NodeList/Array of all matches.
    """
    sel = selector.strip()

    # ── text: BFS innerText match ──────────────────────────────────────
    if sel.startswith("text:"):
        needle = json.dumps(sel[5:].strip().lower())
        if return_all:
            return f"""(function(){{
  const needle={needle};
  const all=[];
  function walk(root){{
    const tw=document.createTreeWalker(root,NodeFilter.SHOW_ELEMENT);
    let n;
    while((n=tw.nextNode())){{
      if(n.shadowRoot)walk(n.shadowRoot);
      if((n.innerText||'').toLowerCase().includes(needle))all.push(n);
    }}
  }}
  walk(document.body||document.documentElement);
  return all;
}})()"""
        else:
            return f"""(function(){{
  const needle={needle};
  function walk(root){{
    const tw=document.createTreeWalker(root,NodeFilter.SHOW_ELEMENT);
    let n;
    while((n=tw.nextNode())){{
      if(n.shadowRoot){{const r=walk(n.shadowRoot);if(r)return r;}}
      if((n.innerText||'').toLowerCase().includes(needle))return n;
    }}
    return null;
  }}
  return walk(document.body||document.documentElement);
}})()"""

    # ── Build CSS selector string ──────────────────────────────────────
    if sel.startswith("aria:"):
        value = sel[5:].strip()
        css = f"[aria-label*='{value}']"
    elif sel.startswith("placeholder:"):
        value = sel[12:].strip()
        css = f"[placeholder*='{value}']"
    elif sel.startswith("role:"):
        value = sel[5:].strip()
        css = f"[role='{value}']"
    elif sel.startswith("data-message-author-role:"):
        value = sel[25:].strip()
        css = f"[data-message-author-role='{value}']"
    elif sel.startswith("id:"):
        value = sel[3:].strip()
        css = f"#{value}"
    elif sel.startswith("css:"):
        css = sel[4:].strip()
    else:
        # Treat bare string as CSS
        css = sel

    css_json = json.dumps(css)

    # Shadow-DOM-aware BFS finder
    bfs_find_one = f"""(function(){{
  const css={css_json};
  function find(root){{
    const el=root.querySelector(css);
    if(el)return el;
    const all=root.querySelectorAll('*');
    for(let i=0;i<all.length;i++){{
      if(all[i].shadowRoot){{const r=find(all[i].shadowRoot);if(r)return r;}}
    }}
    return null;
  }}
  return find(document.body||document.documentElement);
}})()"""

    bfs_find_all = f"""(function(){{
  const css={css_json};
  const results=[];
  function find(root){{
    root.querySelectorAll(css).forEach(e=>results.push(e));
    root.querySelectorAll('*').forEach(e=>{{
      if(e.shadowRoot)find(e.shadowRoot);
    }});
  }}
  find(document.body||document.documentElement);
  return results;
}})()"""

    return bfs_find_all if return_all else bfs_find_one


def click_js(selector: str) -> str:
    """Returns JS to find and click an element. Dispatches mousedown/up/click."""
    finder = selector_to_js_finder(selector)
    return f"""(function(){{
  const el={finder};
  if(!el)return 'NOT_FOUND';
  el.scrollIntoView({{block:'center'}});
  ['mousedown','mouseup','click'].forEach(t=>
    el.dispatchEvent(new MouseEvent(t,{{bubbles:true,cancelable:true}}))
  );
  return 'OK';
}})()"""


def type_js(selector: str, value: str) -> str:
    """Returns JS to fill a field React-safely (native setter bypass)."""
    val_json = json.dumps(value)
    finder = selector_to_js_finder(selector)
    return f"""(function(){{
  const el={finder};
  if(!el)return 'NOT_FOUND';
  el.focus();
  const nativeInputValueSetter=Object.getOwnPropertyDescriptor(
    el.tagName==='TEXTAREA'?window.HTMLTextAreaElement.prototype:window.HTMLInputElement.prototype,
    'value'
  );
  if(nativeInputValueSetter){{
    nativeInputValueSetter.set.call(el,{val_json});
  }} else {{
    el.value={val_json};
  }}
  el.dispatchEvent(new Event('input',{{bubbles:true}}));
  el.dispatchEvent(new Event('change',{{bubbles:true}}));
  return 'OK';
}})()"""


def wait_for_js(selector: str) -> str:
    """Returns a JS polling expression: resolves to 'FOUND' when element appears."""
    finder = selector_to_js_finder(selector)
    return f"({finder})!=null ? 'FOUND' : null"


def wait_while_js(selector: str) -> str:
    """Returns a JS polling expression: resolves to 'GONE' when element disappears."""
    finder = selector_to_js_finder(selector)
    return f"({finder})==null ? 'GONE' : null"


def extract_js(strategy: str) -> str:
    """
    Returns JS to extract text using one of the EXTRACT strategies.

    Strategies:
      last role:assistant     → innerText of last [data-message-author-role="assistant"]
      last css:.class         → innerText of last matched CSS element
      first aria:Label        → innerText of first matched aria element
      last <any-selector>     → innerText of last matched element
      first <any-selector>    → innerText of first matched element
    """
    parts = strategy.strip().split(None, 1)
    if len(parts) < 2:
        return "null"

    position, sel = parts[0].lower(), parts[1].strip()
    finder_all = selector_to_js_finder(sel, return_all=True)

    if position == "last":
        return f"""(function(){{
  const items={finder_all};
  if(!items||!items.length)return null;
  return items[items.length-1].innerText||null;
}})()"""
    else:  # first
        return f"""(function(){{
  const items={finder_all};
  if(!items||!items.length)return null;
  return items[0].innerText||null;
}})()"""


def if_visible_finder_js(selector: str) -> str:
    """Returns JS: 'VISIBLE' if element exists, else 'HIDDEN'."""
    finder = selector_to_js_finder(selector)
    return f"({finder})!=null ? 'VISIBLE' : 'HIDDEN'"

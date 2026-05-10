"""
dsl_selector.py - Converts DSL selector strings into JavaScript expressions.

Selector prefixes:
  aria:Label        ->  [aria-label*='Label']
  placeholder:Text  ->  [placeholder*='Text']
  role:Value        ->  [role='Value']
  id:myId           ->  #myId
  css:.class        ->  .class  (raw CSS)
  text:Stop         ->  BFS innerText match (shadow-DOM aware)
"""

import json


def selector_to_js_finder(selector: str, return_all: bool = False) -> str:
    sel = selector.strip()

    if sel.startswith("text:"):
        needle = json.dumps(sel[5:].strip().lower())
        if return_all:
            return (
                f"(function(){{"
                f"const needle={needle};const all=[];"
                f"function walk(root){{const tw=document.createTreeWalker(root,NodeFilter.SHOW_ELEMENT);let n;"
                f"while((n=tw.nextNode())){{if(n.shadowRoot)walk(n.shadowRoot);"
                f"if((n.innerText||'').toLowerCase().includes(needle))all.push(n);}}}}"
                f"walk(document.body||document.documentElement);return all;}})()"
            )
        else:
            return (
                f"(function(){{"
                f"const needle={needle};"
                f"function walk(root){{const tw=document.createTreeWalker(root,NodeFilter.SHOW_ELEMENT);let n;"
                f"while((n=tw.nextNode())){{if(n.shadowRoot){{const r=walk(n.shadowRoot);if(r)return r;}}"
                f"if((n.innerText||'').toLowerCase().includes(needle))return n;}}"
                f"return null;}}"
                f"return walk(document.body||document.documentElement);}})()"
            )

    if sel.startswith("aria:"):
        css = f"[aria-label*='{sel[5:].strip()}']"
    elif sel.startswith("placeholder:"):
        css = f"[placeholder*='{sel[12:].strip()}']"
    elif sel.startswith("role:"):
        css = f"[role='{sel[5:].strip()}']"
    elif sel.startswith("data-message-author-role:"):
        css = f"[data-message-author-role='{sel[25:].strip()}']"
    elif sel.startswith("id:"):
        css = f"#{sel[3:].strip()}"
    elif sel.startswith("css:"):
        css = sel[4:].strip()
    else:
        css = sel

    css_json = json.dumps(css)

    if return_all:
        return (
            f"(function(){{const css={css_json};const results=[];"
            f"function find(root){{root.querySelectorAll(css).forEach(e=>results.push(e));"
            f"root.querySelectorAll('*').forEach(e=>{{if(e.shadowRoot)find(e.shadowRoot);}});}}"
            f"find(document.body||document.documentElement);return results;}})()"
        )
    else:
        return (
            f"(function(){{const css={css_json};"
            f"function find(root){{const el=root.querySelector(css);if(el)return el;"
            f"const all=root.querySelectorAll('*');"
            f"for(let i=0;i<all.length;i++){{if(all[i].shadowRoot){{const r=find(all[i].shadowRoot);if(r)return r;}}}}"
            f"return null;}}"
            f"return find(document.body||document.documentElement);}})()"
        )


def _is_visible_js(finder: str) -> str:
    return (
        f"(function(){{const el={finder};if(!el)return false;"
        f"const s=window.getComputedStyle(el);"
        f"return s.display!=='none'&&s.visibility!=='hidden'&&s.opacity!=='0';}})()"
    )


def wait_for_js(selector: str) -> str:
    return f"{_is_visible_js(selector_to_js_finder(selector))}?'FOUND':null"


def wait_while_js(selector: str) -> str:
    return f"{_is_visible_js(selector_to_js_finder(selector))}?null:'GONE'"


def wait_url_js(pattern: str) -> str:
    return f"window.location.href.includes({json.dumps(pattern)})?'MATCHED':null"


def wait_stable_js(selector: str) -> str:
    """Returns current innerText - used by _poll_stable to detect content settling."""
    finder = selector_to_js_finder(selector)
    return f"(function(){{const el={finder};if(!el)return null;return el.innerText||'';}})()"


def wait_count_js(selector: str, op: str, count: int) -> str:
    css_json = json.dumps(selector)
    operator = {"gt":">","gte":">=","lt":"<","lte":"<=","eq":"==="}.get(op,">=")
    return f"(function(){{const n=document.querySelectorAll({css_json}).length;return n{operator}{count}?'MET':null;}})()"


def click_js(selector: str) -> str:
    finder = selector_to_js_finder(selector)
    return (
        f"(function(){{const el={finder};if(!el)return 'NOT_FOUND';"
        f"el.scrollIntoView({{block:'center'}});"
        f"['mousedown','mouseup','click'].forEach(t=>el.dispatchEvent(new MouseEvent(t,{{bubbles:true,cancelable:true}})));"
        f"return 'OK';}})()"
    )


def type_js(selector: str, value: str) -> str:
    val_json = json.dumps(value)
    finder = selector_to_js_finder(selector)
    return (
        f"(function(){{const el={finder};if(!el)return 'NOT_FOUND';el.focus();"
        f"const niv=Object.getOwnPropertyDescriptor("
        f"el.tagName==='TEXTAREA'?window.HTMLTextAreaElement.prototype:window.HTMLInputElement.prototype,'value');"
        f"if(niv)niv.set.call(el,{val_json});else el.value={val_json};"
        f"el.dispatchEvent(new Event('input',{{bubbles:true}}));"
        f"el.dispatchEvent(new Event('change',{{bubbles:true}}));"
        f"return 'OK';}})()"
    )


def clear_js(selector: str) -> str:
    finder = selector_to_js_finder(selector)
    return (
        f"(function(){{const el={finder};if(!el)return 'NOT_FOUND';el.focus();"
        f"const niv=Object.getOwnPropertyDescriptor("
        f"el.tagName==='TEXTAREA'?window.HTMLTextAreaElement.prototype:window.HTMLInputElement.prototype,'value');"
        f"if(niv)niv.set.call(el,'');else el.value='';"
        f"el.dispatchEvent(new Event('input',{{bubbles:true}}));"
        f"el.dispatchEvent(new Event('change',{{bubbles:true}}));"
        f"return 'OK';}})()"
    )


def press_key_js(selector: str, key: str) -> str:
    key_json = json.dumps(key)
    code_map = {
        "Enter":"Enter","Tab":"Tab","Escape":"Escape","Backspace":"Backspace",
        "Delete":"Delete","ArrowUp":"ArrowUp","ArrowDown":"ArrowDown",
        "ArrowLeft":"ArrowLeft","ArrowRight":"ArrowRight"," ":"Space",
    }
    code = json.dumps(code_map.get(key, key))
    finder = selector_to_js_finder(selector)
    return (
        f"(function(){{const el={finder};if(!el)return 'NOT_FOUND';el.focus();"
        f"['keydown','keypress','keyup'].forEach(t=>el.dispatchEvent("
        f"new KeyboardEvent(t,{{key:{key_json},code:{code},bubbles:true,cancelable:true}})));"
        f"return 'OK';}})()"
    )


def hover_js(selector: str) -> str:
    finder = selector_to_js_finder(selector)
    return (
        f"(function(){{const el={finder};if(!el)return 'NOT_FOUND';"
        f"el.scrollIntoView({{block:'center'}});"
        f"['mouseenter','mouseover'].forEach(t=>el.dispatchEvent(new MouseEvent(t,{{bubbles:true,cancelable:true}})));"
        f"return 'OK';}})()"
    )


def select_option_js(selector: str, value: str) -> str:
    val_json = json.dumps(value)
    finder = selector_to_js_finder(selector)
    return (
        f"(function(){{const el={finder};if(!el)return 'NOT_FOUND';"
        f"const match=Array.from(el.options).find(o=>o.value==={val_json}||o.text==={val_json});"
        f"if(!match)return 'OPTION_NOT_FOUND';"
        f"el.value=match.value;el.dispatchEvent(new Event('change',{{bubbles:true}}));"
        f"return 'OK';}})()"
    )


def scroll_to_js(selector: str) -> str:
    finder = selector_to_js_finder(selector)
    return (
        f"(function(){{const el={finder};if(!el)return 'NOT_FOUND';"
        f"el.scrollIntoView({{behavior:'smooth',block:'center'}});return 'OK';}})()"
    )


def scroll_page_js(direction: str, amount: int = 300) -> str:
    if direction == "top":    return "window.scrollTo(0,0);'OK'"
    if direction == "bottom": return "window.scrollTo(0,document.body.scrollHeight);'OK'"
    if direction == "up":     return f"window.scrollBy(0,{-amount});'OK'"
    return f"window.scrollBy(0,{amount});'OK'"


def get_attr_js(selector: str, attr: str) -> str:
    finder = selector_to_js_finder(selector)
    attr_json = json.dumps(attr)
    return f"(function(){{const el={finder};if(!el)return null;return el.getAttribute({attr_json});}})()"


def if_visible_finder_js(selector: str) -> str:
    finder = selector_to_js_finder(selector)
    return f"({finder})!=null?'VISIBLE':'HIDDEN'"


def extract_js(strategy: str) -> str:
    """
    EXTRACT strategies:
      last  <selector>        - innerText of last match
      first <selector>        - innerText of first match
      url                     - current page URL
      title                   - document.title
      count <selector>        - number of matching elements as string
      attr  <selector> <attr> - attribute value of first match
    """
    parts = strategy.strip().split(None, 1)
    if not parts:
        return "null"

    if parts[0].lower() == "url":
        return "window.location.href"
    if parts[0].lower() == "title":
        return "document.title"
    if len(parts) < 2:
        return "null"

    position, rest = parts[0].lower(), parts[1].strip()

    if position == "count":
        css_json = json.dumps(rest)
        return f"String(document.querySelectorAll({css_json}).length)"

    if position == "attr":
        sub = rest.split(None, 1)
        if len(sub) == 2:
            return get_attr_js(sub[0], sub[1])
        return "null"

    finder_all = selector_to_js_finder(rest, return_all=True)
    if position == "last":
        return f"(function(){{const items={finder_all};if(!items||!items.length)return null;return items[items.length-1].innerText||null;}})()"
    else:
        return f"(function(){{const items={finder_all};if(!items||!items.length)return null;return items[0].innerText||null;}})()"

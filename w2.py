"""
Wave Chat — real-time collaborative chat with typing indicators,
message editing, deletion, reactions, and live timestamps.

Run with: uvicorn wave:app --reload
"""

import secrets, time
from html_tags import setup_tags, to_html
from py_sse import patch_elements, create_relay, set_cookie, create_app

setup_tags()

app   = create_app()
relay = create_relay()

messages = []
drafts = {}
NAMES = ["Fox", "Owl", "Bear", "Wolf", "Hawk", "Lynx", "Crow", "Deer", "Hare", "Wren"]
REACTIONS = ["👍", "❤️", "😂", "😮", "😢", "🔥"]

def get_user(req): return req["cookies"].get("user")

def ensure_user(req):
    user = get_user(req)
    if not user:
        user = secrets.choice(NAMES) + str(secrets.randbelow(100))
        set_cookie(req, "user", user, path="/", samesite="Lax")
    return user

def find_msg(mid):
    for m in messages:
        if m["id"] == mid: return m
    return None

def fmt_time(ts):
    t = time.localtime(ts)
    h, ampm = (t.tm_hour % 12 or 12), ("AM" if t.tm_hour < 12 else "PM")
    return f"{h}:{t.tm_min:02d} {ampm}"

def fmt_ago(ts):
    d = time.time() - ts
    if d < 60: return "just now"
    if d < 3600: return f"{int(d // 60)}m ago"
    if d < 86400: return f"{int(d // 3600)}h ago"
    return f"{int(d // 86400)}d ago"

def render_reactions(m, user):
    if not m["reactions"] and not m.get("show_picker"): return Span()
    btns = []
    for emoji, users in m["reactions"].items():
        cls = "reaction active" if user in users else "reaction"
        btns.append(Button({"class": cls, "data-on:click": f"@post('/react?id={m['id']}&emoji={emoji}')"}, f"{emoji} {len(users)}"))
    btns.append(Button({"class": "reaction add", "data-on:click": f"@post('/react-picker?id={m['id']}')"}, "+"))
    return Div({"class": "reactions"}, *btns)

def render_picker(m):
    if not m.get("show_picker"): return Span()
    btns = [Button({"class": "picker-btn", "data-on:click": f"@post('/react?id={m['id']}&emoji={e}')"}, e) for e in REACTIONS]
    return Div({"class": "picker"}, *btns)

def render_msg(m, user):
    mid = m["id"]
    is_owner = m["user"] == user
    ts_str = f"{fmt_time(m['ts'])} · {fmt_ago(m['ts'])}"
    edited = " (edited)" if m["edited"] else ""

    actions = []
    if is_owner:
        actions.append(Button({"class": "msg-action", "data-on:click": f"@post('/edit-start?id={mid}')"}, "✏️"))
        actions.append(Button({"class": "msg-action", "data-on:click": f"@post('/delete?id={mid}')"}, "🗑️"))
    actions.append(Button({"class": "msg-action", "data-on:click": f"@post('/react-picker?id={mid}')"}, "😀"))

    if m.get("editing") and is_owner:
        body = Div({"class": "edit-box"},
            Input({"type": "text", "id": f"edit-{mid}", "value": m["text"], "class": "edit-input",
                   "data-on:keydown": f"if(event.key==='Enter'){{@post('/edit?id={mid}&text='+encodeURIComponent(el.value))}} if(event.key==='Escape'){{@post('/edit-cancel?id={mid}')}}"}),
            Button({"class": "msg-action", "data-on:click": f"@post('/edit?id={mid}&text='+encodeURIComponent(document.getElementById('edit-{mid}').value))"}, "✓"),
            Button({"class": "msg-action", "data-on:click": f"@post('/edit-cancel?id={mid}')"}, "✗"))
    else:
        body = Span(m["text"])

    return Div({"class": "msg", "id": f"msg-{mid}"},
        Div({"class": "msg-header"},
            Span({"class": "user"}, m["user"]),
            Span({"class": "ts"}, ts_str + edited),
            Div({"class": "msg-actions"}, *actions)),
        body,
        render_reactions(m, user),
        render_picker(m))

def render_messages(user):
    items = [render_msg(m, user) for m in messages]
    for u, text in drafts.items():
        if text.strip():
            items.append(Div({"class": "msg draft"}, Span({"class": "user"}, u), Span(text), Span({"class": "typing"}, " ...")))
    return Div({"id": "chat"}, *items)

CSS = """\
body { font-family: system-ui; max-width: 500px; margin: 2rem auto; background: #0a0a0a; color: #eee; padding: 0 1rem; }
.chat-box { border: 1px solid #333; border-radius: 0.5rem; padding: 1rem; min-height: 300px; margin-bottom: 1rem; overflow-y: auto; max-height: 70vh; }
.msg { padding: 0.5rem 0; border-bottom: 1px solid #1a1a1a; }
.msg:last-child { border-bottom: none; }
.msg-header { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.2rem; }
.user { font-weight: 700; color: #e54; font-size: 0.85rem; }
.ts { color: #555; font-size: 0.7rem; }
.msg-actions { margin-left: auto; display: flex; gap: 0.2rem; opacity: 0; transition: opacity 0.15s; }
.msg:hover .msg-actions { opacity: 1; }
.msg-action { background: none; border: none; cursor: pointer; font-size: 0.75rem; padding: 0.1rem 0.3rem; border-radius: 0.25rem; color: #888; }
.msg-action:hover { background: #222; color: #eee; }
.draft { opacity: 0.5; }
.typing { color: #666; font-style: italic; }
.reactions { display: flex; gap: 0.3rem; margin-top: 0.3rem; flex-wrap: wrap; }
.reaction { background: #1a1a1a; border: 1px solid #333; border-radius: 1rem; padding: 0.15rem 0.5rem; font-size: 0.75rem; cursor: pointer; color: #ccc; }
.reaction:hover { border-color: #555; }
.reaction.active { border-color: #e54; background: #1a0a0a; }
.reaction.add { font-size: 0.7rem; color: #666; }
.picker { display: flex; gap: 0.3rem; margin-top: 0.3rem; background: #151515; border: 1px solid #333; border-radius: 0.5rem; padding: 0.3rem; }
.picker-btn { background: none; border: none; cursor: pointer; font-size: 1.1rem; padding: 0.2rem; border-radius: 0.25rem; }
.picker-btn:hover { background: #222; }
.edit-box { display: flex; gap: 0.3rem; align-items: center; margin-top: 0.2rem; }
.edit-input { flex: 1; padding: 0.4rem; background: #151515; border: 1px solid #333; border-radius: 0.3rem; color: #eee; font: inherit; font-size: 0.9rem; }
.edit-input:focus { outline: 2px solid #e54; }
input { width: 100%; padding: 0.75rem; background: #151515; border: 1px solid #333; border-radius: 0.5rem; color: #eee; font: inherit; font-size: 16px; box-sizing: border-box; }
input:focus { outline: 2px solid #e54; }
.controls { display: flex; gap: 0.5rem; }
button.send { padding: 0.75rem 1.5rem; background: #e54; border: none; border-radius: 0.5rem; color: #fff; cursor: pointer; font: inherit; }"""

import asyncio

async def _ticker():
    while True:
        await asyncio.sleep(30)
        relay.publish("chat.tick", None)

@app.get("/")
async def home(req):
    user = ensure_user(req)
    return to_html(Html(
        Head(
            Meta({"charset": "UTF-8"}),
            Meta({"name": "viewport", "content": "width=device-width, initial-scale=1.0"}),
            Title("Wave Chat"),
            Script({"type": "module", "src": "https://cdn.jsdelivr.net/gh/starfederation/datastar@1.0.0-RC.8/bundles/datastar.js"}),
            Style(CSS)),
        Body(
            H2("Wave Chat"),
            P({"style": "color:#666; font-size:0.85rem"}, f"You are {user}"),
            Div({"class": "chat-box", "data-init": "@get('/stream')"}, render_messages(user)),
            Div({"class": "controls"},
                Input({"type": "text", "id": "inp", "placeholder": "Type a message...", "autocomplete": "off",
                       "data-on:input__debounce.150ms": "@post('/typing?text=' + encodeURIComponent(el.value))",
                       "data-on:keydown": "if(event.key==='Enter' && el.value.trim()){@post('/send?text=' + encodeURIComponent(el.value)); el.value=''}"}),
                Button({"class": "send", "data-on:click": "var inp=document.getElementById('inp'); if(inp.value.trim()){@post('/send?text='+encodeURIComponent(inp.value)); inp.value=''}"}, "Send")))))

@app.get("/stream")
async def stream(req):
    user = ensure_user(req)
    yield patch_elements(render_messages(user))
    tick = asyncio.create_task(_ticker())
    try:
        async for topic, data in relay.subscribe("chat.*"):
            yield patch_elements(render_messages(user))
    finally:
        tick.cancel()

@app.post("/typing")
async def typing(req):
    user = ensure_user(req)
    drafts[user] = req["query"].get("text", "")
    relay.publish("chat.typing", user)
    return None

@app.post("/send")
async def send_msg(req):
    user = ensure_user(req)
    text = req["query"].get("text", "").strip()
    if text:
        messages.append(dict(id=secrets.token_urlsafe(8), user=user, text=text, ts=time.time(), edited=False, reactions={}, editing=False, show_picker=False))
        drafts.pop(user, None)
        relay.publish("chat.message", user)
    return None

@app.post("/delete")
async def delete_msg(req):
    user = ensure_user(req)
    m = find_msg(req["query"].get("id", ""))
    if m and m["user"] == user:
        messages.remove(m)
        relay.publish("chat.delete", user)
    return None

@app.post("/edit-start")
async def edit_start(req):
    user = ensure_user(req)
    m = find_msg(req["query"].get("id", ""))
    if m and m["user"] == user:
        m["editing"] = True
        relay.publish("chat.edit", user)
    return None

@app.post("/edit-cancel")
async def edit_cancel(req):
    m = find_msg(req["query"].get("id", ""))
    if m:
        m["editing"] = False
        relay.publish("chat.edit", None)
    return None

@app.post("/edit")
async def edit_msg(req):
    user = ensure_user(req)
    m = find_msg(req["query"].get("id", ""))
    text = req["query"].get("text", "").strip()
    if m and m["user"] == user and text:
        m["text"] = text
        m["edited"] = True
        m["editing"] = False
        relay.publish("chat.edit", user)
    return None

@app.post("/react-picker")
async def react_picker(req):
    m = find_msg(req["query"].get("id", ""))
    if m:
        m["show_picker"] = not m.get("show_picker", False)
        relay.publish("chat.react", None)
    return None

@app.post("/react")
async def react(req):
    user = ensure_user(req)
    m = find_msg(req["query"].get("id", ""))
    emoji = req["query"].get("emoji", "")
    if m and emoji:
        if emoji not in m["reactions"]: m["reactions"][emoji] = set()
        if user in m["reactions"][emoji]: m["reactions"][emoji].discard(user)
        else: m["reactions"][emoji].add(user)
        if not m["reactions"][emoji]: del m["reactions"][emoji]
        m["show_picker"] = False
        relay.publish("chat.react", user)
    return None

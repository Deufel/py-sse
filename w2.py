import secrets, time, asyncio, json, os, base64
from html_tags import setup_tags, to_html
from py_sse import (
    patch_elements, create_relay, set_cookie, create_app, signals,
    create_signer, read_body, BodyTooLarge,
    sanitize_html, sanitize_filename, validate_base64, validate_mime,
)

setup_tags()

app   = create_app()
relay = create_relay()

# ── Config ────────────────────────────────────────────────────

SECRET = os.environ.get("COOKIE_SECRET", "change-me-in-production")
signer = create_signer(SECRET)

messages = []
drafts = {}
files = {}  # fid -> file dict (serves via /file/{fid})
MAX_MESSAGES = 200
NAMES = ["Fox", "Owl", "Bear", "Wolf", "Hawk", "Lynx", "Crow", "Deer", "Hare", "Wren"]
REACTIONS = ["👍", "❤️", "😂", "😮", "😢", "🔥"]
MAX_FILE = 5 * 1024 * 1024  # 5MB

# ── Identity ──────────────────────────────────────────────────

def get_user(req) -> str | None:
    raw = req["cookies"].get("user", "")
    return signer.unsign(raw, max_age=None)

def ensure_user(req) -> str:
    user = get_user(req)
    if not user:
        user = secrets.choice(NAMES) + str(secrets.randbelow(100))
        set_cookie(req, "user", signer.sign(user), path="/", samesite="Lax")
    return user

# ── Helpers ───────────────────────────────────────────────────

def find_msg(mid):
    for m in messages:
        if m["id"] == mid: return m
    return None

def append_msg(msg):
    messages.append(msg)
    while len(messages) > MAX_MESSAGES:
        old = messages.pop(0)
        if old.get("file") and old["file"].get("id"):
            files.pop(old["file"]["id"], None)

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

def fmt_size(n):
    if n < 1024: return f"{n}B"
    if n < 1024 * 1024: return f"{n / 1024:.1f}KB"
    return f"{n / (1024 * 1024):.1f}MB"

# ── Rendering (sanitised) ────────────────────────────────────

def render_file(f):
    if not f: return Span()
    name = f["name"]  # already sanitize_filename'd at upload; html_tags escapes attrs+children
    fid = f["id"]
    if f["type"].startswith("image/"):
        return Div({"class": "msg-file"},
            Img({"src": f"/file/{fid}", "class": "msg-img", "alt": name}))
    return Div({"class": "msg-file"},
        A({"href": f"/file/{fid}", "download": name, "class": "file-link"},
            Span({"class": "file-icon"}, "📎"),
            f" {name} ({fmt_size(f['size'])})"))

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
        body = Span(m["text"]) if m["text"] else Span()
    return Div({"class": "msg", "id": f"msg-{mid}"},
        Div({"class": "msg-header"},
            Span({"class": "user"}, m["user"]),
            Span({"class": "ts"}, ts_str + edited),
            Div({"class": "msg-actions"}, *actions)),
        body,
        render_file(m.get("file")),
        render_reactions(m, user),
        render_picker(m))

def render_messages(user):
    items = [render_msg(m, user) for m in messages]
    for u, text in drafts.items():
        if text.strip():
            items.append(Div({"class": "msg draft"}, Span({"class": "user"}, u), Span(text), Span({"class": "typing"}, " ...")))
    return Div({"id": "chat"}, *items)

# ── Assets ────────────────────────────────────────────────────

FILE_JS = """\
function readFile(input) {
    const file = input.files[0];
    if (!file) return;
    if (file.size > %d) { alert('File too large (max 5MB)'); input.value = ''; return; }
    const reader = new FileReader();
    reader.onload = () => {
        const base64 = reader.result.split(',')[1];
        document.getElementById('file-name').textContent = file.name;
        document.getElementById('file-preview').style.display = 'flex';
        window._file = {name: file.name, type: file.type, size: file.size, data: base64};
    };
    reader.readAsDataURL(file);
}
function clearFile() {
    window._file = null;
    document.getElementById('file-input').value = '';
    document.getElementById('file-preview').style.display = 'none';
}
function sendMsg() {
    const inp = document.getElementById('inp');
    const text = inp.value.trim();
    const file = window._file;
    if (!text && !file) return;
    const body = {datastar: {text: text}};
    if (file) body.datastar.fileName = file.name, body.datastar.fileType = file.type, body.datastar.fileSize = file.size, body.datastar.fileData = file.data;
    fetch('/send', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    inp.value = '';
    clearFile();
}""" % MAX_FILE

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
.msg-file { margin-top: 0.3rem; }
.msg-img { max-width: 100%; max-height: 300px; border-radius: 0.5rem; cursor: pointer; }
.file-link { color: #4af; text-decoration: none; display: flex; align-items: center; gap: 0.3rem; font-size: 0.85rem; padding: 0.3rem 0.5rem; background: #151515; border: 1px solid #333; border-radius: 0.3rem; }
.file-link:hover { border-color: #555; }
.file-icon { font-size: 1rem; }
.file-preview { display: none; align-items: center; gap: 0.5rem; padding: 0.3rem 0.5rem; background: #151515; border: 1px solid #333; border-radius: 0.3rem; font-size: 0.8rem; color: #888; margin-bottom: 0.5rem; }
.file-preview button { background: none; border: none; color: #888; cursor: pointer; font-size: 0.9rem; }
.file-preview button:hover { color: #eee; }
input { width: 100%; padding: 0.75rem; background: #151515; border: 1px solid #333; border-radius: 0.5rem; color: #eee; font: inherit; font-size: 16px; box-sizing: border-box; }
input:focus { outline: 2px solid #e54; }
.controls { display: flex; gap: 0.5rem; align-items: center; }
.attach-btn { background: none; border: 1px solid #333; border-radius: 0.5rem; padding: 0.6rem; cursor: pointer; font-size: 1.1rem; color: #888; }
.attach-btn:hover { border-color: #555; color: #eee; }
button.send { padding: 0.75rem 1.5rem; background: #e54; border: none; border-radius: 0.5rem; color: #fff; cursor: pointer; font: inherit; }"""

# ── Ticker ────────────────────────────────────────────────────

async def _ticker():
    while True:
        await asyncio.sleep(30)
        relay.publish("chat.tick", None)

# ── Routes ────────────────────────────────────────────────────

@app.get("/")
async def home(req):
    user = ensure_user(req)
    return to_html(Html(
        Head(
            Meta({"charset": "UTF-8"}),
            Meta({"name": "viewport", "content": "width=device-width, initial-scale=1.0"}),
            Title("Wave Chat"),
            Script({"type": "module", "src": "https://cdn.jsdelivr.net/gh/starfederation/datastar@1.0.0-RC.8/bundles/datastar.js"}),
            Style(CSS),
            Script(FILE_JS)),
        Body(
            H2("Wave Chat"),
            P({"style": "color:#666; font-size:0.85rem"}, f"You are {user}"),
            Div({"class": "chat-box", "data-init": "@get('/stream')"}, render_messages(user)),
            Div({"id": "file-preview", "class": "file-preview"},
                Span({"id": "file-name"}),
                Button({"onclick": "clearFile()"}, "✕")),
            Div({"class": "controls"},
                Button({"class": "attach-btn", "onclick": "document.getElementById('file-input').click()"}, "📎"),
                Input({"type": "file", "id": "file-input", "style": "display:none", "onchange": "readFile(this)"}),
                Input({"type": "text", "id": "inp", "placeholder": "Type a message...", "autocomplete": "off",
                       "data-on:input__debounce.150ms": "@post('/typing?text=' + encodeURIComponent(el.value))",
                       "data-on:keydown": "if(event.key==='Enter'){sendMsg()}"}),
                Button({"class": "send", "onclick": "sendMsg()"}, "Send")))))

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
    try:
        # base64 is ~4/3 of decoded size, plus JSON wrapper overhead
        raw = await read_body(req, max_size=MAX_FILE * 2)
    except BodyTooLarge:
        return None

    data = json.loads(raw)
    s = data.get("datastar", data) if isinstance(data, dict) else data
    text = s.get("text", "").strip()

    file = None
    if s.get("fileData"):
        clean_type = validate_mime(s.get("fileType", "")) or "application/octet-stream"
        clean_name = sanitize_filename(s.get("fileName", "file"))
        raw_data = s["fileData"]

        if not validate_base64(raw_data, max_decoded_size=MAX_FILE):
            return None

        actual_size = len(raw_data) * 3 // 4
        if actual_size > MAX_FILE:
            return None

        fid = secrets.token_urlsafe(12)
        file = dict(id=fid, name=clean_name, type=clean_type, size=actual_size, data=raw_data)
        files[fid] = file

    if text or file:
        append_msg(dict(
            id=secrets.token_urlsafe(8), user=user, text=text,
            ts=time.time(), edited=False, reactions={},
            editing=False, show_picker=False, file=file))
        drafts.pop(user, None)
        relay.publish("chat.message", user)
    return None

@app.post("/delete")
async def delete_msg(req):
    user = ensure_user(req)
    m = find_msg(req["query"].get("id", ""))
    if m and m["user"] == user:
        if m.get("file") and m["file"].get("id"):
            files.pop(m["file"]["id"], None)
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

@app.get("/file/{fid}")
async def serve_file(req):
    """Serve an uploaded file by ID with proper headers."""
    fid = req["params"]["fid"]
    f = files.get(fid)
    if not f:
        return None  # 204 — could do 404 but need send for that
    raw = base64.b64decode(f["data"])
    send = req["internal_send"]
    headers = [
        [b"content-type", f["type"].encode()],
        [b"content-length", str(len(raw)).encode()],
        [b"content-disposition", f'inline; filename="{f["name"]}"'.encode()],
        [b"cache-control", b"private, max-age=3600, immutable"],
    ]
    await send({"type": "http.response.start", "status": 200, "headers": headers})
    await send({"type": "http.response.body", "body": raw})
    req["_sent"] = True
import secrets
from py_sse.router import Router
from py_sse.response import Response
from py_sse.stream import Stream
from py_sse.relay import Relay
from html_tags import setup_tags, patch_elements
setup_tags()

relay = Relay()
app = Router()

# State
messages = []  # [{id, user, text}]
drafts = {}    # user -> current typing text
NAMES = ["Fox", "Owl", "Bear", "Wolf", "Hawk", "Lynx", "Crow", "Deer", "Hare", "Wren"]

def get_user(req):
    return req.cookies.get("user")

def new_user():
    return secrets.choice(NAMES) + str(secrets.randbelow(100))

def ensure_user(req, res):
    "Get existing user or create one and set cookie on res"
    user = get_user(req)
    if not user:
        user = new_user()
        res.cookie("user", user, path="/", samesite="Lax")
    return user

def render_messages():
    items = [Div({"class": "msg"},
        Span({"class": "user"}, m["user"] + ": "),
        Span(m["text"])) for m in messages]
    for user, text in drafts.items():
        if text.strip():
            items.append(Div({"class": "msg draft"},
                Span({"class": "user"}, user + ": "),
                Span(text), Span({"class": "typing"}, " ...")))
    return Div({"id": "chat"}, *items)

CSS = """
body { font-family: system-ui; max-width: 500px; margin: 2rem auto; background: #0a0a0a; color: #eee; }
.chat-box { border: 1px solid #333; border-radius: 0.5rem; padding: 1rem; min-height: 300px; margin-bottom: 1rem; }
.msg { padding: 0.25rem 0; }
.user { font-weight: 700; color: #e54; }
.draft { opacity: 0.5; }
.typing { color: #666; font-style: italic; }
input { width: 100%; padding: 0.75rem; background: #151515; border: 1px solid #333; border-radius: 0.5rem; color: #eee; font: inherit; font-size: 16px; box-sizing: border-box; }
input:focus { outline: 2px solid #e54; }
.controls { display: flex; gap: 0.5rem; }
button { padding: 0.75rem 1.5rem; background: #e54; border: none; border-radius: 0.5rem; color: #fff; cursor: pointer; font: inherit; }
"""

@app.get("/")
async def home(req, send, closed):
    res = Response(send)
    user = ensure_user(req, res)
    await res.html(str(Html(
        Head(
            Meta({"charset": "UTF-8"}),
            Meta({"name": "viewport", "content": "width=device-width, initial-scale=1.0"}),
            Title("Wave Chat"),
            Script({"type": "module", "src": "https://cdn.jsdelivr.net/gh/starfederation/datastar@1.0.0-RC.8/bundles/datastar.js"}),
            Style(CSS)),
        Body(
            H2("Wave Chat"),
            P({"style": "color:#666; font-size:0.85rem"}, f"You are {user}"),
            Div({"class": "chat-box", "data-init": "@get('/stream')"},
                render_messages()),
            Div({"class": "controls"},
                Input({"type": "text", "id": "inp", "placeholder": "Type a message...",
                       "autocomplete": "off",
                       "data-on:input__debounce.150ms": "@post('/typing?text=' + encodeURIComponent(el.value))",
                       "data-on:keydown": "if(event.key==='Enter' && el.value.trim()){@post('/send?text=' + encodeURIComponent(el.value)); el.value=''}"}),
                Button({"data-on:click": "var inp=document.getElementById('inp'); if(inp.value.trim()){@post('/send?text='+encodeURIComponent(inp.value)); inp.value=''}"}, "Send"))))))

@app.get("/stream")
async def stream(req, send, closed):
    s = Stream(send, closed)
    await s.open()
    async for topic, data in s.alive(relay.subscribe("chat.*")):
        await s.send_event(patch_elements(str(render_messages())))

@app.post("/typing")
async def typing(req, send, closed):
    res = Response(send)
    user = ensure_user(req, res)
    text = req.query.get("text", "")
    drafts[user] = text
    relay.publish("chat.typing", user)
    await res.text("ok")

@app.post("/send")
async def send_msg(req, send, closed):
    res = Response(send)
    user = ensure_user(req, res)
    text = req.query.get("text", "").strip()
    if text:
        messages.append({"id": len(messages), "user": user, "text": text})
        drafts.pop(user, None)
        relay.publish("chat.message", user)
    await res.text("ok")

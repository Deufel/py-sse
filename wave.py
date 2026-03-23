from py_sse import *
import secrets
from html_tags import setup_tags
setup_tags()

relay = Relay()
app = Router()

messages = []
drafts = {}
NAMES = ["Fox", "Owl", "Bear", "Wolf", "Hawk", "Lynx", "Crow", "Deer", "Hare", "Wren"]

def ensure_user(req):
    user = req.cookies.get("user")
    if not user:
        user = secrets.choice(NAMES) + str(secrets.randbelow(100))
        req.cookie("user", user, path="/", samesite="Lax")
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

CSS = """..."""  # same as before

@app.get("/")
async def home(req):
    user = ensure_user(req)
    return Html(
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
                Button({"data-on:click": "var inp=document.getElementById('inp'); if(inp.value.trim()){@post('/send?text='+encodeURIComponent(inp.value)); inp.value=''}"}, "Send"))))

@app.get("/stream")
async def stream(req):
    yield patch_elements(render_messages())
    async for topic, data in relay.subscribe("chat.*"):
        yield patch_elements(render_messages())

@app.post("/typing")
async def typing(req):
    user = ensure_user(req)
    drafts[user] = req.query.get("text", "")
    relay.publish("chat.typing", user)
    return {"ok": True}

@app.post("/send")
async def send_msg(req):
    user = ensure_user(req)
    text = req.query.get("text", "").strip()
    if text:
        messages.append({"id": len(messages), "user": user, "text": text})
        drafts.pop(user, None)
        relay.publish("chat.message", user)
    return {"ok": True}

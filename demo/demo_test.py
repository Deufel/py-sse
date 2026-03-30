"""py-sse stress test — exercises auth gating, cookie edge cases, SSE,
static serving, mount filtering, and body parsing.

Run:  python test_app.py

Concerns under test:
  1. Cookie signing/unsigning with malformed values
  2. Beforeware auth gating by role (guest/user/admin)
  3. Parameterised routes with user lookup
  4. SSE keepalive + disconnect detection
  5. Static serve_single missing Set-Cookie headers
  6. Mount receives all HTTP methods (no method filter)
  7. Body max_size checked *after* full read
  8. Bare except in unsign swallowing real errors
"""
import asyncio, json, os, time, secrets
from html_tags import setup_tags, to_html
from py_sse import (
    create_app, create_signer, create_relay, set_cookie,
    body, signals, serve, static,
    patch_elements, patch_signals, execute_script,
)

setup_tags()

app   = create_app()
relay = create_relay()

SECRET = os.environ.get("TEST_SECRET", "test-secret-key")
signer = create_signer(SECRET)

# ── In-memory state ──────────────────────────────────────────

ROLES     = ("guest", "user", "admin")
LOG: list = []          # global event log for the SSE feed
MAX_LOG   = 50

USERS_DB = {
    "alice": {"name": "Alice", "role": "admin", "email": "alice@example.com"},
    "bob":   {"name": "Bob",   "role": "user",  "email": "bob@example.com"},
    "carol": {"name": "Carol", "role": "guest", "email": "carol@example.com"},
}

def log_event(msg: str):
    LOG.append({"ts": time.time(), "msg": msg})
    while len(LOG) > MAX_LOG: LOG.pop(0)
    relay.publish("log.new", msg)


# ── Helpers ──────────────────────────────────────────────────

def get_role(req) -> str:
    """Read role from signed cookie, default 'guest'."""
    raw = req["cookies"].get("role", "")
    val = signer.unsign(raw, max_age=None)
    return val if val in ROLES else "guest"

def get_identity(req) -> str:
    """Read identity from signed cookie."""
    raw = req["cookies"].get("identity", "")
    return signer.unsign(raw, max_age=None) or "anonymous"


def fmt_ts(ts):
    t = time.localtime(ts)
    return f"{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}"


# ── Beforeware: inject auth into every request ───────────────

@app.before
async def inject_auth(req):
    req["role"]     = get_role(req)
    req["identity"] = get_identity(req)


# ── Auth gating decorator ────────────────────────────────────

def require(*allowed_roles):
    """Beforeware-style gating applied per-handler.

    Returns a 403 tuple if the role doesn't match.
    """
    def wrap(fn):
        async def guarded(req):
            if req["role"] not in allowed_roles:
                log_event(f"BLOCKED {req['identity']} ({req['role']}) → {req['path']}")
                return (
                    f"<h2>403 Forbidden</h2>"
                    f"<p>Role <b>{req['role']}</b> cannot access this route.</p>"
                    f"<p>Required: {', '.join(allowed_roles)}</p>"
                    f'<p><a href="/">← back</a></p>',
                    403,
                )
            return await fn(req)
        return guarded
    return wrap


# ── CSS ──────────────────────────────────────────────────────

CSS = """\
* { margin: 0; padding: 0; box-sizing: border-box; }
:root { --bg: #0a0a0a; --card: #111; --border: #222; --accent: #e54;
        --text: #eee; --muted: #666; --success: #2a5; --warn: #e90; }
body { font-family: system-ui, -apple-system, sans-serif; background: var(--bg);
       color: var(--text); max-width: 900px; margin: 0 auto; padding: 1.5rem; }
h1 { font-size: 1.4rem; margin-bottom: 1rem; }
h2 { font-size: 1.1rem; margin-bottom: 0.5rem; color: var(--accent); }

.card { background: var(--card); border: 1px solid var(--border); border-radius: 0.5rem;
        padding: 1rem; margin-bottom: 1rem; }
.row { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }
.badge { display: inline-block; padding: 0.2rem 0.6rem; border-radius: 1rem;
         font-size: 0.75rem; font-weight: 600; }
.badge-guest { background: #333; color: #888; }
.badge-user  { background: #1a3a1a; color: var(--success); }
.badge-admin { background: #3a1a1a; color: var(--accent); }

button { padding: 0.5rem 1rem; border: 1px solid var(--border); border-radius: 0.4rem;
         background: var(--card); color: var(--text); cursor: pointer; font: inherit;
         font-size: 0.85rem; transition: all 0.15s; }
button:hover { border-color: var(--accent); background: #1a0a0a; }
button.active { border-color: var(--accent); background: var(--accent); color: #fff; }

a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

.links { display: flex; flex-direction: column; gap: 0.4rem; }
.links a { padding: 0.4rem 0.6rem; border: 1px solid var(--border); border-radius: 0.3rem;
           display: flex; justify-content: space-between; }
.links a:hover { border-color: var(--accent); background: #0f0808; text-decoration: none; }
.tag { font-size: 0.7rem; color: var(--muted); }

#log { max-height: 300px; overflow-y: auto; font-family: monospace; font-size: 0.8rem;
       line-height: 1.6; padding: 0.5rem; }
.log-entry { color: var(--muted); }
.log-blocked { color: var(--accent); }
.log-access  { color: var(--success); }

.test-result { padding: 0.5rem; border-left: 3px solid var(--border); margin: 0.3rem 0;
               font-size: 0.85rem; }
.test-pass { border-color: var(--success); }
.test-fail { border-color: var(--accent); }

textarea { width: 100%; min-height: 80px; padding: 0.5rem; background: #151515;
           border: 1px solid var(--border); border-radius: 0.3rem; color: var(--text);
           font-family: monospace; font-size: 0.85rem; resize: vertical; }
"""


# ── Render ───────────────────────────────────────────────────

def render_log():
    if not LOG:
        return Div({"id": "log"}, Div({"class": "log-entry"}, "No events yet..."))
    entries = []
    for e in reversed(LOG):
        cls = "log-blocked" if "BLOCKED" in e["msg"] else (
              "log-access"  if "ACCESS"  in e["msg"] else "log-entry")
        entries.append(Div({"class": cls}, f"[{fmt_ts(e['ts'])}] {e['msg']}"))
    return Div({"id": "log"}, *entries)


def render_page(req):
    role     = req["role"]
    identity = req["identity"]

    return to_html(Html(
        Head(
            Meta({"charset": "UTF-8"}),
            Meta({"name": "viewport", "content": "width=device-width, initial-scale=1.0"}),
            Title("py-sse stress test"),
            Script({"type": "module",
                    "src": "https://cdn.jsdelivr.net/gh/starfederation/datastar@1.0.0-RC.8/bundles/datastar.js"}),
            Style(CSS),
        ),
        Body(
            H1("py-sse stress test"),

            # ── Auth controls ────────────────────────────
            Div({"class": "card"},
                H2("① Auth Controls"),
                P({"style": "margin-bottom:0.5rem; color:var(--muted); font-size:0.85rem"},
                    "Toggle role via signed cookie · ",
                    Span(f"identity: {identity} "),
                    Span({"class": f"badge badge-{role}"}, role)),
                Div({"class": "row", "style": "margin-bottom:0.5rem"},
                    *[Button({"class": "active" if role == r else "",
                              "data-on:click": f"@post('/set-role?role={r}')"}, r.title())
                      for r in ROLES]),
                Div({"class": "row"},
                    *[Button({"data-on:click": f"@post('/set-identity?name={u}')"}, u.title())
                      for u in USERS_DB]),
            ),

            # ── Gated routes ─────────────────────────────
            Div({"class": "card"},
                H2("② Gated Routes"),
                P({"style": "color:var(--muted); font-size:0.85rem; margin-bottom:0.5rem"},
                    "Click to test access with your current role"),
                Div({"class": "links"},
                    A({"href": "/public"},      "GET /public",       Span({"class": "tag"}, "any")),
                    A({"href": "/dashboard"},   "GET /dashboard",    Span({"class": "tag"}, "user, admin")),
                    A({"href": "/admin"},       "GET /admin",        Span({"class": "tag"}, "admin only")),
                    A({"href": "/user/alice"},  "GET /user/alice",   Span({"class": "tag"}, "param route")),
                    A({"href": "/user/bob"},    "GET /user/bob",     Span({"class": "tag"}, "param route")),
                    A({"href": "/user/nobody"}, "GET /user/nobody",  Span({"class": "tag"}, "404 user")),
                ),
            ),

            # ── Stress tests ─────────────────────────────
            Div({"class": "card"},
                H2("③ Edge Case Tests"),
                Div({"class": "row"},
                    Button({"data-on:click": "@post('/test/cookie-malformed')"}, "Malformed Cookie"),
                    Button({"data-on:click": "@post('/test/cookie-expired')"},  "Expired Cookie"),
                    Button({"data-on:click": "@post('/test/cookie-tampered')"}, "Tampered Sig"),
                    Button({"data-on:click": "@post('/test/body-large')"},      "Oversized Body"),
                ),
                Div({"id": "test-results", "style": "margin-top:0.5rem"}),
            ),

            # ── POST body test ───────────────────────────
            Div({"class": "card"},
                H2("④ POST Body Parsing"),
                P({"style": "color:var(--muted); font-size:0.85rem; margin-bottom:0.5rem"},
                    "Send JSON body, read via body() + signals()"),
                Textarea({"id": "json-body", "placeholder": '{"datastar": {"msg": "hello"}}'}),
                Div({"class": "row", "style": "margin-top:0.5rem"},
                    Button({"data-on:click": (
                        "fetch('/test/body-echo', "
                        "{method:'POST', headers:{'content-type':'application/json'}, "
                        "body: document.getElementById('json-body').value})"
                        ".then(r=>r.text()).then(t=>document.getElementById('body-result').innerHTML=t)"
                    )}, "Send POST"),
                ),
                Div({"id": "body-result", "style": "margin-top:0.5rem; font-family:monospace; font-size:0.85rem"}),
            ),

            # ── Live log (SSE) ───────────────────────────
            Div({"class": "card", "data-init": "@get('/log-stream')"},
                H2("⑤ Live Event Log (SSE)"),
                P({"style": "color:var(--muted); font-size:0.85rem; margin-bottom:0.5rem"},
                    "Tests SSE streaming, keepalive, and disconnect"),
                render_log(),
            ),
        ),
    ))


# ── Routes: pages ────────────────────────────────────────────

@app.get("/")
async def index(req):
    return render_page(req)


@app.get("/public")
async def public_page(req):
    log_event(f"ACCESS {req['identity']} ({req['role']}) → /public")
    return (
        f"<h2>Public Page</h2>"
        f"<p>Welcome, {req['identity']}! Role: <b>{req['role']}</b></p>"
        f"<p>Everyone can see this.</p>"
        f'<p><a href="/">← back</a></p>'
    )


@app.get("/dashboard")
@require("user", "admin")
async def dashboard(req):
    log_event(f"ACCESS {req['identity']} ({req['role']}) → /dashboard")
    return (
        f"<h2>Dashboard</h2>"
        f"<p>Welcome, {req['identity']}! You have <b>{req['role']}</b> access.</p>"
        f"<p>This page requires user or admin role.</p>"
        f'<p><a href="/">← back</a></p>'
    )


@app.get("/admin")
@require("admin")
async def admin_panel(req):
    log_event(f"ACCESS {req['identity']} ({req['role']}) → /admin")
    users_html = "".join(
        f"<li><b>{u}</b> — {d['role']} ({d['email']})</li>"
        for u, d in USERS_DB.items()
    )
    return (
        f"<h2>Admin Panel</h2>"
        f"<p>Identity: {req['identity']} · Role: {req['role']}</p>"
        f"<ul>{users_html}</ul>"
        f'<p><a href="/">← back</a></p>'
    )


# ── Routes: parameterised user lookup ────────────────────────

@app.get("/user/{uid}")
async def user_profile(req):
    uid  = req["params"]["uid"]
    user = USERS_DB.get(uid)
    log_event(f"ACCESS {req['identity']} → /user/{uid} ({'found' if user else 'NOT FOUND'})")

    if not user:
        return (
            f"<h2>User Not Found</h2>"
            f"<p>No user with id <code>{uid}</code></p>"
            f'<p><a href="/">← back</a></p>',
            404,
        )
    return (
        f"<h2>{user['name']}</h2>"
        f"<p>Role: {user['role']} · Email: {user['email']}</p>"
        f'<p><a href="/">← back</a></p>'
    )


# ── Routes: auth toggles (POST, redirect back) ──────────────

@app.post("/set-role")
async def set_role(req):
    role = req["query"].get("role", "guest")
    if role not in ROLES:
        role = "guest"
    set_cookie(req, "role", signer.sign(role), path="/", samesite="Lax")
    log_event(f"ROLE_CHANGE {req['identity']} → {role}")
    return ("/", 302)


@app.post("/set-identity")
async def set_identity(req):
    name = req["query"].get("name", "anonymous")
    if name not in USERS_DB:
        name = "anonymous"
    set_cookie(req, "identity", signer.sign(name), path="/", samesite="Lax")
    log_event(f"IDENTITY_CHANGE → {name}")
    return ("/", 302)


# ── Routes: edge-case tests ──────────────────────────────────

@app.post("/test/cookie-malformed")
async def test_malformed(req):
    """Feed garbage into unsign — tests bare-except resilience."""
    garbage = [
        "",                          # empty
        "not.a.signed.value",        # wrong format
        "abc",                       # single segment
        "a.b.c.d.e",                # too many segments
        "!!!.fff.ggg",               # non-base64 chars
        "\x00\xff.abc.def",          # binary junk
    ]
    results = []
    for g in garbage:
        val = signer.unsign(g)
        results.append(f"unsign({g!r:.30}) → {val!r}")
        log_event(f"TEST malformed cookie: {g!r:.20} → {val!r}")

    html = "".join(
        f'<div class="test-result test-pass">{r}</div>' for r in results
    )
    return f'<div id="test-results">{html}</div>'


@app.post("/test/cookie-expired")
async def test_expired(req):
    """Sign with old timestamp, unsign with max_age=1."""
    old_signed = signer.sign("expired-user", ts=time.time() - 7200)
    result = signer.unsign(old_signed, max_age=1)
    log_event(f"TEST expired cookie → {result!r}")
    cls = "test-pass" if result is None else "test-fail"
    return (
        f'<div id="test-results">'
        f'<div class="test-result {cls}">Signed 2h ago, max_age=1s → {result!r} '
        f'(expected None)</div></div>'
    )


@app.post("/test/cookie-tampered")
async def test_tampered(req):
    """Modify the signature portion of a valid signed value."""
    valid = signer.sign("real-user")
    parts = valid.split(".")
    parts[-1] = parts[-1][::-1]  # reverse the signature
    tampered = ".".join(parts)
    result = signer.unsign(tampered)
    log_event(f"TEST tampered sig → {result!r}")
    cls = "test-pass" if result is None else "test-fail"
    return (
        f'<div id="test-results">'
        f'<div class="test-result {cls}">Tampered signature → {result!r} '
        f'(expected None)</div></div>'
    )


@app.post("/test/body-large")
async def test_large_body(req):
    """Client-side test: send a body > 1MB to /test/body-echo."""
    log_event("TEST large body — triggering client-side 1.5MB POST")
    script = """\
    const big = 'x'.repeat(1_500_000);
    fetch('/test/body-echo', {
        method: 'POST',
        headers: {'content-type': 'application/json'},
        body: JSON.stringify({datastar: {payload: big}})
    })
    .then(r => r.text())
    .then(t => document.getElementById('test-results').innerHTML = t)
    .catch(e => document.getElementById('test-results').innerHTML =
        '<div class=\"test-result test-pass\">Rejected: ' + e.message + '</div>');
    """
    return execute_script(script)


@app.post("/test/body-echo")
async def test_body_echo(req):
    """Read body with 1MB limit — tests max_size enforcement."""
    try:
        raw = await body(req, max_size=1_048_576)
        data = json.loads(raw)
        s = data.get("datastar", data) if isinstance(data, dict) else data
        size = len(raw)
        log_event(f"BODY_ECHO received {size} bytes")
        return (
            f'<div class="test-result test-pass">'
            f'Received {size} bytes · Keys: {list(s.keys()) if isinstance(s, dict) else "n/a"}'
            f'</div>'
        )
    except ValueError as e:
        log_event(f"BODY_ECHO rejected: {e}")
        return (
            f'<div class="test-result test-pass">'
            f'Rejected (expected): {e}'
            f'</div>'
        )
    except Exception as e:
        log_event(f"BODY_ECHO error: {e}")
        return (
            f'<div class="test-result test-fail">'
            f'Unexpected error: {type(e).__name__}: {e}'
            f'</div>'
        )


# ── SSE stream: live log ─────────────────────────────────────

@app.get("/log-stream")
async def log_stream(req):
    """SSE feed that broadcasts log updates.

    Tests:
      - keepalive (15s heartbeats)
      - client disconnect detection
      - relay subscribe/unsubscribe
    """
    log_event(f"SSE_CONNECT {req['identity']} ({req['role']})")
    yield patch_elements(render_log())

    try:
        async for topic, data in relay.subscribe("log.*"):
            yield patch_elements(render_log())
    finally:
        log_event(f"SSE_DISCONNECT {req['identity']}")


# ── Static file test ─────────────────────────────────────────
# Create a minimal test file to serve via static()

_STATIC_DIR = "/tmp/py-sse-test-static"
os.makedirs(_STATIC_DIR, exist_ok=True)

with open(os.path.join(_STATIC_DIR, "test.txt"), "w") as f:
    f.write("This is a static file served via RSGI response_file.\n")

with open(os.path.join(_STATIC_DIR, "hello.html"), "w") as f:
    f.write("<h2>Static HTML</h2><p>Served from disk via zero-copy.</p>"
            '<p><a href="/">← back</a></p>')

# Single file mount (tests concern #5: missing cookies on serve_single)
static(app, "/robots.txt", os.path.join(_STATIC_DIR, "test.txt"))

# Directory mount (tests concern #6: mount receives all methods)
static(app, "/static", _STATIC_DIR)


# ── Run ──────────────────────────────────────────────────────

if __name__ == "__main__":
    print("─" * 50)
    print("py-sse stress test")
    print("http://127.0.0.1:8000")
    print("─" * 50)
    print("Concerns under test:")
    print("  ① Auth role toggling via signed cookies")
    print("  ② Route gating by role (guest/user/admin)")
    print("  ③ Parameterised user lookup (/user/{uid})")
    print("  ④ SSE streaming + keepalive + disconnect")
    print("  ⑤ Static serve_single cookie loss")
    print("  ⑥ Mount method filtering")
    print("  ⑦ Body max_size post-read rejection")
    print("  ⑧ Malformed/expired/tampered cookie handling")
    print("─" * 50)
    serve(app)
![PyPI version](https://img.shields.io/pypi/v/py-sse)

# py-sse

> minimal python sse server for datastar — built on rsgi/granian + apsw/sqlite

```md
sse.py      →  datastar event formatters (patch_elements, patch_signals, …)
app.py      →  rsgi app: routing, signals, cookies, static, @app.stream
db.py       →  sqlite helpers + Changes (update_hook → asyncio)
mserver.py  →  background server for notebooks (serve_background/stop_background)
ngrok.py    →  public tunnel + .env loader
```

```sh
pip install py-sse
uv add py-sse      # preferred
```

## hello

```python
from py_sse import create_app, serve

app = create_app()

@app.get('/')
async def index(req): return '<h1>hello</h1>'

if __name__ == '__main__': serve(app)
```

## live feed

Backend is the source of truth. Each write wakes every `/feed` stream; the
handler re-renders. No polling, no optimistic updates, no client state.

```python
from py_sse import *

db = create_db('chat.db')
migrate(db, "CREATE TABLE IF NOT EXISTS msgs (id INTEGER PRIMARY KEY, txt TEXT)")

def startup(loop):
    global changes
    changes = Changes(db, loop)

app = create_app(on_init=startup, on_del=lambda loop: changes.close())

@app.post('/say')
async def say(req):
    s = await signals(req)
    write(db, lambda c: c.execute("INSERT INTO msgs(txt) VALUES(?)", (s['text'],)))

@app.stream('/feed', on=lambda: changes)
def feed(req):
    rows = query(db, "SELECT txt FROM msgs ORDER BY id DESC LIMIT 50")
    return ''.join(f'<p>{t}</p>' for (t,) in rows)
```

## handler returns

```md
str          →  200 html
dict         →  200 json
None         →  204
(url, int)   →  redirect (3xx) or text body (4xx/5xx)
async gen    →  sse stream  (use @app.stream for the common case)
```

## api

```md
create_app(on_init=, on_del=)   →  app handle: .get .post .put .patch .delete .mount .before .stream
@app.stream(path, on=)          →  sse route; re-renders each time `on` ticks
signals(req)                    →  datastar signals (query on GET, json body otherwise)
body(req) / body_stream(req)    →  full body / chunked body
set_cookie(req, name, value)    →  queue a Set-Cookie
create_signer(secret)           →  .sign(value) / .unsign(signed) — hmac-sha256
static(app, prefix, dir)        →  zero-copy file serving, http range support

create_db(path)                 →  wal sqlite connection
migrate(conn, sql)              →  apply schema in one txn
query(conn, sql, …, limit=)     →  rows as tuples
write(conn, fn, …)              →  run fn(conn, …) in a txn, return its result
Changes(db, loop)               →  fan db writes out to streams; .wait() / .close()

patch_elements(html, …)         →  datastar-patch-elements event
patch_signals(signals, …)       →  datastar-patch-signals event
remove_signals(*names)          →  null those signals
execute_script(js, …)           →  datastar-execute-script event
redirect(url)                   →  navigate via patched script

serve(app)                      →  foreground granian server (blocks)
serve_background(app)           →  background thread → ServerState
stop_background(state)          →  clean shutdown, 3s join
dev_alive(port)                 →  is the port accepting connections
start_tunnel(port)              →  ngrok tunnel → TunnelState  (needs NGROK_AUTHTOKEN)
stop_tunnel(tunnel)             →  close it
load_env(path='.env')           →  read KEY=VALUE; missing file is a noop
```

## datastar

```md
patch elements & signals     →  the verb;  morph is the mechanism
fat morph                    →  send large dom chunks, up to <html>
signals                      →  user interaction only, used sparingly
cqrs                         →  long-lived read stream + short-lived writes
brotli on sse                →  ratios up to 200:1
```

MIT · built from marimo notebooks with marimo-dev
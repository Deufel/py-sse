![PyPI version](https://img.shields.io/pypi/v/py-sse)

> [!WARNING]
> Under active development — May 2026

# py_sse

> minimal python sse server

A small framework for building [Datastar](https://data-star.dev) apps over
Server-Sent Events. Runs on [Granian](https://github.com/emmett-framework/granian)
via RSGI, talks to SQLite through [apsw](https://github.com/rogerbinns/apsw),
and pushes live DOM updates from the database with no client-side polling.

```sh
pip install py-sse
```

## Hello

```python
from py_sse import create_app, serve

app = create_app()

@app.get('/')
async def index(req):
    return '<h1>hello</h1>'

if __name__ == '__main__':
    serve(app)
```

Handlers return one of: `str` → 200 HTML, `dict` → 200 JSON, `None` → 204,
`(url, status)` → redirect/text, or an async generator → raw SSE stream.

## Live feed

`@app.stream` is the core feature: register an SSE endpoint that re-renders
every time the database changes. `Changes` bridges SQLite's update hook to
asyncio, so any `write()` wakes every connected stream.

```python
from py_sse import (create_app, create_db, migrate, query, write,
                    signals, Changes, serve)

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
    return ''.join(f'<p>{txt}</p>' for (txt,) in rows)
```

Post to `/say` from any tab and every tab subscribed to `/feed` re-renders.

## API

**App** — `create_app(routes=None, *, on_init=None, on_del=None)`. Returns a
handle with `.get/.post/.put/.patch/.delete(path)`, `.mount(prefix, fn)`,
`.before(fn, *, methods=None)`, and `.stream(path, *, on)`. Paths support
`{name}` params. `on_init(loop)`/`on_del(loop)` run at startup/shutdown for
wiring shared state onto the right loop.

**Requests** — `signals(req)` reads Datastar signals (query param on GET, JSON
body otherwise). `body(req)` / `body_stream(req)` read the raw payload.
`set_cookie(req, name, value, **opts)` queues a Set-Cookie.

**SSE events** — `patch_elements`, `patch_signals`, `remove_signals`,
`execute_script`, `redirect`. `@app.stream` calls `patch_elements` for you.

**DB** — `create_db(path)` opens a WAL connection. `migrate(conn, sql)` applies
schema idempotently. `query(conn, sql, bindings=(), *, limit=1000)` returns
rows. `write(conn, fn, *args)` runs `fn` in a transaction. `Changes(db, loop)`
fans writes out to streams.

**Static** — `static(app, url_prefix, directory)` serves a file or directory
with HTTP range support and a directory-traversal guard.

**Cookies** — `create_signer(secret)` returns an HMAC-SHA256 signer with
`.sign(value)` / `.unsign(signed, max_age=3600)`.

**Serving** — `serve(app, *, host, port, **kwargs)` runs in the foreground.
`serve_background(app, host, port)` returns a `ServerState` you stop with
`stop_background(state)`; `dev_alive(state)` checks the port. Useful for
notebooks. `start_tunnel(port)` / `stop_tunnel(t)` expose a local server via
ngrok (requires `ngrok` and `NGROK_AUTHTOKEN`).

## License

MIT
## License

MIT
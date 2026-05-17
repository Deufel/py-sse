![PyPI version](https://img.shields.io/pypi/v/py-sse)

> [!WARNING]
> Under active development — May 2026

# py_sse

A minimal SSE web framework for Python 3.13+. Real-time
hypermedia over HTTP/1.1 with brotli-compressed Server-Sent Events.

One OS thread per connection. Topic-scoped pub/sub via hierarchical
subjects. No async/await. Pure stdlib + brotli + apsw.

## Install

```
uv add py-sse
```

## Hello world

```python
from py_sse import serve, html

def get_root(req):
    return html("<h1>hello, world</h1>")

serve([("GET", "/", get_root)], host="0.0.0.0", port=8000)
```

## Real-time updates

```python
from py_sse import serve, html, Changes, Database, stream_handler

db = Database("app.db", schema="CREATE TABLE IF NOT EXISTS msg (id INTEGER PRIMARY KEY, text TEXT)")

def post_msg(req):
    db.execute("INSERT INTO msg (text) VALUES (?)", (req["body"].decode(),))
    db.changes.notify("msg")
    return ("", [], b"")

@stream_handler(
    resource_id_fn=lambda req: "msgs",
    subscribe_to=lambda req: "msg",
)
def get_msgs(req):
    rows = db.all("SELECT id, text FROM msg ORDER BY id DESC")
    body = "<ul>" + "".join(f"<li>{t}</li>" for _, t in rows) + "</ul>"
    return html(f"<html><body>{body}</body></html>")

serve(
    [("GET", "/", get_msgs), ("POST", "/msg", post_msg)],
    changes=db.changes,
)
```

## Design

* **Pages are database snapshots.** Every render is the truth as of
  query time.
* **Fat morphs.** SSE frames send the whole page; idiomorph patches
  the DOM; brotli compresses across frames.
* **Topic-scoped pub/sub.** `changes.notify("game.5.score")` wakes
  only viewers waiting on matching patterns.
* **Graceful degradation.** Past the live cap, viewers get one-shot
  HTML with `data-on-interval` for polling.
* **One thread per connection.** The thread IS the subscription.
  When it dies, the subscription dies. No registry.

## License

MIT
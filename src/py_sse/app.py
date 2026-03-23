from __future__ import annotations
import asyncio
import inspect
import json
import traceback
from urllib.parse import parse_qs
from html_tags import to_html, Tag

def internal_parse_request(scope: dict, receive) -> dict:
    """Parse an ASGI scope into a plain request dict."""
    headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
    qs = scope.get("query_string", b"").decode()
    raw_cookies = headers.get("cookie", "")
    return {
        "path": scope["path"],
        "method": scope.get("method", "GET"),
        "headers": headers,
        "query": {k: v[0] if len(v) == 1 else v for k, v in parse_qs(qs).items()},
        "cookies": dict(
            pair.strip().split("=", 1)
            for pair in raw_cookies.split(";")
            if "=" in pair
        ),
        "internal_receive": receive,
        "internal_set_cookies": [],
    }

async def body(req: dict) -> bytes:
    """Read the full request body."""
    receive = req["internal_receive"]
    chunks = []
    while True:
        msg = await receive()
        chunks.append(msg.get("body", b""))
        if not msg.get("more_body"):
            break
    return b"".join(chunks)

async def json_body(req: dict) -> dict:
    """Read and parse JSON request body."""
    return json.loads(await body(req))

async def signals(req: dict) -> dict:
    """Read Datastar signals from the request.
 
    GET requests carry signals as a `datastar` query parameter (JSON-encoded).
    Other methods carry signals in the JSON body.
    """
    if req["method"] == "GET":
        raw = req["query"].get("datastar", "{}")
        return json.loads(raw) if isinstance(raw, str) else raw
    data = await json_body(req)
    return data.get("datastar", data) if isinstance(data, dict) else data

def set_cookie(req: dict, name: str, value: str, **opts) -> None:
    """Queue a Set-Cookie header on the request."""
    req["internal_set_cookies"].append((name, value, opts))

def internal_serialize_cookie(name: str, value: str, opts: dict) -> bytes:
    """Serialize a single Set-Cookie header value."""
    parts = [f"{name}={value}"]
    for k, v in opts.items():
        k = k.replace("_", "-")
        if isinstance(v, bool):
            if v:
                parts.append(k)
        else:
            parts.append(f"{k}={v}")
    return "; ".join(parts).encode()

def internal_cookie_headers(req: dict) -> list:
    """Build Set-Cookie headers from queued cookies on a request."""
    return [
        [b"set-cookie", internal_serialize_cookie(n, v, o)]
        for n, v, o in req["internal_set_cookies"]
    ]

async def internal_send_response(send, req: dict, status: int, content_type: bytes, content: str | bytes):
    """Send a complete HTTP response."""
    if isinstance(content, str):
        content = content.encode()
    headers = [[b"content-type", content_type]] + internal_cookie_headers(req)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": content})

async def send_html(send, req: dict, content: str | Tag, status: int = 200):
    """Send an HTML response. Tag objects are rendered automatically."""
    if isinstance(content, Tag):
        content = to_html(content)
    await internal_send_response(send, req, status, b"text/html; charset=utf-8", content)

async def send_json(send, req: dict, data: dict, status: int = 200):
    """Send a JSON response."""
    await internal_send_response(send, req, status, b"application/json", json.dumps(data))

async def send_text(send, req: dict, text: str, status: int = 200):
    """Send a plain text response."""
    await internal_send_response(send, req, status, b"text/plain; charset=utf-8", text)

async def send_redirect(send, req: dict, url: str, status: int = 302):
    """Send a redirect response."""
    headers = [[b"location", url.encode()]] + internal_cookie_headers(req)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": b""})

async def send_error(send, req: dict, status: int = 500, message: str = "Internal Server Error"):
    """Send an error response."""
    await internal_send_response(send, req, status, b"text/plain; charset=utf-8", message)

async def internal_open_sse(send, req: dict, *, compress: bool = False):
    """Open an SSE stream. Returns a brotli compressor or None."""
    headers = [
        [b"content-type", b"text/event-stream"],
        [b"cache-control", b"no-cache"],
        [b"connection", b"keep-alive"],
        [b"x-accel-buffering", b"no"],
    ] + internal_cookie_headers(req)
 
    compressor = None
    if compress:
        try:
            import brotli
            headers.append([b"content-encoding", b"br"])
            compressor = brotli.Compressor(mode=brotli.MODE_TEXT, quality=1)
        except ImportError:
            pass  # No brotli available, send uncompressed
 
    await send({"type": "http.response.start", "status": 200, "headers": headers})
    return compressor

async def internal_send_sse_event(send, compressor, data: str):
    """Send a single SSE event over an open stream."""
    raw = data.encode()
    if compressor:
        raw = compressor.process(raw) + compressor.flush()
    await send({"type": "http.response.body", "body": raw, "more_body": True})

async def internal_close_sse(send, compressor):
    """Close an SSE stream cleanly."""
    tail = b""
    if compressor:
        tail = compressor.finish()
    await send({"type": "http.response.body", "body": tail})

async def internal_keepalive(send, compressor, closed: asyncio.Event, interval: int = 15):
    """Send SSE keepalive comments to prevent proxy timeouts."""
    try:
        while not closed.is_set():
            await asyncio.sleep(interval)
            if not closed.is_set():
                await internal_send_sse_event(send, compressor, ":\n\n")
    except asyncio.CancelledError:
        pass

async def internal_watch_disconnect(receive, closed: asyncio.Event):
    """Watch for client disconnect. Sets the event when detected."""
    try:
        while True:
            msg = await receive()
            if msg.get("type") == "http.disconnect":
                closed.set()
                return
    except Exception:
        closed.set()

from __future__ import annotations
import asyncio, inspect, json, traceback, re
from urllib.parse import parse_qs
from html_tags import to_html, Tag

PARAM_RE = re.compile('\\{(\\w+)\\}')

def _parse_request(scope: dict, receive) -> dict:
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

def _serialize_cookie(name: str, value: str, opts: dict) -> bytes:
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

def _cookie_headers(req: dict) -> list:
    """Build Set-Cookie headers from queued cookies on a request."""
    return [
        [b"set-cookie", _serialize_cookie(n, v, o)]
        for n, v, o in req["internal_set_cookies"]
    ]

async def _send_response(send, req: dict, status: int, content_type: bytes, content: str | bytes):
    """Send a complete HTTP response."""
    if isinstance(content, str):
        content = content.encode()
    headers = [[b"content-type", content_type]] + _cookie_headers(req)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": content})

async def send_html(send, req: dict, content: str | Tag, status: int = 200):
    """Send an HTML response. Tag objects are rendered automatically."""
    if isinstance(content, Tag):
        content = to_html(content)
    await _send_response(send, req, status, b"text/html; charset=utf-8", content)

async def send_json(send, req: dict, data: dict, status: int = 200):
    """Send a JSON response."""
    await _send_response(send, req, status, b"application/json", json.dumps(data))

async def send_text(send, req: dict, text: str, status: int = 200):
    """Send a plain text response."""
    await _send_response(send, req, status, b"text/plain; charset=utf-8", text)

async def send_redirect(send, req: dict, url: str, status: int = 302):
    """Send a redirect response."""
    headers = [[b"location", url.encode()]] + _cookie_headers(req)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": b""})

async def send_error(send, req: dict, status: int = 500, message: str = "Internal Server Error"):
    """Send an error response."""
    await _send_response(send, req, status, b"text/plain; charset=utf-8", message)

async def _open_sse(send, req: dict, *, compress: bool = False):
    """Open an SSE stream. Returns a brotli compressor or None."""
    headers = [
        [b"content-type", b"text/event-stream"],
        [b"cache-control", b"no-cache"],
        [b"connection", b"keep-alive"],
        [b"x-accel-buffering", b"no"],
    ] + _cookie_headers(req)

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

async def _send_sse_event(send, compressor, data: str):
    """Send a single SSE event over an open stream."""
    raw = data.encode()
    if compressor:
        raw = compressor.process(raw) + compressor.flush()
    await send({"type": "http.response.body", "body": raw, "more_body": True})

async def _close_sse(send, compressor):
    """Close an SSE stream cleanly."""
    tail = b""
    if compressor:
        tail = compressor.finish()
    await send({"type": "http.response.body", "body": tail})

async def _keepalive(send, compressor, closed: asyncio.Event, interval: int = 15):
    """Send SSE keepalive comments to prevent proxy timeouts."""
    try:
        while not closed.is_set():
            await asyncio.sleep(interval)
            if not closed.is_set():
                await _send_sse_event(send, compressor, ":\n\n")
    except asyncio.CancelledError:
        pass

async def _watch_disconnect(receive, closed: asyncio.Event):
    """Watch for client disconnect. Sets the event when detected."""
    try:
        while True:
            msg = await receive()
            if msg.get("type") == "http.disconnect":
                closed.set()
                return
    except Exception:
        closed.set()

def create_app(routes: dict | None = None):
    """Create a Datastar ASGI application.

    Usage:
        application = app()

        @application.get("/")
        async def index(req):
            return "<h1>Hello</h1>"

        @application.get("/events/{event_id}")
        async def event(req):
            eid = req["params"]["event_id"]
            return f"<h1>Event {eid}</h1>"

        @application.get("/feed")
        async def feed(req):
            while True:
                yield patch_elements('<div id="time">...</div>')
                await asyncio.sleep(1)

        @application.post("/click")
        async def click(req):
            return None  # 204 No Content

        static(application, "/static", "static/")

        # Run with: uvicorn module:application
    """
    if routes is None:
        routes = {}

    def internal_path_to_regex(path):
        return re.compile("^" + PARAM_RE.sub(r"(?P<\1>[^/]+)", path) + "$")

    param_routes = []
    mounts = []

    def route(method: str, path: str):
        """Register a route handler."""
        def decorator(fn):
            if "{" in path:
                param_routes.append((method.upper(), internal_path_to_regex(path), fn))
            else:
                routes[(method.upper(), path)] = fn
            return fn
        return decorator

    def mount(prefix, fn):
        """Mount a handler at a URL prefix (checked after exact and param routes)."""
        mounts.append((prefix.rstrip("/"), fn))
        mounts.sort(key=lambda x: -len(x[0]))  # longest prefix first

    def get(path: str):    return route("GET", path)
    def post(path: str):   return route("POST", path)
    def put(path: str):    return route("PUT", path)
    def patch(path: str):  return route("PATCH", path)
    def delete(path: str): return route("DELETE", path)

    async def handle(scope, receive, send):
        """ASGI callable."""

        # --- Lifespan protocol ---
        if scope["type"] == "lifespan":
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return

        if scope["type"] != "http":
            return

        req = _parse_request(scope, receive)
        req["internal_send"] = send
        key = (req["method"], req["path"])
        handler = routes.get(key)
        req["params"] = {}

        # Parameterized routes
        if handler is None:
            for method, pattern, fn in param_routes:
                if method == req["method"]:
                    m = pattern.match(req["path"])
                    if m:
                        req["params"] = m.groupdict()
                        handler = fn
                        break

        # Prefix mounts
        if handler is None:
            for prefix, fn in mounts:
                if req["path"] == prefix or req["path"].startswith(prefix + "/"):
                    req["params"]["path"] = req["path"][len(prefix) + 1:]
                    handler = fn
                    break

        if handler is None:
            await send_error(send, req, 404, "Not Found")
            return

        closed = asyncio.Event()

        try:
            result = handler(req)

            if inspect.isasyncgen(result):
                # --- SSE stream ---
                watcher = asyncio.create_task(
                    _watch_disconnect(receive, closed)
                )
                compress = "br" in req["headers"].get("accept-encoding", "")
                compressor = await _open_sse(send, req, compress=compress)
                keepalive_task = asyncio.create_task(
                    _keepalive(send, compressor, closed)
                )
                try:
                    async for event in result:
                        if closed.is_set():
                            break
                        await _send_sse_event(send, compressor, event)
                finally:
                    keepalive_task.cancel()
                    watcher.cancel()
                    if not closed.is_set():
                        await _close_sse(send, compressor)

            else:
                # --- Regular request/response ---
                result = await result
                if req.get("_sent"):
                    return  # Handler already sent response directly

                watcher = asyncio.create_task(
                    _watch_disconnect(receive, closed)
                )
                try:
                    if isinstance(result, tuple) and len(result) == 2:
                        url, status = result
                        await send_redirect(send, req, url, status)
                    elif isinstance(result, Tag):
                        await send_html(send, req, result)
                    elif isinstance(result, dict):
                        await send_json(send, req, result)
                    elif isinstance(result, str):
                        await send_html(send, req, result)
                    elif result is None:
                        headers = _cookie_headers(req)
                        await send({"type": "http.response.start", "status": 204, "headers": headers})
                        await send({"type": "http.response.body", "body": b""})
                    else:
                        await send_error(
                            send, req, 500,
                            f"Handler returned unsupported type: {type(result).__name__}"
                        )
                finally:
                    watcher.cancel()

        except Exception:
            traceback.print_exc()
            try:
                await send_error(send, req, 500, "Internal Server Error")
            except Exception:
                pass

    # Attach route decorators directly to the ASGI callable
    handle.route = route
    handle.get = get
    handle.post = post
    handle.put = put
    handle.patch = patch
    handle.delete = delete
    handle.mount = mount

    return handle

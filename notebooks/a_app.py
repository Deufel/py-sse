import marimo

__generated_with = "0.23.6"
app = marimo.App(width="medium")

with app.setup:
    """Opinionated RSGI app for Datastar.

    Two route types:
      @app.get / @app.post / ...    short request/response (HTML, JSON, redirect)
      @app.stream(path, on=...)     long-lived SSE; re-renders on each tick

    Everything else is plumbing: routing, signals, cookies, static serving,
    beforeware, and RSGI lifecycle hooks.
    """
    import asyncio, base64, hashlib, hmac, inspect, json
    import os, re, threading, time, traceback
    from urllib.parse import parse_qs
    import mimetypes

    from b_sse import patch_elements

    from granian._granian import RSGIProtocolClosed

    PARAM_RE = re.compile(r'\{(\w+)\}')


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # App

    > Opinonated granin implementation of rsgi for sse datastar applications
    """)
    return


@app.function
def internal_parse_request(scope, proto) -> dict:
    "Build a request dict from an RSGI scope and protocol."
    raw_cookies = scope.headers.get("cookie", "")
    return {
        "path":         scope.path,
        "method":       scope.method,
        "headers":      scope.headers,
        "query":        {k: v[0] if len(v) == 1 else v
                         for k, v in parse_qs(scope.query_string).items()},
        "cookies":      dict(
            pair.strip().split("=", 1)
            for pair in raw_cookies.split(";")
            if "=" in pair
        ),
        "scheme":       scope.scheme,
        "client":       scope.client,
        "http_version": scope.http_version,
        "server":       scope.server,
        "authority":    getattr(scope, "authority", None),
        "_proto":       proto,
        "_cookies":     [],
    }


@app.function
async def body(req: dict, *, max_size: int = 1_048_576) -> bytes:
    "Read the full request body, cached, with a size limit."
    if "_body" in req:
        return req["_body"]
    raw = await req["_proto"]()
    if max_size and len(raw) > max_size:
        raise ValueError(f"Request body exceeds {max_size} bytes")
    req["_body"] = raw
    return raw


@app.function
def header_values(req: dict, name: str) -> list[str]:
    "Return all values for a header (multi-value safe). Uses RSGI's native get_all()."
    return req["headers"].get_all(name)


@app.function
async def body_stream(req: dict, *, max_size: int = 1_048_576):
    """Yield request body in chunks without buffering the full payload.

    Mutually exclusive with body() — use one or the other per request.
    Suitable for file uploads.
    """
    proto = req["_proto"]
    total = 0
    async for chunk in proto:
        total += len(chunk)
        if max_size and total > max_size:
            raise ValueError(f"Request body exceeds {max_size} bytes")
        yield chunk


@app.function
async def signals(req: dict) -> dict:
    """Read Datastar signals from a request.

    GET: JSON-encoded `datastar` query parameter.
    Other methods: JSON body, optionally wrapped in `{datastar: ...}`.
    """
    if req["method"] == "GET":
        raw = req["query"].get("datastar", "{}")
        return json.loads(raw) if isinstance(raw, str) else raw
    data = json.loads(await body(req))
    return data.get("datastar", data) if isinstance(data, dict) else data


@app.function
def set_cookie(req: dict, name: str, value: str, **opts) -> None:
    "Queue a Set-Cookie header on the request."
    req["_cookies"].append((name, value, opts))


@app.function
def internal_serialize_cookie(name: str, value: str, opts: dict) -> str:
    parts = [f"{name}={value}"]
    for k, v in opts.items():
        k = k.replace("_", "-")
        if isinstance(v, bool):
            if v: parts.append(k)
        else:   parts.append(f"{k}={v}")
    return "; ".join(parts)


@app.function
def internal_cookie_headers(req: dict) -> list[tuple[str, str]]:
    return [("set-cookie", internal_serialize_cookie(n, v, o))
            for n, v, o in req["_cookies"]]


@app.function
def create_signer(secret: str | bytes | None = None):
    """HMAC-SHA256 cookie signer.

        signer = create_signer("my-secret")
        set_cookie(req, "session", signer.sign("user42"))
        user = signer.unsign(req["cookies"].get("session", ""))
    """
    if secret is None:          secret = os.urandom(32)
    if isinstance(secret, str): secret = secret.encode()

    def _b64e(d: bytes) -> str:
        return base64.urlsafe_b64encode(d).rstrip(b"=").decode()
    def _b64d(s: str) -> bytes:
        return base64.urlsafe_b64decode((s + "=" * (-len(s) % 4)).encode())
    def _mac(payload: str) -> str:
        return _b64e(hmac.new(secret, payload.encode(), hashlib.sha256).digest())

    def sign(value: str, ts: float | None = None) -> str:
        ts = ts or time.time()
        payload = f"{_b64e(value.encode())}.{int(ts):x}"
        return f"{payload}.{_mac(payload)}"

    def unsign(signed: str, max_age: int | None = 3600) -> str | None:
        if not signed: return None
        parts = signed.split(".")
        if len(parts) != 3: return None
        enc_value, ts_hex, sig = parts
        payload = f"{enc_value}.{ts_hex}"
        if not hmac.compare_digest(sig, _mac(payload)): return None
        if max_age is not None:
            try:    ts = int(ts_hex, 16)
            except Exception: return None
            if time.time() - ts > max_age: return None
        try:    return _b64d(enc_value).decode()
        except Exception: return None

    class _Signer:
        __slots__ = ("sign", "unsign")
    s = _Signer()
    s.sign, s.unsign = sign, unsign
    return s


@app.function
def static(app, url_prefix: str, directory: str):
    """Mount a directory or single file for static serving.

    Uses RSGI response_file / response_file_range for zero-copy file I/O.
    Supports HTTP Range requests for resumable downloads and media seeking.

        static(app, "/static", "static/")
        static(app, "/favicon.svg", "favicon.svg")
    """
    directory = os.path.abspath(directory)

    def _guess_type(path):
        ct, _ = mimetypes.guess_type(path)
        return ct or "application/octet-stream"

    def _parse_range(header, file_size):
        if not header or not header.startswith("bytes="):
            return None
        spec = header[6:].strip()
        if "," in spec:
            return None
        left, _, right = spec.partition("-")
        try:
            if left and right:    start, end = int(left), int(right) + 1
            elif left:            start, end = int(left), file_size
            elif right:           start, end = max(0, file_size - int(right)), file_size
            else:                 return None
        except ValueError:
            return None
        if start < 0 or start >= file_size or end > file_size or start >= end:
            return None
        return start, end

    def _serve_file(req, full_path):
        proto = req["_proto"]
        file_size = os.path.getsize(full_path)
        content_type = _guess_type(full_path)
        range_header = req["headers"].get("range", "")
        parsed = _parse_range(range_header, file_size)

        if parsed:
            start, end = parsed
            proto.response_file_range(206, [
                ("content-type",   content_type),
                ("content-length", str(end - start)),
                ("content-range",  f"bytes {start}-{end - 1}/{file_size}"),
                ("accept-ranges",  "bytes"),
                ("cache-control",  "public, max-age=0, must-revalidate"),
            ], full_path, start, end)
        elif range_header:
            proto.response_str(416, [
                ("content-range", f"bytes */{file_size}"),
            ], "Range Not Satisfiable")
        else:
            proto.response_file(200, [
                ("content-type",   content_type),
                ("content-length", str(file_size)),
                ("accept-ranges",  "bytes"),
                ("cache-control",  "public, max-age=3600"),
            ], full_path)
        req["_sent"] = True

    if os.path.isfile(directory):
        async def serve_single(req):
            _serve_file(req, directory)
        app.get(url_prefix)(serve_single)
        return

    async def serve_dir(req):
        rel = req["params"].get("path", "")
        if not rel:
            return ("Not Found", 404)
        full = os.path.normpath(os.path.join(directory, rel))
        # Directory traversal guard
        if not full.startswith(directory + os.sep) or not os.path.isfile(full):
            return ("Not Found", 404)
        _serve_file(req, full)

    app.mount(url_prefix.rstrip("/"), serve_dir)


@app.function
def request_logger(topic_fn):
    """Beforeware that calls topic_fn(req) with each incoming request.

    topic_fn is anything callable — print, a log function, a custom callback.
    Decoupled from any specific pub/sub implementation.

        app.before(request_logger(lambda req: print(f"{req['method']} {req['path']}")))
    """
    def hook(req):
        topic_fn(req)
    return hook


@app.function
def create_app(routes: dict | None = None, *, on_init=None, on_del=None):
    """Create a Datastar RSGI application.

    Lifecycle:
        on_init(loop)  → called at server startup. Loop not yet running.
                         Use to wire shared state (DB connections, Changes,
                         relays) onto the correct loop.
        on_del(loop)   → called at server shutdown. Loop not yet running.
                         Use to close DB connections, detach hooks.

        Both can be sync or async; async ones run via loop.run_until_complete.

    Handler return protocol:
        str          → 200 HTML response
        dict         → 200 JSON response
        None         → 204 No Content
        (url, int)   → redirect (3xx) or text (4xx/5xx)
        async gen    → SSE stream (raw; use @app.stream for the common case)

        async def startup(loop):
            global changes
            changes = Changes(db, loop)

        def shutdown(loop):
            changes.close()

        app = create_app(on_init=startup, on_del=shutdown)
    """
    if routes is None:
        routes = {}

    param_routes = []
    mounts = []
    before_hooks = []

    def _path_re(path):
        return re.compile("^" + PARAM_RE.sub(r"(?P<\1>[^/]+)", path) + "$")

    # ── Routing decorators ───────────────────────────────────

    def route(method: str, path: str):
        def decorator(fn):
            if "{" in path:
                param_routes.append((method.upper(), _path_re(path), fn))
            else:
                routes[(method.upper(), path)] = fn
            return fn
        return decorator

    def mount(prefix, fn):
        mounts.append((prefix.rstrip("/"), fn))
        mounts.sort(key=lambda x: -len(x[0]))

    def get(path):    return route("GET", path)
    def post(path):   return route("POST", path)
    def put(path):    return route("PUT", path)
    def patch(path):  return route("PATCH", path)
    def delete(path): return route("DELETE", path)

    # ── @app.stream — the opinionated SSE route ──────────────

    def stream(path: str, *, on):
        """Register an SSE endpoint that re-renders on each tick from `on`.

        The decorated function is sync (or async), takes `req`, and returns
        either an html_tags tree or a pre-rendered HTML string. The framework
        handles SSE framing, initial render, re-render on change, disconnect
        cleanup, and prompt subscriber teardown.

        `on` is either:
          • a Changes-like object with `async def wait(self)`, or
          • a zero-arg callable returning one (resolved per-request, useful
            when the Changes is created in `on_init` after route registration).

            @app.stream('/feed', on=lambda: changes)
            def feed(req):
                rows = query(db, "SELECT txt FROM msgs ORDER BY id DESC LIMIT 50")
                return h.div(*[h.p(r[0]) for r in rows], id='msgs')
        """
        from html_tags import render as _h_render

        def _resolve_on():
            # `on` may be the Changes instance directly, or a zero-arg callable
            # returning it (for cases where `on_init` creates it later).
            if hasattr(on, 'wait'):
                return on
            return on()

        def decorator(fn):
            is_coro = inspect.iscoroutinefunction(fn)

            async def _render(req):
                result = fn(req)
                if is_coro:
                    result = await result
                if hasattr(result, '__html__'):
                    result = result.__html__()
                elif not isinstance(result, str):
                    result = _h_render(result)
                return patch_elements(result)

            async def handler(req):
                source = _resolve_on()
                yield await _render(req)
                async for _ in source.wait():
                    yield await _render(req)

            route("GET", path)(handler)
            return fn
        return decorator

    # ── Beforeware ───────────────────────────────────────────

    def before(fn=None, *, methods=None):
        def decorator(f):
            m = {x.upper() for x in methods} if methods else None
            before_hooks.append((f, m))
            return f
        if fn is not None:
            before_hooks.append((fn, None))
            return fn
        return decorator

    # ── Response dispatch ────────────────────────────────────

    def _respond(proto, req, result):
        headers = internal_cookie_headers(req)

        if isinstance(result, tuple) and len(result) == 2:
            content, status = result
            if isinstance(status, int) and 300 <= status < 400:
                headers.append(("location", content))
                proto.response_empty(status, headers)
            elif isinstance(status, int):
                headers.append(("content-type", "text/html; charset=utf-8"))
                proto.response_str(status, headers, content)
            return

        if isinstance(result, bytes):
            ct = req.get("_content_type", "application/octet-stream")
            headers.append(("content-type", ct))
            proto.response_bytes(200, headers, result)
        elif isinstance(result, str):
            headers.append(("content-type", "text/html; charset=utf-8"))
            proto.response_str(200, headers, result)
        elif isinstance(result, dict):
            headers.append(("content-type", "application/json"))
            proto.response_str(200, headers, json.dumps(result))
        elif result is None:
            proto.response_empty(204, headers)
        else:
            headers.append(("content-type", "text/plain; charset=utf-8"))
            proto.response_str(500, headers,
                f"Unsupported return type: {type(result).__name__}")

    # ── SSE keepalive ────────────────────────────────────────

    async def _keepalive(transport, closed: asyncio.Event, interval: int = 15):
        try:
            while not closed.is_set():
                await asyncio.sleep(interval)
                if closed.is_set():
                    break
                try:
                    await transport.send_str(":\n\n")
                except RSGIProtocolClosed:
                    closed.set()
                    break
        except asyncio.CancelledError:
            pass

    # ── RSGI entrypoint ──────────────────────────────────────

    async def handle(scope, proto):
        if scope.proto != "http":
            return

        req = internal_parse_request(scope, proto)
        req["params"] = {}

        handler = routes.get((req["method"], req["path"]))
        if handler is None:
            for method, pattern, fn in param_routes:
                if method == req["method"]:
                    m = pattern.match(req["path"])
                    if m:
                        req["params"] = m.groupdict()
                        handler = fn
                        break
        if handler is None:
            for prefix, fn in mounts:
                if req["path"] == prefix or req["path"].startswith(prefix + "/"):
                    req["params"]["path"] = req["path"][len(prefix) + 1:]
                    handler = fn
                    break
        if handler is None:
            proto.response_str(404, [("content-type", "text/plain")], "Not Found")
            return

        try:
            for hook, methods in before_hooks:
                if methods and req["method"] not in methods:
                    continue
                hook_result = hook(req)
                if inspect.isawaitable(hook_result):
                    hook_result = await hook_result
                if hook_result is not None:
                    _respond(proto, req, hook_result)
                    return

            result = handler(req)

            # async def handlers return a coroutine — await it first to see
            # whether the actual result is an async generator (SSE) or value.
            if inspect.iscoroutine(result):
                result = await result
                if req.get("_sent"):
                    return

            if inspect.isasyncgen(result):
                closed = asyncio.Event()
                headers = [
                    ("content-type",      "text/event-stream"),
                    ("cache-control",     "no-cache"),
                    ("x-accel-buffering", "no"),
                ] + internal_cookie_headers(req)

                transport = proto.response_stream(200, headers)
                disconnect = asyncio.ensure_future(proto.client_disconnect())
                keepalive = asyncio.create_task(_keepalive(transport, closed))

                def _on_disconnect(fut):
                    closed.set()
                disconnect.add_done_callback(_on_disconnect)

                try:
                    async for event in result:
                        if closed.is_set():
                            break
                        try:
                            await transport.send_str(event)
                        except RSGIProtocolClosed:
                            closed.set()
                            break
                finally:
                    closed.set()
                    keepalive.cancel()
                    disconnect.cancel()
                    await result.aclose()
            else:
                # `result` is the final value (str/dict/tuple/None) — coroutine
                # was already awaited above if it was one.
                _respond(proto, req, result)

        except Exception:
            traceback.print_exc()
            try:
                proto.response_str(500,
                    [("content-type", "text/plain")],
                    "Internal Server Error")
            except Exception: pass

    # ── RSGI lifecycle hooks ─────────────────────────────────

    def _rsgi_init(loop):
        if on_init:
            result = on_init(loop)
            if inspect.iscoroutine(result):
                loop.run_until_complete(result)

    def _rsgi_del(loop):
        if on_del:
            result = on_del(loop)
            if inspect.iscoroutine(result):
                loop.run_until_complete(result)

    handle.__rsgi_init__ = _rsgi_init
    handle.__rsgi_del__  = _rsgi_del

    # ── Attach decorators to the app handle ──────────────────

    handle.route  = route
    handle.get    = get
    handle.post   = post
    handle.put    = put
    handle.patch  = patch
    handle.delete = delete
    handle.mount  = mount
    handle.before = before
    handle.stream = stream
    return handle


@app.function
def serve(app, *, host: str = "127.0.0.1", port: int = 8000, **kwargs):
    """Run an app with Granian's embedded RSGI server.

        if __name__ == "__main__":
            serve(app)

    Extra kwargs forward to granian.server.embed.Server
    (e.g. log_access=True, websockets=False, ssl_cert=...).
    """
    from granian.server.embed import Server
    from granian.constants import Interfaces

    server = Server(app, address=host, port=port, interface=Interfaces.RSGI, **kwargs)

    async def _run():
        await server.serve()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    app.run()

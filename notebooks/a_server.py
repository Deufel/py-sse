import marimo

__generated_with = "0.23.6"
app = marimo.App(width="medium")

with app.setup:
    """py_sse server — a Wirth-style minimal SSE web framework.

    Pure Python. Pure stdlib + brotli + apsw. One OS thread per connection.
    No async/await. Each function does one thing, named for it.

    DEPLOYMENT MODEL:
        py_sse runs BEHIND a reverse proxy (caddy, nginx) that terminates
        TLS and speaks HTTP/1.1 cleartext to us on localhost. We handle:
          * Per-connection timeouts (slowloris defense)
          * Bounded concurrent connections (thread/RAM cap)
          * Strict request-line and header validation
          * Cookie value sanitization (no response splitting)
          * Generic 500 responses (no exception text leaked)
          * Graceful shutdown on SIGINT/SIGTERM
          * Brotli SSE compression across frames (huge wins for fat morph)
    """

    import logging
    import re
    import signal
    import socket
    import threading
    import time
    import zlib
    from contextlib import contextmanager
    from functools import wraps
    from urllib.parse import parse_qs, unquote

    import brotli

    # ─── Configuration ────────────────────────────────────────────────────

    MAX_HEADER_BYTES = 64 * 1024
    MAX_BODY_BYTES = 16 * 1024 * 1024
    MAX_CONNECTIONS = 256
    HEADER_READ_TIMEOUT = 10
    BODY_READ_TIMEOUT = 60
    SSE_WRITE_TIMEOUT = 60
    SHUTDOWN_GRACE = 5
    SSE_BROTLI_LGWIN = 18
    SSE_BROTLI_QUALITY = 4
    SSE_GZIP_LEVEL = 6

    _METHOD_RE = re.compile(b'^[A-Z]{1,16}$')
    _TARGET_RE = re.compile(b'^/[\\x21-\\x7e]{0,2047}$')
    _HEADER_NAME_RE = re.compile(b"^[!#$%&'*+\\-.0-9A-Z^_`a-z|~]{1,128}$")
    _COOKIE_FORBIDDEN = re.compile('[\\r\\n\\x00]')
    PARAM_RE = re.compile('\\{(\\w+)\\}')

    logger = logging.getLogger('py_sse')




@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Py-sse.server
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Changes
    """)
    return


@app.cell
def _():
    # ─── Section 1: Changes — topic-scoped pub/sub ────────────────────────
    #
    # Hierarchical subjects with dotted notation: "game.5.score".
    # Patterns: exact ("game.5.score"), prefix wildcard ("game.5.*"), or
    # bare wildcard ("*"). A notify on "a.b.c" wakes waiters on "a.b.c",
    # "a.b.*", "a.*", and "*".
    #
    # Subscribers are implicit: they're the threads currently parked in
    # wait(). A dropped connection ends its thread, ends the subscription.

    return


@app.class_definition
class Changes:
    """In-process pub/sub. Threads wait on dotted subject patterns;
    publishes match by walking the hierarchy.

    Usage:
        # Writer
        changes.notify("game.5.score")

        # Reader (in an SSE stream)
        while True:
            changes.wait("game.5.*", timeout=15)
            yield render_frame()
    """

    def __init__(self):
        self._events = {}
        self._lock = threading.Lock()

    def _event_for(self, pattern):
        with self._lock:
            if pattern not in self._events:
                self._events[pattern] = threading.Event()
            return self._events[pattern]

    def notify(self, subject):
        """Wake all subscribers whose pattern matches this subject.

        Matching walks the hierarchy: notify("a.b.c") wakes waiters
        registered on "a.b.c", "a.b.*", "a.*", and "*".
        """
        parts = subject.split(".")
        patterns = [subject]
        for i in range(len(parts) - 1, -1, -1):
            patterns.append(".".join(parts[:i] + ["*"]))

        with self._lock:
            for p in patterns:
                if p in self._events:
                    self._events[p].set()

    def wait(self, pattern, timeout=None):
        """Wait for a notify whose subject matches this pattern.

        Returns True if matched, False on timeout.
        """
        evt = self._event_for(pattern)
        ok = evt.wait(timeout=timeout)
        if ok:
            evt.clear()
        return ok


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## low-level I/O
    """)
    return


@app.function
def read_until_double_crlf(sock):
    "Read socket bytes until \\r\\n\\r\\n. Returns (head_bytes, leftover)."
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("client closed before sending headers")
        buf += chunk
        if len(buf) > MAX_HEADER_BYTES:
            raise ValueError("request headers exceed limit")
    head, _, leftover = buf.partition(b"\r\n\r\n")
    return head, leftover


@app.function
def read_body(sock, content_length, already_have, limit=MAX_BODY_BYTES):
    "Read exactly content_length bytes."
    if content_length < 0:
        raise ValueError("negative content-length")
    if content_length > limit:
        raise ValueError(f"body exceeds limit ({content_length} > {limit})")
    buf = already_have
    while len(buf) < content_length:
        chunk = sock.recv(min(65536, content_length - len(buf)))
        if not chunk:
            raise ConnectionError("client closed mid-body")
        buf += chunk
    return buf[:content_length]


@app.function
def write_response(sock, status, headers, body=b""):
    "Write a complete HTTP/1.1 response."
    if isinstance(body, str):
        body = body.encode("utf-8")
    reason = {200: "OK", 204: "No Content", 303: "See Other",
              400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
              404: "Not Found", 408: "Request Timeout",
              413: "Payload Too Large", 500: "Internal Server Error",
              503: "Service Unavailable",
              507: "Insufficient Storage"}.get(status, "OK")
    lines = [f"HTTP/1.1 {status} {reason}"]
    headers = list(headers)
    headers.append(("content-length", str(len(body))))
    headers.append(("connection", "close"))
    for k, v in headers:
        lines.append(f"{k}: {v}")
    head = "\r\n".join(lines).encode("ascii") + b"\r\n\r\n"
    try:
        sock.sendall(head + body)
    except OSError:
        pass


@app.function
def write_sse_headers(sock, extra_headers=(), encoding="identity"):
    "Write the response head for a streaming SSE response."
    lines = [
        "HTTP/1.1 200 OK",
        "content-type: text/event-stream",
        "cache-control: no-cache",
        "x-accel-buffering: no",
        "proxy-buffering: off",
        "connection: keep-alive",
    ]
    if encoding and encoding != "identity":
        lines.append(f"content-encoding: {encoding}")
        lines.append("vary: accept-encoding")
    for k, v in extra_headers:
        lines.append(f"{k}: {v}")
    sock.sendall("\r\n".join(lines).encode("ascii") + b"\r\n\r\n")


@app.function
def write_sse_frame(sock, payload, encoder=None):
    "Write one SSE frame, optionally compressed."
    raw = payload.encode("utf-8") + b"\n\n"
    if encoder is None or encoder.name == "identity":
        sock.sendall(raw)
        return
    chunk = encoder.encode(raw) + encoder.flush()
    if chunk:
        sock.sendall(chunk)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## SSE encoder
    """)
    return


@app.class_definition
class internal_SseEncoder:
    """Per-connection streaming encoder.

    Brotli's cross-frame state is the whole game: frame N+1 mostly
    equals frame N, so the encoder emits "copy from N KB ago" for
    almost everything. Each per-frame flush() emits a syncable point
    without ending the stream.
    """
    __slots__ = ("name", "_c")

    def __init__(self, encoding):
        self.name = encoding
        if encoding == "br":
            self._c = brotli.Compressor(
                quality=SSE_BROTLI_QUALITY, lgwin=SSE_BROTLI_LGWIN)
        elif encoding == "gzip":
            self._c = zlib.compressobj(level=SSE_GZIP_LEVEL, wbits=31)
        elif encoding == "identity":
            self._c = None
        else:
            raise ValueError(f"unsupported sse encoding: {encoding}")

    def encode(self, data):
        if self._c is None:
            return data
        if self.name == "br":
            return self._c.process(data)
        return self._c.compress(data)

    def flush(self):
        if self._c is None:
            return b""
        if self.name == "br":
            return self._c.flush()
        return self._c.flush(zlib.Z_SYNC_FLUSH)

    def finish(self):
        if self._c is None:
            return b""
        if self.name == "br":
            return self._c.finish()
        return self._c.flush(zlib.Z_FINISH)


@app.function
def pick_encoding(req, prefer=("br", "gzip")):
    "Pick the best encoding the client supports."
    raw = req["headers"].get("accept-encoding", "").lower()
    if not raw:
        return "identity"
    offered = {part.split(";", 1)[0].strip() for part in raw.split(",")}
    for enc in prefer:
        if enc == "br" and "br" in offered:
            return "br"
        if enc == "gzip" and "gzip" in offered:
            return "gzip"
    return "identity"


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Request parsing
    """)
    return


@app.function
def parse_request(sock):
    """Read one request off the socket, return a request dict.

    Validates request line and header names strictly. Raises ValueError
    for malformed input, ConnectionError for truncated reads.
    """
    sock.settimeout(HEADER_READ_TIMEOUT)
    head, leftover = read_until_double_crlf(sock)

    raw_lines = head.split(b"\r\n")
    if not raw_lines or not raw_lines[0]:
        raise ValueError("empty request")

    rl = raw_lines[0]
    if len(rl) > 8192:
        raise ValueError("request line too long")
    parts = rl.split(b" ")
    if len(parts) != 3:
        raise ValueError("malformed request line")
    method_b, target_b, version_b = parts
    if not _METHOD_RE.match(method_b):
        raise ValueError("invalid method")
    if not _TARGET_RE.match(target_b):
        raise ValueError("invalid target")
    if version_b not in (b"HTTP/1.1", b"HTTP/1.0"):
        raise ValueError("unsupported HTTP version")
    method = method_b.decode("ascii")
    target = target_b.decode("ascii")

    raw_path, _, raw_query = target.partition("?")
    path = unquote(raw_path)
    query = {k: v[0] if len(v) == 1 else v
             for k, v in parse_qs(raw_query).items()}

    headers = {}
    for raw in raw_lines[1:]:
        if not raw:
            continue
        if b":" not in raw:
            raise ValueError("malformed header line")
        name_b, _, value_b = raw.partition(b":")
        if not _HEADER_NAME_RE.match(name_b):
            raise ValueError("invalid header name")
        if any(b in value_b for b in (b"\r", b"\n", b"\x00")):
            raise ValueError("invalid byte in header value")
        headers[name_b.decode("ascii").lower()] = value_b.decode("iso-8859-1").strip()

    cookies = parse_cookies(headers.get("cookie", ""))

    content_length = 0
    if "content-length" in headers:
        try:
            content_length = int(headers["content-length"])
        except ValueError:
            raise ValueError("invalid content-length")
    if content_length:
        sock.settimeout(BODY_READ_TIMEOUT)
        body = read_body(sock, content_length, leftover)
    else:
        body = b""

    return {
        "method":  method,
        "path":    path,
        "query":   query,
        "headers": headers,
        "cookies": cookies,
        "body":    body,
        "_sock":   sock,
        "params":  {},
        "_cookies_out": [],
    }


@app.function
def parse_cookies(cookie_header):
    "Parse a Cookie: header value into a {name: value} dict."
    out = {}
    for pair in cookie_header.split(";"):
        if "=" in pair:
            k, _, v = pair.partition("=")
            k, v = k.strip(), v.strip()
            if k and not _COOKIE_FORBIDDEN.search(k) and not _COOKIE_FORBIDDEN.search(v):
                out[k] = v
    return out


@app.function
def set_cookie(req, name, value, **opts):
    """Queue a Set-Cookie on the response.
    Options: max_age (int), path (str), httponly (bool), samesite ('Lax'|...).
    """
    if _COOKIE_FORBIDDEN.search(name) or _COOKIE_FORBIDDEN.search(str(value)):
        raise ValueError("cookie name/value contains forbidden control characters")
    pieces = [f"{name}={value}"]
    for k, v in opts.items():
        k = k.replace("_", "-")
        if _COOKIE_FORBIDDEN.search(str(v)):
            raise ValueError(f"cookie option {k} contains forbidden characters")
        if isinstance(v, bool):
            if v:
                pieces.append(k)
        else:
            pieces.append(f"{k}={v}")
    req["_cookies_out"].append("; ".join(pieces))


@app.function
def signals(req):
    """Parse Datastar signals from a request.

    GET: JSON-encoded 'datastar' query parameter.
    POST/PUT/PATCH/DELETE: JSON body.
    """
    import json
    if req["method"] == "GET":
        raw = req["query"].get("datastar", "{}")
        return json.loads(raw) if isinstance(raw, str) else raw
    if not req["body"]:
        return {}
    data = json.loads(req["body"])
    return data.get("datastar", data) if isinstance(data, dict) else data


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Routing
    """)
    return


@app.function
def compile_routes(routes):
    "Compile path patterns to regex."
    compiled = []
    for method, path, handler in routes:
        if "{" in path:
            regex = "^" + PARAM_RE.sub(r"(?P<\1>[^/]+)", path) + "$"
            compiled.append((method.upper(), re.compile(regex), handler))
        else:
            compiled.append((method.upper(), re.compile("^" + re.escape(path) + "$"), handler))
    return compiled


@app.cell
def _():
    return


@app.function
def match_route(routes, method, path):
    "Find the first matching route. Returns (handler, params) or None."
    for route_method, pattern, handler in routes:
        if route_method != method:
            continue
        m = pattern.match(path)
        if m:
            return handler, m.groupdict()
    return None


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Response helpers
    """)
    return


@app.cell
def _():
    # ─── Section 6: response helpers ──────────────────────────────────────

    return


@app.function
def html(body, status=200):
    "Return an HTML page response."
    return (status, [("content-type", "text/html; charset=utf-8")], body)


@app.function
def redirect(location, status=303):
    "Return a redirect response."
    if "\r" in location or "\n" in location:
        raise ValueError("redirect target contains forbidden characters")
    return (status, [("location", location)], b"")


@app.function
def no_content():
    "Return a 204 No Content response."
    return (204, [], b"")


@app.function
def blob(data, content_type, filename=None):
    "Return a Response carrying raw bytes."
    headers = [
        ("content-type", content_type),
        ("x-content-type-options", "nosniff"),
    ]
    if filename:
        safe = "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_"
                       for c in filename)[:200] or "file"
        headers.append(("content-disposition", f'attachment; filename="{safe}"'))
    return (200, headers, data)


@app.function
def error(status, message=""):
    "Return an error response."
    return (status, [("content-type", "text/plain; charset=utf-8")], message)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## SSE primitives
    """)
    return


@app.cell
def _():
    # ─── Section 7: SSE primitives ────────────────────────────────────────

    return


@app.function
def sse_data(text):
    "Format a string as an SSE data line."
    return "\n".join(f"data: {line}" for line in (text.splitlines() or [""]))


@app.function
def sse_event(event_name, data):
    "Format a named SSE event."
    return f"event: {event_name}\n{sse_data(data)}"


@app.function
def sse_keepalive():
    "An SSE comment line. Keeps the connection alive without firing an event."
    return ":"


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Stream Handler Decorator
    """)
    return


@app.cell
def _():
    # ─── Section 8: stream_handler decorator ──────────────────────────────
    #
    # Wraps a plain page handler with SSE streaming. The handler stays pure
    # — fetch data, render HTML, return it. The decorator adds:
    #   * Live vs polling degradation based on viewer count
    #   * Subscribe to a Changes pattern; re-render on notify
    #   * Keepalive on timeout
    #   * Disconnect handling
    #
    # The decorator reads `live` and `changes` from the request dict
    # (req["_live"], req["_changes"]) so it has no hidden module state.
    # `serve()` attaches them to every request. Apps that wire up requests
    # themselves must do the same.

    return


@app.function
def stream_handler(resource_id_fn, subscribe_to):
    """Decorator. Wraps a handler with SSE/polling degradation.

    Args:
        resource_id_fn: callable(req) -> str. Identifier used by
            LiveCounter to decide live vs polling for this resource.
            E.g. lambda req: f"game-{req['params']['id']}"
        subscribe_to: callable(req) -> str. The Changes pattern this
            stream subscribes to for re-render triggers.
            E.g. lambda req: f"game.{req['params']['id']}.*"

    The wrapped handler is called once initially to render the page,
    then again on each matching notify. The handler returns the same
    shape as any other handler: (status, headers, body).

    Requires req["_live"] and req["_changes"] to be set. serve()
    handles this automatically.

    Usage:
        @stream_handler(
            resource_id_fn=lambda req: f"game-{req['params']['id']}",
            subscribe_to=lambda req: f"game.{req['params']['id']}.*"
        )
        def get_scorecard(req):
            game = get_game(...)
            return html(h_render(full_page(...)))
    """
    def decorator(handler):
        @wraps(handler)
        def wrapper(req):
            live = req.get("_live")
            changes = req.get("_changes")
            if live is None or changes is None:
                raise RuntimeError(
                    "stream_handler requires req['_live'] and "
                    "req['_changes'] to be set. serve() does this "
                    "automatically; apps wiring requests manually "
                    "must do the same.")

            resource = resource_id_fn(req)
            pattern = subscribe_to(req)

            def render_html():
                response = handler(req)
                if isinstance(response, tuple):
                    _, _, body = response
                else:
                    body = response
                return body.decode("utf-8") if isinstance(body, bytes) else body

            # Initial render
            html_str = render_html()

            # Polling fallback: above the live cap
            if not live.should_be_live(resource):
                yield f"event: datastar-patch-elements\ndata: elements {html_str}"
                return

            # Live stream
            with live.join(resource):
                yield f"event: datastar-patch-elements\ndata: elements {html_str}"
                while True:
                    if changes.wait(pattern, timeout=15):
                        try:
                            html_str = render_html()
                            yield f"event: datastar-patch-elements\ndata: elements {html_str}"
                        except (OSError, BrokenPipeError):
                            return
                    else:
                        yield sse_keepalive()

        return wrapper
    return decorator


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Connection handling
    """)
    return


@app.function
def handle_connection(sock, addr, routes, before_hooks,
                      live=None, changes=None, access_log=True):
    """Run one request to completion, then close the socket.
    Called in its own OS thread. Never raises out of this function.

    `live` and `changes` are attached to req as req["_live"] and
    req["_changes"] so stream_handler-decorated handlers can find them
    without consulting module globals.
    """
    start = time.time()
    status = 0
    method = path = "?"
    req = None
    try:
        # Parse
        try:
            req = parse_request(sock)
            req["_live"] = live
            req["_changes"] = changes
        except socket.timeout:
            status = 408
            write_response(sock, 408, [("content-type", "text/plain")],
                           "request timeout")
            return
        except (ValueError, ConnectionError) as e:
            status = 400
            logger.info("400 from %s: %s", addr[0] if addr else "?", e)
            write_response(sock, 400, [("content-type", "text/plain")],
                           "bad request")
            return
        except Exception:
            status = 400
            logger.exception("error parsing request from %s", addr)
            write_response(sock, 400, [("content-type", "text/plain")],
                           "bad request")
            return

        method = req["method"]
        path = req["path"]

        # Route
        matched = match_route(routes, method, path)
        if matched is None:
            status = 404
            write_response(sock, 404, [("content-type", "text/plain")],
                           "not found")
            return
        handler, params = matched
        req["params"] = params

        # Before-hooks
        try:
            for hook in before_hooks:
                hook(req)
        except Exception:
            status = 500
            logger.exception("before-hook failed")
            write_response(sock, 500, [("content-type", "text/plain")],
                           "internal error")
            return

        # Handler
        try:
            result = handler(req)
        except Exception:
            status = 500
            logger.exception("handler raised for %s %s", method, path)
            write_response(sock, 500, [("content-type", "text/plain")],
                           "internal error")
            return

        if result is None:
            result = no_content()

        # Short response (tuple)
        if isinstance(result, tuple):
            status_, headers, body = result
            status = status_
            for c in req["_cookies_out"]:
                headers = list(headers) + [("set-cookie", c)]
            sock.settimeout(SSE_WRITE_TIMEOUT)
            write_response(sock, status, headers, body)
            return

        # SSE stream (generator)
        status = 200
        sock.settimeout(SSE_WRITE_TIMEOUT)
        extra = [("set-cookie", c) for c in req["_cookies_out"]]
        encoding = pick_encoding(req)
        encoder = internal_SseEncoder(encoding)
        try:
            write_sse_headers(sock, extra, encoding=encoding)
            for frame in result:
                if frame is None:
                    continue
                write_sse_frame(sock, frame, encoder=encoder)
        except (OSError, ConnectionError, socket.timeout):
            pass
        except Exception:
            logger.exception("SSE generator raised for %s %s", method, path)
        finally:
            try:
                tail = encoder.finish()
                if tail:
                    sock.sendall(tail)
            except Exception:
                pass
            try:
                result.close()
            except Exception:
                pass

    finally:
        try:
            sock.close()
        except Exception:
            pass
        if access_log:
            dt_ms = (time.time() - start) * 1000
            logger.info("%s %s %s → %d %.1fms",
                        addr[0] if addr else "?", method, path, status, dt_ms)


@app.cell
def _():
    # ─── Section 10: the listen loop ──────────────────────────────────────

    return


@app.class_definition
class internal_ShutdownFlag:
    def __init__(self):
        self._stop = False

    def set(self):
        self._stop = True

    def is_set(self):
        return self._stop


@app.function
def serve(routes, *, host="127.0.0.1", port=8000,
          before_hooks=(), live=None, changes=None,
          max_connections=MAX_CONNECTIONS, access_log=True):
    """Run the server. Blocks until SIGINT/SIGTERM.

    `routes`:           list of (method, path, handler) tuples.
    `before_hooks`:     run before each handler, in order; may mutate req.
    `live`:             LiveCounter instance for capacity management.
                        Default: LiveCounter(soft_cap=200, ...).
    `changes`:          Changes instance for pub/sub notifications.
                        Default: a fresh Changes().
    `max_connections`:  cap on concurrent connection threads.
    `access_log`:       one INFO line per request to the py_sse logger.

    The `live` and `changes` instances are attached to each request
    dict as req["_live"] and req["_changes"] so handlers (especially
    those decorated with @stream_handler) can find them without
    consulting any module globals.
    """
    if not logger.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(h)
        logger.setLevel(logging.INFO)

    # Avoid circular import: import LiveCounter from .live at call time
    from .live import LiveCounter
    if live is None:
        live = LiveCounter(soft_cap=200, min_poll_ms=1_000,
                           max_poll_ms=8_000, ramp_users=50)
    if changes is None:
        changes = Changes()

    compiled = compile_routes(routes)
    semaphore = threading.BoundedSemaphore(max_connections)
    stop = internal_ShutdownFlag()

    def _signal(_signum, _frame):
        logger.info("shutdown signal received")
        stop.set()
    # signal.signal() only works from the main thread. In tests we may
    # run serve() in a background thread; skip signal handlers there.
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, _signal)
        signal.signal(signal.SIGTERM, _signal)
    else:
        logger.info("serve() running in background thread; SIGINT/SIGTERM "
                    "handlers not installed")

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.listen(128)
    s.settimeout(0.5)
    logger.info("py_sse listening on http://%s:%d (max_connections=%d)",
                host, port, max_connections)

    in_flight = []

    try:
        while not stop.is_set():
            try:
                conn, addr = s.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            if not semaphore.acquire(blocking=False):
                logger.warning("connection cap (%d) reached, dropping %s",
                               max_connections, addr[0])
                try:
                    write_response(conn, 503,
                                   [("content-type", "text/plain"),
                                    ("retry-after", "1")],
                                   "server busy")
                except Exception:
                    pass
                conn.close()
                continue

            def _run(c=conn, a=addr, lv=live, ch=changes):
                try:
                    handle_connection(c, a, compiled, before_hooks,
                                      live=lv, changes=ch,
                                      access_log=access_log)
                finally:
                    semaphore.release()

            t = threading.Thread(target=_run, daemon=True)
            t.start()
            in_flight.append(t)
            if len(in_flight) > 1024:
                in_flight = [x for x in in_flight if x.is_alive()]

    finally:
        s.close()
        deadline = time.time() + SHUTDOWN_GRACE
        for t in in_flight:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            t.join(timeout=remaining)
        alive_threads = sum(1 for t in in_flight if t.is_alive())
        if alive_threads:
            logger.warning(
                "shutdown: %d threads still running after grace period",
                alive_threads)
        logger.info("shutdown complete")


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()

import marimo

__generated_with = "0.23.6"
app = marimo.App(width="full")

with app.setup:
    """py_sse server — Wirth-style minimal SSE web framework.

    One OS thread per connection. Topic-scoped pub/sub. SSE with brotli
    cross-frame compression. No async/await.

    A page is a function that returns html_tags elements. The framework
    wraps them in a stable envelope (html/head/body) so idiomorph has
    something to anchor to.

    A "live" page is a page wrapped with live(handler, topic). It serves
    three transport modes based on per-page viewer count:

        0 to soft_cap         → live SSE       (data-init opens stream)
        soft_cap to hard_cap  → polling        (data-on-interval refetches)
        above hard_cap        → static         (no automatic updates)

    One URL serves all three modes. The framework dispatches on the
    incoming Accept header: text/event-stream means open a stream; anything
    else means return a one-shot HTML page with the right transport
    attribute baked into the wrapper.
    """

    import logging
    import re
    import signal
    import socket
    import threading
    import time
    import zlib
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

    # The DOM id used for the live wrapper. Idiomorph anchors on this.
    LIVE_ROOT_ID = "live-root"





@app.cell
def _():
    import marimo as mo

    return


@app.cell
def _():
    # ─── Section 1: Changes — topic-scoped pub/sub ────────────────────────

    return


@app.class_definition
class Changes:
    """In-process pub/sub. Threads wait on dotted subject patterns;
    publishes match by walking the hierarchy.

        # Writer
        changes.notify("game.5.score")

        # Reader
        while True:
            changes.wait("game.5.*", timeout=15)
            yield render_frame()

    Matching: notify("a.b.c") wakes waiters on "a.b.c", "a.b.*",
    "a.*", and "*".
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
        parts = subject.split(".")
        patterns = [subject]
        for i in range(len(parts) - 1, -1, -1):
            patterns.append(".".join(parts[:i] + ["*"]))
        with self._lock:
            for p in patterns:
                if p in self._events:
                    self._events[p].set()

    def wait(self, pattern, timeout=None):
        evt = self._event_for(pattern)
        ok = evt.wait(timeout=timeout)
        if ok:
            evt.clear()
        return ok


@app.cell
def _():
    # ─── Section 2: low-level I/O ─────────────────────────────────────────

    return


@app.function
def read_until_double_crlf(sock):
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
    raw = payload.encode("utf-8") + b"\n\n"
    if encoder is None or encoder.name == "identity":
        sock.sendall(raw)
        return
    chunk = encoder.encode(raw) + encoder.flush()
    if chunk:
        sock.sendall(chunk)


@app.cell
def _():
    # ─── Section 3: SSE encoder ───────────────────────────────────────────

    return


@app.class_definition
class internal_SseEncoder:
    """Per-connection streaming encoder. Brotli's cross-frame state
    is the whole game: frame N+1 mostly equals frame N, so the encoder
    emits 'copy from N KB ago' for almost everything."""
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


@app.cell
def _():
    # ─── Section 4: request parsing ───────────────────────────────────────

    return


@app.function
def parse_request(sock):
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
    """Parse Datastar signals from a request."""
    import json
    if req["method"] == "GET":
        raw = req["query"].get("datastar", "{}")
        return json.loads(raw) if isinstance(raw, str) else raw
    if not req["body"]:
        return {}
    data = json.loads(req["body"])
    return data.get("datastar", data) if isinstance(data, dict) else data


@app.cell
def _():
    # ─── Section 5: routing ───────────────────────────────────────────────

    return


@app.function
def compile_routes(routes):
    compiled = []
    for method, path, handler in routes:
        if "{" in path:
            regex = "^" + PARAM_RE.sub(r"(?P<\1>[^/]+)", path) + "$"
            compiled.append((method.upper(), re.compile(regex), handler))
        else:
            compiled.append((method.upper(), re.compile("^" + re.escape(path) + "$"), handler))
    return compiled


@app.function
def match_route(routes, method, path):
    for route_method, pattern, handler in routes:
        if route_method != method:
            continue
        m = pattern.match(path)
        if m:
            return handler, m.groupdict()
    return None


@app.cell
def _():
    # ─── Section 6: response helpers ──────────────────────────────────────

    return


@app.function
def html(body, status=200):
    return (status, [("content-type", "text/html; charset=utf-8")], body)


@app.function
def redirect(location, status=303):
    if "\r" in location or "\n" in location:
        raise ValueError("redirect target contains forbidden characters")
    return (status, [("location", location)], b"")


@app.function
def no_content():
    return (204, [], b"")


@app.function
def blob(data, content_type, filename=None):
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
    return (status, [("content-type", "text/plain; charset=utf-8")], message)


@app.cell
def _():
    # ─── Section 7: SSE primitives ────────────────────────────────────────

    return


@app.function
def sse_data(text):
    return "\n".join(f"data: {line}" for line in (text.splitlines() or [""]))


@app.function
def sse_event(event_name, data):
    return f"event: {event_name}\n{sse_data(data)}"


@app.function
def sse_keepalive():
    return ":"


@app.cell
def _():
    # ─── Section 8: html_tags rendering helpers ───────────────────────────
    #
    # The framework calls h_render() on whatever the handler returns. Users
    # can return:
    #   - A single html_tags element
    #   - A list of html_tags elements
    #   - A string (already rendered)
    #
    # This is what makes "handlers just return HTML" honest — the framework
    # normalizes the representation.

    return


@app.function
def internal_render_content(value):
    """Turn a handler's return value into an HTML string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8")
    # Import inside the function so html_tags isn't a hard dependency.
    try:
        from html_tags import render as h_render
    except ImportError:
        raise RuntimeError(
            "Handler returned html_tags elements but html_tags is not "
            "installed. Install it or have your handler return a string.")
    if isinstance(value, (list, tuple)):
        return "".join(h_render(v) for v in value)
    return h_render(value)


@app.function
def internal_envelope(head_fragments, body_inner, ui_theme="dark"):
    """Wrap body content in a stable html/head/body envelope.

    The envelope is byte-identical for every render of every page —
    same <head>, same outer attributes. Idiomorph leaves it alone.
    """
    from html_tags import h, render as h_render
    page = h.html(
        {"id": "page", "data-ui-theme": ui_theme},
        h.head(
            h.meta(charset="utf-8"),
            h.meta(name="viewport", content="width=device-width, initial-scale=1"),
            *head_fragments,
        ),
        h.body({"class": "page stage"}, h.raw(body_inner) if hasattr(h, "raw") else body_inner),
    )
    return h_render(page)


@app.function
def internal_envelope_safe(head_fragments, body_inner_html, ui_theme="dark"):
    """Like _envelope but inserts pre-rendered HTML into the body
    via string interpolation (since html_tags may not have a 'raw' tag)."""
    from html_tags import h, render as h_render

    head_html = h_render(h.head(
        h.meta(charset="utf-8"),
        h.meta(name="viewport", content="width=device-width, initial-scale=1"),
        *head_fragments,
    ))
    return (f'<!doctype html><html id="page" data-ui-theme="{ui_theme}">'
            f'{head_html}'
            f'<body class="page stage">{body_inner_html}</body>'
            f'</html>')


@app.cell
def _():
    # ─── Section 9: live() — the heart of the new API ─────────────────────
    #
    # live(handler, topic) wraps a page handler with SSE/polling/static
    # degradation. The same URL serves all three modes; the framework
    # dispatches on the request's Accept header.
    #
    # The handler returns html_tags elements (a list or a single element).
    # The framework:
    #
    #   1. Renders the elements to a string.
    #   2. Wraps them in <div id="live-root" data-...> based on transport.
    #   3. For SSE requests: streams the wrapped content as patch-elements
    #      events, re-rendering on changes.notify(topic).
    #   4. For non-SSE requests: returns the full HTML page (envelope +
    #      wrapped content) with the appropriate transport attribute.

    return


@app.function
def live(handler=None, *, topic=None, hard_cap=None):
    """Wrap a handler for SSE/polling/static degradation.

    Usable as a function or a decorator:

        # As a function (Wirth-style explicit composition)
        routes = [("GET", "/", live(home, topic="todo"))]

        # As a decorator
        @live(topic="todo")
        def home(req): ...
        routes = [("GET", "/", home)]

    Args:
        handler:  the page handler. Returns html_tags element(s) or a
                  string. If None (decorator form), returns a decorator
                  that takes the handler.
        topic:    string or callable(req) -> string. Identifies the page
                  for LiveCounter capacity decisions and as the Changes
                  subscription pattern. Required.
        hard_cap: optional override of the LiveCounter's hard_cap for
                  just this route.
    """
    # Decorator form: live(topic="foo") or live(topic="foo", hard_cap=X)
    if handler is None:
        def decorator(h):
            return live(h, topic=topic, hard_cap=hard_cap)
        return decorator

    if topic is None:
        raise ValueError("live() requires a topic")

    def _topic_for(req):
        return topic(req) if callable(topic) else topic

    @wraps(handler)
    def wrapper(req):
        live_counter = req["_live"]
        changes = req["_changes"]
        head_fragments = req.get("_head", [])
        ui_theme = req.get("_ui_theme", "dark")
        if live_counter is None or changes is None:
            raise RuntimeError(
                "live() requires req['_live'] and req['_changes']")

        t = _topic_for(req)

        def render_inner():
            """Render the handler's output to an HTML string (no envelope)."""
            return internal_render_content(handler(req))

        accept = req["headers"].get("accept", "")
        is_sse_request = "text/event-stream" in accept

        if is_sse_request:
            # SSE stream loop. Wrapper id="live-root" anchors idiomorph.
            # No data-init on stream frames (would re-trigger the stream).
            return internal_stream_live(t, live_counter, changes, render_inner)

        # Non-SSE: initial page load, polling refetch, or static.
        # Decide mode based on viewer count.
        mode = live_counter.mode(t)
        if hard_cap is not None:
            # Per-route override
            count = live_counter.count(t)
            if count >= hard_cap:
                mode = "static"

        inner = render_inner()
        path = req["path"]

        if mode == "live":
            # Initial page: data-init triggers the SSE stream.
            wrapper_html = (
                f'<div id="{LIVE_ROOT_ID}" '
                f'data-init="@get(\'{path}\')">{inner}</div>'
            )
        elif mode == "poll":
            interval_ms = live_counter.poll_interval_ms(t)
            wrapper_html = (
                f'<div id="{LIVE_ROOT_ID}" '
                f'data-on-interval__duration.{interval_ms}ms="@get(\'{path}\')">'
                f'{inner}</div>'
            )
        else:  # static
            wrapper_html = f'<div id="{LIVE_ROOT_ID}">{inner}</div>'

        full = internal_envelope_safe(head_fragments, wrapper_html, ui_theme)
        return html(full)

    return wrapper


@app.function
def internal_stream_live(topic, live_counter, changes, render_inner):
    """Generator for the SSE live stream. Each frame patches the
    #live-root element with the latest content."""

    def frame():
        return (
            f"event: datastar-patch-elements\n"
            f"data: elements <div id=\"{LIVE_ROOT_ID}\">{render_inner()}</div>"
        )

    with live_counter.join(topic):
        yield frame()
        while True:
            if changes.wait(topic, timeout=15):
                try:
                    yield frame()
                except (OSError, BrokenPipeError):
                    return
            else:
                yield sse_keepalive()


@app.cell
def _():
    # ─── Section 10: connection handling ──────────────────────────────────

    return


@app.function
def handle_connection(sock, addr, routes, before_hooks,
                      live_counter=None, changes_obj=None,
                      head_fragments=None, ui_theme="dark",
                      access_log=True):
    """Run one request to completion, then close the socket."""
    start = time.time()
    status = 0
    method = path = "?"
    req = None
    head_fragments = head_fragments or []

    try:
        try:
            req = parse_request(sock)
            req["_live"] = live_counter
            req["_changes"] = changes_obj
            req["_head"] = head_fragments
            req["_ui_theme"] = ui_theme
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

        matched = match_route(routes, method, path)
        if matched is None:
            status = 404
            write_response(sock, 404, [("content-type", "text/plain")],
                           "not found")
            return
        handler, params = matched
        req["params"] = params

        try:
            for hook in before_hooks:
                hook(req)
        except Exception:
            status = 500
            logger.exception("before-hook failed")
            write_response(sock, 500, [("content-type", "text/plain")],
                           "internal error")
            return

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

        # Tuple response (one-shot)
        if isinstance(result, tuple):
            status_, headers, body = result
            status = status_
            for c in req["_cookies_out"]:
                headers = list(headers) + [("set-cookie", c)]
            sock.settimeout(SSE_WRITE_TIMEOUT)
            write_response(sock, status, headers, body)
            return

        # Generator response (SSE)
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
    # ─── Section 11: serve() ──────────────────────────────────────────────

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
          head=None, ui_theme="dark",
          max_connections=MAX_CONNECTIONS, access_log=True):
    """Run the server.

    Args:
        routes:          list of (method, path, handler) tuples
        before_hooks:    callables run before each handler; may mutate req
        live:            LiveCounter instance (default: LiveCounter())
        changes:         Changes instance (default: new Changes())
        head:            list of html_tags elements injected into <head>
                         of every live-page response (stylesheet, scripts,
                         title, etc). Same on every render so idiomorph
                         leaves head alone.
        ui_theme:        value for the <html data-ui-theme="..."> attr
        max_connections: cap on concurrent connection threads
        access_log:      one INFO line per request
    """
    if not logger.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(h)
        logger.setLevel(logging.INFO)

    from .counter import LiveCounter
    if live is None:
        live = LiveCounter()
    if changes is None:
        changes = Changes()
    head_fragments = list(head or [])

    compiled = compile_routes(routes)
    semaphore = threading.BoundedSemaphore(max_connections)
    stop = internal_ShutdownFlag()

    def _signal(_signum, _frame):
        logger.info("shutdown signal received")
        stop.set()
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

            def _run(c=conn, a=addr, lv=live, ch=changes,
                     hf=head_fragments, theme=ui_theme):
                try:
                    handle_connection(
                        c, a, compiled, before_hooks,
                        live_counter=lv, changes_obj=ch,
                        head_fragments=hf, ui_theme=theme,
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
        alive = sum(1 for t in in_flight if t.is_alive())
        if alive:
            logger.warning(
                "shutdown: %d threads still running after grace period", alive)
        logger.info("shutdown complete")


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()

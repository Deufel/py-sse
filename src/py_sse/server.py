import logging
import re
import signal
import socket
import threading
import time
from urllib.parse import parse_qs, unquote

MAX_HEADER_BYTES = 64 * 1024
MAX_BODY_BYTES = 16 * 1024 * 1024
MAX_CONNECTIONS = 256
HEADER_READ_TIMEOUT = 10
BODY_READ_TIMEOUT = 60
SSE_WRITE_TIMEOUT = 60
SHUTDOWN_GRACE = 5
_METHOD_RE = re.compile(b'^[A-Z]{1,16}$')
_TARGET_RE = re.compile(b'^/[\\x21-\\x7e]{0,2047}$')
_HEADER_NAME_RE = re.compile(b"^[!#$%&'*+\\-.0-9A-Z^_`a-z|~]{1,128}$")
_COOKIE_FORBIDDEN = re.compile('[\\r\\n\\x00]')
logger = logging.getLogger('nano_sse')
PARAM_RE = re.compile('\\{(\\w+)\\}')

"""nano_sse — a Wirth-style minimal SSE web framework.

    Pure Python. Pure stdlib. One OS thread per connection. No async/await.

    Goal: see every byte from socket to handler. Each function does one
    thing, named for it. Data structures are plain dicts. The flow is:

        serve(routes)               accepts TCP, spawns threads
            handle_connection(...)  parses request, finds route, calls handler
                handler(req)        returns a Response or yields SSE frames

    DEPLOYMENT MODEL:
        nano_sse is meant to run BEHIND a reverse proxy (caddy, nginx).
        The proxy terminates TLS, speaks HTTP/1.1 cleartext to us on
        localhost, and handles all the things we deliberately don't:
        TLS, HTTP/2, keepalive coalescing, response compression, IP
        spoofing defense.

        Wire protocol to nano_sse: HTTP/1.1 cleartext. One request per
        connection — we close after responding. Simpler than keepalive
        and fine because the proxy keeps its own pool to us.

    HARDENING (relevant for any non-local exposure, even behind a proxy):
      * Per-connection timeouts (slowloris defense)
      * Bounded concurrent connections (thread/RAM cap)
      * Strict request-line and header validation
      * Cookie value sanitization (no response splitting)
      * Generic 500 responses (no exception text leaked to clients)
      * Graceful shutdown on SIGINT/SIGTERM
      * Access logging
    """

# ─── Section 0: low-level I/O ─────────────────────────────────────────
#
# Read until we see the end of headers (`\r\n\r\n`). Write status/headers/
# body. Nothing higher-level here — just bytes on a socket.

def read_until_double_crlf(sock):
    "Read socket bytes until we see \\r\\n\\r\\n. Returns (head_bytes, leftover)."
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

def read_body(sock, content_length, already_have, limit=MAX_BODY_BYTES):
    "Read exactly content_length bytes, with `already_have` as a prefix."
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

def write_response(sock, status, headers, body=b""):
    "Write a complete HTTP/1.1 response. Body may be bytes or str."
    if isinstance(body, str):
        body = body.encode("utf-8")
    reason = {200:"OK", 204:"No Content", 303:"See Other",
              400:"Bad Request", 401:"Unauthorized", 404:"Not Found",
              408:"Request Timeout", 413:"Payload Too Large",
              500:"Internal Server Error", 503:"Service Unavailable",
              507:"Insufficient Storage"}.get(status, "OK")
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

def write_sse_headers(sock, extra_headers=()):
    "Write the response head for a streaming SSE response. No content-length."
    lines = [
        "HTTP/1.1 200 OK",
        "content-type: text/event-stream",
        "cache-control: no-cache",
        "x-accel-buffering: no",   # disable proxy buffering
        "connection: keep-alive",
        "proxy-buffering: off", 
    ]
    for k, v in extra_headers:
        lines.append(f"{k}: {v}")
    sock.sendall("\r\n".join(lines).encode("ascii") + b"\r\n\r\n")

def write_sse_frame(sock, payload):
    """Write one SSE frame. `payload` is the data line(s) verbatim — caller
    decides whether to send 'data: …', 'event: …\\ndata: …', etc."""
    sock.sendall(payload.encode("utf-8") + b"\n\n")

# ─── Section 2: parsing ───────────────────────────────────────────────
#
# An HTTP request becomes a plain dict. Cookies are parsed once. Path
# parameters are filled in by the route matcher (Section 3).

def parse_request(sock):
    """Read one request off the socket, return a request dict.

    Validates request line and header names strictly. Raises ValueError
    for malformed input, ConnectionError for truncated reads. Callers
    map both to a 400 response.

    The request dict shape is:
        {
            "method":  "GET" | "POST" | ...,
            "path":    "/login",
            "query":   {"k": "v"},
            "headers": {"cookie": "...", ...},  lowercased keys
            "cookies": {"session": "..."},
            "body":    b"...",                  raw bytes; may be b""
            "_sock":   sock,                    for SSE handlers
            "params":  {},                      filled by route matcher
            "_cookies_out": [],                 queued by set_cookie()
        }
    """
    sock.settimeout(HEADER_READ_TIMEOUT)
    head, leftover = read_until_double_crlf(sock)

    # The request head must be valid ASCII (request line) + ISO-8859-1
    # (header values). We split on raw bytes so a stray non-ASCII char
    # in the request line is caught by the strict regex.
    raw_lines = head.split(b"\r\n")
    if not raw_lines or not raw_lines[0]:
        raise ValueError("empty request")

    # Strict request line: METHOD SP TARGET SP HTTP/1.1
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

    # Split path and query string
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
        # Header values may contain ISO-8859-1; reject CR/LF/NUL though.
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

def parse_cookies(cookie_header):
    "Parse a Cookie: header value into a {name: value} dict."
    out = {}
    for pair in cookie_header.split(";"):
        if "=" in pair:
            k, _, v = pair.partition("=")
            k, v = k.strip(), v.strip()
            # Defensive: reject control chars rather than store them.
            if k and not _COOKIE_FORBIDDEN.search(k) and not _COOKIE_FORBIDDEN.search(v):
                out[k] = v
    return out

def set_cookie(req, name, value, **opts):
    """Queue a Set-Cookie on the response. Called from inside a handler.
    Options: max_age (int), path (str), httponly (bool), samesite ('Lax'|...)

    Rejects any name/value containing CR, LF, or NUL — those would allow
    response-splitting attacks via header injection.
    """
    if _COOKIE_FORBIDDEN.search(name) or _COOKIE_FORBIDDEN.search(str(value)):
        raise ValueError("cookie name/value contains forbidden control characters")
    pieces = [f"{name}={value}"]
    for k, v in opts.items():
        k = k.replace("_", "-")
        if _COOKIE_FORBIDDEN.search(str(v)):
            raise ValueError(f"cookie option {k} contains forbidden characters")
        if isinstance(v, bool):
            if v: pieces.append(k)
        else:
            pieces.append(f"{k}={v}")
    req["_cookies_out"].append("; ".join(pieces))

def signals(req):
    """Parse Datastar signals from a request.
    GET: JSON-encoded `datastar` query parameter.
    POST/PUT/PATCH/DELETE: JSON body."""
    import json
    if req["method"] == "GET":
        raw = req["query"].get("datastar", "{}")
        return json.loads(raw) if isinstance(raw, str) else raw
    if not req["body"]:
        return {}
    data = json.loads(req["body"])
    return data.get("datastar", data) if isinstance(data, dict) else data

# ─── Section 3: routing ───────────────────────────────────────────────
#
# Routes are a plain list of (method, regex_pattern, handler) tuples,
# built once by compile_routes(). Matching is a linear scan; for a chat
# app with ~10 routes that's negligible.



def compile_routes(routes):
    """Take a list of (method, path, handler) tuples and compile path
    patterns to regex. Returns a list ready for match_route()."""
    compiled = []
    for method, path, handler in routes:
        if "{" in path:
            regex = "^" + PARAM_RE.sub(r"(?P<\1>[^/]+)", path) + "$"
            compiled.append((method.upper(), re.compile(regex), handler))
        else:
            compiled.append((method.upper(), re.compile("^" + re.escape(path) + "$"), handler))
    return compiled

def match_route(routes, method, path):
    "Find the first route matching (method, path). Returns (handler, params) or None."
    for route_method, pattern, handler in routes:
        if route_method != method:
            continue
        m = pattern.match(path)
        if m:
            return handler, m.groupdict()
    return None

def html(body, status=200):
    "Return a Response for an HTML page."
    return (status, [("content-type", "text/html; charset=utf-8")], body)

def redirect(location, status=303):
    "Return a Response that redirects to `location`."
    # Don't allow header injection via crafted location values.
    if "\r" in location or "\n" in location:
        raise ValueError("redirect target contains forbidden characters")
    return (status, [("location", location)], b"")

def no_content():
    "Return a 204 No Content response."
    return (204, [], b"")

def blob(data, content_type, filename=None):
    """Return a Response carrying raw bytes. If filename is given, sets
    Content-Disposition: attachment so the browser downloads it."""
    headers = [
        ("content-type", content_type),
        ("x-content-type-options", "nosniff"),
    ]
    if filename:
        safe = "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_"
                       for c in filename)[:200] or "file"
        headers.append(("content-disposition", f'attachment; filename="{safe}"'))
    return (200, headers, data)

def error(status, message=""):
    "Return an error Response with optional plain-text body."
    return (status, [("content-type", "text/plain; charset=utf-8")], message)

# ─── Section 5: connection handling ───────────────────────────────────
#
# This is the per-thread entry point. It does:
#   1. Parse the request from the socket (with timeouts)
#   2. Find a matching route
#   3. Call the handler
#   4. Write the response — short, redirect, or SSE stream
#
# Errors are caught, logged, and turned into generic responses. The
# socket is closed in a single `finally` regardless of code path.

def handle_connection(sock, addr, routes, before_hooks, access_log=True):
    """Run one request to completion, then close the socket.
    Called in its own OS thread. Never raises out of this function."""
    start = time.time()
    status = 0
    method = path = "?"
    req = None
    try:
        # ── parse ──────────────────────────────────────────────
        try:
            req = parse_request(sock)
        except socket.timeout:
            status = 408
            write_response(sock, 408, [("content-type","text/plain")], "request timeout")
            return
        except (ValueError, ConnectionError) as e:
            status = 400
            # Log the *real* reason locally; tell the client nothing.
            logger.info("400 from %s: %s", addr[0] if addr else "?", e)
            write_response(sock, 400, [("content-type","text/plain")], "bad request")
            return
        except Exception:
            status = 400
            logger.exception("error parsing request from %s", addr)
            write_response(sock, 400, [("content-type","text/plain")], "bad request")
            return

        method = req["method"]; path = req["path"]

        # ── route ──────────────────────────────────────────────
        matched = match_route(routes, method, path)
        if matched is None:
            status = 404
            write_response(sock, 404, [("content-type","text/plain")], "not found")
            return
        handler, params = matched
        req["params"] = params

        # ── before-hooks ───────────────────────────────────────
        try:
            for hook in before_hooks:
                hook(req)
        except Exception:
            status = 500
            logger.exception("before-hook failed")
            write_response(sock, 500, [("content-type","text/plain")], "internal error")
            return

        # ── handler ────────────────────────────────────────────
        try:
            result = handler(req)
        except Exception:
            status = 500
            logger.exception("handler raised for %s %s", method, path)
            write_response(sock, 500, [("content-type","text/plain")], "internal error")
            return

        if result is None:
            result = no_content()

        # ── short response: tuple ──────────────────────────────
        if isinstance(result, tuple):
            status_, headers, body = result
            status = status_
            for c in req["_cookies_out"]:
                headers = list(headers) + [("set-cookie", c)]
            # Clear any earlier read timeout before the write; some clients
            # take a beat to ACK the response.
            sock.settimeout(SSE_WRITE_TIMEOUT)
            write_response(sock, status, headers, body)
            return

        # ── SSE stream: generator ──────────────────────────────
        status = 200
        sock.settimeout(SSE_WRITE_TIMEOUT)
        extra = [("set-cookie", c) for c in req["_cookies_out"]]
        try:
            write_sse_headers(sock, extra)
            for frame in result:
                if frame is None:
                    continue
                write_sse_frame(sock, frame)
        except (OSError, ConnectionError, socket.timeout):
            pass  # client disconnected or stalled; normal for SSE
        except Exception:
            logger.exception("SSE generator raised for %s %s", method, path)
        finally:
            try: result.close()
            except Exception: pass

    finally:
        try: sock.close()
        except Exception: pass
        if access_log:
            dt_ms = (time.time() - start) * 1000
            logger.info("%s %s %s → %d %.1fms",
                        addr[0] if addr else "?", method, path, status, dt_ms)

# ─── Section 6: the listen loop ───────────────────────────────────────
#
# Bind, listen, accept. Each connection gets one OS thread, but we cap
# the total via a semaphore — past that, new connections get an
# immediate 503 and close, so a flood can't OOM the process.

class _ShutdownFlag:
    "A flag set by SIGINT/SIGTERM to stop the accept loop."
    def __init__(self):
        self._stop = False
    def set(self): self._stop = True
    def is_set(self): return self._stop

def serve(routes, *, host="127.0.0.1", port=8000,
          before_hooks=(), max_connections=MAX_CONNECTIONS,
          access_log=True):
    """Run the server. Blocks until SIGINT/SIGTERM.

    `routes`:           list of (method, path, handler) tuples.
    `before_hooks`:     run before each handler, in order; may mutate req.
    `max_connections`:  cap on concurrent connection threads.
    `access_log`:       one INFO line per request to the nano_sse logger.

    Hardening behaviors:
      * Per-conn read timeouts: HEADER_READ_TIMEOUT, BODY_READ_TIMEOUT.
      * Bounded thread count: connections past `max_connections` get an
        immediate 503 and close — the process won't OOM under flood.
      * Graceful shutdown on SIGINT/SIGTERM: stop accepting, wait up to
        SHUTDOWN_GRACE seconds for in-flight requests, then exit.
    """
    if not logger.handlers:
        # Default to stderr at INFO level. Caller can override.
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(h)
        logger.setLevel(logging.INFO)

    compiled = compile_routes(routes)
    semaphore = threading.BoundedSemaphore(max_connections)
    stop = _ShutdownFlag()

    def _signal(_signum, _frame):
        logger.info("shutdown signal received")
        stop.set()
    signal.signal(signal.SIGINT, _signal)
    signal.signal(signal.SIGTERM, _signal)

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.listen(128)
    s.settimeout(0.5)  # let the accept loop tick every 500ms to check `stop`
    logger.info("nano_sse listening on http://%s:%d (max_connections=%d)",
                host, port, max_connections)

    in_flight = []  # weak list of running threads, for graceful shutdown wait

    try:
        while not stop.is_set():
            try:
                conn, addr = s.accept()
            except socket.timeout:
                continue
            except OSError:
                break  # socket closed

            # Try to acquire a slot in the bounded pool. Past the cap,
            # immediately answer 503 and close — don't queue, don't fork
            # a thread we can't sustain.
            if not semaphore.acquire(blocking=False):
                logger.warning("connection cap (%d) reached, dropping %s",
                               max_connections, addr[0])
                try:
                    write_response(conn, 503,
                                   [("content-type","text/plain"),
                                    ("retry-after","1")],
                                   "server busy")
                except Exception:
                    pass
                conn.close()
                continue

            def _run(c=conn, a=addr):
                try:
                    handle_connection(c, a, compiled, before_hooks, access_log)
                finally:
                    semaphore.release()

            t = threading.Thread(target=_run, daemon=True)
            t.start()
            in_flight.append(t)
            # Prune dead threads occasionally so the list doesn't grow forever.
            if len(in_flight) > 1024:
                in_flight = [x for x in in_flight if x.is_alive()]

    finally:
        s.close()
        # Graceful drain: wait for in-flight threads up to SHUTDOWN_GRACE.
        deadline = time.time() + SHUTDOWN_GRACE
        for t in in_flight:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            t.join(timeout=remaining)
        live = sum(1 for t in in_flight if t.is_alive())
        if live:
            logger.warning("shutdown: %d threads still running after grace period",
                           live)
        logger.info("shutdown complete")

class Changes:
    """A single shared bit: 'something changed'. Subscribers are implicit —
    they are the threads currently parked in wait(). No registry, no list.
 
    A writer flips the bit; every waiter wakes; each waiter re-renders from
    the DB on its own. When a connection dies, its generator dies, its
    thread dies, and the subscription disappears by construction.
 
    Usage:
        changes = Changes()
        # in SSE generator:
        changes.wait(timeout=15)   # block until set, or timeout
        # in writer (or from the DB update hook):
        changes.notify()           # wake everyone
    """
    def __init__(self):
        self._event = threading.Event()
 
    def notify(self):
        """Wake every thread parked in wait(). Safe to call from any
        thread, including from inside an APSW update_hook callback."""
        self._event.set()
        self._event.clear()
 
    def wait(self, timeout=15):
        """Block until the next notify(), or until `timeout` seconds pass.
        Returns when there's something new OR on timeout (for keepalive).
 
        Note the race: if notify() runs between two wait() calls, the
        set/clear pair completes before this wait() sees it, and we'll
        block until the next event or timeout. For chat-scale traffic
        with a 15s keepalive ceiling that's fine — the next render is
        at most 15s late, and only if no further writes happen.
        """
        self._event.wait(timeout=timeout)

def sse_data(text):
    "Format a string as an SSE data line."
    return "\n".join(f"data: {line}" for line in text.splitlines() or [""])

def sse_event(event_name, data):
    "Format a named SSE event."
    return f"event: {event_name}\n{sse_data(data)}"

def sse_keepalive():
    "An SSE comment line that keeps the connection alive without firing a real event."
    return ":"

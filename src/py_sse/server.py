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
_live = None
_changes = None

"""py_sse server — a minimal SSE web framework.

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

def write_sse_frame(sock, payload, encoder=None):
    "Write one SSE frame, optionally compressed."
    raw = payload.encode("utf-8") + b"\n\n"
    if encoder is None or encoder.name == "identity":
        sock.sendall(raw)
        return
    chunk = encoder.encode(raw) + encoder.flush()
    if chunk:
        sock.sendall(chunk)

class _SseEncoder:
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

def match_route(routes, method, path):
    "Find the first matching route. Returns (handler, params) or None."
    for route_method, pattern, handler in routes:
        if route_method != method:
            continue
        m = pattern.match(path)
        if m:
            return handler, m.groupdict()
    return None

def html(body, status=200):
    "Return an HTML page response."
    return (status, [("content-type", "text/html; charset=utf-8")], body)

def redirect(location, status=303):
    "Return a redirect response."
    if "\r" in location or "\n" in location:
        raise ValueError("redirect target contains forbidden characters")
    return (status, [("location", location)], b"")

def no_content():
    "Return a 204 No Content response."
    return (204, [], b"")

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

def error(status, message=""):
    "Return an error response."
    return (status, [("content-type", "text/plain; charset=utf-8")], message)

def sse_data(text):
    "Format a string as an SSE data line."
    return "\n".join(f"data: {line}" for line in (text.splitlines() or [""]))

def sse_event(event_name, data):
    "Format a named SSE event."
    return f"event: {event_name}\n{sse_data(data)}"

def sse_keepalive():
    "An SSE comment line. Keeps the connection alive without firing an event."
    return ":"

def handle_connection(sock, addr, routes, before_hooks, access_log=True):
    """Run one request to completion, then close the socket.
    Called in its own OS thread. Never raises out of this function.
    """
    start = time.time()
    status = 0
    method = path = "?"
    req = None
    try:
        # Parse
        try:
            req = parse_request(sock)
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
        encoder = _SseEncoder(encoding)
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

class _ShutdownFlag:
    def __init__(self):
        self._stop = False

    def set(self):
        self._stop = True

    def is_set(self):
        return self._stop

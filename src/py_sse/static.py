import mimetypes, os
from email.utils import formatdate

MIME_OVERRIDES = {'.svg': 'image/svg+xml', '.js': 'application/javascript', '.mjs': 'application/javascript', '.woff2': 'font/woff2', '.woff': 'font/woff'}

def _etag(stat):
    return f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'

def _last_modified(stat):
    return formatdate(stat.st_mtime, usegmt=True)

async def _send_file(send, req, full_path, stat):
    ext = os.path.splitext(full_path)[1].lower()
    content_type = MIME_OVERRIDES.get(ext) or mimetypes.guess_type(full_path)[0] or "application/octet-stream"
    etag = _etag(stat)

    if req["headers"].get("if-none-match") == etag:
        await send({"type": "http.response.start", "status": 304, "headers": [[b"etag", etag.encode()]]})
        await send({"type": "http.response.body", "body": b""})
        req["_sent"] = True
        return

    with open(full_path, "rb") as f:
        body = f.read()

    headers = [
        [b"content-type", content_type.encode()],
        [b"content-length", str(len(body)).encode()],
        [b"etag", etag.encode()],
        [b"last-modified", _last_modified(stat).encode()],
        [b"cache-control", b"public, max-age=0, must-revalidate"],
    ]
    await send({"type": "http.response.start", "status": 200, "headers": headers})
    await send({"type": "http.response.body", "body": body})
    req["_sent"] = True

def static(app, url_prefix, directory):
    """Mount a directory (or single file) for static file serving.

    Usage:
        static(app, "/static", "static/")
        static(app, "/favicon.svg", "favicon.svg")
    """
    directory = os.path.abspath(directory)

    if os.path.isfile(directory):
        async def serve_single(req):
            stat = os.stat(directory)
            await _send_file(req["internal_send"], req, directory, stat)
        app.get(url_prefix)(serve_single)
        return

    async def serve_dir(req):
        rel_path = req["params"].get("path", "")
        if not rel_path:
            return None
        full = os.path.normpath(os.path.join(directory, rel_path))
        if not full.startswith(directory) or not os.path.isfile(full):
            return None
        stat = os.stat(full)
        await _send_file(req["internal_send"], req, full, stat)

    app.mount(url_prefix.rstrip("/"), serve_dir)

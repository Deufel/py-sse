import asyncio

class Stream:
    __slots__ = ('_send', '_closed', '_headers', '_keepalive_task', '_compressor')

    def __init__(self, send, closed):
        self._send = send
        self._closed = closed
        self._headers = []
        self._keepalive_task = None
        self._compressor = None

    def cookie(self, name, value, **opts):
        parts = [f"{name}={value}"]
        for k, v in opts.items():
            k = k.replace("_", "-")
            if isinstance(v, bool):
                if v: parts.append(k)
            else: parts.append(f"{k}={v}")
        self._headers.append([b"set-cookie", "; ".join(parts).encode()])
        return self

    async def open(self, keepalive=15, compress=False):
        headers = [
            [b"content-type", b"text/event-stream"],
            [b"cache-control", b"no-cache"],
            [b"connection", b"keep-alive"],
            [b"x-accel-buffering", b"no"],
        ] + self._headers
        if compress:
            import brotli
            headers.append([b"content-encoding", b"br"])
            self._compressor = brotli.Compressor(mode=brotli.MODE_TEXT, quality=1)
        await self._send({"type": "http.response.start", "status": 200, "headers": headers})
        if keepalive:
            self._keepalive_task = asyncio.create_task(self._auto_keepalive(keepalive))

    async def _auto_keepalive(self, interval):
        try:
            while not self._closed.is_set():
                await asyncio.sleep(interval)
                await self.send_event(":\n\n")
        except asyncio.CancelledError:
            pass

    async def send_event(self, data):
        if self._closed.is_set(): return
        raw = data.encode()
        if self._compressor:
            raw = self._compressor.process(raw) + self._compressor.flush()
        try:
            await self._send({"type": "http.response.body", "body": raw, "more_body": True})
        except Exception:
            self._closed.set()

    async def close(self):
        if self._keepalive_task:
            self._keepalive_task.cancel()
        if self._closed.is_set(): return
        self._closed.set()
        body = b""
        if self._compressor:
            body = self._compressor.finish()
        await self._send({"type": "http.response.body", "body": body})

    async def alive(self, events):
        async for event in events:
            if self._closed.is_set(): break
            yield event

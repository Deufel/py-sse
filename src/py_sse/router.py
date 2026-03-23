from .request import Request
from .response import Response
from .stream import Stream
import asyncio

async def internal_watch_disconnect(receive, closed):
    while True:
        msg = await receive()
        if msg.get("type") == "http.disconnect":
            closed.set()
            return

class Router:
    __slots__ = ('_routes',)

    def __init__(self):
        self._routes = {}

    def get(self, path):
        def decorator(fn):
            self._routes[("GET", path)] = fn
            return fn
        return decorator

    def post(self, path):
        def decorator(fn):
            self._routes[("POST", path)] = fn
            return fn
        return decorator

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return
        if scope["type"] != "http": return
        req = Request(scope, receive)
        key = (req.method, req.path)
        handler = self._routes.get(key)
        if not handler:
            res = Response(send)
            await res.text("Not Found", status=404)
            return
        closed = asyncio.Event()
        task = asyncio.create_task(internal_watch_disconnect(receive, closed))
        try:
            await handler(req, send, closed)
        finally:
            task.cancel()

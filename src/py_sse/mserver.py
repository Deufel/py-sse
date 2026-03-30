import asyncio
import threading
import socket
import time
from collections import deque

DEV_LOG = deque(maxlen=200)
STATE = {'server': None, 'thread': None, 'loop': None, 'stop_event': None}

def serve_background(app, host="127.0.0.1", port=8000, **kwargs):
    """Run a py-sse app in a background thread. Restarts if already running."""
    stop_background()

    from granian.server.embed import Server
    from granian.constants import Interfaces

    server = Server(app, address=host, port=port, interface=Interfaces.RSGI, **kwargs)
    loop = asyncio.new_event_loop()

    async def run():
        asyncio.set_event_loop(loop)
        await server.serve()

    def thread_target():
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run())
        except RuntimeError:
            pass  # expected on restart — old loop stopped

    thread = threading.Thread(target=thread_target, daemon=True)
    thread.start()

    STATE.update(server=server, thread=thread, loop=loop)
    DEV_LOG.append(f"started on {host}:{port}")

def stop_background():
    if _state["loop"] and _state["loop"].is_running():
        _state["loop"].call_soon_threadsafe(_state["loop"].stop)
    STATE.update(server=None, thread=None, loop=None)
    DEV_LOG.append("stopped")

def dev_alive(port=8000):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0

def recent(n=20):
    entries = list(DEV_LOG)
    return entries[-n:] if entries else []

def request_logger(req):
    """py-sse beforeware that logs to the dev monitor.
    [todo: just use the relay better ...]
    """
    ts = time.strftime("%H:%M:%S")
    DEV_LOG.append(f"{ts} {req['method']} {req['path']}")
    return None  # continue to handler

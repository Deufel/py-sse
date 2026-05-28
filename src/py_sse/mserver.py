import asyncio
import socket
import threading
from dataclasses import dataclass

"""Background server bootstrap for dev/notebook use.

    `serve` (in app.py) is the production path. This module gives you a
    background-thread server with explicit start/stop, suitable for marimo
    notebooks and other "run while a cell is parked" workflows.
    """

@dataclass
class ServerState:
    "Handle returned by serve_background; pass to stop_background."
    server: object = None
    loop:   object = None
    thread: object = None
    host:   str    = "127.0.0.1"
    port:   int    = 8000

def serve_background(app, host: str = "127.0.0.1", port: int = 8000, **kwargs) -> ServerState:
    """Run a py-sse app in a background thread.

        state = serve_background(app)
        # ... later ...
        stop_background(state)
    """
    from granian.server.embed import Server
    from granian.constants import Interfaces

    server = Server(app, address=host, port=port, interface=Interfaces.RSGI, **kwargs)
    loop = asyncio.new_event_loop()

    async def run():
        await server.serve()

    def thread_target():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run())

    thread = threading.Thread(target=thread_target, daemon=True)
    thread.start()
    return ServerState(server=server, loop=loop, thread=thread, host=host, port=port)

def stop_background(state: ServerState) -> None:
    """Stop a background server via Granian's clean shutdown path.

    Joins the worker thread with a 3-second timeout. If the thread is still
    alive after that, prints a warning — a zombie thread will prevent the
    next serve_background on the same port from binding.
    """
    if state.server and state.loop and state.loop.is_running():
        state.loop.call_soon_threadsafe(state.server.stop)
    if state.thread:
        state.thread.join(timeout=3)
        if state.thread.is_alive():
            print(f"WARNING: server thread on port {state.port} did not stop within 3s")

def dev_alive(port_or_state) -> bool:
    "Check whether a port (or ServerState's port) is accepting connections."
    port = port_or_state.port if isinstance(port_or_state, ServerState) else port_or_state
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0

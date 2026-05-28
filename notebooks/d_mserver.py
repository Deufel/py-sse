import marimo

__generated_with = "0.23.6"
app = marimo.App()

with app.setup:
    """Background server bootstrap for dev/notebook use.

    `serve` (in app.py) is the production path. This module gives you a
    background-thread server with explicit start/stop, suitable for marimo
    notebooks and other "run while a cell is parked" workflows.
    """
    import asyncio
    import socket
    import threading
    from dataclasses import dataclass


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Package: py-sse
    ## Module: .mserver
    > run a thread safe server (useful in a repl or notebook enviorment)
    """)
    return


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.class_definition
@dataclass
class ServerState:
    "Handle returned by serve_background; pass to stop_background."
    server: object = None
    loop:   object = None
    thread: object = None
    host:   str    = "127.0.0.1"
    port:   int    = 8000


@app.function
def serve_background(
    app,                    # the py-sse app to run
    host:str="127.0.0.1",   # bind address
    port:int=8000,          # bind port
    **kwargs,               # forwarded to granian's embedded Server
)->ServerState:             # handle to pass to stop_background
    "Run a py-sse app in a background thread."
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


@app.function
def stop_background(
    state:ServerState, # handle from serve_background
)->None:
    "Stop a background server via Granian's clean shutdown; warns if the thread won't die in 3s."
    if state.server and state.loop and state.loop.is_running():
        state.loop.call_soon_threadsafe(state.server.stop)
    if state.thread:
        state.thread.join(timeout=3)
        if state.thread.is_alive():
            print(f"WARNING: server thread on port {state.port} did not stop within 3s")


@app.function
def dev_alive(
    port_or_state, # a port int, or a ServerState to read .port from
)->bool:           # True if the port is accepting connections
    "Check whether a port (or ServerState's port) is accepting connections."
    port = port_or_state.port if isinstance(port_or_state, ServerState) else port_or_state
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


if __name__ == "__main__":
    app.run()

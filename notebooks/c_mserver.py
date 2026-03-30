import marimo

__generated_with = "0.21.1"
app = marimo.App(layout_file="layouts/c_mserver.slides.json")

with app.setup:
    import asyncio
    import threading
    import socket
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
    """Handle returned by serve_background, passed to stop_background."""
    server: object = None
    loop:   object = None
    thread: object = None


@app.function
def serve_background(app, host="127.0.0.1", port=8000, **kwargs) -> ServerState:
    """Run a py-sse app in a background thread.
 
    Returns a ServerState handle for stop_background.
 
        state = serve_background(app)
        # later ...
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
    return ServerState(server=server, loop=loop, thread=thread)


@app.function
def stop_background(state: ServerState) -> None:
    """Stop a background server via Granian's clean shutdown path."""
    if state.server and state.loop and state.loop.is_running():
        state.loop.call_soon_threadsafe(state.server.stop)
    if state.thread:
        state.thread.join(timeout=3)


@app.function
def dev_alive(port=8000):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


@app.function
def request_logger(relay, topic="dev.request"):
    """Create a beforeware that publishes requests to a relay.
 
        relay = create_relay()
        app.before(request_logger(relay))
    """
    def hook(req):
        relay.publish(topic, f"{req['method']} {req['path']}")
    return hook


if __name__ == "__main__":
    app.run()

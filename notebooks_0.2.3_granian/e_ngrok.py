import marimo

__generated_with = "0.23.6"
app = marimo.App()

with app.setup:
    """Ngrok tunnel helpers for exposing a dev server."""
    from dataclasses import dataclass



@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Package: py-sse
    ## Module: .ngrok
    > ngrok for live testing
    """)
    return


@app.class_definition
@dataclass
class TunnelState:
    "Handle returned by start_tunnel; pass to stop_tunnel."
    listener: object = None
    url:      str    = ""


@app.function
def load_env(path: str = ".env"):
    "Minimal .env loader: KEY=VALUE per line, # for comments."
    import os
    for line in open(path):
        if "=" in (line := line.strip()) and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


@app.function
def start_tunnel(port: int = 8000, **kwargs) -> TunnelState:
    """Open an ngrok tunnel to localhost:port.

    Requires the `ngrok` package and NGROK_AUTHTOKEN env var.
    Extra kwargs forward to ngrok.forward().

        tunnel = start_tunnel(8000)
        print(tunnel.url)
        stop_tunnel(tunnel)
    """
    import threading
    import ngrok

    result = [None]

    def _connect():
        result[0] = ngrok.forward(port, authtoken_from_env=True, **kwargs)

    t = threading.Thread(target=_connect)
    t.start()
    t.join()

    listener = result[0]
    return TunnelState(listener=listener, url=listener.url())


@app.function
def stop_tunnel(tunnel: TunnelState) -> None:
    "Close an ngrok tunnel."
    if tunnel.listener:
        import ngrok
        ngrok.disconnect(tunnel.url)


if __name__ == "__main__":
    app.run()

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
def load_env(
    path:str=".env", # env file to read; missing file is a no-op
):
    "Minimal .env loader: `KEY=VALUE` per line, `#` for comments. Won't override existing vars."
    import os
    if not os.path.exists(path): return
    with open(path, encoding="utf-8") as f:
        for line in f:
            if "=" in (line := line.strip()) and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


@app.function
def start_tunnel(
    port:int=8000, # local port to forward
    **kwargs,      # forwarded to ngrok.forward()
)->TunnelState:    # handle to pass to stop_tunnel
    "Open an ngrok tunnel to localhost:`port` (needs the `ngrok` pkg + NGROK_AUTHTOKEN)."
    import threading
    import ngrok

    result, error = [None], [None]

    def _connect():
        try:                   result[0] = ngrok.forward(port, authtoken_from_env=True, **kwargs)
        except Exception as e: error[0]  = e

    t = threading.Thread(target=_connect)
    t.start()
    t.join()

    if error[0] is not None: raise error[0]
    return TunnelState(listener=result[0], url=result[0].url())


@app.function
def stop_tunnel(
    tunnel:TunnelState, # handle from start_tunnel
)->None:                # nothing
    "Close an ngrok tunnel."
    if tunnel.listener:
        import ngrok
        ngrok.disconnect(tunnel.url)


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()

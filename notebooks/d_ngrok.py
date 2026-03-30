import marimo

__generated_with = "0.21.1"
app = marimo.App()

with app.setup:
    from dataclasses import dataclass
    import ngrok


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
    """Handle returned by start_tunnel, passed to stop_tunnel."""
    listener: object = None
    url:      str    = ""


@app.function
def start_tunnel(port=8000, **kwargs) -> TunnelState:
    """Open an ngrok tunnel to localhost:port.
 
    Requires the ``ngrok`` package and NGROK_AUTHTOKEN env var.
    Extra kwargs are forwarded to ngrok.forward().
 
        tunnel = start_tunnel(8000)
        print(tunnel.url)
        # later ...
        stop_tunnel(tunnel)
    """
    import ngrok
    listener = ngrok.forward(port, authtoken_from_env=True, **kwargs)
    return TunnelState(listener=listener, url=listener.url())


@app.function
def stop_tunnel(tunnel: TunnelState) -> None:
    """Close an ngrok tunnel."""
    if tunnel.listener:
        import ngrok
        ngrok.disconnect(tunnel.url)


@app.cell
def _():
    import marimo as mo

    return (mo,)


if __name__ == "__main__":
    app.run()

from dataclasses import dataclass

"""Ngrok tunnel helpers for exposing a dev server."""

@dataclass
class TunnelState:
    "Handle returned by start_tunnel; pass to stop_tunnel."
    listener: object = None
    url:      str    = ""

def load_env(path: str = ".env"):
    "Minimal .env loader: KEY=VALUE per line, # for comments."
    import os
    for line in open(path):
        if "=" in (line := line.strip()) and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

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

def stop_tunnel(tunnel: TunnelState) -> None:
    "Close an ngrok tunnel."
    if tunnel.listener:
        import ngrok
        ngrok.disconnect(tunnel.url)

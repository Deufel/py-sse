from dataclasses import dataclass

"""Ngrok tunnel helpers for exposing a dev server."""

@dataclass
class TunnelState:
    "Handle returned by start_tunnel; pass to stop_tunnel."
    listener: object = None
    url:      str    = ""

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

def stop_tunnel(
    tunnel:TunnelState, # handle from start_tunnel
)->None:                # nothing
    "Close an ngrok tunnel."
    if tunnel.listener:
        import ngrok
        ngrok.disconnect(tunnel.url)

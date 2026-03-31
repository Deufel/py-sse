from dataclasses import dataclass
import ngrok

@dataclass
class TunnelState:
    """Handle returned by start_tunnel, passed to stop_tunnel."""
    listener: object = None
    url:      str    = ""

def load_env(path=".env"):
    for line in open(path):
        if "=" in (line := line.strip()) and not line.startswith("#"):
            k, v = line.split("=", 1)
            __import__("os").environ.setdefault(k.strip(), v.strip())

def start_tunnel(port=8000, **kwargs) -> TunnelState:
    """Open an ngrok tunnel to localhost:port.
 
    Requires the ``ngrok`` package and NGROK_AUTHTOKEN env var.
    Extra kwargs are forwarded to ngrok.forward().
 
        tunnel = start_tunnel(8000)
        print(tunnel.url)
        # later ...
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
    """Close an ngrok tunnel."""
    if tunnel.listener:
        import ngrok
        ngrok.disconnect(tunnel.url)

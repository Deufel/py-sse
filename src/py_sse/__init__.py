"""Minimal application wrapper for granian"""
__version__ = '0.1.1'
__author__ = 'Deufel'
from .app import body, signals, set_cookie, create_relay, create_signer, static, create_app, serve
from .sse import patch_elements, patch_signals, remove_signals, execute_script
from .mserver import ServerState, serve_background, stop_background, dev_alive, request_logger
__all__ = [
    "ServerState",
    "body",
    "create_app",
    "create_relay",
    "create_signer",
    "dev_alive",
    "execute_script",
    "patch_elements",
    "patch_signals",
    "remove_signals",
    "request_logger",
    "serve",
    "serve_background",
    "set_cookie",
    "signals",
    "static",
    "stop_background",
]

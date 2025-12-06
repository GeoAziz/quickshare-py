"""QuickShare package — lightweight P2P chunked transfer primitives.

This package complements the existing top-level quickshare.py web server.
Provide a compact, backwards-compatible re-export surface for convenience.
"""
from .discovery import DiscoveryManager as Discovery
from .control import ControlServer, send_control_offer
from .transfer import Sender, Receiver
from .fileutils import get_chunk_sha256, sha256_file, preallocate_file

__all__ = [
    'Discovery', 'ControlServer', 'send_control_offer', 'Sender', 'Receiver',
    'get_chunk_sha256', 'sha256_file', 'preallocate_file'
]


def get_local_ip():
    """Return the local network IP of this machine (same behavior as quickshare.py)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

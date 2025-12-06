"""Simple LAN discovery: UDP announcer and listener.

Announcer periodically broadcasts a small JSON payload containing `name` and
`port` to a chosen UDP address (default port 37020). Listener receives these
announcements and keeps an in-memory peer list with last-seen timestamps.

This is intentionally small and test-friendly: by default it sends to
127.0.0.1 (loopback) so tests can run reliably. In production you can set
broadcast_addr to e.g. '<broadcast>' or a multicast address.
"""
import socket
import threading
import time
import json
from typing import Dict, Any, Optional


DEFAULT_PORT = 37020
DEFAULT_INTERVAL = 2.0
STALE_TIMEOUT = 5.0


class DiscoveryListener:
    """Listen for UDP JSON announcements and maintain a peer list."""

    def __init__(self, bind_addr: str = '127.0.0.1', bind_port: int = DEFAULT_PORT, stale_timeout: float = STALE_TIMEOUT):
        self.bind_addr = bind_addr
        self.bind_port = bind_port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # bind to the chosen address/port
        self._sock.bind((self.bind_addr, self.bind_port))
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._recv_loop, daemon=True)
        # peers: key -> {name, port, addr, last_seen, data}
        self.peers: Dict[str, Dict[str, Any]] = {}
        self.stale_timeout = stale_timeout

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            # closing socket will break recv
            self._sock.close()
        except Exception:
            pass
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _recv_loop(self) -> None:
        self._sock.settimeout(0.5)
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                self._cleanup_stale()
                continue
            except OSError:
                break
            try:
                payload = json.loads(data.decode('utf-8'))
                name = payload.get('name')
                port = int(payload.get('port'))
            except Exception:
                continue
            key = f"{addr[0]}:{port}:{name}"
            self.peers[key] = {'name': name, 'port': port, 'addr': addr[0], 'last_seen': time.time(), 'data': payload}

    def _cleanup_stale(self) -> None:
        now = time.time()
        stale = [k for k, v in self.peers.items() if now - v['last_seen'] > self.stale_timeout]
        for k in stale:
            del self.peers[k]

    def get_peers(self) -> Dict[str, Dict[str, Any]]:
        self._cleanup_stale()
        return dict(self.peers)


class DiscoveryAnnouncer:
    """Periodically announce name/port via UDP to a target address."""

    def __init__(self, name: str, port: int, target_addr: str = '127.0.0.1', target_port: int = DEFAULT_PORT, interval: float = DEFAULT_INTERVAL):
        self.name = name
        self.port = port
        self.target_addr = target_addr
        self.target_port = target_port
        self.interval = interval
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # allow broadcast if target is broadcast
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except Exception:
            pass
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def announce_now(self) -> None:
        payload = json.dumps({'name': self.name, 'port': self.port}).encode('utf-8')
        try:
            self._sock.sendto(payload, (self.target_addr, self.target_port))
        except Exception:
            pass

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.announce_now()
            time.sleep(self.interval)


class DiscoveryManager:
    """Convenience wrapper: announcer + listener and peer access."""

    def __init__(self, name: str, control_port: int, bind_addr: str = '127.0.0.1', bind_port: int = DEFAULT_PORT, interval: float = DEFAULT_INTERVAL):
        self.listener = DiscoveryListener(bind_addr=bind_addr, bind_port=bind_port)
        self.announcer = DiscoveryAnnouncer(name=name, port=control_port, target_addr=bind_addr, target_port=bind_port, interval=interval)

    def start(self) -> None:
        self.listener.start()
        self.announcer.start()

    def stop(self) -> None:
        self.announcer.stop()
        self.listener.stop()

    def get_peers(self):
        return self.listener.get_peers()
"""Discovery module: simple UDP broadcast + listener for LAN peer discovery."""
import socket
import threading
import json
import time

DISCOVERY_PORT = 37020
BUF_SIZE = 4096


class Discovery:
    def __init__(self, name: str, control_port: int, interval: float = 2.0):
        self.name = name
        self.control_port = control_port
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None

    def _broadcast_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        payload = json.dumps({'name': self.name, 'port': self.control_port}).encode('utf-8')
        while not self._stop.is_set():
            try:
                sock.sendto(payload, ('<broadcast>', DISCOVERY_PORT))
            except Exception:
                pass
            time.sleep(self.interval)
        sock.close()

    def start_broadcast(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._broadcast_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)


class Listener:
    """Listen for discovery packets and call a callback(payload, addr).

    Callback receives a dict and (ip,port).
    """
    def __init__(self, callback):
        self.callback = callback
        self._stop = threading.Event()
        self._thread = None

    def _listen_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('', DISCOVERY_PORT))
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(BUF_SIZE)
                try:
                    payload = json.loads(data.decode('utf-8'))
                except Exception:
                    continue
                self.callback(payload, addr)
            except Exception:
                continue
        sock.close()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

"""Control channel: newline-delimited JSON handshake over TCP.

Provides:
- ControlServer(handler): a lightweight TCP control server running in a
  background thread. The handler(offer) -> reply dict is invoked per-connection.
- send_control_offer(host, port, offer): client helper used by Sender.send.
"""
import socket
import threading
import json
import typing
import os


class ControlServer:
    """A simple control server that accepts newline-delimited JSON offers.

    Parameters
    - host, port: bind address. Use port=0 for ephemeral.
    - handler: callable(offer: dict) -> reply: dict
    """

    def __init__(self, host: str = '0.0.0.0', port: int = 0, handler: typing.Optional[typing.Callable] = None):
        self.host = host
        self.port = port
        self.handler = handler or (lambda offer: {'type': 'reject'})
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(5)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve_loop, daemon=True)
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Start serving in a background thread."""
        self._thread.start()

    def stop(self, timeout: float = 1.0) -> None:
        """Stop the server and wait up to `timeout` seconds for the thread."""
        self._stop_event.set()
        try:
            self._sock.close()
        except Exception:
            pass
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _serve_loop(self) -> None:
        # Periodic timeout so stop event is checked.
        self._sock.settimeout(0.5)
        while not self._stop_event.is_set():
            try:
                conn, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = threading.Thread(target=self._handle_conn, args=(conn, addr), daemon=True)
            t.start()

    def _handle_conn(self, conn: socket.socket, addr) -> None:
        with conn:
            try:
                # read one newline-terminated JSON line
                f = conn.makefile('rb')
                line = f.readline()
                if not line:
                    return
                offer = json.loads(line.decode('utf-8'))
            except Exception:
                return

            try:
                reply = self.handler(offer)
            except Exception:
                reply = {'type': 'error'}

            try:
                conn.sendall((json.dumps(reply) + '\n').encode('utf-8'))
            except Exception:
                return


def send_control_offer(host: str, port: int, offer: dict, timeout: float = 5.0) -> dict:
    """Send offer to control server and return parsed reply dict.

    This is the helper expected by `Sender.send` in `transfer.py`.
    """
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall((json.dumps(offer) + '\n').encode('utf-8'))
        # read until newline
        buf = b''
        while True:
            b = s.recv(4096)
            if not b:
                break
            buf += b
            if b.find(b'\n') != -1:
                break
        line = buf.split(b'\n', 1)[0]
        try:
            return json.loads(line.decode('utf-8'))
        except Exception:
            return {}


if __name__ == '__main__':
    # Demo server that returns accept with ephemeral ports and a save_path
    def demo_handler(offer):
        filename = offer.get('filename', 'received')
        total_chunks = int(offer.get('total_chunks', 1))
        ports = []
        for _ in range(total_chunks):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('127.0.0.1', 0))
            ports.append(s.getsockname()[1])
            s.close()
        save_path = os.path.abspath(filename + '.part')
        return {'type': 'accept', 'ports': ports, 'save_path': save_path}

    srv = ControlServer(host='127.0.0.1', port=0, handler=demo_handler)
    srv.start()
    print('Control server listening on', srv.port)
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()

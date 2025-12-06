"""Parallel chunked transfer primitives: Sender and Receiver.

Simple protocol used in tests:
- Sender connects to receiver control port and sends an "offer" JSON line with
  filename, size, chunk_size, total_chunks.
- Receiver opens one TCP data listener per chunk (ephemeral ports) and replies
  with JSON: {"type":"accept","ports":[...],"save_path":"/abs/path"}
- Sender connects to each port and sends a newline-delimited JSON header
  {"chunk":i,"length":len} followed by the raw bytes of that chunk.
"""
import socket
import threading
import json
import os
import time
from typing import Optional
from .fileutils import preallocate_file, write_chunk, get_chunk_sha256, sha256_file


def _read_n(conn, n):
    data = b''
    while len(data) < n:
        chunk = conn.recv(min(65536, n - len(data)))
        if not chunk:
            raise EOFError('unexpected EOF')
        data += chunk
    return data


class Receiver:
    def __init__(self, save_path: str, chunk_size: int, total_chunks: int):
        self.save_path = os.path.abspath(save_path)
        self.chunk_size = chunk_size
        self.total_chunks = total_chunks
        self._data_listeners = []  # (socket, thread, port)
        self._received = {}
        self._lock = threading.Lock()

    def _start_data_listeners(self):
        listeners = []
        for _ in range(self.total_chunks):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('0.0.0.0', 0))
            s.listen(1)
            port = s.getsockname()[1]
            listeners.append((s, port))
        self._data_listeners = listeners
        return [p for (sock, p) in listeners]

    def _accept_and_receive(self, listen_sock):
        conn, addr = listen_sock.accept()
        with conn:
            # read header line
            buf = b''
            while True:
                b = conn.recv(4096)
                if not b:
                    return
                buf += b
                if b.find(b'\n') != -1:
                    break
            header_line, rest = buf.split(b'\n', 1)
            try:
                header = json.loads(header_line.decode('utf-8'))
                chunk_idx = int(header.get('chunk'))
                length = int(header.get('length'))
            except Exception:
                return
            # read remaining data (rest may contain some)
            data = rest
            if len(data) < length:
                data += _read_n(conn, length - len(data))
            # write chunk to file at offset
            offset = chunk_idx * self.chunk_size
            write_chunk(self.save_path, offset, data[:length])
            # store checksum
            ch = get_chunk_sha256(data[:length])
            with self._lock:
                self._received[chunk_idx] = ch

    def handle_offer_and_receive(self, offer: dict):
        """Given an offer dict, prepare save file, open data listeners and return reply.

        This method also starts background threads that accept connections and
        write incoming chunks. It returns a dict reply that includes ports.
        """
        filename = offer.get('filename', 'received_file')
        size = int(offer.get('size', 0))
        chunk_size = int(offer.get('chunk_size', self.chunk_size))
        total_chunks = int(offer.get('total_chunks', self.total_chunks))
        # ensure values
        self.chunk_size = chunk_size
        self.total_chunks = total_chunks
        # create temp path in same dir
        base = os.path.basename(filename)
        tmp = os.path.abspath(filename + '.part')
        # preallocate
        preallocate_file(tmp, size)
        self.save_path = tmp

        ports = self._start_data_listeners()

        # start accept threads, one per listener
        for sock, port in self._data_listeners:
            t = threading.Thread(target=self._accept_and_receive, args=(sock,), daemon=True)
            t.start()

        reply = {'type': 'accept', 'ports': ports, 'save_path': tmp}
        return reply

    def finalize(self, expected_sha256: Optional[str] = None):
        """Wait a small amount for threads to finish and compute full sha256."""
        # Close listeners
        for sock, _ in self._data_listeners:
            try:
                sock.close()
            except Exception:
                pass
        # compute file hash
        full = sha256_file(self.save_path)
        ok = True
        if expected_sha256 and full != expected_sha256:
            ok = False
        return {'sha256': full, 'ok': ok, 'received_chunks': sorted(self._received.keys())}


class Sender:
    def __init__(self, filepath: str, chunk_size: int = 1024 * 1024):
        self.filepath = os.path.abspath(filepath)
        self.chunk_size = chunk_size
        self.total_size = os.path.getsize(self.filepath)
        self.total_chunks = (self.total_size + self.chunk_size - 1) // self.chunk_size

    def send(self, receiver_host: str, receiver_control_port: int, control_send_func, progress_callback=None):
        """Send an offer and stream chunks to provided ports.

        control_send_func should implement sending the offer and returning the reply dict,
        for example `control.send_control_offer`.
        """
        offer = {
            'filename': os.path.basename(self.filepath),
            'size': self.total_size,
            'chunk_size': self.chunk_size,
            'total_chunks': self.total_chunks,
        }
        reply = control_send_func(receiver_host, receiver_control_port, offer)
        ports = reply.get('ports', [])
        if len(ports) != self.total_chunks:
            # For simplicity this implementation expects one port per chunk
            raise RuntimeError('Receiver did not give matching ports')

        # send each chunk over its port
        STREAM_BLOCK = 64 * 1024

        def _send_chunk(chunk_idx, port):
            # Stream the chunk in smaller blocks and call progress_callback for bytes sent
            with open(self.filepath, 'rb') as f:
                f.seek(chunk_idx * self.chunk_size)
                remaining = min(self.chunk_size, self.total_size - chunk_idx * self.chunk_size)
            header = json.dumps({'chunk': chunk_idx, 'length': remaining}) + '\n'
            # connection retry/backoff
            max_conn_retries = 3
            conn_backoff = 0.1
            conn = None
            for attempt in range(max_conn_retries):
                try:
                    conn = socket.create_connection((receiver_host, port), timeout=5.0)
                    break
                except Exception:
                    if attempt + 1 == max_conn_retries:
                        raise
                    else:
                        time.sleep(conn_backoff)
                        conn_backoff *= 2
            # ensure we have a connection
            if conn is None:
                raise RuntimeError('failed to establish connection')
            # send header and stream blocks with limited retries for sendall
            with conn:
                try:
                    conn.sendall(header.encode('utf-8'))
                except Exception:
                    # header couldn't be sent; propagate
                    raise
                with open(self.filepath, 'rb') as f:
                    f.seek(chunk_idx * self.chunk_size)
                    to_send = remaining
                    while to_send > 0:
                        bs = min(STREAM_BLOCK, to_send)
                        block = f.read(bs)
                        if not block:
                            break
                        # limited retries for block send
                        max_block_retries = 2
                        block_attempt = 0
                        while True:
                            try:
                                conn.sendall(block)
                                # report progress per-block
                                try:
                                    if progress_callback:
                                        progress_callback(chunk_idx, len(block))
                                except Exception:
                                    pass
                                break
                            except Exception:
                                block_attempt += 1
                                if block_attempt > max_block_retries:
                                    # give up and propagate
                                    raise
                                # small backoff before retrying on same connection
                                time.sleep(0.05)
                        to_send -= len(block)

        threads = []
        for i, port in enumerate(ports):
            t = threading.Thread(target=_send_chunk, args=(i, port), daemon=True)
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

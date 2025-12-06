import os
import time
import tempfile

from quickshare.transfer import Sender, Receiver
from quickshare.control import ControlServer, send_control_offer
from quickshare.fileutils import sha256_file


def _wait_for_received(receiver: Receiver, total: int, timeout: float = 5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with receiver._lock:
            if len(receiver._received) >= total:
                return True
        time.sleep(0.01)
    return False


def test_transfer_one_chunk(tmp_path):
    # small file so everything fits in one chunk
    data = os.urandom(50 * 1024)
    src = tmp_path / 'one.bin'
    src.write_bytes(data)

    sender = Sender(str(src), chunk_size=1024 * 1024)
    # receiver will pick up save_path from offer; init with placeholder
    receiver = Receiver(save_path=str(tmp_path / 'out.bin'), chunk_size=sender.chunk_size, total_chunks=sender.total_chunks)

    srv = ControlServer(host='127.0.0.1', port=0, handler=lambda offer: receiver.handle_offer_and_receive(offer))
    srv.start()
    try:
        sender.send('127.0.0.1', srv.port, send_control_offer)
        assert _wait_for_received(receiver, sender.total_chunks, timeout=2.0), 'receiver did not report chunk'
        res = receiver.finalize(expected_sha256=sha256_file(str(src)))
        assert res['ok'], f"sha mismatch: {res['sha256']}"
        assert res['sha256'] == sha256_file(str(src))
        assert sorted(res['received_chunks']) == list(range(sender.total_chunks))
    finally:
        srv.stop()


def test_transfer_eight_chunks(tmp_path):
    # create an 8-chunk file using 16KB chunks
    chunk_size = 16 * 1024
    total_chunks = 8
    size = chunk_size * total_chunks
    data = os.urandom(size)
    src = tmp_path / 'eight.bin'
    src.write_bytes(data)

    sender = Sender(str(src), chunk_size=chunk_size)
    assert sender.total_chunks == total_chunks

    receiver = Receiver(save_path=str(tmp_path / 'out2.bin'), chunk_size=sender.chunk_size, total_chunks=sender.total_chunks)

    srv = ControlServer(host='127.0.0.1', port=0, handler=lambda offer: receiver.handle_offer_and_receive(offer))
    srv.start()
    try:
        sender.send('127.0.0.1', srv.port, send_control_offer)
        assert _wait_for_received(receiver, sender.total_chunks, timeout=5.0), 'receiver did not receive all chunks in time'
        res = receiver.finalize(expected_sha256=sha256_file(str(src)))
        assert res['ok']
        assert res['sha256'] == sha256_file(str(src))
        assert sorted(res['received_chunks']) == list(range(sender.total_chunks))
    finally:
        srv.stop()
import os
import tempfile
import threading
import time
import random

from quickshare.transfer import Sender, Receiver
from quickshare.control import send_control_offer, ControlServer


def _make_temp_file(path, size):
    with open(path, 'wb') as f:
        f.write(os.urandom(size))


def test_simple_parallel_transfer(tmp_path):
    # create test file ~1MB
    src = tmp_path / 'source.bin'
    size = 1024 * 1024
    _make_temp_file(str(src), size)

    # Receiver side: control server that uses Receiver.handle_offer_and_receive
    received_dir = str(tmp_path)
    receiver_obj = None

    def handler(offer):
        nonlocal receiver_obj
        filename = offer.get('filename', 'source.bin')
        save_path = os.path.join(received_dir, filename)
        # total_chunks from offer
        total_chunks = int(offer.get('total_chunks', 1))
        chunk_size = int(offer.get('chunk_size', 1024 * 1024))
        r = Receiver(save_path, chunk_size, total_chunks)
        receiver_obj = r
        return r.handle_offer_and_receive(offer)

    control_port = 60001
    server = ControlServer(port=control_port, handler=handler)

    def server_thread():
        server.start()

    t = threading.Thread(target=server_thread, daemon=True)
    t.start()

    time.sleep(0.1)

    # Sender: send file to localhost
    s = Sender(str(src), chunk_size=256 * 1024)
    # Sender performs handshake and sends chunks (Sender.send performs the control handshake)
    s.send('127.0.0.1', control_port, send_control_offer)

    # give the receiver a short moment to finalize
    time.sleep(0.5)

    # finalize and validate
    assert receiver_obj is not None
    res = receiver_obj.finalize()
    dest = receiver_obj.save_path
    assert os.path.exists(dest)
    # verify sizes
    assert os.path.getsize(dest) == s.total_size

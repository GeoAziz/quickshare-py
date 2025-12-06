"""CLI: discovery, list peers, send files.

This CLI announces the local node (via DiscoveryManager) so it can be
discovered by peers, lists discovered peers, and sends files using the
control + transfer modules.

Designed to be loopback-friendly: default bind/target is 127.0.0.1:37020.
"""
import argparse
import socket
import sys
import time
import os
from typing import List, Tuple

from quickshare.discovery import DiscoveryManager
from quickshare.control import send_control_offer
from quickshare.transfer import Sender


def _collect_peers(name: str, control_port: int, bind_addr: str, bind_port: int, wait: float) -> List[Tuple[str, dict]]:
    mgr = DiscoveryManager(name=name, control_port=control_port, bind_addr=bind_addr, bind_port=bind_port, interval=1.0)
    mgr.start()
    try:
        time.sleep(wait)
        peers = mgr.get_peers()
        ordered = list(peers.items())
        return ordered
    finally:
        mgr.stop()


def cmd_list(args) -> int:
    name = args.name or socket.gethostname()
    peers = _collect_peers(name, control_port=args.port, bind_addr=args.bind, bind_port=args.bind_port, wait=args.wait)
    if not peers:
        print('No peers discovered')
        return 0
    for i, (k, v) in enumerate(peers):
        print(f"[{i}] {v['name']} @ {v['addr']}:{v['port']}")
    return 0


def cmd_send(args) -> int:
    name = args.name or socket.gethostname()
    peers = _collect_peers(name, control_port=args.local_port, bind_addr=args.bind, bind_port=args.bind_port, wait=args.wait)
    if not peers:
        print('No peers discovered')
        return 1
    for i, (k, v) in enumerate(peers):
        print(f"[{i}] {v['name']} @ {v['addr']}:{v['port']}")

    idx = args.peer
    if idx is None:
        try:
            idx = int(input('Select peer index: '))
        except Exception:
            print('Invalid input')
            return 2
    if idx < 0 or idx >= len(peers):
        print('peer index out of range')
        return 3

    _, peer = peers[idx]
    host = peer['addr']
    port = int(peer['port'])
    if not os.path.exists(args.file):
        print('file not found:', args.file)
        return 4

    sender = Sender(args.file, chunk_size=args.chunk_size)
    print(f"Sending {args.file} -> {peer['name']} ({host}:{port}) using {sender.total_chunks} chunks")
    # progress tracking
    total_chunks = sender.total_chunks
    completed = 0
    total_bytes_sent = 0
    lock = __import__('threading').Lock()
    start_time = None
    last_time = None
    last_ui_update = 0.0
    UI_MIN_INTERVAL = 0.25  # seconds (max 4 Hz)

    def _format_time(seconds: float) -> str:
        if seconds is None or seconds != seconds:
            return '--:--'
        if seconds < 0:
            return '--:--'
        m, s = divmod(int(seconds), 60)
        return f"{m:02d}:{s:02d}"

    def _progress_cb(chunk_idx, bytes_sent):
        nonlocal completed, total_bytes_sent, start_time, last_time, last_ui_update
        now = time.time()
        with lock:
            if start_time is None:
                start_time = now
                last_time = now
            completed += 1
            total_bytes_sent += bytes_sent
            elapsed = now - start_time if start_time else 0.0
            avg_bps = total_bytes_sent / elapsed if elapsed > 0 else 0.0
            # instantaneous throughput (since last callback)
            interval = now - last_time if last_time else 0.0
            inst_bps = bytes_sent / interval if interval > 0 else avg_bps
            last_time = now
            remaining = max(0, sender.total_size - total_bytes_sent)
            eta = remaining / avg_bps if avg_bps > 0 else float('nan')
            pct = (total_bytes_sent / sender.total_size) * 100 if sender.total_size else 100.0
            # single-line updating progress bar with ETA and throughput
            bar_len = 40
            filled = int(bar_len * pct / 100)
            bar = '=' * filled + '-' * (bar_len - filled)
            # rate-limit UI updates to at most 4 Hz
            now_ui = now
            if (now_ui - last_ui_update) >= UI_MIN_INTERVAL or completed == total_chunks:
                sys.stdout.write(
                    f"\rProgress: [{bar}] {pct:6.2f}% ({completed}/{total_chunks} chunks) "
                    f"{total_bytes_sent}/{sender.total_size} bytes "
                    f"{avg_bps/1024:7.2f} KB/s ETA {_format_time(eta)}"
                )
                sys.stdout.flush()
                last_ui_update = now_ui

    try:
        sender.send(host, port, send_control_offer, progress_callback=_progress_cb)
    except Exception as e:
        # ensure we end the progress line before printing error
        try:
            sys.stdout.write('\n')
            sys.stdout.flush()
        except Exception:
            pass
        print('Transfer failed:', e)
        return 5
    # finalize progress line
    try:
        sys.stdout.write('\n')
        sys.stdout.flush()
    except Exception:
        pass
    print('Transfer completed')
    return 0


def cmd_recv(args) -> int:
    # Start a control receiver that will accept offers and write to out_dir
    from quickshare.control import ControlServer
    from quickshare.transfer import Receiver

    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)

    receivers = []

    def handler(offer):
        filename = offer.get('filename', 'received')
        size = int(offer.get('size', 0))
        chunk_size = int(offer.get('chunk_size', args.chunk_size))
        total_chunks = int(offer.get('total_chunks', 1))
        save_path = os.path.join(out_dir, filename)
        r = Receiver(save_path, chunk_size, total_chunks)
        receivers.append(r)
        return r.handle_offer_and_receive(offer)

    srv = ControlServer(host=args.bind, port=args.port, handler=handler)
    srv.start()
    print(f"Receiver control listening on {args.bind}:{args.port}. Press Ctrl-C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print('Stopping receiver...')
        srv.stop()
        # finalize receivers
        for r in receivers:
            res = r.finalize()
            print('Received:', res)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog='quickshare')
    sub = p.add_subparsers(dest='cmd')

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--bind', default='127.0.0.1', help='Bind address for discovery/control (default loopback)')
    common.add_argument('--bind-port', default=37020, type=int, help='Discovery port (default 37020)')

    l = sub.add_parser('list', parents=[common])
    l.add_argument('--wait', type=float, default=1.0, help='Seconds to wait for discovery')
    l.add_argument('--name', help='Local name to announce (optional)')
    l.add_argument('--port', type=int, default=0, help='Local control port announced (optional)')

    s = sub.add_parser('send', parents=[common])
    s.add_argument('--wait', type=float, default=1.0, help='Seconds to wait for discovery')
    s.add_argument('--name', help='Local name to announce (optional)')
    s.add_argument('--local-port', type=int, default=0, help='Local control port to announce (optional)')
    s.add_argument('--peer', type=int, help='Peer index to send to (optional)')
    s.add_argument('--chunk-size', type=int, default=1024 * 1024)
    s.add_argument('file', help='Path to file to send')

    r = sub.add_parser('recv', parents=[common])
    r.add_argument('--port', type=int, default=60000, help='Control port to listen on')
    r.add_argument('--chunk-size', type=int, default=1024 * 1024, help='Expected chunk size')
    r.add_argument('--out', default='received', help='Output directory for received files')

    args = p.parse_args(argv)
    if args.cmd == 'list' or args.cmd is None:
        return cmd_list(args)
    if args.cmd == 'send':
        return cmd_send(args)
    if args.cmd == 'recv':
        return cmd_recv(args)
    p.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())

# QuickSharePy v1.0.0 — Release Notes

Release date: 2025-12-06

Overview
--------
This release introduces a next‑generation parallel P2P file transfer engine
integrated into the QuickSharePy repository. It sits alongside the original
HTTP-based quickshare script and targets high-speed LAN transfers using
parallel TCP streams, simple P2P discovery, and an easy CLI.

Key features
------------
- Peer discovery: UDP announcements on LAN (default port 37020).
- Control channel: TCP handshake (offer/accept), JSON protocol for metadata.
- Parallel chunked transfer: file split into chunks (default 1 MiB), each
  chunk streamed over its own TCP connection to saturate LAN links.
- Chunk streaming: chunks sent in 64 KiB blocks for smooth progress updates.
- Integrity: per-chunk SHA256 verification and final full-file SHA256 check.
- CLI: `list`, `send`, and `recv` commands with a single-line progress bar,
  throughput (KB/s) and ETA (MM:SS); progress updates are rate-limited to avoid
  terminal flicker.
- Robustness: connection retries (3 attempts) with exponential backoff and per-block send retries (2 attempts).

Modules added/updated
---------------------
- `quickshare/discovery.py` — DiscoveryManager, DiscoveryListener, DiscoveryAnnouncer
- `quickshare/control.py` — ControlServer and `send_control_offer`
- `quickshare/transfer.py` — Sender and Receiver with streaming and retries
- `quickshare/fileutils.py` — preallocate_file, write_chunk, get_chunk_sha256, sha256_file
- `quickshare/cli.py` — CLI: `list`, `send`, and `recv` commands and progress UI

Tests
-----
- `tests/test_transfer.py` — loopback tests for 1-chunk and 8-chunk transfers.
- `tests/test_discovery.py` — loopback discovery test.

Usage examples
--------------
Discover peers (short wait):

```bash
python -m quickshare.cli list --wait 1.0
```

Send a file (interactive selection if `--peer` omitted):

```bash
python -m quickshare.cli send --file /path/to/file --wait 1.0
# or specify peer index from `list`:
python -m quickshare.cli send --peer 0 --file /path/to/file
```

Run a receiver to accept incoming transfers:

```bash
python -m quickshare.cli recv --port 60000 --out ./received
```

Progress & robustness notes
--------------------------
- The CLI displays a single-line progress bar with percentage, chunk progress, bytes transferred, average throughput in KB/s, and ETA (MM:SS).
- Progress updates are rate-limited to at most 4 updates per second to avoid terminal flicker on very fast transfers.
- Each chunk is streamed in 64 KiB blocks and the CLI updates per-block.
- Retry behavior:
  - Connection establishment: up to 3 retries with exponential backoff.
  - Each block send is retried up to 2 times with a short backoff.

Configuration options (CLI flags)
-------------------------------
- `--bind` — the address to bind discovery and/or receivers (default `127.0.0.1`)
- `--bind-port` — discovery UDP port (default `37020`)
- `--port` / `--local-port` — control TCP port to listen/announce
- `--chunk-size` — logical chunk size (default 1 MiB)
- Block streaming size (internal): 64 KiB per-block (tunable in code)

Notes & roadmap
---------------
This release focuses on a robust, high-performance LAN transfer UX. Planned
future improvements (next iteration):

- Resumable chunk transfers (protocol changes for chunk offset/partial writes)
- TLS/DTLS for control and/or data channels
- EMA smoothing for throughput and ETA and per-stream stats
- Transfer logging and a persistent transfer history

References
----------
- See `CHANGELOG.md` and `RELEASE_CHECKLIST.md` for more details and release steps.

# QuickSharePy

QuickSharePy is a tiny zero-configuration Python utility to share any folder over your local network. No cloud, no accounts — just run one command and other devices on the same LAN can browse and download files using a web browser.

## Features

- Instant setup: share the current folder with one command
- LAN-based access: other devices on the same Wi‑Fi/LAN can connect
- Optional password (Bearer token) for basic access protection
- Optional QR code output for quick mobile access (requires `qrcode`)
- Optional upload support via HTML form (enabled by default in the script)
- Cross-platform — runs anywhere Python 3 is available

This repository also contains a next-generation parallel P2P file transfer
engine (co-existing with the original HTTP share script). The new tool is
designed for high-speed LAN transfers using parallel TCP streams and simple
peer discovery.

## Quick start

1. (Optional) Install optional dependencies for QR and colored output:

```powershell
python -m pip install qrcode colorama
```

2. Run in the folder you want to share (or pass a folder):

```powershell
# Share current folder on default port 8080
python quickshare.py

# Share a specific folder on port 8080
python quickshare.py "C:\path\to\folder" --port 8080

# Enable password (clients must send header Authorization: Bearer mypass)
python quickshare.py . --password mypass

# Show QR code (requires `qrcode` package)
python quickshare.py . --qr
```

When running you'll see output like:

```
🚀 QuickSharePy
Serving folder: C:\path\to\folder
Access at: http://192.168.1.25:8080

✅ Server running. Press Ctrl+C to stop.
```

Open the displayed URL from another device on the same network.

---

## Next‑gen P2P file transfer (new)

This project includes a parallel, chunked, P2P transfer engine with these
capabilities:

- Peer discovery on LAN via UDP announcements
- TCP control handshake (offer / accept / port list)
- Parallel chunked data transfer: one TCP stream per chunk
- Receiver-side preallocation and per-chunk + full-file SHA256 verification
- CLI for discovery, sending, and receiving with real-time progress/ETA
- Robustness: connection retries and per-block send retries

Core modules and where to find them:

- `quickshare/discovery.py` — UDP announcer and listener (DiscoveryManager)
- `quickshare/control.py` — TCP control handshake (ControlServer, send_control_offer)
- `quickshare/transfer.py` — Sender and Receiver (parallel chunked transfer)
- `quickshare/fileutils.py` — preallocation, chunk writes, hashing helpers
- `quickshare/cli.py` — CLI: `list`, `send`, `recv` commands and progress UI

Run the built-in tests to verify everything:

```bash
PYTHONPATH=$(pwd) pytest -q
```

CLI usage examples

- Discover peers (prints a short list):

```bash
python -m quickshare.cli list --wait 1.0
```

- Send a file to a discovered peer (interactive selection if --peer omitted):

```bash
python -m quickshare.cli send --file /path/to/file --wait 1.0
# or specify peer index from `list`:
python -m quickshare.cli send --peer 0 --file /path/to/file
```

- Run a receiver (accept offers and save incoming files):

```bash
python -m quickshare.cli recv --port 60000 --out ./received
```

Progress, throughput and retries

- The CLI displays a single-line progress bar with percentage, completed
	chunks, bytes transferred, average throughput (KB/s), and ETA (MM:SS).
- Progress updates are rate-limited to at most 4 updates per second to
	avoid terminal flicker on very fast transfers.
- Each chunk is streamed in 64 KiB blocks and the CLI updates per-block.
- Retry behavior:
	- Connection establishment retries up to 3 times with exponential backoff.
	- Each block send is retried up to 2 times with a short backoff.

Configuration options (CLI flags)

- `--bind` — the address to bind discovery and/or receivers (default `127.0.0.1`)
- `--bind-port` — discovery UDP port (default `37020`)
- `--port` / `--local-port` — control TCP port to listen/announce
- `--chunk-size` — logical chunk size (default 1 MiB)
- Block streaming size (internal): 64 KiB per-block (tunable in code)

Notes on robustness and future work

- Current retry logic covers transient connection or send failures. If a
	connection is lost mid-chunk the receiver currently does not support
	resuming the same chunk (that would require protocol extensions).
- Future improvements (recommended for later releases): resumable chunk
	transfers, TLS for control/data, EMA smoothing for ETA, transfer logs.


## Uploads

The root directory listing includes a simple file upload form that saves uploaded files to the current directory. This is intentionally minimal — treat uploads as an opt-in, local convenience.

Improvements in this version:

- Uploads are saved into an `uploads/` subfolder by default.
- Filenames are sanitized to avoid directory traversal and unsafe characters.
- The server enforces a maximum upload size (default 200 MB). You can customize via environment variables:

```powershell
# Example (PowerShell):
$env:QUICKSHARE_UPLOAD_DIR = 'C:\path\to\uploads'
$env:QUICKSHARE_MAX_UPLOAD_BYTES = 104857600  # 100 MB
python quickshare.py
```

Notes: Uploaded files will get a numeric suffix if a file with the same name already exists to avoid accidental overwrite.

## Security notes

- Password protection is a simple Bearer token checked against the `Authorization` header.
- This tool is intended for trusted local networks only. Do not expose the port to the public internet.

## Future ideas

- HTTPS support
- Persistent bookmarks and config
- Drag-and-drop web UI

## License

MIT

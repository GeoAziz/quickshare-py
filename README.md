# QuickSharePy

QuickSharePy is a tiny zero-configuration Python utility to share any folder over your local network. No cloud, no accounts — just run one command and other devices on the same LAN can browse and download files using a web browser.

## Features

- Instant setup: share the current folder with one command
- LAN-based access: other devices on the same Wi‑Fi/LAN can connect
- Optional password (Bearer token) for basic access protection
- Optional QR code output for quick mobile access (requires `qrcode`)
- Optional upload support via HTML form (enabled by default in the script)
- Cross-platform — runs anywhere Python 3 is available

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

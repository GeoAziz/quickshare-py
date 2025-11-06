#!/usr/bin/env python3
"""
QuickSharePy — Instant File Sharing Over LAN (v2 with Uploads)
Author: Your Name
License: MIT
"""

import http.server
import socketserver
import os
import argparse
import socket
import sys
import io
import cgi
import html
import urllib.parse
import datetime
import base64
from pathlib import Path
import json
import zipfile

# Optional extras
try:
    import qrcode
    from colorama import Fore, Style, init
    init(autoreset=True)
except ImportError:
    qrcode = None
    Fore = Style = None


def get_local_ip():
    """Return the local network IP of this machine."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't actually send packets — used to determine outbound IP
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


class UploadHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler with upload + optional password support."""
    password = None

    def do_AUTHHEAD(self):
        self.send_response(401)
        # Use Bearer token in Authorization header: Authorization: Bearer <token>
        self.send_header("WWW-Authenticate", 'Bearer realm="QuickSharePy"')
        self.end_headers()

    def check_auth(self):
        """Check if password protection is enabled and valid."""
        if self.password:
            auth = self.headers.get("Authorization")
            if auth != f"Bearer {self.password}":
                self.do_AUTHHEAD()
                self.wfile.write(b"Unauthorized: Missing or invalid token.\n")
                return False
        return True
        
    def do_GET(self):
        if not self.check_auth():
            return
        # Show directory + upload form for root
        if self.path in ("/", ""):
            self.list_directory(os.getcwd())
        else:
            super().do_GET()

    def list_directory(self, path):
        """Override directory listing to include upload form and enhanced UI."""
        try:
            file_list = os.listdir(path)
        except OSError:
            self.send_error(404, "No permission to list directory")
            return None

        file_list.sort(key=lambda a: a.lower())

        def human_size(n):
            for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
                if n < 1024.0:
                    return f"{n:3.1f} {unit}"
                n /= 1024.0
            return f"{n:.1f} PB"

        def mtime(ts):
            return datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')

        display = io.StringIO()
        display.write("<!doctype html><html><head><meta charset=\"utf-8\"><title>QuickSharePy</title>")
        display.write("<style>body{font-family:Segoe UI,Roboto,Arial,sans-serif;margin:0;padding:0;background:#f6f8fa;color:#0b0c0c}header{background:#0b5cff;color:#fff;padding:14px 20px}main{padding:20px}h1{margin:0;font-size:18px}a{color:#0b5cff;text-decoration:none}table{width:100%;border-collapse:collapse;margin-top:12px;background:#fff;border:1px solid #e1e4e8}th,td{padding:10px;border-bottom:1px solid #e1e4e8;text-align:left}th{background:#f1f5f9}tr:hover{background:#fbfdff}#uploadArea{border:2px dashed #cbd5e1;padding:18px;border-radius:8px;text-align:center;color:#475569;background:#ffffff}#uploadArea.dragover{background:#eef2ff;border-color:#7c3aed}button.copy{background:#0b5cff;color:#fff;border:none;padding:6px 8px;border-radius:4px;cursor:pointer}small{color:#6b7280}</style>")
        display.write("</head><body>")
        display.write('<header><h1>📂 QuickSharePy — File Share</h1></header>')
        display.write('<main>')

        try:
            port = self.server.server_address[1]
        except Exception:
            port = ''
        base_url = f"http://{get_local_ip()}:{port}" if port else get_local_ip()
        display.write(f'<p><strong>Serving:</strong> {html.escape(os.getcwd())} &nbsp; <small> | Access at: <a href="{base_url}">{base_url}</a></small></p>')

        # QR code embed (if available)
        if qrcode:
            try:
                img = qrcode.make(base_url)
                bio = io.BytesIO()
                img.save(bio, format='PNG')
                b64 = base64.b64encode(bio.getvalue()).decode('ascii')
                display.write(f'<p><img alt="QR code" src="data:image/png;base64,{b64}" style="width:120px;height:120px;border:1px solid #e5e7eb;border-radius:8px"/></p>')
            except Exception:
                pass

        # Upload area + download selected button
        display.write('<section id="uploadArea">')
        display.write('<p><strong>Drag & drop files here</strong> or click to select files to upload.</p>')
        display.write('<input id="fileInput" type="file" name="file" style="display:none" multiple/>')
        display.write('<div style="display:flex;gap:8px;align-items:center"><button id="chooseBtn" class="copy" type="button">Choose files</button><button id="downloadBtn" class="copy" type="button">Download selected</button><label style="margin-left:8px"><input type="checkbox" id="selectAll"> Select all</label></div>')
        display.write('<div id="uploadMsg" style="margin-top:8px;color:#6b7280"></div>')
        display.write('</section>')

        # File table
        display.write('<table id="files"><thead><tr><th>Name</th><th style="width:120px">Size</th><th style="width:180px">Modified</th><th style="width:120px">Actions</th></tr></thead><tbody>')
        for name in file_list:
            fullname = os.path.join(path, name)
            esc_name = html.escape(name)
            href = urllib.parse.quote(name)
            try:
                stats = os.stat(fullname)
                size = human_size(stats.st_size) if os.path.isfile(fullname) else '-'
                modified = mtime(stats.st_mtime)
            except Exception:
                size = '-'
                modified = '-'
            is_dir = os.path.isdir(fullname)
            icon = '📁' if is_dir else '📄'
            display.write(f'<tr><td><input type="checkbox" class="selectFile" data-file="{href}"> {icon} <a href="{href}">{esc_name}</a></td><td>{size}</td><td>{modified}</td><td><button class="copy-link" data-file="{href}">Copy link</button></td></tr>')
        display.write('</tbody></table>')

        display.write('<p style="margin-top:12px"><small>QuickSharePy — lightweight LAN file sharing</small></p>')

        # JS: drag-and-drop, file chooser, copy link, download selected
        display.write("<script>")
        display.write("const uploadArea=document.getElementById('uploadArea');")
        display.write("const fileInput=document.getElementById('fileInput');")
        display.write("const chooseBtn=document.getElementById('chooseBtn');")
        display.write("const uploadMsg=document.getElementById('uploadMsg');")
        display.write("const downloadBtn=document.getElementById('downloadBtn');")
        display.write("const selectAll=document.getElementById('selectAll');")
        display.write("uploadArea.addEventListener('click',()=>fileInput.click());")
        display.write("chooseBtn.addEventListener('click',()=>fileInput.click());")
        display.write("['dragenter','dragover'].forEach(e=>uploadArea.addEventListener(e,ev=>{ev.preventDefault();uploadArea.classList.add('dragover')}));")
        display.write("['dragleave','drop'].forEach(e=>uploadArea.addEventListener(e,ev=>{ev.preventDefault();uploadArea.classList.remove('dragover')}));")
        display.write("uploadArea.addEventListener('drop',async ev=>{const files=ev.dataTransfer.files; await uploadFiles(files)});")
        display.write("fileInput.addEventListener('change',async ev=>{await uploadFiles(ev.target.files)});")
        display.write("async function uploadFiles(files){if(!files||files.length===0)return;uploadMsg.textContent='Uploading...';for(const f of files){const form=new FormData();form.append('file',f,f.name);try{const res=await fetch(window.location.pathname,{method:'POST',body:form});if(!res.ok){uploadMsg.textContent='Upload failed';continue}}catch(err){uploadMsg.textContent='Upload failed';continue}}uploadMsg.textContent='Upload complete';setTimeout(()=>location.reload(),700)}")
        display.write("document.querySelectorAll('button.copy-link').forEach(btn=>{btn.addEventListener('click',async ()=>{const file=btn.getAttribute('data-file');if(!file){alert('No file to copy');return;}const url=new URL(file, window.location.href).href;try{if(navigator.clipboard && window.isSecureContext){await navigator.clipboard.writeText(url);}else{const textArea=document.createElement('textarea');textArea.value=url;textArea.style.position='fixed';textArea.style.left='-999999px';document.body.appendChild(textArea);textArea.select();try{document.execCommand('copy');}catch(e){console.error('Copy failed:',e);}document.body.removeChild(textArea);}btn.textContent='Copied';setTimeout(()=>btn.textContent='Copy link',1500)}catch(e){console.error('Copy failed:',e);alert('Failed to copy link. URL: '+url);}})});")
        display.write("selectAll.addEventListener('change',()=>{document.querySelectorAll('input.selectFile').forEach(cb=>cb.checked=selectAll.checked)});")
        display.write("downloadBtn.addEventListener('click',async ()=>{const checked=[...document.querySelectorAll('input.selectFile:checked')].map(i=>i.getAttribute('data-file'));if(checked.length===0){alert('No files selected');return}downloadBtn.disabled=true;const multipleFiles=checked.length>1;downloadBtn.textContent=multipleFiles?'Preparing ZIP...':'Preparing download...';try{const res=await fetch('/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({files:checked})});if(!res.ok){const txt=await res.text();alert('Download failed: '+txt);return}const blob=await res.blob();const filename=res.headers.get('content-disposition')?.split('filename=')[1]?.replace(/[\"\']/g,'')||`download${multipleFiles?'.zip':''}`; const url=window.URL.createObjectURL(new Blob([blob],{type:'application/octet-stream'}));const a=document.createElement('a');a.style.display='none';a.href=url;a.setAttribute('download',filename);a.setAttribute('target','_blank');document.body.appendChild(a);setTimeout(()=>{a.click();window.URL.revokeObjectURL(url);document.body.removeChild(a);},0);downloadBtn.textContent='Download started';setTimeout(()=>{downloadBtn.disabled=false;downloadBtn.textContent='Download selected';},1500);}catch(e){console.error('Download failed:',e);alert('Download failed: '+(e.message||e));}finally{if(downloadBtn.textContent!=='Download started'){downloadBtn.disabled=false;downloadBtn.textContent='Download selected'}}});")
        display.write("</script>")

        display.write('</main></body></html>')

        encoded = display.getvalue().encode('utf-8', 'surrogateescape')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
        return None
        

    def do_POST(self):
        if not self.check_auth():
            return
        # If client requests download of selected files, handle JSON POST to /download
        if self.path == '/download':
            try:
                length = int(self.headers.get('Content-Length') or 0)
                raw = self.rfile.read(length) if length else b''
                payload = json.loads(raw.decode('utf-8')) if raw else {}
                files = payload.get('files', [])
                if not files:
                    self.send_error(400, 'No files requested')
                    return
            except Exception as e:
                self.send_error(400, f'Bad request: {e}')
                return

            cwd = os.path.abspath(os.getcwd())
            valid_files = []
            
            # Validate all requested files first
            for fn in files:
                fn_unquoted = urllib.parse.unquote(fn)
                target = os.path.abspath(os.path.join(cwd, fn_unquoted))
                if target.startswith(cwd) and os.path.isfile(target):
                    valid_files.append((target, fn_unquoted))

            if not valid_files:
                self.send_error(404, 'No valid files to download')
                return

            # For single file, send it directly
            if len(valid_files) == 1:
                filepath, filename = valid_files[0]
                try:
                    file_size = os.path.getsize(filepath)
                    with open(filepath, 'rb') as f:
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/octet-stream')
                        self.send_header('Content-Disposition', f'attachment; filename="{os.path.basename(filename)}"')
                        self.send_header('Content-Length', str(file_size))
                        self.send_header('X-Content-Type-Options', 'nosniff')
                        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
                        self.end_headers()
                        # Send file in chunks to handle large files
                        while True:
                            chunk = f.read(64 * 1024)  # 64KB chunks
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                except Exception as e:
                    self.send_error(500, f'Failed to send file: {e}')
                return

            # For multiple files, create a ZIP
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                for filepath, filename in valid_files:
                    arcname = os.path.relpath(filepath, cwd)
                    zf.write(filepath, arcname)

            buf.seek(0)
            data = buf.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            date_str = datetime.datetime.now().strftime('%Y%m%d')
            self.send_header('Content-Disposition', f'attachment; filename="quickshare-{date_str}.zip"')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.end_headers()
            self.wfile.write(data)
            return
        # Safer upload handling: save to uploads/ directory, sanitize filename,
        # and enforce a maximum upload size to avoid resource exhaustion.
        content_type = self.headers.get('content-type')
        if not content_type:
            self.send_error(400, 'Missing Content-Type')
            return

        ctype, pdict = cgi.parse_header(content_type)
        if ctype != 'multipart/form-data':
            self.send_error(400, 'Bad request: expected multipart/form-data')
            return

        # Prepare upload directory
        UPLOAD_DIR = os.environ.get('QUICKSHARE_UPLOAD_DIR', 'uploads')
        MAX_UPLOAD_SIZE = int(os.environ.get('QUICKSHARE_MAX_UPLOAD_BYTES', str(200 * 1024 * 1024)))  # default 200MB
        os.makedirs(UPLOAD_DIR, exist_ok=True)

        # Parse form using cgi.FieldStorage but stream to disk in chunks
        environ = {
            'REQUEST_METHOD': 'POST',
            'CONTENT_TYPE': content_type,
            'CONTENT_LENGTH': self.headers.get('content-length')
        }

        form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ=environ, keep_blank_values=True)
        if 'file' not in form:
            self.send_error(400, 'No file field')
            return

        uploaded = form['file']
        raw_filename = uploaded.filename or ''
        filename = os.path.basename(raw_filename)
        # sanitize: allow letters, numbers, dot, dash, underscore
        def sanitize(name):
            name = name.strip()
            # remove leading dots to avoid hidden files like .env
            while name.startswith('.'):
                name = name[1:]
            # replace problematic characters
            safe = []
            for ch in name:
                if ch.isalnum() or ch in (' ', '.', '-', '_'):
                    safe.append(ch)
                else:
                    safe.append('_')
            out = ''.join(safe).strip()
            if not out:
                out = 'uploaded_file'
            return out

        filename = sanitize(filename)
        dest_path = os.path.join(UPLOAD_DIR, filename)

        # If file exists, add a suffix to avoid overwrite
        base, ext = os.path.splitext(dest_path)
        counter = 1
        while os.path.exists(dest_path):
            dest_path = f"{base}_{counter}{ext}"
            counter += 1

        # Write uploaded content to destination in chunks
        try:
            uploaded_fileobj = uploaded.file
            total = 0
            with open(dest_path, 'wb') as out:
                while True:
                    chunk = uploaded_fileobj.read(64 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    total += len(chunk)
                    if total > MAX_UPLOAD_SIZE:
                        out.close()
                        os.remove(dest_path)
                        self.send_error(413, 'Uploaded file is too large')
                        return
        except Exception as e:
            # cleanup partial file
            try:
                if os.path.exists(dest_path):
                    os.remove(dest_path)
            except Exception:
                pass
            self.send_error(500, f'Failed to save file: {e}')
            return

        # Success response
        self.send_response(200)
        self.end_headers()
        resp = f"<html><body><h3>✅ Uploaded: {html.escape(os.path.basename(dest_path))}</h3><a href='/'>Back</a></body></html>"
        self.wfile.write(resp.encode('utf-8'))


def main():
    parser = argparse.ArgumentParser(description="QuickSharePy — Instant File Sharing Over LAN (v2)")
    parser.add_argument("folder", nargs="?", default=".", help="Folder to share (default: current directory)")
    parser.add_argument("--port", type=int, default=8080, help="Port number (default: 8080)")
    parser.add_argument("--password", type=str, help="Optional access password")
    parser.add_argument("--qr", action="store_true", help="Show QR code for quick access")
    args = parser.parse_args()

    folder = os.path.abspath(args.folder)
    try:
        os.chdir(folder)
    except Exception as e:
        print(f"❌ Unable to change directory to {folder}: {e}")
        sys.exit(1)

    ip = get_local_ip()
    url = f"http://{ip}:{args.port}"

    # Display startup info
    if Fore:
        print(Fore.CYAN + f"\n🚀 QuickSharePy — File Sharing + Uploads\n" + Style.RESET_ALL)
        print(Fore.YELLOW + f"📂 Serving folder: {folder}")
        print(Fore.GREEN + f"🌍 Access at: {url}" + Style.RESET_ALL)
    else:
        print(f"\n🚀 QuickSharePy\nServing folder: {folder}\nAccess at: {url}\n")

    if args.password:
        UploadHTTPRequestHandler.password = args.password
        print("🔐 Password protection enabled (Bearer token).\n")

    if args.qr:
        if qrcode:
            qr = qrcode.QRCode()
            qr.add_data(url)
            qr.make()
            try:
                qr.print_ascii(invert=True)
            except Exception:
                # fall back to printing url if ascii printing isn't available
                print(url)
        else:
            print("⚠️ Install `qrcode` to enable QR code support (pip install qrcode)")

    try:
        # Use a threading TCP server to allow multiple simultaneous downloads
        with socketserver.ThreadingTCPServer(("", args.port), UploadHTTPRequestHandler) as httpd:
            httpd.allow_reuse_address = True
            print("\n✅ Server running. Press Ctrl+C to stop.")
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Server stopped by user.")
    except OSError as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Minimal Flask web UI for quickshare (MVP).

Features:
- List discovered peers (using DiscoveryManager briefly)
- Start/stop a receiver
- Package the `Portfolio/` folder into `artifacts/portfolio.tar.gz`
- Send the tarball to a selected peer

This is intentionally small — it runs locally and is for demo/test use.
"""
from flask import Flask, request, render_template_string, redirect, url_for, jsonify, send_file
from flask_socketio import SocketIO
from werkzeug.utils import secure_filename
import os
import threading
import time
import sqlite3
import queue
import atexit
import signal
from typing import Optional, Dict, Any
from quickshare.discovery import DiscoveryManager
from quickshare.control import ControlServer
from quickshare.discovery import DiscoveryAnnouncer
from quickshare.transfer import Sender
from quickshare.control import send_control_offer
from quickshare.fileutils import sha256_file
import base64
import io

app = Flask(__name__)

# Limit uploads to a sane default (200 MB) to avoid accidental OOMs; can be overridden via env
app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get('QUICKSHARE_MAX_UPLOAD_BYTES', 200 * 1024 * 1024))

# Upload folder and cleanup policy
UPLOAD_DIR = os.path.abspath('uploads')
UPLOAD_TTL_SECONDS = int(os.environ.get('QUICKSHARE_UPLOAD_TTL', 24 * 3600))
UPLOAD_CLEANUP_INTERVAL = int(os.environ.get('QUICKSHARE_UPLOAD_CLEANUP_INTERVAL', 60 * 60))

_upload_cleanup_stop = threading.Event()

def _upload_cleanup_worker():
  """Periodically delete files older than UPLOAD_TTL_SECONDS from UPLOAD_DIR."""
  try:
    # status trackers
    global UPLOAD_CLEANUP_LAST_RUN, UPLOAD_LAST_CLEANED_COUNT, UPLOAD_TOTAL_BYTES
    UPLOAD_CLEANUP_LAST_RUN = None
    UPLOAD_LAST_CLEANED_COUNT = 0
    UPLOAD_TOTAL_BYTES = 0
    while not _upload_cleanup_stop.is_set():
      cleaned = 0
      try:
        if os.path.isdir(UPLOAD_DIR):
          now = time.time()
          # compute initial total bytes
          total_bytes = 0
          for fn in os.listdir(UPLOAD_DIR):
            pth = os.path.join(UPLOAD_DIR, fn)
            try:
              if os.path.isfile(pth):
                total_bytes += os.path.getsize(pth)
            except Exception:
              pass
          # remove old files
          for fn in os.listdir(UPLOAD_DIR):
            path = os.path.join(UPLOAD_DIR, fn)
            try:
              if not os.path.isfile(path):
                continue
              mtime = os.path.getmtime(path)
              if now - mtime > UPLOAD_TTL_SECONDS:
                try:
                  os.remove(path)
                  cleaned += 1
                except Exception:
                  pass
            except Exception:
              pass
          # recompute total after cleanup
          total_after = 0
          for fn in os.listdir(UPLOAD_DIR):
            pth = os.path.join(UPLOAD_DIR, fn)
            try:
              if os.path.isfile(pth):
                total_after += os.path.getsize(pth)
            except Exception:
              pass
          UPLOAD_TOTAL_BYTES = total_after
      except Exception:
        pass
      UPLOAD_LAST_CLEANED_COUNT = cleaned
      UPLOAD_CLEANUP_LAST_RUN = time.time()
      _upload_cleanup_stop.wait(UPLOAD_CLEANUP_INTERVAL)
  except Exception:
    pass

# start cleanup thread
try:
  _upload_cleanup_thread = threading.Thread(target=_upload_cleanup_worker, daemon=True)
  _upload_cleanup_thread.start()
except Exception:
  _upload_cleanup_thread = None

# SocketIO for real-time push updates
socketio = SocketIO(app, cors_allowed_origins='*')

# UI configuration
AUTO_EXTRACT = True if os.environ.get('QUICKSHARE_AUTO_EXTRACT', '1') in ('1', 'true', 'True') else False
APP_ENV = os.environ.get('QUICKSHARE_ENV', 'DEV')  # 'DEV' or 'PROD'

RECV_STATE: Dict[str, Optional[Any]] = {'server': None, 'announcer': None}

# Progress persistence DB (simple SQLite)
DB_PATH = os.path.abspath('.quickshare_progress.db')


def init_db():
  conn = sqlite3.connect(DB_PATH)
  try:
    conn.execute(
      'CREATE TABLE IF NOT EXISTS transfers (tid TEXT PRIMARY KEY, bytes_sent INTEGER, total_bytes INTEGER, completed INTEGER, error TEXT, started_at TEXT, finished_at TEXT, sha256 TEXT, save_path TEXT)'
    )
    # Ensure columns exist for older DBs: add started_at/finished_at if missing
    cur = conn.execute("PRAGMA table_info('transfers')")
    cols = [r[1] for r in cur.fetchall()]
    if 'started_at' not in cols:
      try:
        conn.execute("ALTER TABLE transfers ADD COLUMN started_at TEXT")
      except Exception:
        pass
    if 'finished_at' not in cols:
      try:
        conn.execute("ALTER TABLE transfers ADD COLUMN finished_at TEXT")
      except Exception:
        pass
    if 'sha256' not in cols:
      try:
        conn.execute("ALTER TABLE transfers ADD COLUMN sha256 TEXT")
      except Exception:
        pass
    if 'save_path' not in cols:
      try:
        conn.execute("ALTER TABLE transfers ADD COLUMN save_path TEXT")
      except Exception:
        pass
    if 'extraction_path' not in cols:
      try:
        conn.execute("ALTER TABLE transfers ADD COLUMN extraction_path TEXT")
      except Exception:
        pass
    if 'extraction_ok' not in cols:
      try:
        conn.execute("ALTER TABLE transfers ADD COLUMN extraction_ok INTEGER")
      except Exception:
        pass
    # events table for persistent notification panel
    try:
      conn.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, tid TEXT, event TEXT, ts TEXT, meta TEXT)')
    except Exception:
      pass
    conn.commit()
  finally:
    conn.close()


init_db()

# Lightweight background queue for event persistence to avoid DB contention
EVENT_QUEUE = queue.Queue()

# in-memory cache of event counts per tid to reduce DB reads; updated by _event_worker
EVENT_COUNTS_CACHE = {}
EVENT_COUNTS_LOCK = threading.Lock()

def _event_worker():
  while True:
    item = EVENT_QUEUE.get()
    if item is None:
      break
    try:
      tid = item.get('tid')
      ev = item.get('event')
      ts = item.get('ts') or time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
      meta = item.get('meta') or ''
      conn = sqlite3.connect(DB_PATH)
      try:
        conn.execute('INSERT INTO events (tid, event, ts, meta) VALUES (?, ?, ?, ?)', (tid, ev, ts, meta))
        conn.commit()
      finally:
        conn.close()
      # update in-memory counts cache
      try:
        if tid:
          with EVENT_COUNTS_LOCK:
            EVENT_COUNTS_CACHE[tid] = EVENT_COUNTS_CACHE.get(tid, 0) + 1
      except Exception:
        pass
      try:
        socketio.emit('event', {'tid': tid, 'event': ev, 'ts': ts, 'meta': meta})
      except Exception:
        pass
    except Exception:
      pass
    finally:
      EVENT_QUEUE.task_done()

# start worker thread
_evt_thread = threading.Thread(target=_event_worker, daemon=True)
_evt_thread.start()

def enqueue_event(tid: str, event_name: str, meta: str = '', ts: Optional[str] = None):
  EVENT_QUEUE.put({'tid': tid, 'event': event_name, 'meta': meta, 'ts': ts})

# Transfer update queue: batch per-chunk DB writes to reduce SQLite contention
TRANSFER_QUEUE = queue.Queue()
# sentinel used to request worker shutdown and final flush
TRANSFER_SENTINEL = (None, None)

def _transfer_worker():
  agg = {}
  while True:
    try:
      try:
        item = TRANSFER_QUEUE.get(timeout=0.25)
      except Exception:
        item = None
      # timeout case: flush any aggregated deltas
      if item is None:
        if agg:
          conn = sqlite3.connect(DB_PATH)
          try:
            for tid, delta in list(agg.items()):
              try:
                conn.execute('UPDATE transfers SET bytes_sent = bytes_sent + ? WHERE tid = ?', (delta, tid))
              except Exception:
                pass
            conn.commit()
            agg.clear()
          finally:
            conn.close()
        continue
      # sentinel for graceful shutdown
      if item == TRANSFER_SENTINEL:
        # flush aggregated and exit
        if agg:
          conn = sqlite3.connect(DB_PATH)
          try:
            for tid, delta in list(agg.items()):
              try:
                conn.execute('UPDATE transfers SET bytes_sent = bytes_sent + ? WHERE tid = ?', (delta, tid))
              except Exception:
                pass
            conn.commit()
            agg.clear()
          finally:
            conn.close()
        break
      tid, delta = item
      agg[tid] = agg.get(tid, 0) + delta
      if agg[tid] >= 1024*1024:
        conn = sqlite3.connect(DB_PATH)
        try:
          try:
            conn.execute('UPDATE transfers SET bytes_sent = bytes_sent + ? WHERE tid = ?', (agg[tid], tid))
            conn.commit()
          except Exception:
            pass
        finally:
          conn.close()
        del agg[tid]
    except Exception:
      time.sleep(0.1)

_t_worker = threading.Thread(target=_transfer_worker, daemon=True)
_t_worker.start()

def enqueue_transfer_delta(tid: str, delta: int):
  TRANSFER_QUEUE.put((tid, delta))


# Graceful shutdown: flush queues and stop background threads
def _shutdown_workers():
  try:
    # request event worker to stop
    try:
      EVENT_QUEUE.put(None)
    except Exception:
      pass
    # request transfer worker to stop (and flush)
    try:
      TRANSFER_QUEUE.put(TRANSFER_SENTINEL)
    except Exception:
      pass
    # join threads with short timeout
    try:
      _evt_thread.join(timeout=2.0)
    except Exception:
      pass
    try:
      _t_worker.join(timeout=2.0)
    except Exception:
      pass
    # stop upload cleanup thread
    try:
      _upload_cleanup_stop.set()
    except Exception:
      pass
    try:
      if '_upload_cleanup_thread' in globals() and _upload_cleanup_thread is not None:
        _upload_cleanup_thread.join(timeout=2.0)
    except Exception:
      pass
  except Exception:
    pass


# register shutdown handlers
atexit.register(_shutdown_workers)
try:
  signal.signal(signal.SIGINT, lambda *_: _shutdown_workers())
  signal.signal(signal.SIGTERM, lambda *_: _shutdown_workers())
except Exception:
  # some environments may not allow signal registration
  pass

TEMPLATE = """
<!doctype html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>quickshare — LAN File Transfer</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
html { background: linear-gradient(135deg, #0f0c29, #302b63, #24243e); min-height: 100vh; }
body { 
  font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
  background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
  color: #e0e0e0;
  padding: 20px;
  min-height: 100vh;
}
.container { max-width: 1200px; margin: 0 auto; }
.header {
  text-align: center;
  margin-bottom: 40px;
  animation: fadeInDown 0.8s ease-out;
}
.header h1 {
  font-size: 3.5em;
  background: linear-gradient(135deg, #00f5ff, #ff006e);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  margin-bottom: 10px;
  font-weight: 900;
  letter-spacing: 2px;
  text-shadow: 0 0 30px rgba(0, 245, 255, 0.3);
}
.header p { font-size: 1.1em; color: #aaa; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 20px; margin-bottom: 30px; }
.card {
  background: rgba(255, 255, 255, 0.05);
  backdrop-filter: blur(10px);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 15px;
  padding: 25px;
  animation: slideUp 0.6s ease-out;
  transition: all 0.3s ease;
  box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.2);
}
.card:hover {
  background: rgba(255, 255, 255, 0.08);
  border-color: rgba(0, 245, 255, 0.3);
  box-shadow: 0 8px 32px 0 rgba(0, 245, 255, 0.2);
  transform: translateY(-5px);
}
.card h2 {
  font-size: 1.4em;
  margin-bottom: 15px;
  background: linear-gradient(135deg, #00f5ff, #ff006e);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}
.form-group { margin-bottom: 15px; }
.form-group label { display: block; font-size: 0.9em; margin-bottom: 8px; color: #aaa; }
.form-group input,
.form-group select {
  width: 100%;
  padding: 10px 15px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 8px;
  color: #e0e0e0;
  font-size: 0.95em;
  transition: all 0.3s ease;
}
.form-group input:focus,
.form-group select:focus {
  outline: none;
  background: rgba(255, 255, 255, 0.12);
  border-color: rgba(0, 245, 255, 0.5);
  box-shadow: 0 0 10px rgba(0, 245, 255, 0.2);
}
.btn {
  background: linear-gradient(135deg, #00f5ff, #0099ff);
  color: #000;
  border: none;
  padding: 12px 25px;
  border-radius: 8px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.3s ease;
  font-size: 0.95em;
  box-shadow: 0 4px 15px rgba(0, 245, 255, 0.3);
}
.btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(0, 245, 255, 0.5);
}
.btn:active { transform: translateY(0); }
.btn-danger {
  background: linear-gradient(135deg, #ff006e, #ff4d94);
  box-shadow: 0 4px 15px rgba(255, 0, 110, 0.3);
}
.btn-danger:hover {
  box-shadow: 0 6px 20px rgba(255, 0, 110, 0.5);
}
.status-badge {
  display: inline-block;
  padding: 8px 16px;
  border-radius: 20px;
  font-size: 0.85em;
  font-weight: 600;
  margin: 5px 0;
  background: rgba(0, 245, 255, 0.15);
  border: 1px solid rgba(0, 245, 255, 0.3);
  color: #00f5ff;
}
.status-badge.off {
  background: rgba(255, 100, 100, 0.15);
  border-color: rgba(255, 100, 100, 0.3);
  color: #ff6464;
}
.progress-container {
  margin-top: 20px;
  padding: 15px;
  background: rgba(0, 0, 0, 0.3);
  border-radius: 10px;
  display: none;
}
.progress-container.active { display: block; }
.progress-bar {
  width: 100%;
  height: 8px;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 4px;
  overflow: hidden;
  margin: 10px 0;
}
.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #00f5ff, #ff006e);
  width: 0%;
  transition: width 0.3s ease;
  box-shadow: 0 0 10px rgba(0, 245, 255, 0.5);
}
.progress-text { font-size: 0.85em; color: #aaa; margin-top: 5px; }
.status-info {
  padding: 15px;
  background: rgba(0, 245, 255, 0.08);
  border-left: 3px solid rgba(0, 245, 255, 0.5);
  border-radius: 5px;
  margin: 10px 0;
  font-size: 0.9em;
}
.link-area { text-align: center; margin-top: 30px; }
.link-area a {
  color: #00f5ff;
  text-decoration: none;
  font-weight: 500;
  transition: all 0.3s ease;
  margin: 0 15px;
}
.link-area a:hover { color: #ff006e; text-decoration: underline; }
.file-input-wrapper {
  position: relative;
  overflow: hidden;
  display: inline-block;
  width: 100%;
}
.file-input-wrapper input[type="file"] {
  display: none;
}
.file-input-label {
  display: block;
  padding: 10px 15px;
  background: rgba(255, 255, 255, 0.08);
  border: 2px dashed rgba(0, 245, 255, 0.4);
  border-radius: 8px;
  color: #e0e0e0;
  text-align: center;
  cursor: pointer;
  transition: all 0.3s ease;
  font-size: 0.95em;
}
.file-input-label:hover {
  background: rgba(255, 255, 255, 0.12);
  border-color: rgba(0, 245, 255, 0.7);
}
.file-input-label.drag-over {
  background: rgba(0, 245, 255, 0.15);
  border-color: rgba(0, 245, 255, 0.8);
  box-shadow: 0 0 10px rgba(0, 245, 255, 0.3);
}
.file-name {
  display: block;
  margin-top: 8px;
  font-size: 0.85em;
  color: #00f5ff;
  word-break: break-all;
}

@keyframes fadeInDown {
  from { opacity: 0; transform: translateY(-20px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes slideUp {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.7; }
}
.pulse { animation: pulse 2s infinite; }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>⚡ quickshare</h1>
    <p>Ultra-fast LAN file transfer</p>
  </div>

  <div class="grid">
    <!-- Receiver Card -->
    <div class="card">
      <h2>📡 Receiver</h2>
      {% if recv_running %}
        <div class="status-badge">Running on port {{ recv_port }}</div>
        <p style="margin-top: 15px; font-size: 0.9em; color: #aaa;">Listening for incoming transfers...</p>
        <div style="text-align: center; margin: 20px 0;">
          <div style="font-size: 0.85em; color: #888; margin-bottom: 10px;">Share this QR code with sender:</div>
          <img id="qrcode-img" src="/qrcode?addr={{ recv_addr }}&port={{ recv_port }}" alt="QR Code" style="width: 150px; height: 150px; border-radius: 10px; background: white; padding: 5px;">
        </div>
        <div style="text-align: center; font-size: 0.8em; color: #999; margin: 10px 0;">
          <code style="background: rgba(0,0,0,0.3); padding: 8px 12px; border-radius: 5px; display: inline-block;">{{ recv_addr }}:{{ recv_port }}</code>
          <button onclick="navigator.clipboard.writeText('{{ recv_addr }}:{{ recv_port }}'); _makeToast('Address copied', 'success', 2000);" class="btn" style="padding: 6px 12px; font-size: 0.8em; margin-left: 8px;">Copy</button>
        </div>
        <form method="post" action="/stop-recv" style="margin-top: 15px;">
          <button class="btn btn-danger" type="submit">Stop Receiver</button>
        </form>
      {% else %}
        <div class="status-badge off">Offline</div>
        <form method="post" action="/start-recv" style="margin-top: 15px;">
          <div class="form-group">
            <label>Bind Address</label>
            <input type="text" name="bind" value="0.0.0.0">
          </div>
          <div class="form-group">
            <label>Port</label>
            <input type="number" name="port" value="60000">
          </div>
          <button class="btn" type="submit">Start Receiver</button>
        </form>
      {% endif %}
    </div>

    <!-- Package Card -->
    <div class="card">
      <h2>📦 Package</h2>
      <p style="font-size: 0.9em; color: #aaa; margin-bottom: 15px;">Create a tarball of a folder to send</p>
      <form method="post" action="/package" id="package-form">
        <div class="form-group">
          <label>Source Folder</label>
          <div class="file-input-wrapper">
            <input type="file" name="src" id="package-input" accept=".*" webkitdirectory mozdirectory msdirectory odirectory directory multiple>
            <label for="package-input" class="file-input-label">
              <span id="package-label-text">📁 Click to select or drag folder</span>
            </label>
            <span id="package-name" class="file-name"></span>
          </div>
          <input type="hidden" name="src" id="package-src-hidden" value="">
        </div>
        <button class="btn" type="submit" id="package-btn" disabled>Package to TAR.GZ</button>
      </form>
    </div>

    <!-- Peers & Send Card -->
    <div class="card">
      <h2>🚀 Send</h2>
      <form method="post" action="/send" id="send-form">
        <div class="form-group">
          <label>Target Peer</label>
          <select name="peer">
            {% if peers %}
              {% for k, v in peers.items() %}
                <option value="{{ v['addr'] }}:{{ v['port'] }}">{{ v['name'] }} @ {{ v['addr'] }}:{{ v['port'] }}</option>
              {% endfor %}
            {% else %}
              <option disabled>No peers discovered</option>
            {% endif %}
          </select>
        </div>
        <div class="form-group">
          <label>File to Send</label>
          <div class="file-input-wrapper">
            <input type="file" name="file" id="send-input" accept=".tar.gz,.tar,.zip,.gz">
            <label for="send-input" class="file-input-label">
              <span id="send-label-text">📄 Click to select or drag file</span>
              <small id="send-max-size" style="color:#bbb; margin-left:8px; font-size:0.9em;">(max: {{ max_upload_human }})</small>
            </label>
            <span id="send-name" class="file-name"></span>
          </div>
          <input type="hidden" name="file" id="send-file-hidden" value="">
          <!-- Upload progress (for browser -> server upload) -->
          <div id="upload-progress-container" class="progress-container" style="display:none; margin-top:10px;">
            <div style="font-weight:600; margin-bottom:8px;">Uploading to server...</div>
            <div class="progress-bar">
              <div class="progress-fill" id="upload-progress-fill" style="width:0%"></div>
            </div>
            <div class="progress-text"><span id="upload-progress-pct">0</span>%</div>
          </div>
        </div>
        <button class="btn" type="submit" id="send-btn" disabled>Send File</button>
        <div id="progress-container" class="progress-container">
          <div style="font-weight: 600; margin-bottom: 10px;">Transfer in progress...</div>
          <div class="progress-bar">
            <div class="progress-fill" id="progress-fill"></div>
          </div>
          <div class="progress-text">
            <span id="progress-pct">0</span>% — <span id="progress-bytes">0</span>/<span id="progress-total">0</span> bytes
              <div style="margin-top:8px;font-size:0.9em;color:#bbb">Speed: <span id="progress-speed">-</span> — ETA: <span id="progress-eta">-</span></div>
          </div>
        </div>
      </form>
    </div>
  </div>

  <div class="status-info" style="margin-bottom: 20px;">
    <strong>Status:</strong> {{ status }}
  </div>

  <div class="link-area">
    <a href="/dashboard">📊 Dashboard</a>
    <a href="/history">History</a>
    <a href="/" style="cursor: pointer; color: #aaa;">🔄 Refresh</a>
  </div>
</div>

<script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
<!-- Toast container -->
<style>
/* Simple bottom-right toast notifications */
.qs-toast-container { position: fixed; right: 20px; bottom: 20px; z-index: 9999; display: flex; flex-direction: column; gap: 10px; }
.qs-toast { min-width: 240px; max-width: 360px; background: rgba(30,30,30,0.95); color: #fff; padding: 12px 14px; border-radius: 8px; box-shadow: 0 6px 20px rgba(0,0,0,0.5); font-size: 0.95em; display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.qs-toast.success { border-left: 4px solid #27ae60; }
.qs-toast.info { border-left: 4px solid #3498db; }
.qs-toast.warn { border-left: 4px solid #f39c12; }
.qs-toast .close { cursor: pointer; opacity: 0.8; margin-left: 8px; }
</style>
<div id="qs-toast-root" class="qs-toast-container" aria-live="polite" aria-atomic="true"></div>
<script>
// Max upload size in bytes (server-provided)
const MAX_UPLOAD_BYTES = {{ max_upload_bytes|default(0) }};

function qsGet(name){
  const url = new URL(window.location.href);
  return url.searchParams.get(name);
}

// Socket.IO real-time updates
const socket = io();
socket.on('connect', ()=>{ console.debug('socket connected'); });

// Toast helpers
function _makeToast(msg, cls='info', ttl=5000){
  try{
    const root = document.getElementById('qs-toast-root');
    if(!root) return;
    const el = document.createElement('div');
    el.className = 'qs-toast ' + cls;
    el.innerHTML = `<div style="flex:1">${msg}</div><div class="close">✖</div>`;
    const closeBtn = el.querySelector('.close');
    closeBtn.addEventListener('click', ()=>{ if(el && el.parentNode) el.parentNode.removeChild(el); });
    root.appendChild(el);
    setTimeout(()=>{ try{ if(el && el.parentNode) el.parentNode.removeChild(el); }catch(e){} }, ttl);
  }catch(e){ console.debug('toast error', e); }
}

// File picker handlers
function setupFilePicker(inputId, labelId, nameId, hiddenId, btnId) {
  const input = document.getElementById(inputId);
  const label = document.getElementById(labelId);
  const nameEl = document.getElementById(nameId);
  const hidden = document.getElementById(hiddenId);
  const btn = document.getElementById(btnId);
  
  if(!input || !label) return;
  
  // When file is selected
  input.addEventListener('change', (e) => {
    if(e.target.files && e.target.files.length > 0) {
      const files = Array.from(e.target.files);
      const fileName = files.map(f => f.name).join(', ');
      if(nameEl) nameEl.textContent = fileName.length > 50 ? fileName.substring(0, 47) + '...' : fileName;
      // For the send-input we need to upload the file to the server first because
      // browsers do not reveal the client's filesystem path to the server.
      if(inputId === 'send-input'){
        if(btn) btn.disabled = true;
        if(hidden && files.length > 0){
          // upload the selected file and set hidden input to the server path
          uploadFile(files[0], hidden, btn, nameEl);
        }
      } else {
        // Non-send inputs (e.g., package folder selection) — we only show the name
        if(btn) btn.disabled = false;
        if(hidden && files.length > 0) {
          hidden.value = files[0].name;
        }
      }
    }
  });
  
  // Drag and drop
  label.addEventListener('dragover', (e) => {
    e.preventDefault();
    label.classList.add('drag-over');
  });
  
  label.addEventListener('dragleave', () => {
    label.classList.remove('drag-over');
  });
  
  label.addEventListener('drop', (e) => {
    e.preventDefault();
    label.classList.remove('drag-over');
    input.files = e.dataTransfer.files;
    const event = new Event('change', { bubbles: true });
    input.dispatchEvent(event);
  });
  
  label.addEventListener('click', () => input.click());
}

// Upload helper: uploads file via /upload and writes returned server path into hidden input
// Upload helper: uploads file via /upload (XHR) and writes returned server path into hidden input
function uploadFile(file, hiddenEl, btn, nameEl){
  try{
    const upContainer = document.getElementById('upload-progress-container');
    const upFill = document.getElementById('upload-progress-fill');
    const upPct = document.getElementById('upload-progress-pct');
    if(upContainer) upContainer.style.display = 'block';
    if(upFill) upFill.style.width = '0%';
    if(upPct) upPct.textContent = '0';
    const xhr = new XMLHttpRequest();
    const fd = new FormData();
    fd.append('file', file, file.name);
    xhr.open('POST', '/upload', true);
    xhr.upload.onprogress = function(e){
      try{
        if(e.lengthComputable){
          const pct = Math.round((e.loaded / e.total) * 100);
          if(upFill) upFill.style.width = pct + '%';
          if(upPct) upPct.textContent = pct.toString();
        }
      }catch(err){ console.debug('upload progress err', err); }
    };
    xhr.onload = function(){
      try{
        if(upContainer) setTimeout(()=>{ upContainer.style.display = 'none'; }, 800);
        if(xhr.status >= 200 && xhr.status < 300){
          let js = {};
          try{ js = JSON.parse(xhr.responseText || '{}'); }catch(e){ js = {}; }
          if(js && js.path){
            if(hiddenEl) hiddenEl.value = js.path;
            if(btn) btn.disabled = false;
            _makeToast('File uploaded to server', 'success', 2500);
            return;
          }
        }
        _makeToast('Upload failed', 'warn', 4000);
        if(btn) btn.disabled = false;
      }catch(e){ console.debug('upload onload err', e); _makeToast('Upload failed', 'warn', 4000); if(btn) btn.disabled = false; }
    };
    xhr.onerror = function(){
      try{ if(upContainer) upContainer.style.display = 'none'; }catch(e){}
      _makeToast('Upload failed', 'warn', 4000);
      if(btn) btn.disabled = false;
    };
    xhr.send(fd);
  }catch(e){ console.debug('uploadFile error', e); if(btn) btn.disabled = false; }
}

// Initialize file pickers on page load
window.addEventListener('load', () => {
  setupFilePicker('package-input', 'package-label-text', 'package-name', 'package-src-hidden', 'package-btn');
  setupFilePicker('send-input', 'send-label-text', 'send-name', 'send-file-hidden', 'send-btn');
});

// socket events -> toasts
socket.on('receiver_started', (d)=>{ _makeToast(`Incoming: ${d.filename} (${d.size || 'unknown'} bytes)`, 'info', 6000); });
socket.on('receiver_completed', (d)=>{ _makeToast(`Received: ${d.save_path || d.tid} — sha256 ${d.sha256 ? d.sha256.slice(0,12) : 'unknown'}`, 'success', 8000); });
socket.on('receiver_extracted', (d)=>{ _makeToast(d.ok ? `Extracted to ${d.extracted_to}` : `Extraction failed for ${d.tid}`, d.ok ? 'success' : 'warn', 7000); });

function showProgressContainer(){
  const container = document.getElementById('progress-container');
  if(container) container.classList.add('active');
}

function hideProgressContainer(){
  const container = document.getElementById('progress-container');
  if(container) container.classList.remove('active');
}

socket.on('progress', (data)=>{
  try{
    const currentTid = qsGet('tid');
    if(!currentTid || data.tid !== currentTid) return;
    showProgressContainer();
    const fill = document.getElementById('progress-fill');
    const pct = document.getElementById('progress-pct');
    const bytes = document.getElementById('progress-bytes');
    const total = document.getElementById('progress-total');
    const totalBytes = data.total_bytes || 0;
    const sent = data.bytes_sent || 0;
    const percent = totalBytes > 0 ? Math.min(100, (100 * sent / totalBytes)) : 0;
    if(fill) fill.style.width = percent + '%';
    if(pct) pct.textContent = percent.toFixed(1);
    if(bytes) bytes.textContent = (sent/1024/1024).toFixed(2) + ' MB';
    if(total) total.textContent = (totalBytes/1024/1024).toFixed(2) + ' MB';
    if(data.completed){
      if(fill) fill.style.width = '100%';
      if(pct) pct.textContent = '100';
      setTimeout(()=>{ hideProgressContainer(); }, 2000);
    }
  }catch(e){ console.debug('progress handler error', e); }
});

// format bytes/sec to human readable
function hrBps(bps){
  if(!bps || bps <= 0) return '-';
  if(bps > 1024*1024) return (bps/1024/1024).toFixed(2) + ' MB/s';
  if(bps > 1024) return (bps/1024).toFixed(1) + ' KB/s';
  return bps.toFixed(0) + ' B/s';
}

// update speed/eta if present
socket.on('progress', (data)=>{
  try{
    const currentTid = qsGet('tid');
    if(!currentTid || data.tid !== currentTid) return;
    const speedEl = document.getElementById('progress-speed');
    const etaEl = document.getElementById('progress-eta');
    if(speedEl){ speedEl.textContent = data.bps ? hrBps(data.bps) : '-'; }
    if(etaEl){
      if(data.bps && data.total_bytes && data.bytes_sent < data.total_bytes){
        const rem = data.total_bytes - data.bytes_sent;
        const secs = Math.max(0, Math.round(rem / data.bps));
        const m = Math.floor(secs/60); const s = secs%60;
        etaEl.textContent = m>0? `${m}m ${s}s` : `${s}s`;
      } else if(data.completed){ etaEl.textContent = 'done'; } else { etaEl.textContent = '-'; }
    }
  }catch(e){ console.debug('progress speed handler error', e); }
});

window.addEventListener('load', ()=>{
  const tid = qsGet('tid');
  if(tid) showProgressContainer();
});
</script>
</body>
</html>
"""


DASHBOARD_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>quickshare — Dashboard</title>
  <style>
    body{font-family:Segoe UI, Tahoma, Geneva, Verdana, sans-serif;background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:#e0e0e0;padding:20px}
    .container{max-width:1100px;margin:0 auto}
    h1{background:linear-gradient(135deg,#00f5ff,#ff006e);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
    table{width:100%;border-collapse:collapse;margin-top:18px}
    th,td{padding:8px;border-bottom:1px solid rgba(255,255,255,0.06);text-align:left;font-size:0.95em}
    .progress-bar{background:rgba(255,255,255,0.06);height:12px;border-radius:6px;overflow:hidden}
    .progress-fill{height:100%;background:linear-gradient(90deg,#00f5ff,#ff006e);width:0}
    .badge{padding:4px 8px;border-radius:6px;font-weight:600}
    .badge.ok{background:rgba(39,174,96,0.12);color:#27ae60}
    .badge.bad{background:rgba(224,64,64,0.12);color:#ff6464}
    .row-new{box-shadow:0 6px 18px rgba(0,245,255,0.08);transition:background 0.6s}
    .controls{margin-top:12px}
    #qs-toast-root{position:fixed;right:20px;bottom:20px;z-index:9999;display:flex;flex-direction:column;gap:10px}
  </style>
</head>
<body>
  <div class="container">
    <h1>📊 QuickShare Dashboard</h1>
    <p>Live transfers, receiver status, peers and history (real-time).</p>

    <div class="controls">
      <a href="/">Home</a> • <a href="/history">History</a>
    </div>

    <!-- Persistent Notifications Panel -->
    <div style="margin-top:18px; margin-bottom:8px;">
      <button id="toggle-events" class="btn" style="padding:8px 12px;font-size:0.9em">Toggle Notifications</button>
    </div>
    <div id="events-panel" style="display:none; background:rgba(255,255,255,0.03); padding:12px; border-radius:8px; margin-bottom:12px; max-height:320px; overflow:auto">
      <h3 style="margin-top:0;font-size:1.05em">Recent Events</h3>
      <div style="display:flex;gap:8px;margin-bottom:8px;align-items:center">
        <input id="filter-tid" placeholder="Filter by tid" style="flex:1;padding:8px;border-radius:6px;border:1px solid rgba(255,255,255,0.06);background:transparent;color:#e0e0e0">
        <input id="filter-event" placeholder="Filter by event" style="width:160px;padding:8px;border-radius:6px;border:1px solid rgba(255,255,255,0.06);background:transparent;color:#e0e0e0">
        <button id="apply-filters" class="btn" style="padding:8px 10px">Apply</button>
      </div>
      <ul id="events-list" style="list-style:none;padding-left:0;margin:0;display:flex;flex-direction:column;gap:8px"></ul>
    </div>

    <table id="dashboard-table">
      <thead>
        <tr><th>tid / filename</th><th>progress</th><th>bytes</th><th>speed</th><th>eta</th><th>sha256</th><th>extraction</th><th>started</th></tr>
      </thead>
      <tbody id="dashboard-body">
        {% for it in items %}
        <tr id="row-{{ it.tid }}">
          <td style="max-width:260px;overflow:hidden">{{ it.tid }}<div style="font-size:0.85em;color:#aaa">{{ it.save_path or '' }}</div></td>
          <td style="width:35%">
            <div class="progress-bar"><div class="progress-fill" style="width:{{ (it.bytes_sent / it.total_bytes * 100) if it.total_bytes else 0 }}%"></div></div>
          </td>
          <td>{{ it.bytes_sent or 0 }} / {{ it.total_bytes or 0 }}</td>
          <td style="font-family:monospace">{{ (it.sha256[:12] if it.sha256) or '' }}</td>
          <td>{% if it.extraction_ok == 1 %}<span class="badge ok">extracted</span>{% elif it.extraction_ok == 0 and it.extraction_path %}<span class="badge bad">failed</span>{% else %}-{% endif %}</td>
          <td>{{ it.started_at or '' }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>

    <div id="qs-toast-root" aria-live="polite" aria-atomic="true"></div>
  </div>

  <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
  <script>
    const socket = io();
    const rows = {};
    function mkRow(it){
      const tr = document.createElement('tr');
      tr.id = 'row-'+it.tid;
      tr.innerHTML = `<td style="max-width:260px;overflow:hidden">`+
                     `<div style="display:flex;justify-content:space-between;align-items:center"><div>${it.tid}</div><div class=\"events-badge\" style=\"background:rgba(255,255,255,0.06);color:#e0e0e0;padding:4px 8px;border-radius:12px;font-size:0.8em;margin-left:8px\">0</div></div>`+
                     `<div style=\"font-size:0.85em;color:#aaa\">${it.save_path||''}</div></td>`+
                     `<td style="width:35%"><div class="progress-bar"><div class="progress-fill" style="width:${it.total_bytes? (it.bytes_sent/it.total_bytes*100):0}%"></div></div></td>`+
                     `<td>${it.bytes_sent||0} / ${it.total_bytes||0}</td>`+
                     `<td class="speed-cell">-</td>`+
                     `<td class="eta-cell">-</td>`+
                     `<td style="font-family:monospace">${it.sha256?it.sha256.slice(0,12):''}</td>`+
                     `<td>${(it.extraction_ok==1)?'<span class="badge ok">extracted</span>':(it.extraction_ok==0 && it.extraction_path)?'<span class="badge bad">failed</span>':'-'}</td>`+
                     `<td>${it.started_at||''}</td>`;
      return tr;
    }

    // populate existing rows
    const initial = {{ items | tojson }};
    const tbody = document.getElementById('dashboard-body');
    const initialTids = [];
    initial.forEach(it=>{ const r = mkRow(it); rows[it.tid]=r; tbody.appendChild(r); initialTids.push(it.tid); });
    // fetch event counts for initial rows in batch
    if(initialTids.length){
      fetch('/events/counts?tids='+encodeURIComponent(initialTids.join(','))).then(r=>r.json()).then(js=>{
        try{ Object.keys(js).forEach(tid=>{ const el = document.querySelector('#row-'+tid+' .events-badge'); if(el) el.textContent = js[tid]; }); }catch(e){}
      }).catch(e=>console.debug('counts fetch failed', e));
    }

    // toast helper (same as home)
    function _makeToast(msg, cls='info', ttl=5000){ try{ const root=document.getElementById('qs-toast-root'); if(!root) return; const el=document.createElement('div'); el.className='qs-toast '+cls; el.innerHTML=`<div style="flex:1">${msg}</div><div class="close">✖</div>`; const cb=el.querySelector('.close'); cb.addEventListener('click',()=>{el.parentNode&&el.parentNode.removeChild(el)}); root.appendChild(el); setTimeout(()=>{ try{ el.parentNode&&el.parentNode.removeChild(el);}catch(e){} },ttl);}catch(e){console.debug(e);} }

    socket.on('progress', d=>{
      const tid = d.tid; let row = rows[tid];
      if(!row){
        // create placeholder row
        const it = {tid:tid, bytes_sent:d.bytes_sent, total_bytes:d.total_bytes, sha256:null, save_path:null, extraction_ok:null, extraction_path:null, started_at:null};
        row = mkRow(it); rows[tid]=row; tbody.prepend(row);
        // fetch count for this new tid
        try{ fetch('/events/counts?tids='+encodeURIComponent(tid)).then(r=>r.json()).then(js=>{ try{ const el = document.querySelector('#row-'+tid+' .events-badge'); if(el) el.textContent = js[tid]||0; }catch(e){} }).catch(e=>{}); }catch(e){}
      }
      // update progress
      const fill = row.querySelector('.progress-fill');
      if(fill && d.total_bytes) fill.style.width = Math.min(100, (d.bytes_sent/d.total_bytes*100)) + '%';
      const cells = row.querySelectorAll('td'); if(cells[2]) cells[2].textContent = `${d.bytes_sent || 0} / ${d.total_bytes || 0}`;
      // update speed & ETA
      try{
        const sp = cells[3]; const et = cells[4];
        if(sp) sp.textContent = d.bps? (d.bps>1024*1024? (d.bps/1024/1024).toFixed(2)+' MB/s' : (d.bps>1024? (d.bps/1024).toFixed(1)+' KB/s' : d.bps+' B/s')) : '-';
        if(et){
          if(d.bps && d.total_bytes && d.bytes_sent < d.total_bytes){
            const rem = d.total_bytes - d.bytes_sent; const secs = Math.max(0, Math.round(rem / d.bps)); const m = Math.floor(secs/60); const s = secs%60; et.textContent = m>0? `${m}m ${s}s` : `${s}s`;
          } else if(d.completed) { et.textContent = 'done'; } else { et.textContent = '-'; }
        }
      }catch(e){ console.debug('dashboard speed update error', e); }
    });

    // append to events panel
    const MAX_EVENTS = 200;

    function renderEventRow(ev){
      const li = document.createElement('li');
      li.style.padding = '8px';
      li.style.background = 'rgba(255,255,255,0.02)';
      li.style.borderRadius = '6px';
      li.style.fontSize = '0.95em';
      // try to parse JSON meta if present
      let metaStr = ev.meta || '';
      let metaObj = null;
      try{ metaObj = JSON.parse(metaStr); }catch(e){ metaObj = null; }
      const ts = ev.ts || new Date().toISOString();
      const tid = ev.tid || '';
      const evt = ev.event || '';
      const summary = metaObj ? (metaObj.filename || metaObj.save_path || metaStr) : metaStr;
      // build actions: go-to-transfer, extract (if save_path present)
      const actions = document.createElement('div');
      actions.style.marginTop = '6px';
      actions.style.display = 'flex';
      actions.style.gap = '8px';

      const goto = document.createElement('button');
      goto.textContent = 'Open transfer';
      goto.className = 'btn';
      goto.style.padding = '6px 8px';
      goto.style.fontSize = '0.85em';
      goto.addEventListener('click', ()=>{ scrollToRow(tid); });
      actions.appendChild(goto);

      if(metaObj && metaObj.save_path){
        const ext = document.createElement('button');
        ext.textContent = 'Extract';
        ext.className = 'btn';
        ext.style.padding = '6px 8px';
        ext.style.fontSize = '0.85em';
        ext.addEventListener('click', ()=>{ doExtract(metaObj.save_path, li); });
        actions.appendChild(ext);
      }

      li.innerHTML = `<div style="font-weight:600">${ts} — ${evt} ${tid?('- '+tid):''}</div><div style="color:#bbb;margin-top:4px">${summary||''}</div>`;
      // attach event id to avoid duplicates
      if(ev && ev.id){ try{ li.dataset.eid = ev.id; }catch(e){} }
      li.appendChild(actions);
      return li;
    }

    function addEvent(ev){
      try{
        const list = document.getElementById('events-list');
        if(!list) return;
        if(ev && ev.id){
          // skip if already rendered
          if(document.querySelector('#events-list li[data-eid="'+ev.id+'"]')) return;
        }
        const li = renderEventRow(ev);
        list.prepend(li);
        // cap list length
        while(list.children.length > MAX_EVENTS){ list.removeChild(list.lastChild); }
      }catch(e){console.debug('addEvent error', e);}    
    }

    function addEventAppend(ev){
      try{
        const list = document.getElementById('events-list');
        if(!list) return;
        if(ev && ev.id){
          if(document.querySelector('#events-list li[data-eid="'+ev.id+'"]')) return;
        }
        const li = renderEventRow(ev);
        list.appendChild(li);
        // cap list length (remove from top if too many)
        while(list.children.length > MAX_EVENTS){ list.removeChild(list.firstChild); }
      }catch(e){console.debug('addEventAppend error', e);}    
    }

    function scrollToRow(tid){
      if(!tid) return;
      const el = document.getElementById('row-'+tid);
      if(el){ el.scrollIntoView({behavior:'smooth', block:'center'}); el.style.boxShadow='0 6px 18px rgba(0,245,255,0.08)'; setTimeout(()=>el.style.boxShadow='',1600); }
      else{
        // fallback: open history page filtered by tid
        window.location.href = '/history';
      }
    }

    function doExtract(path, hostEl){
      try{
        // find the Extract button inside the host element (li) and disable it
        let btn = null;
        try{ btn = hostEl.querySelector('button'); }catch(e){ btn = null; }
        if(btn){ btn.disabled = true; btn.textContent = 'Extracting...'; }
        // add a small status element
        let status = hostEl.querySelector('.ev-status');
        if(!status){ status = document.createElement('div'); status.className = 'ev-status'; status.style.marginTop='6px'; status.style.color='#9bd'; hostEl.appendChild(status); }
        status.textContent = 'Extracting...';
        // register pending extract so socket handler can re-enable and update
        try{ window._pendingExtracts = window._pendingExtracts || {}; window._pendingExtracts[path] = { el: hostEl, btn: btn }; }catch(e){}
        fetch('/extract', {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body:`path=${encodeURIComponent(path)}`})
          .then(r=>r.json())
          .then(js=>{
            // rely on server-emitted socket 'receiver_extracted' to update UI; still show immediate toast on error
            if(js && js.error){
              _makeToast('Extract failed: '+js.error,'warn',6000);
              status.textContent = 'Extract failed: '+js.error;
              addEvent({tid: null, event: 'extract_failed', ts: new Date().toISOString(), meta: JSON.stringify({error: js.error})});
              // cleanup pending
              try{ if(window._pendingExtracts && window._pendingExtracts[path]){ const pending = window._pendingExtracts[path]; if(pending && pending.btn){ pending.btn.disabled = false; pending.btn.textContent = 'Extract'; } delete window._pendingExtracts[path]; } }catch(e){}
            }
          })
          .catch(e=>{ _makeToast('Extract request failed','warn',4000); status.textContent = 'Request failed'; addEvent({tid: null, event: 'extract_failed', ts: new Date().toISOString(), meta: JSON.stringify({error: String(e)})}); try{ if(window._pendingExtracts && window._pendingExtracts[path]){ const pending = window._pendingExtracts[path]; if(pending && pending.btn){ pending.btn.disabled = false; pending.btn.textContent = 'Extract'; } delete window._pendingExtracts[path]; } }catch(ee){} });
      }catch(e){ console.debug('doExtract error', e); }
    }

    // paginated fetch for initial events with "Load more"
    let EV_LIMIT = 50;
    let EV_OFFSET = 0;
    const loadMoreBtn = document.createElement('button');
    loadMoreBtn.textContent = 'Load more';
    loadMoreBtn.className = 'btn';
    loadMoreBtn.style.marginTop = '8px';
    loadMoreBtn.addEventListener('click', ()=>{ loadEvents(); });
    const eventsPanel = document.getElementById('events-panel');
    if(eventsPanel) eventsPanel.appendChild(loadMoreBtn);

    // cursor-based pagination: server returns newest-first. We append pages (older items) to the bottom.
    let minEventId = null; // smallest id we've seen (used as 'before' cursor)
    function loadEvents(){
      const tidFilter = document.getElementById('filter-tid') ? document.getElementById('filter-tid').value.trim() : '';
      const eventFilter = document.getElementById('filter-event') ? document.getElementById('filter-event').value.trim() : '';
      let url = `/events?limit=${EV_LIMIT}`;
      if(minEventId){ url += `&before=${minEventId}`; }
      if(tidFilter) url += `&tid=${encodeURIComponent(tidFilter)}`;
      if(eventFilter) url += `&event=${encodeURIComponent(eventFilter)}`;
      fetch(url).then(r=>r.json()).then(js=>{
        try{
          if(!js || js.length === 0){ loadMoreBtn.disabled = true; loadMoreBtn.textContent = 'No more'; return; }
          // server returns newest-first for the page; append in that order so newest of the page is above older in that page
          js.forEach(ev=>addEventAppend(ev));
          // update minEventId to the smallest id we received
          try{ const ids = js.map(e=>e.id).filter(x=>typeof x==='number' || typeof x==='string').map(x=>parseInt(x,10)).filter(x=>!isNaN(x)); if(ids.length){ const localMin = Math.min(...ids); minEventId = (minEventId===null)? localMin : Math.min(minEventId, localMin); }
          }catch(e){}
          if(js.length < EV_LIMIT){ loadMoreBtn.disabled = true; loadMoreBtn.textContent = 'No more'; }
        }catch(e){ console.debug('processing events failed', e); }
      }).catch(e=>console.debug('events fetch failed', e));
    }

    // apply filters button resets pagination and reloads
    document.getElementById('apply-filters').addEventListener('click', ()=>{
      EV_OFFSET = 0; const list = document.getElementById('events-list'); if(list) list.innerHTML=''; loadMoreBtn.disabled = false; loadMoreBtn.textContent = 'Load more'; loadEvents();
    });

    // initial load
    loadEvents();

    // live update: when a new event is persisted, increment badge and prepend to list
    socket.on('event', ev=>{
      try{
        // prepend to events list so newest appear at top
        addEvent(ev);
        // increment badge for tid if present
        if(ev && ev.tid){
          try{
            const el = document.querySelector('#row-'+ev.tid+' .events-badge');
            if(el){ el.textContent = (parseInt(el.textContent||'0',10) + 1).toString(); }
            // also update local pending cache if exists
            if(window._pendingExtracts && window._pendingExtracts[ev.tid] && window._pendingExtracts[ev.tid].btn){
              // noop
            }
          }catch(e){}
        }
      }catch(e){ console.debug('event socket handler error', e); }
    });

    socket.on('receiver_started', d=>{ _makeToast(`Incoming: ${d.filename} (${d.size||'unknown'})`,'info',6000); addEvent({tid:d.tid, event:'receiver_started', ts: new Date().toISOString(), meta: JSON.stringify({filename:d.filename, size:d.size})}); });
    socket.on('receiver_completed', d=>{
      _makeToast(`Received ${d.save_path || d.tid} — ${d.sha256?d.sha256.slice(0,12):''}`,'success',8000);
      const tid=d.tid; // refresh/create row
      let row = rows[tid];
      const it={tid:tid, bytes_sent:d.total_bytes||0, total_bytes:d.total_bytes||0, sha256:d.sha256, save_path:d.save_path, extraction_ok:null, extraction_path:null, started_at:null};
      if(!row){ row = mkRow(it); rows[tid]=row; tbody.prepend(row);} else { const newr = mkRow(it); tbody.replaceChild(newr,row); rows[tid]=newr; row=newr }
      row.classList.add('row-new'); setTimeout(()=>row.classList.remove('row-new'),1600);
      addEvent({tid:d.tid, event:'receiver_completed', ts: new Date().toISOString(), meta: JSON.stringify({sha256:d.sha256, save_path:d.save_path, ok:d.ok})});
    });
    socket.on('receiver_extracted', d=>{ _makeToast(d.ok?`Extracted to ${d.extracted_to}`:`Extraction failed for ${d.tid}`,(d.ok?'success':'warn'),7000); const row = rows[d.tid]; if(row){ const cell = row.querySelectorAll('td')[4]; if(cell){ cell.innerHTML = d.ok?'<span class="badge ok">extracted</span>':'<span class="badge bad">failed</span>'; } } addEvent({tid:d.tid, event:'receiver_extracted', ts: new Date().toISOString(), meta: JSON.stringify({extracted_to:d.extracted_to, ok:d.ok})}); });
    // handle pending manual extracts: re-enable button and update status when server emits receiver_extracted
    socket.on('receiver_extracted', d=>{
      try{
        // find any pending extract host element for this extracted path
        if(d && d.extracted_to){
          const pendingKey = d.extracted_to;
          const pending = window._pendingExtracts && window._pendingExtracts[pendingKey];
          if(pending){
            try{ const hostEl = pending.el; const btn = pending.btn; if(btn){ btn.disabled = false; btn.textContent = 'Extract'; } if(hostEl){ let status = hostEl.querySelector('.ev-status'); if(!status){ status = document.createElement('div'); status.className='ev-status'; status.style.marginTop='6px'; status.style.color='#9bd'; hostEl.appendChild(status); } status.textContent = d.ok?('Extracted: '+(d.extracted_to||'')):('Extraction failed'); } }
            catch(e){ console.debug('pending extract update failed', e); }
            try{ delete window._pendingExtracts[pendingKey]; }catch(e){}
          }
        }
      }catch(e){ console.debug('pending extract handler error', e); }
    });

    // toggle events panel
    document.getElementById('toggle-events').addEventListener('click', ()=>{
      const p = document.getElementById('events-panel'); if(!p) return; p.style.display = (p.style.display==='none')? 'block' : 'none';
    });
  </script>
</body>
</html>
"""


HISTORY_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>quickshare — History</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    html { background: linear-gradient(135deg, #0f0c29, #302b63, #24243e); min-height: 100vh; }
    body {
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
      background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
      color: #e0e0e0;
      padding: 20px;
      min-height: 100vh;
    }
    .container { max-width: 1200px; margin: 0 auto; }
    .header {
      text-align: center;
      margin-bottom: 30px;
      animation: fadeInDown 0.8s ease-out;
    }
    .header h1 {
      font-size: 2.5em;
      background: linear-gradient(135deg, #00f5ff, #ff006e);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
      margin-bottom: 10px;
    }
    .back-btn {
      color: #00f5ff;
      text-decoration: none;
      font-weight: 500;
      transition: all 0.3s ease;
      margin-bottom: 20px;
      display: inline-block;
    }
    .back-btn:hover { color: #ff006e; }
    
    .controls {
      display: flex;
      gap: 10px;
      margin-bottom: 20px;
      flex-wrap: wrap;
    }
    .search-box {
      padding: 10px 15px;
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid rgba(255, 255, 255, 0.15);
      border-radius: 8px;
      color: #e0e0e0;
      font-size: 0.95em;
      flex: 1;
      min-width: 200px;
    }
    .search-box:focus {
      outline: none;
      background: rgba(255, 255, 255, 0.12);
      border-color: rgba(0, 245, 255, 0.5);
    }
    
    .btn {
      background: linear-gradient(135deg, #00f5ff, #0099ff);
      color: #000;
      border: none;
      padding: 10px 20px;
      border-radius: 8px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.3s ease;
      font-size: 0.95em;
    }
    .btn:hover {
      transform: translateY(-2px);
      box-shadow: 0 6px 20px rgba(0, 245, 255, 0.5);
    }
    .btn-danger {
      background: linear-gradient(135deg, #ff006e, #ff4d94);
    }
    
    .transfers-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: 20px;
    }
    
    .transfer-card {
      background: rgba(255, 255, 255, 0.05);
      backdrop-filter: blur(10px);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 15px;
      padding: 20px;
      animation: slideUp 0.6s ease-out;
      transition: all 0.3s ease;
      box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.2);
    }
    .transfer-card:hover {
      background: rgba(255, 255, 255, 0.08);
      border-color: rgba(0, 245, 255, 0.3);
      box-shadow: 0 8px 32px 0 rgba(0, 245, 255, 0.2);
      transform: translateY(-5px);
    }
    
    .transfer-name {
      font-size: 1.1em;
      font-weight: 600;
      color: #00f5ff;
      margin-bottom: 10px;
      word-break: break-all;
    }
    .transfer-info {
      font-size: 0.85em;
      color: #aaa;
      margin: 8px 0;
      display: flex;
      justify-content: space-between;
    }
    .transfer-size {
      color: #bbb;
      font-weight: 500;
    }
    .status-badge {
      display: inline-block;
      padding: 5px 12px;
      border-radius: 12px;
      font-size: 0.8em;
      font-weight: 600;
      margin: 8px 0;
    }
    .status-ok {
      background: rgba(39, 174, 96, 0.2);
      border: 1px solid rgba(39, 174, 96, 0.4);
      color: #27ae60;
    }
    .status-error {
      background: rgba(231, 76, 60, 0.2);
      border: 1px solid rgba(231, 76, 60, 0.4);
      color: #e74c3c;
    }
    .status-pending {
      background: rgba(52, 152, 219, 0.2);
      border: 1px solid rgba(52, 152, 219, 0.4);
      color: #3498db;
    }
    
    .hash-display {
      background: rgba(0, 0, 0, 0.3);
      padding: 10px;
      border-radius: 6px;
      font-family: 'Courier New', monospace;
      font-size: 0.75em;
      color: #9bd;
      margin: 10px 0;
      word-break: break-all;
    }
    
    .card-actions {
      display: flex;
      gap: 8px;
      margin-top: 15px;
      flex-wrap: wrap;
    }
    .action-btn {
      background: rgba(0, 245, 255, 0.15);
      color: #00f5ff;
      border: 1px solid rgba(0, 245, 255, 0.3);
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 0.8em;
      cursor: pointer;
      transition: all 0.3s ease;
    }
    .action-btn:hover {
      background: rgba(0, 245, 255, 0.3);
      box-shadow: 0 2px 8px rgba(0, 245, 255, 0.2);
    }
    .action-btn:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }
    
    .empty-state {
      text-align: center;
      padding: 40px 20px;
      color: #aaa;
    }
    .empty-state svg { width: 64px; height: 64px; opacity: 0.5; margin-bottom: 20px; }
    
    @keyframes fadeInDown {
      from { opacity: 0; transform: translateY(-20px); }
      to { opacity: 1; transform: translateY(0); }
    }
    @keyframes slideUp {
      from { opacity: 0; transform: translateY(20px); }
      to { opacity: 1; transform: translateY(0); }
    }
  </style>
</head>
<body>
<div class="container">
  <div class="header">
    <a href="/" class="back-btn">← Back to Home</a>
    <h1>📜 Transfer History</h1>
  </div>
  
  <div class="controls">
    <input type="text" id="search-box" class="search-box" placeholder="Search by filename or transfer ID...">
    <button class="btn btn-danger" onclick="clearHistory()">Clear All</button>
  </div>
  
  <div id="transfers-container" class="transfers-grid">
    <!-- Cards generated by JS -->
  </div>
</div>

<script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
<style>
/* Toast styles */
.qs-toast-container { position: fixed; right: 20px; bottom: 20px; z-index: 9999; display: flex; flex-direction: column; gap: 10px; }
.qs-toast { min-width: 240px; max-width: 360px; background: rgba(30,30,30,0.95); color: #fff; padding: 12px 14px; border-radius: 8px; box-shadow: 0 6px 20px rgba(0,0,0,0.5); font-size: 0.95em; display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.qs-toast.success { border-left: 4px solid #27ae60; }
.qs-toast.warn { border-left: 4px solid #f39c12; }
.qs-toast .close { cursor: pointer; opacity: 0.8; }
</style>
<div id="qs-toast-root" class="qs-toast-container"></div>

<script>
function _makeToast(msg, cls='info', ttl=5000) {
  try {
    const root = document.getElementById('qs-toast-root');
    if(!root) return;
    const el = document.createElement('div');
    el.className = 'qs-toast ' + cls;
    el.innerHTML = `<div style="flex:1">${msg}</div><div class="close">✖</div>`;
    const closeBtn = el.querySelector('.close');
    closeBtn.addEventListener('click', ()=>{ if(el && el.parentNode) el.parentNode.removeChild(el); });
    root.appendChild(el);
    setTimeout(()=>{ try{ if(el && el.parentNode) el.parentNode.removeChild(el); }catch(e){} }, ttl);
  } catch(e) { console.debug('toast error', e); }
}

const transfers = {{ transfers_json | safe }};  // Rendered by Flask

function formatBytes(bytes) {
  if(bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function renderTransfers(filtered = transfers) {
  const container = document.getElementById('transfers-container');
  if(!filtered || filtered.length === 0) {
    container.innerHTML = '<div class="empty-state"><p>No transfers yet</p></div>';
    return;
  }
  
  container.innerHTML = filtered.map(t => {
    const status = t.error ? 'error' : (t.completed ? 'ok' : 'pending');
    const statusClass = `status-${status}`;
    const statusText = t.error ? ('Error: ' + t.error) : (t.completed ? 'Completed' : 'In Progress');
    const filename = t.save_path ? t.save_path.split('/').pop() : 'Unknown';
    const progress = t.total_bytes > 0 ? Math.min(100, (t.bytes_sent / t.total_bytes) * 100) : 0;
    
    let actions = '';
    if(t.completed && t.save_path) {
      actions += `<button class="action-btn" onclick="doExtract('${t.save_path}')">Extract</button>`;
      actions += `<button class="action-btn" onclick="doVerify('${t.tid}')">Verify</button>`;
    }
    if(t.sha256) {
      actions += `<button class="action-btn" onclick="copyHash('${t.sha256}')">Copy Hash</button>`;
    }
    
    return `
      <div class="transfer-card">
        <div class="transfer-name">${filename}</div>
        <div class="transfer-info">
          <span>Size:</span>
          <span class="transfer-size">${formatBytes(t.total_bytes)}</span>
        </div>
        <div class="transfer-info">
          <span>Transferred:</span>
          <span>${formatBytes(t.bytes_sent)}</span>
        </div>
        <div class="transfer-info">
          <span>Progress:</span>
          <span>${progress.toFixed(1)}%</span>
        </div>
        <span class="status-badge ${statusClass}">${statusText}</span>
        ${t.sha256 ? `<div class="hash-display" title="SHA256">${t.sha256.substring(0, 16)}...</div>` : ''}
        <div class="card-actions">
          ${actions}
        </div>
        <div style="margin-top:10px; font-size:0.75em; color:#999;">
          ID: ${t.tid.substring(0, 8)}...
        </div>
      </div>
    `;
  }).join('');
}

function filterTransfers() {
  const query = document.getElementById('search-box').value.toLowerCase();
  const filtered = transfers.filter(t => {
    const filename = t.save_path ? t.save_path.toLowerCase() : '';
    const tid = t.tid.toLowerCase();
    return filename.includes(query) || tid.includes(query);
  });
  renderTransfers(filtered);
}

function clearHistory() {
  if(!confirm('Delete all transfer history?')) return;
  fetch('/clear-history', {method: 'POST'})
    .then(r => r.json())
    .then(js => {
      _makeToast(js.message || 'History cleared', 'success');
      transfers.length = 0;
      renderTransfers([]);
    })
    .catch(e => _makeToast('Failed to clear history', 'warn'));
}

function copyHash(hash) {
  navigator.clipboard.writeText(hash);
  _makeToast('Hash copied', 'success', 2000);
}

function doExtract(path) {
  fetch('/extract', {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: `path=${encodeURIComponent(path)}`})
    .then(r => r.json())
    .then(js => {
      if(js.error) {
        _makeToast('Extract failed: ' + js.error, 'warn');
      } else {
        _makeToast('Extraction started', 'success');
      }
    })
    .catch(e => _makeToast('Extract request failed', 'warn'));
}

function doVerify(tid) {
  fetch('/verify', {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: `tid=${encodeURIComponent(tid)}`})
    .then(r => r.json())
    .then(js => {
      if(js.ok) {
        _makeToast('Verification passed', 'success');
      } else {
        _makeToast('Verification failed: ' + (js.error || 'unknown'), 'warn');
      }
    })
    .catch(e => _makeToast('Verify request failed', 'warn'));
}

document.getElementById('search-box').addEventListener('input', filterTransfers);

// Initial render
window.addEventListener('load', () => renderTransfers());
</script>
</body>
</html>
"""


def discover_peers(wait=1.0):
    # Use DiscoveryManager briefly to announce nothing but listen
    mgr = DiscoveryManager(name='web-ui', control_port=0, bind_addr='127.0.0.1', bind_port=37020, interval=0.5)
    mgr.start()
    try:
        time.sleep(wait)
        return mgr.get_peers()
    finally:
        mgr.stop()


@app.route('/dashboard')
def dashboard():
  # return recent transfers for dashboard initial render
  conn = sqlite3.connect(DB_PATH)
  try:
    cur = conn.execute('SELECT tid, bytes_sent, total_bytes, completed, error, started_at, finished_at, sha256, save_path, extraction_path, extraction_ok FROM transfers ORDER BY started_at DESC LIMIT 100')
    rows = cur.fetchall()
    items = []
    for r in rows:
      items.append({
        'tid': r[0],
        'bytes_sent': r[1] or 0,
        'total_bytes': r[2] or 0,
        'completed': bool(r[3]),
        'error': r[4],
        'started_at': r[5],
        'finished_at': r[6],
        'sha256': r[7],
        'save_path': r[8],
        'extraction_path': r[9],
        'extraction_ok': r[10],
      })
  finally:
    conn.close()
  return render_template_string(DASHBOARD_TEMPLATE, items=items)


@app.route('/')
def index():
    peers = discover_peers(0.8)
    recv_running = RECV_STATE['server'] is not None
    recv_port = RECV_STATE['server'].port if RECV_STATE['server'] else None
    recv_addr = '127.0.0.1'  # default; in production would detect actual interface
    status = []
    status.append(f"Receiver running: {recv_running}")
    if recv_running:
        status.append(f"Receiver port: {recv_port}")
    # pass through optional tid so UI can poll
    tid = request.args.get('tid') if request else None
    max_upload = app.config.get('MAX_CONTENT_LENGTH', 0)
    # human friendly display (MB if >=1MB else KB)
    if max_upload >= 1024*1024:
      max_upload_human = f"{max_upload/1024/1024:.1f} MB"
    elif max_upload >= 1024:
      max_upload_human = f"{max_upload/1024:.1f} KB"
    else:
      max_upload_human = f"{max_upload} B"
    return render_template_string(TEMPLATE, peers=peers, recv_running=recv_running, recv_port=recv_port, recv_addr=recv_addr, status='\n'.join(status), tid=tid, max_upload_bytes=max_upload, max_upload_human=max_upload_human)


@app.route('/start-recv', methods=['POST'])
def start_recv():
    bind = request.form.get('bind', '0.0.0.0')
    port = int(request.form.get('port', 60000))
    if RECV_STATE['server']:
        return redirect(url_for('index'))

    def make_handler(offer):
        # simple behavior: write to received/<filename>.part
        from quickshare.transfer import Receiver
        import uuid, datetime

        outdir = os.path.abspath('received')
        os.makedirs(outdir, exist_ok=True)
        filename = offer.get('filename', 'received')
        save_path = os.path.join(outdir, filename)
        total_chunks = int(offer.get('total_chunks', 1))
        chunk_size = int(offer.get('chunk_size', 1024*1024))

        # allocate a tid for this incoming transfer so we can track it in DB
        tid = str(uuid.uuid4())

        def on_start(meta):
            # meta contains filename, size, total_chunks, save_path
            try:
                started = datetime.datetime.utcnow().isoformat()
                conn = sqlite3.connect(DB_PATH)
                try:
                    conn.execute('INSERT OR REPLACE INTO transfers (tid, bytes_sent, total_bytes, completed, error, started_at, save_path) VALUES (?, ?, ?, ?, ?, ?, ?)', (tid, 0, int(meta.get('size', 0)), 0, None, started, meta.get('save_path')))
                    conn.commit()
                finally:
                    conn.close()
            except Exception:
                pass
            try:
                socketio.emit('receiver_started', {'tid': tid, 'filename': meta.get('filename'), 'size': meta.get('size')})
            except Exception:
                pass
            # persist event (async)
            try:
                ts = datetime.datetime.utcnow().isoformat()
                enqueue_event(tid, 'receiver_started', str({'filename': meta.get('filename'), 'size': meta.get('size')}), ts=ts)
            except Exception:
                pass

        def on_complete(meta):
            # meta contains save_path, sha256, ok
            try:
                finished = datetime.datetime.utcnow().isoformat()
                conn = sqlite3.connect(DB_PATH)
                try:
                    conn.execute('UPDATE transfers SET completed = 1, finished_at = ?, sha256 = ?, save_path = ? WHERE tid = ?', (finished, meta.get('sha256'), meta.get('save_path'), tid))
                    conn.commit()
                finally:
                    conn.close()
            except Exception:
                pass

            try:
                socketio.emit('receiver_completed', {'tid': tid, 'sha256': meta.get('sha256'), 'ok': meta.get('ok'), 'save_path': meta.get('save_path')})
            except Exception:
                pass
            # persist event (async)
            try:
                ts = datetime.datetime.utcnow().isoformat()
                enqueue_event(tid, 'receiver_completed', str({'sha256': meta.get('sha256'), 'save_path': meta.get('save_path'), 'ok': meta.get('ok')}), ts=ts)
            except Exception:
                pass

            # auto-extract if enabled and file appears to be a tarball
            if AUTO_EXTRACT:
                try:
                    sp = meta.get('save_path')
                    if sp and os.path.exists(sp):
                        import tarfile
                        dest = os.path.join(outdir, os.path.splitext(os.path.basename(sp))[0])
                        os.makedirs(dest, exist_ok=True)
                        extraction_ok = False
                        if tarfile.is_tarfile(sp):
                            try:
                                with tarfile.open(sp, 'r:*') as tf:
                                    tf.extractall(dest)
                                extraction_ok = True
                            except Exception:
                                extraction_ok = False
                        # update DB with extraction info
                        try:
                            conn = sqlite3.connect(DB_PATH)
                            try:
                                conn.execute('UPDATE transfers SET extraction_path = ?, extraction_ok = ? WHERE tid = ?', (dest, 1 if extraction_ok else 0, tid))
                                conn.commit()
                            finally:
                                conn.close()
                        except Exception:
                            pass
                        # emit event
                        try:
                            socketio.emit('receiver_extracted', {'tid': tid, 'extracted_to': dest, 'ok': extraction_ok})
                        except Exception:
                            pass
                except Exception:
                    pass

        r = Receiver(save_path, chunk_size, total_chunks, out_dir=outdir, on_start=on_start, on_complete=on_complete)
        return r.handle_offer_and_receive(offer)

    srv = ControlServer(host=bind, port=port, handler=make_handler)
    srv.start()
    ann = DiscoveryAnnouncer(name='web-ui-recv', port=srv.port, target_addr='127.0.0.1', target_port=37020, interval=1.0)
    ann.start()
    RECV_STATE['server'] = srv
    RECV_STATE['announcer'] = ann
    return redirect(url_for('index'))


@app.route('/upload', methods=['POST'])
def upload_file():
  """Accept a single file upload and persist it to server 'uploads/' directory.
  Returns JSON with the server-side path which can then be used by /send.
  """
  f = request.files.get('file')
  if not f:
    return jsonify({'error': 'no file provided'}), 400
  upload_dir = os.path.abspath('uploads')
  os.makedirs(upload_dir, exist_ok=True)
  # sanitize filename
  filename = secure_filename(getattr(f, 'filename', 'upload'))
  if not filename:
    filename = 'upload'
  save_path = os.path.join(upload_dir, filename)
  base, ext = os.path.splitext(filename)
  i = 1
  while os.path.exists(save_path):
    save_path = os.path.join(upload_dir, f"{base}-{i}{ext}")
    i += 1
  try:
    f.save(save_path)
  except Exception as e:
    return jsonify({'error': str(e)}), 500
  return jsonify({'path': save_path})


@app.route('/upload/cleanup-status', methods=['GET'])
def upload_cleanup_status():
  """Return simple JSON about upload cleanup runs and current storage usage."""
  try:
    total_files = 0
    total_bytes = 0
    if os.path.isdir(UPLOAD_DIR):
      for fn in os.listdir(UPLOAD_DIR):
        pth = os.path.join(UPLOAD_DIR, fn)
        if os.path.isfile(pth):
          total_files += 1
          try:
            total_bytes += os.path.getsize(pth)
          except Exception:
            pass
  except Exception:
    total_files = 0
    total_bytes = 0
  last_run = globals().get('UPLOAD_CLEANUP_LAST_RUN')
  last_cleaned = globals().get('UPLOAD_LAST_CLEANED_COUNT', 0)
  total_bytes_tracked = globals().get('UPLOAD_TOTAL_BYTES', total_bytes)
  return jsonify({'last_run': last_run, 'last_cleaned_count': last_cleaned, 'total_files': total_files, 'total_bytes': total_bytes_tracked, 'ttl_seconds': UPLOAD_TTL_SECONDS})


@app.route('/stop-recv', methods=['POST'])
def stop_recv():
    if RECV_STATE['announcer']:
        try:
            RECV_STATE['announcer'].stop()
        except Exception:
            pass
    if RECV_STATE['server']:
        try:
            RECV_STATE['server'].stop()
        except Exception:
            pass
    RECV_STATE['announcer'] = None
    RECV_STATE['server'] = None
    return redirect(url_for('index'))


@app.route('/package', methods=['POST'])
def package():
    src = request.form.get('src', 'Portfolio')
    src = os.path.abspath(src)
    os.makedirs('artifacts', exist_ok=True)
    out = os.path.abspath('artifacts/portfolio.tar.gz')
    # create tar.gz
    import tarfile
    with tarfile.open(out, 'w:gz') as tf:
        # add contents of the folder at top-level
        for root, dirs, files in os.walk(src):
            for f in files:
                full = os.path.join(root, f)
                arcname = os.path.relpath(full, src)
                tf.add(full, arcname=arcname)
    return redirect(url_for('index'))


@app.route('/send', methods=['POST'])
def send():
    target = request.form.get('peer')
    file = request.form.get('file')
    if not target or not file:
        return redirect(url_for('index'))
    host, port = target.split(':')
    port = int(port)
    file = os.path.abspath(file)
    if not os.path.exists(file):
        return f"File not found: {file}", 400
    # run send in a background thread so the web UI doesn't block
    def _bg_send():
        tid = thread_tid
        try:
            s = Sender(file, chunk_size=1024*1024)
            # initialize progress row in DB
            conn = sqlite3.connect(DB_PATH)
            try:
                import datetime
                started = datetime.datetime.utcnow().isoformat()
                conn.execute('INSERT OR REPLACE INTO transfers (tid, bytes_sent, total_bytes, completed, error, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (tid, 0, s.total_size, 0, None, started, None))
                conn.commit()
            finally:
                conn.close()

            # per-transfer smoothing state captured in closure
            last_sample = None
            bytes_sent_local = 0

            def _cb(idx, b):
                nonlocal last_sample, bytes_sent_local
                try:
                    # update local counter (fast, in-memory)
                    bytes_sent_local += b
                    total_bytes = s.total_size
                    # compute instantaneous bps and apply simple exponential smoothing
                    now_ts = time.time()
                    if last_sample is None:
                        last_sample = {'ts': now_ts, 'bytes': bytes_sent_local, 'smoothed': 0.0}
                    else:
                        delta_b = max(0, bytes_sent_local - last_sample['bytes'])
                        delta_t = max(1e-6, now_ts - last_sample['ts'])
                        inst_bps = delta_b / delta_t
                        alpha = 0.3
                        sm = (alpha * inst_bps) + ((1 - alpha) * last_sample.get('smoothed', 0.0))
                        last_sample = {'ts': now_ts, 'bytes': bytes_sent_local, 'smoothed': sm}
                    # emit progress with smoothed bps and local bytes_sent
                    try:
                        socketio.emit('progress', {'tid': tid, 'bytes_sent': bytes_sent_local, 'total_bytes': total_bytes, 'bps': int(last_sample.get('smoothed', 0))})
                    except Exception:
                        pass
                    # enqueue DB delta for background flush
                    try:
                        enqueue_transfer_delta(tid, b)
                    except Exception:
                        pass
                except Exception:
                    pass

            s.send(host, port, send_control_offer, progress_callback=_cb)

            conn = sqlite3.connect(DB_PATH)
            try:
                import datetime
                finished = datetime.datetime.utcnow().isoformat()
                conn.execute('UPDATE transfers SET completed = 1, finished_at = ? WHERE tid = ?', (finished, tid))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            print('send failed', e)
            conn = None
            try:
                conn = sqlite3.connect(DB_PATH)
                import datetime
                finished = datetime.datetime.utcnow().isoformat()
                conn.execute('UPDATE transfers SET error = ?, finished_at = ? WHERE tid = ?', (str(e), finished, tid))
                conn.commit()
            except Exception:
                pass
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

    import uuid
    thread_tid = str(uuid.uuid4())
    threading.Thread(target=_bg_send, daemon=True).start()
    # redirect back to index with tid so page will poll progress
    return redirect(url_for('index', tid=thread_tid))


@app.route('/progress')
def progress():
    tid = request.args.get('tid')
    if not tid:
        return ({}, 404)
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute('SELECT bytes_sent, total_bytes, completed, error, started_at, finished_at FROM transfers WHERE tid = ?', (tid,))
        row = cur.fetchone()
        if not row:
            return ({}, 404)
        bytes_sent, total_bytes, completed, error, started_at, finished_at = row
        resp = {'bytes_sent': bytes_sent, 'total_bytes': total_bytes, 'completed': bool(completed), 'started_at': started_at, 'finished_at': finished_at}
        if error:
            resp['error'] = error
        return resp
    finally:
        conn.close()



@app.route('/history')
def history():
  conn = sqlite3.connect(DB_PATH)
  try:
    cur = conn.execute('SELECT tid, bytes_sent, total_bytes, completed, error, started_at, finished_at, sha256, save_path FROM transfers ORDER BY started_at DESC')
    rows = cur.fetchall()
    items = [{'tid': r[0], 'bytes_sent': r[1], 'total_bytes': r[2], 'completed': bool(r[3]), 'error': r[4], 'started_at': r[5], 'finished_at': r[6], 'sha256': r[7], 'save_path': r[8]} for r in rows]
    import json
    return render_template_string(HISTORY_TEMPLATE, transfers_json=json.dumps(items))
  finally:
    conn.close()


@app.route('/clear-history', methods=['POST'])
def clear_history():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute('DELETE FROM transfers')
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for('history'))


@app.route('/events')
def events():
  # server-side filtering and pagination: support ?limit=&offset=&tid=&event=
  # Prefer cursor-based pagination using ?before=<id> (fetch id < before) for stable live-updating.
  limit = int(request.args.get('limit', 200))
  offset = request.args.get('offset')
  before = request.args.get('before')
  tid_filter = request.args.get('tid')
  event_filter = request.args.get('event')

  base = 'SELECT id, tid, event, ts, meta FROM events'
  params = []
  wheres = []
  if tid_filter:
    wheres.append('tid = ?')
    params.append(tid_filter)
  if event_filter:
    wheres.append('event = ?')
    params.append(event_filter)
  # cursor pagination (preferred)
  if before:
    try:
      before_id = int(before)
      wheres.append('id < ?')
      params.append(before_id)
    except Exception:
      pass

  sql = base
  if wheres:
    sql += ' WHERE ' + ' AND '.join(wheres)

  sql += ' ORDER BY id DESC LIMIT ?'
  params.append(limit)

  # legacy offset support
  if offset is not None:
    try:
      off = int(offset)
      sql += ' OFFSET ?'
      params.append(off)
    except Exception:
      pass

  conn = sqlite3.connect(DB_PATH)
  try:
    cur = conn.execute(sql, tuple(params))
    rows = cur.fetchall()
    items = [{'id': r[0], 'tid': r[1], 'event': r[2], 'ts': r[3], 'meta': r[4]} for r in rows]
    return jsonify(items)
  finally:
    conn.close()


@app.route('/events/counts')
def events_counts():
  # return counts for a comma-separated list of tids
  tids_raw = request.args.get('tids', '')
  if not tids_raw:
    return jsonify({})
  tids = [t for t in tids_raw.split(',') if t]
  if not tids:
    return jsonify({})
  placeholders = ','.join('?' for _ in tids)
  sql = f'SELECT tid, COUNT(*) as c FROM events WHERE tid IN ({placeholders}) GROUP BY tid'
  # check in-memory cache first; fill missing tids from DB and return
  out = {}
  missing = []
  with EVENT_COUNTS_LOCK:
    for t in tids:
      if t in EVENT_COUNTS_CACHE:
        out[t] = EVENT_COUNTS_CACHE[t]
      else:
        missing.append(t)

  if missing:
    placeholders = ','.join('?' for _ in missing)
    sql = f'SELECT tid, COUNT(*) as c FROM events WHERE tid IN ({placeholders}) GROUP BY tid'
    conn = sqlite3.connect(DB_PATH)
    try:
      cur = conn.execute(sql, tuple(missing))
      rows = cur.fetchall()
      for r in rows:
        out[r[0]] = r[1]
      # set zeros for not present
      for t in missing:
        out.setdefault(t, 0)
    finally:
      conn.close()
    # update cache for missing
    with EVENT_COUNTS_LOCK:
      for t in missing:
        EVENT_COUNTS_CACHE[t] = out.get(t, 0)

  return jsonify(out)


@app.route('/verify', methods=['POST'])
def verify():
  tid = request.form.get('tid')
  if not tid:
    return jsonify({'error': 'missing tid'}), 400
  conn = sqlite3.connect(DB_PATH)
  try:
    cur = conn.execute('SELECT save_path, sha256 FROM transfers WHERE tid = ?', (tid,))
    row = cur.fetchone()
    if not row:
      return jsonify({'error': 'not found'}), 404
    save_path, recorded = row
  finally:
    conn.close()
  if not save_path or not os.path.exists(save_path):
    return jsonify({'error': 'file not found'}), 404
  try:
    actual = sha256_file(save_path)
    ok = (recorded == actual) if recorded else False
    return jsonify({'tid': tid, 'recorded': recorded, 'actual': actual, 'ok': ok})
  except Exception as e:
    return jsonify({'error': str(e)}), 500


@app.route('/extract', methods=['POST'])
def extract():
  # Extract a tarball from the received folder and compute SHA256
  path = request.form.get('path')
  if not path:
    return jsonify({'error': 'missing path'}), 400
  abs_path = os.path.abspath(path)
  received_dir = os.path.abspath('received')
  # ensure the path is inside received_dir
  try:
    if os.path.commonpath([received_dir, abs_path]) != received_dir:
      return jsonify({'error': 'path not allowed'}), 403
  except Exception:
    return jsonify({'error': 'path validation failed'}), 400
  if not os.path.exists(abs_path):
    return jsonify({'error': 'not found'}), 404
  import tarfile
  dest = os.path.join(received_dir, os.path.splitext(os.path.basename(abs_path))[0])
  os.makedirs(dest, exist_ok=True)
  try:
    if tarfile.is_tarfile(abs_path):
      with tarfile.open(abs_path, 'r:*') as tf:
        tf.extractall(dest)
    # compute sha256
    sha = sha256_file(abs_path)
    # update transfers DB if we can find a matching save_path
    conn = None
    try:
      conn = sqlite3.connect(DB_PATH)
      cur = conn.execute('SELECT tid FROM transfers WHERE save_path = ? LIMIT 1', (abs_path,))
      row = cur.fetchone()
      tid = row[0] if row else None
      if tid:
        try:
          conn.execute('UPDATE transfers SET extraction_path = ?, extraction_ok = ? WHERE tid = ?', (dest, 1, tid))
          conn.commit()
        except Exception:
          pass
        # persist event and emit
        try:
          socketio.emit('receiver_extracted', {'tid': tid, 'extracted_to': dest, 'ok': True})
        except Exception:
          pass
        try:
          import datetime
          enqueue_event(tid, 'receiver_extracted', str({'extracted_to': dest, 'ok': True}), ts=datetime.datetime.utcnow().isoformat())
        except Exception:
          pass
    except Exception:
      pass
    finally:
      if conn:
        try:
          conn.close()
        except Exception:
          pass

    return jsonify({'ok': True, 'sha256': sha, 'extracted_to': dest})
  except Exception as e:
    return jsonify({'error': str(e)}), 500


@app.route('/status')
def status_page():
  peers = discover_peers(0.8)
  recv_running = RECV_STATE['server'] is not None
  recv_port = RECV_STATE['server'].port if RECV_STATE['server'] else None
  # recent transfers
  conn = sqlite3.connect(DB_PATH)
  try:
    cur = conn.execute('SELECT tid, bytes_sent, total_bytes, completed, error, started_at, finished_at FROM transfers ORDER BY started_at DESC LIMIT 20')
    rows = cur.fetchall()
    items = [{'tid': r[0], 'bytes_sent': r[1], 'total_bytes': r[2], 'completed': bool(r[3]), 'error': r[4], 'started_at': r[5], 'finished_at': r[6]} for r in rows]
  finally:
    conn.close()
  # simple status HTML
  html = ['<h1>QuickShare Status</h1>', '<a href="/">Back</a>']
  html.append(f"<p>Receiver running: {recv_running}</p>")
  if recv_running:
    html.append(f"<p>Receiver port: {recv_port}</p>")
  html.append('<h2>Discovered peers</h2>')
  if peers:
    html.append('<ul>')
    for k, v in peers.items():
      html.append(f"<li>{v['name']} @ {v['addr']}:{v['port']}</li>")
    html.append('</ul>')
  else:
    html.append('<p>No peers discovered (short scan)</p>')
  html.append('<h2>Recent Transfers</h2>')
  html.append('<table border="1"><tr><th>tid</th><th>bytes_sent</th><th>total_bytes</th><th>completed</th><th>started_at</th><th>finished_at</th></tr>')
  for it in items:
    html.append(f"<tr><td>{it['tid']}</td><td>{it['bytes_sent']}</td><td>{it['total_bytes']}</td><td>{it['completed']}</td><td>{it['started_at']}</td><td>{it['finished_at']}</td></tr>")
  html.append('</table>')
  return '\n'.join(html)


@app.route('/qrcode')
def qrcode():
  """Generate QR code for receiver address (IP:port)"""
  try:
    import qrcode
  except ImportError:
    return {'error': 'qrcode library not installed'}, 400
  
  addr = request.args.get('addr', '127.0.0.1')
  port = request.args.get('port', '9999')
  data = f"quickshare://{addr}:{port}"
  
  qr = qrcode.QRCode(version=1, box_size=10, border=2)
  qr.add_data(data)
  qr.make(fit=True)
  img = qr.make_image(fill_color='#00f5ff', back_color='#0a0e27')
  
  buf = io.BytesIO()
  img.save(buf, 'PNG')
  buf.seek(0)
  return send_file(buf, mimetype='image/png')


if __name__ == '__main__':
  # use SocketIO run to enable WebSocket support
  # In DEV we keep the Werkzeug dev server with allow_unsafe_werkzeug for convenience.
  # In PROD, prefer eventlet (or gevent) for proper WebSocket support and concurrency.
  env = os.environ.get('QUICKSHARE_ENV', APP_ENV or 'DEV').lower()
  if env == 'prod':
    try:
      import eventlet  # type: ignore
      # eventlet monkey patch to make standard library cooperative
      eventlet.monkey_patch()
      print('Starting in PROD mode with eventlet')
    except Exception:
      print('eventlet not available; falling back to built-in server (not recommended for production)')
    # socketio.run will use eventlet if available
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
  else:
    # Development mode — local bind only
    socketio.run(app, port=5000, host='127.0.0.1', allow_unsafe_werkzeug=True, debug=True)

#!/usr/bin/env python3
"""Minimal Flask web UI for quickshare (MVP).

Features:
- List discovered peers (using DiscoveryManager briefly)
- Start/stop a receiver
- Package the `Portfolio/` folder into `artifacts/portfolio.tar.gz`
- Send the tarball to a selected peer

This is intentionally small — it runs locally and is for demo/test use.
"""
from flask import Flask, request, render_template_string, redirect, url_for, jsonify
from flask_socketio import SocketIO
import os
import threading
import time
import sqlite3
from typing import Optional, Dict, Any
from quickshare.discovery import DiscoveryManager
from quickshare.control import ControlServer
from quickshare.discovery import DiscoveryAnnouncer
from quickshare.transfer import Sender
from quickshare.control import send_control_offer
from quickshare.fileutils import sha256_file

app = Flask(__name__)

# SocketIO for real-time push updates
socketio = SocketIO(app, cors_allowed_origins='*')

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
    conn.commit()
  finally:
    conn.close()


init_db()

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
      <form method="post" action="/package">
        <div class="form-group">
          <label>Source Folder</label>
          <input type="text" name="src" value="Portfolio">
        </div>
        <button class="btn" type="submit">Package to TAR.GZ</button>
      </form>
    </div>

    <!-- Peers & Send Card -->
    <div class="card">
      <h2>🚀 Send</h2>
      <form method="post" action="/send">
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
          <input type="text" name="file" value="artifacts/portfolio.tar.gz">
        </div>
        <button class="btn" type="submit">Send File</button>
        <div id="progress-container" class="progress-container">
          <div style="font-weight: 600; margin-bottom: 10px;">Transfer in progress...</div>
          <div class="progress-bar">
            <div class="progress-fill" id="progress-fill"></div>
          </div>
          <div class="progress-text">
            <span id="progress-pct">0</span>% — <span id="progress-bytes">0</span>/<span id="progress-total">0</span> bytes
          </div>
        </div>
      </form>
    </div>
  </div>

  <div class="status-info" style="margin-bottom: 20px;">
    <strong>Status:</strong> {{ status }}
  </div>

  <div class="link-area">
    <a href="/history">📊 View History</a>
    <a href="/" style="cursor: pointer; color: #aaa;">🔄 Refresh</a>
  </div>
</div>

<script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
<script>
function qsGet(name){
  const url = new URL(window.location.href);
  return url.searchParams.get(name);
}

// Socket.IO real-time updates
const socket = io();
socket.on('connect', ()=>{ console.debug('socket connected'); });

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

window.addEventListener('load', ()=>{
  const tid = qsGet('tid');
  if(tid) showProgressContainer();
});
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


@app.route('/')
def index():
    peers = discover_peers(0.8)
    recv_running = RECV_STATE['server'] is not None
    recv_port = RECV_STATE['server'].port if RECV_STATE['server'] else None
    status = []
    status.append(f"Receiver running: {recv_running}")
    if recv_running:
        status.append(f"Receiver port: {recv_port}")
    # pass through optional tid so UI can poll
    tid = request.args.get('tid') if request else None
    return render_template_string(TEMPLATE, peers=peers, recv_running=recv_running, recv_port=recv_port, status='\n'.join(status), tid=tid)


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

    def on_complete(meta):
      # meta contains save_path, sha256, ok
      try:
        finished = datetime.datetime.utcnow().isoformat()
        conn = sqlite3.connect(DB_PATH)
        try:
          conn.execute('UPDATE transfers SET completed = 1, finished_at = ?, sha256 = ? WHERE tid = ?', (finished, meta.get('sha256'), tid))
          conn.commit()
        finally:
          conn.close()
      except Exception:
        pass
      try:
        socketio.emit('receiver_completed', {'tid': tid, 'sha256': meta.get('sha256'), 'ok': meta.get('ok'), 'save_path': meta.get('save_path')})
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

            def _cb(idx, b):
                conn = None
                try:
                    conn = sqlite3.connect(DB_PATH)
                    conn.execute('UPDATE transfers SET bytes_sent = bytes_sent + ? WHERE tid = ?', (b, tid))
                    conn.commit()
                    # fetch current bytes_sent to emit to clients
                    cur = conn.execute('SELECT bytes_sent, total_bytes FROM transfers WHERE tid = ?', (tid,))
                    row = cur.fetchone()
                    if row:
                        bytes_sent_now, total_bytes = row
                        try:
                            socketio.emit('progress', {'tid': tid, 'bytes_sent': bytes_sent_now, 'total_bytes': total_bytes})
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
    # simple HTML with Extract/Verify buttons for completed transfers
    html = ['<h1>Transfer history</h1>', '<a href="/">Back</a>', '<table border="1"><tr><th>tid</th><th>bytes_sent</th><th>total_bytes</th><th>completed</th><th>started_at</th><th>finished_at</th><th>sha256</th><th>actions</th></tr>']
    for it in items:
      actions = ''
      if it['completed'] and it.get('save_path'):
        # Extract button posts to /extract with path
        actions = (
          f"<form method=\"post\" action=\"/extract\" style=\"display:inline\">"
          f"<input type=\"hidden\" name=\"path\" value=\"{it['save_path']}\">"
          f"<button>Extract</button></form>"
          f"<form method=\"post\" action=\"/verify\" style=\"display:inline;margin-left:8px\">"
          f"<input type=\"hidden\" name=\"tid\" value=\"{it['tid']}\">"
          f"<button>Verify only</button></form>"
        )
      html.append(f"<tr><td>{it['tid']}</td><td>{it['bytes_sent']}</td><td>{it['total_bytes']}</td><td>{it['completed']}</td><td>{it['started_at']}</td><td>{it['finished_at']}</td><td>{it.get('sha256') or ''}</td><td>{actions}</td></tr>")
    html.append('</table>')
    html.append('<form method="post" action="/clear-history"><button>Clear history</button></form>')
    return '\n'.join(html)
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


if __name__ == '__main__':
  # use SocketIO run to enable WebSocket support
  # allow_unsafe_werkzeug=True is required for the built-in dev server with
  # recent Flask versions when using SocketIO without an async worker in dev.
  socketio.run(app, port=5000, host='127.0.0.1', allow_unsafe_werkzeug=True)

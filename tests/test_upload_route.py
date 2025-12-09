import io
import os
import time

from ui import app, UPLOAD_DIR


def test_upload_success(tmp_path):
    client = app.test_client()
    # ensure upload dir exists and clean
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    # small file
    data = {
        'file': (io.BytesIO(b'hello world'), 'hello.txt')
    }
    resp = client.post('/upload', data=data, content_type='multipart/form-data')
    assert resp.status_code == 200, f"expected 200 got {resp.status_code}"
    js = resp.get_json()
    assert js and 'path' in js
    path = js['path']
    assert os.path.exists(path), "uploaded file not found on disk"
    # cleanup
    try:
        os.remove(path)
    except Exception:
        pass


def test_upload_oversize(monkeypatch):
    client = app.test_client()
    # set a tiny max upload size
    monkeypatch.setitem(app.config, 'MAX_CONTENT_LENGTH', 10)
    # create payload larger than 10 bytes
    data = {
        'file': (io.BytesIO(b'a' * 1024), 'big.bin')
    }
    resp = client.post('/upload', data=data, content_type='multipart/form-data')
    # Flask should return 413 Request Entity Too Large
    assert resp.status_code in (413, 400), f"expected 413/400 got {resp.status_code}"
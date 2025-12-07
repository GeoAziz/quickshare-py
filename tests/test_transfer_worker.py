import os
import time
import uuid
import sqlite3

import scripts.ui as ui


def test_transfer_worker_flushes_delta(tmp_path):
    # create a unique tid and ensure a transfers row exists
    tid = str(uuid.uuid4())
    conn = sqlite3.connect(ui.DB_PATH)
    try:
        started = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        conn.execute(
            'INSERT OR REPLACE INTO transfers (tid, bytes_sent, total_bytes, completed, error, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (tid, 0, 100000, 0, None, started, None),
        )
        conn.commit()
    finally:
        conn.close()

    # enqueue a delta and wait for the background worker to flush it to DB
    delta = 12345
    ui.enqueue_transfer_delta(tid, delta)

    # poll the DB for up to 3 seconds for the flush to occur (worker flushes every ~0.25s)
    found = False
    deadline = time.time() + 3.0
    while time.time() < deadline:
        conn = sqlite3.connect(ui.DB_PATH)
        try:
            cur = conn.execute('SELECT bytes_sent FROM transfers WHERE tid = ?', (tid,))
            row = cur.fetchone()
            if row and row[0] and int(row[0]) >= delta:
                found = True
                break
        finally:
            conn.close()
        time.sleep(0.1)

    assert found, "transfer worker did not flush delta into DB in time"

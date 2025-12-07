import uuid
import sqlite3
import time
import scripts.ui as ui

DB = ui.DB_PATH


def test_enqueue_event_persists():
    tid = uuid.uuid4().hex
    meta = '{"foo": "bar"}'
    ui.enqueue_event(tid, 'test_event', meta)
    # wait for queue to be processed
    ui.EVENT_QUEUE.join()
    conn = sqlite3.connect(DB)
    try:
        cur = conn.execute('SELECT event, meta FROM events WHERE tid = ? ORDER BY id DESC LIMIT 1', (tid,))
        row = cur.fetchone()
        assert row is not None, 'event row not found'
        ev, m = row
        assert ev == 'test_event'
        assert m == meta
    finally:
        # cleanup
        conn.execute('DELETE FROM events WHERE tid = ?', (tid,))
        conn.commit()
        conn.close()

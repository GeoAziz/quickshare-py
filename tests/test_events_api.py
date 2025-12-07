import sqlite3
import time
import uuid

import scripts.ui as ui


def setup_test_event(tid=None, event='test_event', meta='{}'):
    tid = tid or str(uuid.uuid4())
    conn = sqlite3.connect(ui.DB_PATH)
    try:
        ts = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        conn.execute('INSERT INTO events (tid, event, ts, meta) VALUES (?, ?, ?, ?)', (tid, event, ts, meta))
        conn.commit()
    finally:
        conn.close()
    return tid


def test_events_limit_offset_and_filters():
    # create multiple events
    tid_a = setup_test_event(event='alpha')
    tid_b = setup_test_event(event='beta')
    tid_c = setup_test_event(event='alpha')

    # raw fetch using Flask test client
    app = ui.app.test_client()

    # fetch first page (limit=2)
    r = app.get('/events?limit=2')
    assert r.status_code == 200
    js = r.get_json()
    assert isinstance(js, list) and len(js) <= 2
    # if we got items, test cursor paging: get min id and request before that id
    if js:
        ids = [int(x['id']) for x in js]
        min_id = min(ids)
        r2 = app.get(f'/events?before={min_id}&limit=2')
        assert r2.status_code == 200
        js2 = r2.get_json()
        # any returned items should have id < min_id
        assert all(int(e['id']) < min_id for e in js2)

    # fetch by tid
    r2 = app.get(f'/events?tid={tid_a}&limit=10')
    assert r2.status_code == 200
    js2 = r2.get_json()
    assert any(e.get('tid') == tid_a for e in js2)

    # fetch by event name
    r3 = app.get('/events?event=alpha&limit=10')
    assert r3.status_code == 200
    js3 = r3.get_json()
    assert all(e.get('event') == 'alpha' for e in js3)

def test_events_counts_api():
    # prepare events
    tid_x = setup_test_event(event='cnt_test')
    tid_y = setup_test_event(event='cnt_test')
    app = ui.app.test_client()
    r = app.get(f'/events/counts?tids={tid_x},{tid_y}')
    assert r.status_code == 200
    js = r.get_json()
    assert js.get(tid_x) >= 1
    assert js.get(tid_y) >= 1

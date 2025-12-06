import time
from quickshare.discovery import DiscoveryListener, DiscoveryAnnouncer


def test_discovery_loopback_once():
    # Start a listener bound to loopback
    listener = DiscoveryListener(bind_addr='127.0.0.1', bind_port=37020, stale_timeout=2.0)
    listener.start()
    try:
        announcer = DiscoveryAnnouncer(name='tester', port=12345, target_addr='127.0.0.1', target_port=37020, interval=0.5)
        # announce once synchronously for test reliability
        announcer.announce_now()
        # give listener a little time to receive
        time.sleep(0.1)
        peers = listener.get_peers()
        assert any(p['name'] == 'tester' and p['port'] == 12345 for p in peers.values())
    finally:
        listener.stop()

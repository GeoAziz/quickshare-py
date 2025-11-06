import quickshare


def test_get_local_ip_non_empty():
    ip = quickshare.get_local_ip()
    assert isinstance(ip, str)
    assert len(ip) > 0
    # basic sanity: contains at least one dot or is localhost
    assert '.' in ip or ip == '127.0.0.1'

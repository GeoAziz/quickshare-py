#!/usr/bin/env python3
import time, os
from quickshare.control import ControlServer
from quickshare.transfer import Receiver
from quickshare.discovery import DiscoveryAnnouncer

received_dir='received'
os.makedirs(received_dir, exist_ok=True)

def handler(offer):
    filename=offer.get('filename','received_file')
    # Use absolute path to ensure Receiver writes into the intended directory
    save_path=os.path.abspath(os.path.join(received_dir, filename))
    total_chunks=int(offer.get('total_chunks',1))
    chunk_size=int(offer.get('chunk_size',1024*1024))
    r=Receiver(save_path, chunk_size, total_chunks)
    return r.handle_offer_and_receive(offer)

if __name__ == '__main__':
    srv=ControlServer(host='127.0.0.1', port=60001, handler=handler)
    srv.start()
    ann=DiscoveryAnnouncer(name='test_receiver', port=srv.port, target_addr='127.0.0.1', target_port=37020, interval=1.0)
    ann.start()
    print('Receiver ready', srv.port)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        ann.stop(); srv.stop()

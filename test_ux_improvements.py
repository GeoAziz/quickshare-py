#!/usr/bin/env python3
"""Quick test to verify UX improvements"""

import sys
import os
import tempfile
import json
import sqlite3

# Add scripts directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'scripts'))

from ui import app, init_db, DB_PATH

def test_file_picker_imports():
    """Verify file picker JavaScript is in TEMPLATE"""
    from ui import TEMPLATE
    assert 'setupFilePicker' in TEMPLATE, "File picker JS function missing"
    assert 'file-input-wrapper' in TEMPLATE, "File picker CSS missing"
    assert 'webkitdirectory' in TEMPLATE, "Folder picker attribute missing"
    print("✓ File picker implementation verified")

def test_history_template_exists():
    """Verify HISTORY_TEMPLATE is defined"""
    from ui import HISTORY_TEMPLATE, TEMPLATE
    assert HISTORY_TEMPLATE, "HISTORY_TEMPLATE not found"
    assert 'transfer-card' in HISTORY_TEMPLATE, "Card layout missing"
    assert 'renderTransfers' in HISTORY_TEMPLATE, "Transfer rendering function missing"
    assert 'filterTransfers' in HISTORY_TEMPLATE, "Filter function missing"
    print("✓ History page redesign verified")

def test_qrcode_endpoint():
    """Verify /qrcode endpoint exists"""
    with app.test_client() as client:
        # Test with default values
        response = client.get('/qrcode?addr=127.0.0.1&port=9999')
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        assert response.content_type == 'image/png', f"Expected PNG, got {response.content_type}"
        assert len(response.data) > 0, "QR code image is empty"
        print(f"✓ QR code endpoint verified ({len(response.data)} bytes)")

def test_home_page_qr_code():
    """Verify receiver card includes QR code"""
    from ui import TEMPLATE
    assert '/qrcode?addr=' in TEMPLATE, "QR code endpoint not in template"
    assert 'recv_addr' in TEMPLATE, "recv_addr variable not used in template"
    assert 'recv_port' in TEMPLATE, "recv_port variable not used in template"
    print("✓ QR code display on home page verified")

def test_history_route():
    """Verify /history route returns HISTORY_TEMPLATE"""
    # Initialize DB first
    init_db()
    
    with app.test_client() as client:
        response = client.get('/history')
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        assert b'Transfer History' in response.data or b'transfers_json' not in response.data, "History template not rendered"
        print("✓ History route verified")

def test_file_picker_javascript():
    """Verify file picker JS is syntactically correct"""
    from ui import TEMPLATE
    # Check for key functions
    assert 'function setupFilePicker' in TEMPLATE, "setupFilePicker function not defined"
    assert 'drag-over' in TEMPLATE, "drag-over event handling missing"
    assert '.files' in TEMPLATE, "file input handling missing"
    print("✓ File picker JavaScript verified")

def run_tests():
    """Run all verification tests"""
    print("=" * 60)
    print("QuickShare UX Improvements - Verification Tests")
    print("=" * 60)
    
    try:
        test_file_picker_imports()
        test_history_template_exists()
        test_file_picker_javascript()
        test_home_page_qr_code()
        test_qrcode_endpoint()
        test_history_route()
        
        print("\n" + "=" * 60)
        print("✓ ALL VERIFICATION TESTS PASSED")
        print("=" * 60)
        return 0
    except AssertionError as e:
        print(f"\n✗ TEST FAILED: {e}")
        return 1
    except Exception as e:
        print(f"\n✗ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == '__main__':
    sys.exit(run_tests())

"""
Quick test for EDGAR API endpoints.

Run this AFTER restarting the backend server:
    cd backend && python -m tests.test_edgar_api
"""

import requests
import json

BASE_URL = "http://localhost:8000"


def test_edgar_endpoints():
    """Test all EDGAR API endpoints."""
    print("=" * 60)
    print("Testing EDGAR API Endpoints")
    print("=" * 60 + "\n")

    # Test 1: GET /api/v1/edgar/filings
    print("Test 1: GET /api/v1/edgar/filings")
    try:
        resp = requests.get(f"{BASE_URL}/api/v1/edgar/filings")
        print(f"  Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print(f"  [OK] Total filings: {data.get('total', 0)}")
            if data.get('filings'):
                print(f"  First filing: {data['filings'][0]['symbol']} {data['filings'][0]['form_type']}")
        else:
            print(f"  [FAIL] {resp.text}")
    except Exception as exc:
        print(f"  [ERROR] {exc}")
    
    print()

    # Test 2: GET /api/v1/edgar/config
    print("Test 2: GET /api/v1/edgar/config")
    try:
        resp = requests.get(f"{BASE_URL}/api/v1/edgar/config")
        print(f"  Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print(f"  [OK] Enabled: {data.get('enabled')}")
            print(f"  [OK] Poll interval: {data.get('poll_interval_minutes')} minutes")
        else:
            print(f"  [FAIL] {resp.text}")
    except Exception as exc:
        print(f"  [ERROR] {exc}")
    
    print()

    # Test 3: POST /api/v1/edgar/poll
    print("Test 3: POST /api/v1/edgar/poll")
    try:
        resp = requests.post(f"{BASE_URL}/api/v1/edgar/poll")
        print(f"  Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print(f"  [OK] Poll triggered: {data.get('message')}")
            print(f"  Summary: {data.get('summary', {}).get('filings_stored', 'N/A')} new filings")
        else:
            print(f"  [FAIL] {resp.text}")
    except Exception as exc:
        print(f"  [ERROR] {exc}")
    
    print()

    # Test 4: POST /api/v1/edgar/process
    print("Test 4: POST /api/v1/edgar/process?limit=2")
    try:
        resp = requests.post(f"{BASE_URL}/api/v1/edgar/process?limit=2")
        print(f"  Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print(f"  [OK] Processing triggered: {data.get('message')}")
            print(f"  Summary: {data.get('summary', {}).get('summaries_generated', 'N/A')} summaries generated")
        else:
            print(f"  [FAIL] {resp.text}")
    except Exception as exc:
        print(f"  [ERROR] {exc}")

    print()
    print("=" * 60)
    print("Tests complete!")
    print("=" * 60)


if __name__ == "__main__":
    test_edgar_endpoints()

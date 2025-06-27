#!/usr/bin/env python3
"""
Test script for new Signal Type and Sell All management features.
This script tests the new functionality added to the Trading Signal Processor.
"""

import asyncio
import requests
import json
import time
from typing import Dict, Any

# Configuration
BASE_URL = "http://localhost:8000"
ADMIN_TOKEN = "your_admin_token_here"  # Replace with actual admin token

def test_signal_type_filtering():
    """Test signal type filtering in audit trail."""
    print("🧪 Testing signal type filtering...")
    
    # Test getting audit trail with signal type filter
    response = requests.get(f"{BASE_URL}/admin/audit-trail", params={
        "signal_type": "buy",
        "limit": 10
    })
    
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Signal type filtering works - found {len(data.get('events', []))} buy signals")
        return True
    else:
        print(f"❌ Signal type filtering failed: {response.status_code}")
        return False

def test_manual_ticker_addition():
    """Test manually adding a ticker to Sell All list."""
    print("🧪 Testing manual ticker addition...")
    
    test_ticker = "TEST123"
    
    response = requests.post(f"{BASE_URL}/admin/sell-all-queue", json={
        "token": ADMIN_TOKEN,
        "ticker": test_ticker
    })
    
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Manual ticker addition works - {data.get('message', 'Success')}")
        return True
    else:
        print(f"❌ Manual ticker addition failed: {response.status_code}")
        if response.status_code == 403:
            print("   Make sure to set the correct ADMIN_TOKEN in this script")
        return False

def test_individual_sell_order():
    """Test creating an individual sell order."""
    print("🧪 Testing individual sell order...")
    
    test_ticker = "SELL123"
    
    response = requests.post(f"{BASE_URL}/admin/order/sell-individual", json={
        "token": ADMIN_TOKEN,
        "ticker": test_ticker
    })
    
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Individual sell order works - {data.get('message', 'Success')}")
        return True
    else:
        print(f"❌ Individual sell order failed: {response.status_code}")
        if response.status_code == 403:
            print("   Make sure to set the correct ADMIN_TOKEN in this script")
        return False

def test_sell_all_queue():
    """Test getting the Sell All queue."""
    print("🧪 Testing Sell All queue retrieval...")
    
    response = requests.get(f"{BASE_URL}/admin/sell-all-queue")
    
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Sell All queue retrieval works - {data.get('count', 0)} tickers in queue")
        return True
    else:
        print(f"❌ Sell All queue retrieval failed: {response.status_code}")
        return False

def test_top_n_tickers():
    """Test getting Top-N tickers."""
    print("🧪 Testing Top-N tickers endpoint...")
    
    response = requests.get(f"{BASE_URL}/admin/top-n-tickers")
    
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Top-N tickers endpoint works - {data.get('count', 0)} approved tickers")
        return True
    else:
        print(f"❌ Top-N tickers endpoint failed: {response.status_code}")
        return False

def test_audit_trail_with_signal_type():
    """Test audit trail with signal type filter."""
    print("🧪 Testing audit trail with signal type filter...")
    
    response = requests.get(f"{BASE_URL}/admin/audit-trail", params={
        "limit": 5,
        "signal_type": "manual_sell"
    })
    
    if response.status_code == 200:
        data = response.json()
        events = data.get('events', [])
        print(f"✅ Audit trail with signal type filter works - {len(events)} manual_sell events found")
        
        # Check if signal_type is present in events
        if events:
            for event in events[:2]:  # Check first 2 events
                if 'signal_type' in event:
                    print(f"   📋 Event has signal_type: {event['signal_type']}")
                else:
                    print(f"   ⚠️  Event missing signal_type field")
        
        return True
    else:
        print(f"❌ Audit trail with signal type filter failed: {response.status_code}")
        return False

def test_health_check():
    """Test basic health check to ensure server is running."""
    print("🧪 Testing health check...")
    
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=5)
        if response.status_code == 200:
            print("✅ Server is running and healthy")
            return True
        else:
            print(f"❌ Health check failed: {response.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"❌ Cannot connect to server: {e}")
        print("   Make sure the server is running on http://localhost:8000")
        return False

def main():
    """Run all tests."""
    print("🚀 Starting tests for new Signal Type and Sell All features...")
    print("=" * 60)
    
    if ADMIN_TOKEN == "your_admin_token_here":
        print("⚠️  WARNING: Please set the correct ADMIN_TOKEN in this script")
        print("   Some tests that require authentication will fail")
        print()
    
    tests = [
        ("Health Check", test_health_check),
        ("Signal Type Filtering", test_signal_type_filtering),
        ("Top-N Tickers", test_top_n_tickers),
        ("Sell All Queue", test_sell_all_queue),
        ("Audit Trail with Signal Type", test_audit_trail_with_signal_type),
        ("Manual Ticker Addition", test_manual_ticker_addition),
        ("Individual Sell Order", test_individual_sell_order),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        print(f"📋 Running: {test_name}")
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ {test_name} crashed: {e}")
            results.append((test_name, False))
        
        print()  # Empty line between tests
        time.sleep(0.5)  # Small delay between tests
    
    # Summary
    print("=" * 60)
    print("📊 TEST RESULTS SUMMARY:")
    print("=" * 60)
    
    passed = 0
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} {test_name}")
        if result:
            passed += 1
    
    print("=" * 60)
    print(f"📈 OVERALL: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! New features are working correctly.")
    else:
        print("⚠️  Some tests failed. Check the output above for details.")
    
    return passed == total

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)

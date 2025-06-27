#!/usr/bin/env python3
"""
Quick test script to verify the sell functionality is working.
"""

import requests
import json

BASE_URL = "http://localhost:8000"
ADMIN_TOKEN = "your_admin_token_here"  # Replace with your actual token

def test_individual_sell():
    """Test individual sell order."""
    print("Testing individual sell order...")
    
    response = requests.post(f"{BASE_URL}/admin/order/sell-individual", json={
        "token": ADMIN_TOKEN,
        "ticker": "TEST"
    })
    
    print(f"Status Code: {response.status_code}")
    if response.status_code == 200:
        print("✅ Individual sell order works!")
        print(f"Response: {response.json()}")
    else:
        print("❌ Individual sell order failed!")
        print(f"Response: {response.text}")

def test_sell_all():
    """Test sell all order."""
    print("\nTesting sell all order...")
    
    # First add a ticker to the sell all list
    response = requests.post(f"{BASE_URL}/admin/sell-all-queue", json={
        "token": ADMIN_TOKEN,
        "ticker": "TESTALL"
    })
    
    if response.status_code == 200:
        print("✅ Added ticker to sell all list")
        
        # Now execute sell all
        response = requests.post(f"{BASE_URL}/admin/order/sell-all", json={
            "token": ADMIN_TOKEN
        })
        
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            print("✅ Sell all order works!")
            print(f"Response: {response.json()}")
        else:
            print("❌ Sell all order failed!")
            print(f"Response: {response.text}")
    else:
        print("❌ Failed to add ticker to sell all list")
        print(f"Response: {response.text}")

if __name__ == "__main__":
    if ADMIN_TOKEN == "your_admin_token_here":
        print("⚠️  Please set the correct ADMIN_TOKEN in the script")
        exit(1)
    
    print("🧪 Testing sell functionality...")
    print("=" * 50)
    
    try:
        test_individual_sell()
        test_sell_all()
    except requests.exceptions.ConnectionError:
        print("❌ Cannot connect to server. Make sure it's running on localhost:8000")
    except Exception as e:
        print(f"❌ Test failed with error: {e}")

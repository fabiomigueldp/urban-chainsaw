#!/usr/bin/env python3
"""
Signal Reprocessing Engine - Installation Verification Script

This script verifies that all components of the enhanced Signal Reprocessing Engine
are properly installed and functioning correctly.
"""

import sys
import traceback
from typing import Dict, Any

def test_imports() -> bool:
    """Test that all required modules can be imported."""
    print("🔍 Testing imports...")
    try:
        from signal_reprocessing_engine import (
            SignalReprocessingEngine,
            SignalValidator, 
            SignalReconstructor,
            ReprocessingMetrics,
            ReprocessingStatus,
            SignalReprocessingOutcome
        )
        print("✅ All Signal Reprocessing Engine components imported successfully")
        return True
    except Exception as e:
        print(f"❌ Import failed: {e}")
        traceback.print_exc()
        return False

def test_validator() -> bool:
    """Test SignalValidator functionality."""
    print("\n🔍 Testing SignalValidator...")
    try:
        from signal_reprocessing_engine import SignalValidator
        
        validator = SignalValidator()
        
        # Test BUY signal detection
        buy_signals = [
            {"side": "buy", "signal_type": "", "original_signal": {}},
            {"side": "BUY", "signal_type": "", "original_signal": {}},
            {"side": " buy ", "signal_type": "", "original_signal": {}},
            {"side": "", "signal_type": "buy", "original_signal": {}},
            {"side": "", "signal_type": "", "original_signal": {"action": "long"}},
            {"side": "", "signal_type": "", "original_signal": {}},  # Default case
        ]
        
        for i, signal in enumerate(buy_signals):
            result = validator.is_buy_signal(signal)
            if not result:
                print(f"❌ BUY signal test {i+1} failed: {signal}")
                return False
        
        # Test SELL signal detection
        sell_signals = [
            {"side": "sell", "signal_type": "", "original_signal": {}},
            {"side": "SELL", "signal_type": "", "original_signal": {}},
            {"side": "", "signal_type": "sell", "original_signal": {}},
            {"side": "", "signal_type": "", "original_signal": {"action": "exit"}},
        ]
        
        for i, signal in enumerate(sell_signals):
            result = validator.is_buy_signal(signal)
            if result:
                print(f"❌ SELL signal test {i+1} failed (should be False): {signal}")
                return False
        
        # Test validation
        valid_signal = {
            "signal_id": "test-123",
            "ticker": "AAPL", 
            "normalised_ticker": "AAPL",
            "original_signal": {"ticker": "AAPL"}
        }
        is_valid, error = validator.validate_signal_data(valid_signal)
        if not is_valid:
            print(f"❌ Valid signal validation failed: {error}")
            return False
        
        # Test invalid signal
        invalid_signal = {"ticker": "AAPL"}  # Missing signal_id
        is_valid, error = validator.validate_signal_data(invalid_signal)
        if is_valid:
            print("❌ Invalid signal validation should have failed")
            return False
        
        print("✅ SignalValidator tests passed")
        return True
        
    except Exception as e:
        print(f"❌ SignalValidator test failed: {e}")
        traceback.print_exc()
        return False

def test_metrics() -> bool:
    """Test ReprocessingMetrics functionality."""
    print("\n🔍 Testing ReprocessingMetrics...")
    try:
        from signal_reprocessing_engine import ReprocessingMetrics
        
        metrics = ReprocessingMetrics()
        
        # Test initial state
        if metrics.get_success_rate() != 0.0:
            print("❌ Initial success rate should be 0.0")
            return False
        
        # Test with data
        metrics.signals_processed = 10
        metrics.signals_successful = 8
        if metrics.get_success_rate() != 80.0:
            print("❌ Success rate calculation incorrect")
            return False
        
        # Test reset
        metrics.tickers_processed.add("AAPL")
        metrics.reset()
        if metrics.signals_processed != 0 or len(metrics.tickers_processed) != 0:
            print("❌ Metrics reset failed")
            return False
        
        print("✅ ReprocessingMetrics tests passed")
        return True
        
    except Exception as e:
        print(f"❌ ReprocessingMetrics test failed: {e}")
        traceback.print_exc()
        return False

def test_finviz_integration() -> bool:
    """Test integration with FinvizEngine."""
    print("\n🔍 Testing FinvizEngine integration...")
    try:
        # Check if the enhanced method exists
        with open("finviz_engine.py", "r") as f:
            content = f.read()
            if "_legacy_reprocess_signals_for_new_tickers" not in content:
                print("❌ Legacy fallback method not found in finviz_engine.py")
                return False
            
            if "SignalReprocessingEngine" not in content:
                print("❌ SignalReprocessingEngine import not found in finviz_engine.py")
                return False
        
        print("✅ FinvizEngine integration verified")
        return True
        
    except Exception as e:
        print(f"❌ FinvizEngine integration test failed: {e}")
        return False

def test_api_endpoints() -> bool:
    """Test that new API endpoints are properly defined."""
    print("\n🔍 Testing API endpoints...")
    try:
        with open("main.py", "r") as f:
            content = f.read()
            
            if "/admin/reprocessing/health" not in content:
                print("❌ Health endpoint not found in main.py")
                return False
            
            if "/admin/reprocessing/trigger" not in content:
                print("❌ Manual trigger endpoint not found in main.py")
                return False
            
            if "get_reprocessing_health" not in content:
                print("❌ Health function not found in main.py")
                return False
        
        print("✅ API endpoints verified")
        return True
        
    except Exception as e:
        print(f"❌ API endpoints test failed: {e}")
        return False

def test_admin_interface() -> bool:
    """Test admin interface enhancements."""
    print("\n🔍 Testing admin interface...")
    try:
        with open("templates/admin.html", "r") as f:
            content = f.read()
            
            if "reprocessingHealthStatus" not in content:
                print("❌ Health status element not found in admin.html")
                return False
            
            if "loadReprocessingHealth" not in content:
                print("❌ Health loading function not found in admin.html")
                return False
            
            if "updateReprocessingHealthDisplay" not in content:
                print("❌ Health display function not found in admin.html")
                return False
        
        print("✅ Admin interface enhancements verified")
        return True
        
    except Exception as e:
        print(f"❌ Admin interface test failed: {e}")
        return False

def main():
    """Run all verification tests."""
    print("🚀 Signal Reprocessing Engine - Installation Verification")
    print("=" * 60)
    
    tests = [
        ("Module Imports", test_imports),
        ("Signal Validator", test_validator), 
        ("Metrics System", test_metrics),
        ("Finviz Integration", test_finviz_integration),
        ("API Endpoints", test_api_endpoints),
        ("Admin Interface", test_admin_interface),
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        print(f"\n📋 Running test: {test_name}")
        try:
            if test_func():
                passed += 1
            else:
                print(f"❌ Test failed: {test_name}")
        except Exception as e:
            print(f"❌ Test error: {test_name} - {e}")
    
    print(f"\n{'='*60}")
    print(f"📊 Test Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 ALL TESTS PASSED! Signal Reprocessing Engine is ready for use.")
        print("\n📚 Next steps:")
        print("1. Start the application: python run.py")
        print("2. Access admin interface: http://localhost/admin")
        print("3. Configure reprocessing in the admin panel")
        print("4. Monitor health status at: http://localhost/admin/reprocessing/health")
        return True
    else:
        print("⚠️  Some tests failed. Please review the errors above.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)

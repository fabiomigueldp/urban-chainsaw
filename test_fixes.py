#!/usr/bin/env python3
"""
Test script for testing the system info and reprocess configuration fixes.
"""

import asyncio
import sys
sys.path.append('.')
from main import get_system_info, load_finviz_config

async def test_system_info():
    """Test system info endpoint structure."""
    try:
        result = await get_system_info()
        print('System info structure:')
        print(f'Has system_info: {"system_info" in result}')
        if 'system_info' in result:
            sys_info = result['system_info']
            print(f'  - finviz_engine_paused: {sys_info.get("finviz_engine_paused")}')
            print(f'  - webhook_rate_limiter_paused: {sys_info.get("webhook_rate_limiter_paused")}')
            print(f'  - reprocess_enabled: {sys_info.get("reprocess_enabled")}')
            print(f'  - finviz_ticker_count: {sys_info.get("finviz_ticker_count")}')
        
        print(f'Has metrics: {"metrics" in result}')
        print(f'Has queue: {"queue" in result}')
        
        return True
    except Exception as e:
        print(f'Error testing system info: {e}')
        return False

def test_finviz_config():
    """Test finviz config loading."""
    try:
        finviz_config = load_finviz_config()
        print('\nFinviz config:')
        print(f'  - reprocess_enabled: {finviz_config.get("reprocess_enabled")}')
        print(f'  - reprocess_window_seconds: {finviz_config.get("reprocess_window_seconds")}')
        print(f'  - finviz_url: {finviz_config.get("finviz_url")}')
        print(f'  - top_n: {finviz_config.get("top_n")}')
        print(f'  - refresh_interval_sec: {finviz_config.get("refresh_interval_sec")}')
        return True
    except Exception as e:
        print(f'Error testing finviz config: {e}')
        return False

async def main():
    """Run all tests."""
    print("Testing system info and reprocess configuration fixes...")
    print("=" * 60)
    
    # Test finviz config first (synchronous)
    config_ok = test_finviz_config()
    
    # Test system info (asynchronous)
    system_ok = await test_system_info()
    
    print("\n" + "=" * 60)
    if config_ok and system_ok:
        print("✅ All tests passed! The fixes should work correctly.")
    else:
        print("❌ Some tests failed. Please check the errors above.")

if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
import httpx
import json

def test_system_info():
    try:
        with httpx.Client() as client:
            response = client.get('http://localhost:8000/admin/system-info')
            if response.status_code == 200:
                data = response.json()
                print('=== System Info Response ===')
                if 'system_info' in data:
                    system_info = data['system_info']
                    print(f'finviz_engine_paused: {system_info.get("finviz_engine_paused")}')
                    print(f'webhook_rate_limiter_paused: {system_info.get("webhook_rate_limiter_paused")}')
                    print(f'reprocess_enabled: {system_info.get("reprocess_enabled")}')
                else:
                    print('No system_info found in response')
                    print('Response keys:', list(data.keys()))
                
                # Check root level fields for backward compatibility
                print('\n=== Root Level Fields ===')
                print(f'finviz_engine_paused (root): {data.get("finviz_engine_paused")}')
                print(f'webhook_rate_limiter_paused (root): {data.get("webhook_rate_limiter_paused")}')
                print(f'reprocess_enabled (root): {data.get("reprocess_enabled")}')
                
            else:
                print(f'Error: Status {response.status_code}')
                print(f'Response: {response.text}')
    except Exception as e:
        print(f'Error: {e}')

if __name__ == "__main__":
    test_system_info()

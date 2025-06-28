#!/usr/bin/env python3
"""
Script para testar as correções feitas na interface admin.
"""

import httpx
import json
import time

BASE_URL = "http://localhost:8000"
ADMIN_TOKEN = "YOUR_SECRET_TOKEN_HERE"  # Substitua pelo token real

def test_system_status():
    """Testa se o endpoint /admin/system-info retorna informações corretas sobre pause status"""
    try:
        with httpx.Client() as client:
            response = client.get(f"{BASE_URL}/admin/system-info")
            if response.status_code == 200:
                data = response.json()
                print("=== System Status ===")
                system_info = data.get("system_info", {})
                print(f"  - finviz_engine_paused: {system_info.get('finviz_engine_paused')}")
                print(f"  - webhook_rate_limiter_paused: {system_info.get('webhook_rate_limiter_paused')}")
                print(f"  - reprocess_enabled: {system_info.get('reprocess_enabled')}")
                
                # Também check campos de compatibilidade
                print(f"  - finviz_engine_paused (root): {data.get('finviz_engine_paused')}")
                print(f"  - webhook_rate_limiter_paused (root): {data.get('webhook_rate_limiter_paused')}")
                print(f"  - reprocess_enabled (root): {data.get('reprocess_enabled')}")
                
                return True
            else:
                print(f"Erro: Status {response.status_code}")
                return False
    except Exception as e:
        print(f"Erro na requisição: {e}")
        return False

def test_pause_engine():
    """Testa pausar o engine"""
    try:
        with httpx.Client() as client:
            payload = {"token": ADMIN_TOKEN}
            response = client.post(f"{BASE_URL}/admin/engine/pause", json=payload)
            if response.status_code == 204:
                print("✅ Engine pausado com sucesso")
                return True
            else:
                print(f"❌ Erro pausando engine: Status {response.status_code}, Response: {response.text}")
                return False
    except Exception as e:
        print(f"❌ Erro na requisição de pause: {e}")
        return False

def test_pause_rate_limiter():
    """Testa pausar o rate limiter"""
    try:
        with httpx.Client() as client:
            payload = {"token": ADMIN_TOKEN}
            response = client.post(f"{BASE_URL}/admin/webhook-rate-limiter/pause", json=payload)
            if response.status_code == 204:
                print("✅ Rate limiter pausado com sucesso")
                return True
            else:
                print(f"❌ Erro pausando rate limiter: Status {response.status_code}, Response: {response.text}")
                return False
    except Exception as e:
        print(f"❌ Erro na requisição de pause do rate limiter: {e}")
        return False

def test_resume_engine():
    """Testa retomar o engine"""
    try:
        with httpx.Client() as client:
            payload = {"token": ADMIN_TOKEN}
            response = client.post(f"{BASE_URL}/admin/engine/resume", json=payload)
            if response.status_code == 204:
                print("✅ Engine retomado com sucesso")
                return True
            else:
                print(f"❌ Erro retomando engine: Status {response.status_code}, Response: {response.text}")
                return False
    except Exception as e:
        print(f"❌ Erro na requisição de resume: {e}")
        return False

def test_resume_rate_limiter():
    """Testa retomar o rate limiter"""
    try:
        with httpx.Client() as client:
            payload = {"token": ADMIN_TOKEN}
            response = client.post(f"{BASE_URL}/admin/webhook-rate-limiter/resume", json=payload)
            if response.status_code == 204:
                print("✅ Rate limiter retomado com sucesso")
                return True
            else:
                print(f"❌ Erro retomando rate limiter: Status {response.status_code}, Response: {response.text}")
                return False
    except Exception as e:
        print(f"❌ Erro na requisição de resume do rate limiter: {e}")
        return False

def main():
    print("🔧 Testando correções da interface admin...")
    print()
    
    # Teste inicial do status
    print("1. Verificando status inicial...")
    test_system_status()
    print()
    
    # Teste de pause do engine
    print("2. Testando pause do FinvizEngine...")
    if test_pause_engine():
        print("   Aguardando 3 segundos...")
        time.sleep(3)
        print("   Verificando status após pause...")
        test_system_status()
    print()
    
    # Teste de pause do rate limiter
    print("3. Testando pause do WebhookRateLimiter...")
    if test_pause_rate_limiter():
        print("   Aguardando 3 segundos...")
        time.sleep(3)
        print("   Verificando status após pause...")
        test_system_status()
    print()
    
    # Teste de resume
    print("4. Testando resume do FinvizEngine...")
    if test_resume_engine():
        print("   Aguardando 3 segundos...")
        time.sleep(3)
        print("   Verificando status após resume...")
        test_system_status()
    print()
    
    # Teste de resume do rate limiter
    print("5. Testando resume do WebhookRateLimiter...")
    if test_resume_rate_limiter():
        print("   Aguardando 3 segundos...")
        time.sleep(3)
        print("   Verificando status final...")
        test_system_status()
    print()
    
    print("🏁 Testes concluídos!")

if __name__ == "__main__":
    main()

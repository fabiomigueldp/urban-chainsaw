#!/usr/bin/env python3
"""
Teste rápido para validar se os endpoints implementados estão funcionando.
Execute este script para testar as funcionalidades da Fase 2.
"""

import asyncio
import aiohttp
import json
import sys

BASE_URL = "http://localhost:8000"
ADMIN_TOKEN = "your_admin_token_here"  # Substituir pelo token real

async def test_orders_endpoints():
    """Testa os endpoints de ordens implementados."""
    
    async with aiohttp.ClientSession() as session:
        print("🧪 Testando endpoints de ordens...")
        
        # Teste 1: GET /admin/orders
        try:
            async with session.get(f"{BASE_URL}/admin/orders") as response:
                if response.status == 200:
                    data = await response.json()
                    print(f"✅ GET /admin/orders: {len(data.get('orders', []))} ordens encontradas")
                else:
                    print(f"❌ GET /admin/orders: Erro {response.status}")
        except Exception as e:
            print(f"❌ GET /admin/orders: Exceção {e}")
        
        # Teste 2: GET /admin/orders/stats
        try:
            async with session.get(f"{BASE_URL}/admin/orders/stats") as response:
                if response.status == 200:
                    data = await response.json()
                    stats = data.get('stats', {})
                    print(f"✅ GET /admin/orders/stats: {stats.get('open', 0)} abertas, {stats.get('closed_today', 0)} fechadas hoje")
                else:
                    print(f"❌ GET /admin/orders/stats: Erro {response.status}")
        except Exception as e:
            print(f"❌ GET /admin/orders/stats: Exceção {e}")
        
        # Teste 3: POST /admin/sell-all-queue (adicionar ticker manual)
        try:
            payload = {
                "token": ADMIN_TOKEN,
                "ticker": "TEST"
            }
            async with session.post(f"{BASE_URL}/admin/sell-all-queue", json=payload) as response:
                if response.status in [201, 200]:
                    data = await response.json()
                    print(f"✅ POST /admin/sell-all-queue: Ticker TEST adicionado (position_id: {data.get('position_id')})")
                elif response.status == 403:
                    print("⚠️ POST /admin/sell-all-queue: Token inválido (esperado)")
                else:
                    print(f"❌ POST /admin/sell-all-queue: Erro {response.status}")
        except Exception as e:
            print(f"❌ POST /admin/sell-all-queue: Exceção {e}")

async def test_database_clear():
    """Testa o endpoint de limpeza do banco (corrigido)."""
    
    async with aiohttp.ClientSession() as session:
        print("\n🧪 Testando limpeza do banco...")
        
        try:
            payload = {"token": ADMIN_TOKEN}
            async with session.post(f"{BASE_URL}/admin/clear-database", json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    print(f"✅ POST /admin/clear-database: {data.get('deleted_signals', 0)} signals, {data.get('deleted_events', 0)} eventos, {data.get('deleted_positions', 0)} posições deletadas")
                elif response.status == 403:
                    print("⚠️ POST /admin/clear-database: Token inválido (esperado)")
                else:
                    print(f"❌ POST /admin/clear-database: Erro {response.status}")
        except Exception as e:
            print(f"❌ POST /admin/clear-database: Exceção {e}")

async def main():
    """Função principal de teste."""
    print("🚀 Iniciando testes dos endpoints implementados...")
    print(f"📍 URL Base: {BASE_URL}")
    print("⚠️ Certifique-se de que o servidor está rodando e substitua o ADMIN_TOKEN")
    print("-" * 60)
    
    await test_orders_endpoints()
    await test_database_clear()
    
    print("-" * 60)
    print("✅ Testes concluídos!")
    print("\n📋 Próximos passos:")
    print("1. Verificar interface web em http://localhost:8000/admin")
    print("2. Testar funcionalidades via frontend")
    print("3. Verificar logs do servidor para erros")
    print("4. Proceder com Fase 3 se tudo estiver funcionando")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        ADMIN_TOKEN = sys.argv[1]
        print(f"Token fornecido: {ADMIN_TOKEN[:10]}...")
    
    asyncio.run(main())

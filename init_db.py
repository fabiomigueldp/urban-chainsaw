#!/usr/bin/env python3
"""
Script para inicializar o banco de dados do Trading Signal Processor.
Abordagem SIMPLES: Apaga e recria tudo do zero.
"""

import asyncio
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine
from database.simple_models import Base
from config import settings

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def recreate_database():
    """Apaga e recria o banco de dados do zero."""
    print("🗑️  RECRIANDO BANCO DE DADOS DO ZERO")
    print("=" * 50)
    
    # Extrair informações da URL do banco
    db_url = str(settings.DATABASE_URL)
    
    # URL para conectar ao servidor PostgreSQL (sem especificar o banco)
    server_url = db_url.replace("/trading_signals", "/postgres")
    
    print(f"📡 Conectando ao servidor: {server_url}")
    
    # Criar engine para conectar ao servidor
    engine = create_async_engine(server_url)
    
    try:
        async with engine.begin() as conn:
            # 1. Terminar conexões ativas
            print("🔌 Terminando conexões ativas...")
            await conn.execute(text("""
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = 'trading_signals' AND pid <> pg_backend_pid()
            """))
            
            # 2. Dropar banco se existir
            print("🗑️  Apagando banco 'trading_signals'...")
            await conn.execute(text("DROP DATABASE IF EXISTS trading_signals"))
            
            # 3. Criar banco novo
            print("🏗️  Criando banco 'trading_signals'...")
            await conn.execute(text("CREATE DATABASE trading_signals"))
            
        print("✅ Banco recriado com sucesso!")
        
    except Exception as e:
        print(f"❌ Erro ao recriar banco: {e}")
        raise
    finally:
        await engine.dispose()

async def create_tables():
    """Cria todas as tabelas no banco novo."""
    print("\n🏗️  CRIANDO TABELAS")
    print("=" * 30)
    
    # Conectar ao banco novo
    engine = create_async_engine(str(settings.DATABASE_URL))
    
    try:
        async with engine.begin() as conn:
            # Criar todas as tabelas definidas nos modelos
            await conn.run_sync(Base.metadata.create_all)
            
        print("✅ Tabelas criadas:")
        print("   📊 signals")
        print("   📝 signal_events")
        
    except Exception as e:
        print(f"❌ Erro ao criar tabelas: {e}")
        raise
    finally:
        await engine.dispose()

async def verify_schema():
    """Verifica se o schema foi criado corretamente."""
    print("\n🔍 VERIFICANDO SCHEMA")
    print("=" * 25)
    
    engine = create_async_engine(str(settings.DATABASE_URL))
    
    try:
        async with engine.begin() as conn:
            # Verificar tabelas
            result = await conn.execute(text("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
                ORDER BY table_name
            """))
            tables = [row[0] for row in result]
            
            print("📊 Tabelas encontradas:")
            for table in tables:
                print(f"   ✅ {table}")
            
            # Verificar colunas da tabela signals
            result = await conn.execute(text("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns 
                WHERE table_name = 'signals'
                ORDER BY ordinal_position
            """))
            
            print(f"\n📋 Colunas da tabela 'signals':")
            for row in result:
                nullable = "NULL" if row[2] == "YES" else "NOT NULL"
                print(f"   📝 {row[0]} ({row[1]}) {nullable}")
                
    except Exception as e:
        print(f"❌ Erro ao verificar schema: {e}")
        raise
    finally:
        await engine.dispose()

async def test_basic_operations():
    """Testa operações básicas no banco."""
    print("\n🧪 TESTANDO OPERAÇÕES BÁSICAS")
    print("=" * 35)
    
    from database.DBManager import db_manager
    from database.simple_models import SignalStatusEnum
    
    try:
        # Inicializar DBManager
        db_manager.initialize(str(settings.DATABASE_URL))
        
        # Teste 1: Inserir um sinal de teste
        print("📥 Inserindo sinal de teste...")
        
        # Simular um sinal
        class MockSignal:
            signal_id = "test-init-123"
            ticker = "AAPL"
            side = "BUY"
            price = 150.0
            
            def normalised_ticker(self):
                return self.ticker.upper()
            
            def dict(self):
                return {
                    "signal_id": self.signal_id,
                    "ticker": self.ticker,
                    "side": self.side,
                    "price": self.price
                }
        
        mock_signal = MockSignal()
        signal_id = await db_manager.create_signal_with_initial_event(mock_signal)
        print(f"   ✅ Sinal criado: {signal_id}")
        
        # Teste 2: Adicionar evento
        print("📝 Adicionando evento de teste...")
        success = await db_manager.log_signal_event(
            signal_id=signal_id,
            event_type=SignalStatusEnum.APPROVED,
            details="Teste de aprovação"
        )
        print(f"   ✅ Evento adicionado: {success}")
        
        # Teste 3: Consultar métricas
        print("📊 Consultando métricas...")
        analytics = await db_manager.get_system_analytics()
        print(f"   ✅ Total de sinais: {analytics['overview']['total_signals']}")
        
        print("\n🎉 TODOS OS TESTES PASSARAM!")
        
    except Exception as e:
        print(f"❌ Erro nos testes: {e}")
        raise
    finally:
        await db_manager.close()

async def main():
    """Executa a inicialização completa do banco."""
    print("🚀 INICIALIZAÇÃO DO BANCO DE DADOS")
    print("🔄 Abordagem: RECRIAR DO ZERO (mais simples que migrações)")
    print("=" * 60)
    
    try:
        # 1. Recriar banco
        await recreate_database()
        
        # 2. Criar tabelas
        await create_tables()
        
        # 3. Verificar schema
        await verify_schema()
        
        # 4. Testar operações
        await test_basic_operations()
        
        print("\n" + "=" * 60)
        print("🎯 BANCO INICIALIZADO COM SUCESSO!")
        print("✅ Schema híbrido aplicado (enums → strings)")
        print("✅ Tabelas criadas")
        print("✅ Operações básicas testadas")
        print("🚀 Sistema pronto para uso!")
        
    except Exception as e:
        print(f"\n❌ FALHA NA INICIALIZAÇÃO: {e}")
        print("🔧 Verifique as configurações do banco em config.py")

if __name__ == "__main__":
    asyncio.run(main())

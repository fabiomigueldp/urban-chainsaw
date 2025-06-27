#!/usr/bin/env python3
"""
Script to initialize the Trading Signal Processor database.
SIMPLE approach: Deletes and recreates everything from scratch.
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
    """Deletes and recreates the database from scratch."""
    print("🗑️  RECREATING DATABASE FROM SCRATCH")
    print("=" * 50)
    
    # Extract information from database URL
    db_url = str(settings.DATABASE_URL)
    
    # URL to connect to PostgreSQL server (without specifying the database)
    server_url = db_url.replace("/trading_signals", "/postgres")
    
    print(f"📡 Conectando ao servidor: {server_url}")
    
    # Create engine to connect to server
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
            
            # 2. Drop database if it exists
            print("🗑️  Dropping database 'trading_signals'...")
            await conn.execute(text("DROP DATABASE IF EXISTS trading_signals"))
            
            # 3. Create new database
            print("🏗️  Creating database 'trading_signals'...")
            await conn.execute(text("CREATE DATABASE trading_signals"))
            
        print("✅ Database recreated successfully!")
        
    except Exception as e:
        print(f"❌ Error recreating database: {e}")
        raise
    finally:
        await engine.dispose()

async def create_tables():
    """Creates all tables in the new database."""
    print("\n🏗️  CREATING TABLES")
    print("=" * 30)
    
    # Connect to the new database
    engine = create_async_engine(str(settings.DATABASE_URL))
    
    try:
        async with engine.begin() as conn:
            # Create all tables defined in the models
            await conn.run_sync(Base.metadata.create_all)
            
        print("✅ Tables created:")
        print("   📊 signals")
        print("   📝 signal_events")
        
    except Exception as e:
        print(f"❌ Error creating tables: {e}")
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
            # Check tables
            result = await conn.execute(text("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
                ORDER BY table_name
            """))
            tables = [row[0] for row in result]
            
            print("📊 Tables found:")
            for table in tables:
                print(f"   ✅ {table}")
            
            # Check columns of the signals table
            result = await conn.execute(text("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns 
                WHERE table_name = 'signals'
                ORDER BY ordinal_position
            """))
            
            print(f"\n📋 Columns of table 'signals':")
            for row in result:
                nullable = "NULL" if row[2] == "YES" else "NOT NULL"
                print(f"   📝 {row[0]} ({row[1]}) {nullable}")
                
    except Exception as e:
        print(f"❌ Error checking schema: {e}")
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
    """Executes complete database initialization."""
    print("🚀 DATABASE INITIALIZATION")
    print("🔄 Approach: RECREATE FROM SCRATCH (simpler than migrations)")
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

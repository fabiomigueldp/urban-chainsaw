"""Simple database initialization - creates tables for new signals only."""

from sqlalchemy.ext.asyncio import create_async_engine
from database.simple_models import Base
import logging

_logger = logging.getLogger("db_init")

async def init_database(database_url: str):
    """Initialize database tables for new signals."""
    _logger.info("Creating database tables for signal tracking...")
    engine = create_async_engine(database_url, echo=False)
    
    try:
        async with engine.begin() as conn:
            # Create tables only if they don't exist (preserve existing data)
            await conn.run_sync(Base.metadata.create_all)
            _logger.info("✅ Database tables created successfully (preserving existing data)")
            
    except Exception as e:
        _logger.error(f"❌ Error creating database tables: {e}")
        raise
    finally:
        await engine.dispose()

async def check_database_health(database_url: str) -> bool:
    """Quick health check of database connection."""
    engine = create_async_engine(database_url)
    
    try:
        async with engine.begin() as conn:
            await conn.execute("SELECT 1")
            return True
    except Exception as e:
        _logger.error(f"Database health check failed: {e}")
        return False
    finally:
        await engine.dispose()

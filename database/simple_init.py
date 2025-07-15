"""Simple database initialization - creates tables for new signals only."""

from sqlalchemy.ext.asyncio import create_async_engine
from database.simple_models import Base
import logging

_logger = logging.getLogger("db_init")

async def init_database(database_url: str):
    """Initialize database tables for signal tracking."""
    _logger.info("Creating database tables for signal tracking...")
    engine = create_async_engine(database_url, echo=False)
    
    try:
        async with engine.begin() as conn:
            # Create tables only if they don't exist (preserve existing data)
            await conn.run_sync(Base.metadata.create_all)
            _logger.info("✅ Database tables created successfully (preserving existing data)")
            
            # Create unique constraint for active Finviz URLs
            await conn.execute("""
                CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_finviz_urls_active 
                ON finviz_urls (is_active) 
                WHERE is_active = TRUE
            """)
            _logger.info("✅ Database constraints created successfully")
            
    except Exception as e:
        _logger.error(f"❌ Error creating database tables: {e}")
        raise
    finally:
        await engine.dispose()

async def ensure_default_finviz_url(db_manager):
    """Ensures that there is always at least one active URL in the system."""
    try:
        # Check if any URLs exist
        urls_count = await db_manager.count_finviz_urls()
        
        if urls_count == 0:
            # Create default strategy if none exist
            default_strategy = {
                "name": "Default Strategy",
                "url": "https://finviz.com/screener.ashx?v=111&ft=4",
                "description": "Default system strategy with basic configuration",
                "top_n": 100,
                "refresh_interval_sec": 10,
                "reprocess_enabled": False,
                "reprocess_window_seconds": 300,
                "respect_sell_chronology_enabled": True,
                "sell_chronology_window_seconds": 300,
                "is_active": True
            }
            await db_manager.create_finviz_url(**default_strategy)
            _logger.info("✅ Created default Finviz strategy preset")
        else:
            # Ensure at least one URL is active
            active_url = await db_manager.get_active_finviz_url()
            if not active_url:
                # If none is active, activate the first one
                first_url = await db_manager.get_first_finviz_url()
                if first_url:
                    await db_manager.set_active_finviz_url(first_url['id'])
                    _logger.info(f"✅ Activated first URL: {first_url['name']}")
    except Exception as e:
        _logger.error(f"❌ Error ensuring default URL: {e}")
        raise

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

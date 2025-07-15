"""
Database initialization utility for Trading Signal Processor.
Ensures default Finviz strategy exists and database is properly set up.
"""

import asyncio
import logging
from database.DBManager import db_manager
from config import settings

_logger = logging.getLogger("db_init")

async def ensure_default_finviz_url():
    """Ensures that there's always at least one active Finviz strategy in the system."""
    try:
        # Check if any URLs exist in database
        urls_count = await db_manager.count_finviz_urls()
        
        if urls_count == 0:
            # Create default strategy with complete configuration
            default_strategy = {
                "name": "Default Strategy",
                "url": "https://finviz.com/screener.ashx?v=111&ft=4",
                "description": "Default system strategy with basic configuration for new setups",
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
                    _logger.info(f"✅ Activated first strategy: {first_url['name']}")
                    
    except Exception as e:
        _logger.error(f"❌ Error ensuring default Finviz URL: {e}")
        raise

async def initialize_database():
    """Complete database initialization."""
    try:
        # Initialize DBManager
        db_manager.initialize(settings.DATABASE_URL)
        _logger.info("🔗 Database connection initialized")
        
        # Ensure default Finviz strategy exists
        await ensure_default_finviz_url()
        
        _logger.info("🎯 Database initialization completed successfully")
        
    except Exception as e:
        _logger.error(f"❌ Database initialization failed: {e}")
        raise

async def get_current_finviz_strategy():
    """Gets the currently active Finviz strategy for engine initialization."""
    try:
        active_strategy = await db_manager.get_active_finviz_url()
        if not active_strategy:
            raise RuntimeError("No active Finviz strategy found - database may not be initialized")
        
        _logger.info(f"📊 Active strategy: {active_strategy['name']} - {active_strategy['url'][:50]}...")
        return active_strategy
        
    except Exception as e:
        _logger.error(f"❌ Error retrieving active Finviz strategy: {e}")
        raise

if __name__ == "__main__":
    # Can be run standalone for database setup
    logging.basicConfig(level=logging.INFO)
    asyncio.run(initialize_database())

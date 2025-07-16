"""Database initialization and migration logic."""

import logging
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine
from database.simple_models import Base
from database.simple_init import init_database, ensure_default_finviz_url, check_database_health

_logger = logging.getLogger("db_init")

async def run_migration_script(database_url: str, script_path: str):
    """Run a specific SQL migration script."""
    engine = create_async_engine(database_url, echo=False)
    
    try:
        script_file = Path(script_path)
        if not script_file.exists():
            _logger.warning(f"Migration script not found: {script_path}")
            return
            
        with open(script_file, 'r', encoding='utf-8') as f:
            sql_content = f.read()
            
        if not sql_content.strip():
            _logger.info(f"Migration script is empty: {script_path}")
            return
            
        _logger.info(f"Running migration script: {script_path}")
        
        async with engine.begin() as conn:
            # Split SQL by statements and execute each one
            statements = [stmt.strip() for stmt in sql_content.split(';') if stmt.strip()]
            
            for stmt in statements:
                if stmt.upper().startswith(('CREATE', 'INSERT', 'UPDATE', 'ALTER', 'DROP', 'COMMENT', 'DO')):
                    await conn.exec_driver_sql(stmt)
                    
        _logger.info(f"✅ Migration script completed: {script_path}")
        
    except Exception as e:
        _logger.error(f"❌ Error running migration script {script_path}: {e}")
        raise
    finally:
        await engine.dispose()

async def initialize_database_with_migrations(database_url: str, db_manager=None):
    """Complete database initialization including migrations."""
    try:
        # First, create basic tables using SQLAlchemy models
        await init_database(database_url)
        
        # Run migration scripts for additional tables
        migration_script = Path(__file__).parent / "migration_finviz_urls_admin_actions.sql"
        await run_migration_script(database_url, str(migration_script))
        
        # Ensure default data if db_manager is provided
        if db_manager:
            await ensure_default_finviz_url(db_manager)
            
        _logger.info("✅ Complete database initialization finished successfully")
        
    except Exception as e:
        _logger.error(f"❌ Database initialization failed: {e}")
        raise

async def check_and_repair_database(database_url: str):
    """Check database health and repair if needed."""
    try:
        # Basic health check
        if not await check_database_health(database_url):
            raise Exception("Database connection failed")
            
        # Check if required tables exist
        engine = create_async_engine(database_url, echo=False)
        async with engine.begin() as conn:
            # Check for key tables
            result = await conn.exec_driver_sql("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name IN ('signals', 'finviz_urls', 'admin_actions')
            """)
            
            existing_tables = [row[0] for row in result.fetchall()]
            required_tables = ['signals', 'finviz_urls', 'admin_actions']
            missing_tables = [t for t in required_tables if t not in existing_tables]
            
            if missing_tables:
                _logger.warning(f"Missing tables detected: {missing_tables}")
                await engine.dispose()
                
                # Re-run initialization
                await initialize_database_with_migrations(database_url)
                return True
            else:
                _logger.info("✅ All required database tables present")
                await engine.dispose()
                return True
                
    except Exception as e:
        _logger.error(f"❌ Database check and repair failed: {e}")
        return False

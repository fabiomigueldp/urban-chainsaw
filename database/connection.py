"""Database connection and session management for async PostgreSQL."""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from contextlib import asynccontextmanager
from typing import AsyncGenerator
import logging
import os

_logger = logging.getLogger("database")

class DatabaseManager:
    """Manages async database connections and sessions."""
    
    def __init__(self):
        self.engine = None
        self.async_session_factory = None
        self._initialized = False
    
    def initialize(self, database_url: str, **engine_kwargs):
        """Initialize the database engine and session factory."""
        if self._initialized:
            _logger.warning("Database already initialized")
            return
        
        # Default engine configuration for async PostgreSQL
        default_kwargs = {
            'echo': os.getenv('DATABASE_ECHO', 'false').lower() == 'true',
            'pool_size': int(os.getenv('DATABASE_POOL_SIZE', '20')),
            'max_overflow': int(os.getenv('DATABASE_MAX_OVERFLOW', '30')),
            'pool_pre_ping': True,
            'pool_recycle': int(os.getenv('DATABASE_POOL_RECYCLE', '3600')),
        }
        
        # Use NullPool for testing environments
        if os.getenv('TESTING', 'false').lower() == 'true':
            default_kwargs.update({'poolclass': NullPool})
        
        # Merge with provided kwargs
        engine_config = {**default_kwargs, **engine_kwargs}
        
        self.engine = create_async_engine(database_url, **engine_config)
        self.async_session_factory = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False
        )
        
        self._initialized = True
        _logger.info(f"Database initialized with pool_size={engine_config['pool_size']}")
    
    async def close(self):
        """Close all database connections."""
        if self.engine:
            await self.engine.dispose()
            _logger.info("Database connections closed")
    
    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get an async database session with automatic cleanup."""
        if not self._initialized:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        
        async with self.async_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
    
    async def execute_raw_sql(self, sql: str, params: dict = None):
        """Execute raw SQL with parameters."""
        async with self.get_session() as session:
            result = await session.execute(sql, params or {})
            return result
    
    async def health_check(self) -> bool:
        """Check if database connection is healthy."""
        try:
            async with self.get_session() as session:
                await session.execute("SELECT 1")
                return True
        except Exception as e:
            _logger.error(f"Database health check failed: {e}")
            return False

# Global database manager instance
db_manager = DatabaseManager()

# Convenience function for getting sessions
async def get_db_session():
    """Get a database session - for use with FastAPI dependency injection."""
    async with db_manager.get_session() as session:
        yield session

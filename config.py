"""Application configuration.

All runtime parameters are exposed as environment variables so that
the same container image can be used across dev → staging → prod
without modification.  We rely on *pydantic* for robust parsing and
automatic type‑conversion of env vars.

Usage
-----
```python
from config import settings
print(settings.DEST_WEBHOOK_URL)
```
"""

from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import AnyHttpUrl, Field
import os
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    """Strongly‑typed runtime settings."""

    # ------------------------------------------------------------------ #
    # Outbound                                                           #
    # ------------------------------------------------------------------ #
    DEST_WEBHOOK_URL: AnyHttpUrl = Field(
        ...,
        description="Webhook that will receive APPROVED signals.",
    )
    DEST_WEBHOOK_TIMEOUT: int = Field(5, description="Timeout in seconds for requests to destination webhook.")
    
    MAP_SIDE_TO_ACTION_TRADERSPOST: bool = Field(
        True,
        description="Map 'side' field to 'action' for TradersPost compatibility."
    )

    # ------------------------------------------------------------------ #
    # Filtering logic                                                     #
    # ------------------------------------------------------------------ #
    TOP_N: int = Field(
        15,
        description="Size of the Finviz ranking to accept (e.g. Top‑15)."
    )

    FINVIZ_REFRESH_SEC: int = Field(
        10,
        description="How often (seconds) we refresh the Finviz list."
    )

    # ------------------------------------------------------------------ #
    # Worker pool                                                         #
    # ------------------------------------------------------------------ #
    WORKER_CONCURRENCY: int = Field(
        16,
        description="Number of background workers processing the queue."
    )
    
    FORWARDING_WORKERS: int = Field(
        5,
        description="Number of dedicated workers for forwarding approved signals with rate limiting."
    )

    # ------------------------------------------------------------------ #
    # Server Configuration                                               #
    # ------------------------------------------------------------------ #
    SERVER_PORT: int = Field(
        80,
        description="Port for the FastAPI server to listen on."
    )

    # ------------------------------------------------------------------ #
    # Miscellaneous                                                       #
    # ------------------------------------------------------------------ #
    LOG_LEVEL: str = Field("INFO", description="Python log level.")

    FINVIZ_UPDATE_TOKEN: Optional[str] = Field(
        os.getenv("FINVIZ_UPDATE_TOKEN", "changeme-token"),
        description="Token for updating the Finviz URL via the /finviz/url endpoint."
    )

    # --- New settings for FinvizEngine ---
    FINVIZ_TICKERS_PER_PAGE: int = Field(
        20,
        description="Number of tickers to fetch per page from Finviz."
    )
    MAX_REQ_PER_MIN: int = Field(
        59,
        description="Maximum number of requests to Finviz per minute."
    )
    MAX_CONCURRENCY: int = Field(
        20,
        description="Maximum number of concurrent requests to Finviz."
    )
    # Default refresh interval, can be overridden by finviz_config.json or /finviz/config endpoint
    DEFAULT_TICKER_REFRESH_SEC: int = Field(
        10,
        description="Default refresh interval for tickers."
    )

    # --- Finviz settings file ---
    FINVIZ_CONFIG_FILE: str = Field(
        "finviz_config.json",
        description="Path to the Finviz configuration file."
    )

    # --- Prometheus Metrics ---
    PROMETHEUS_PORT: int = Field(
        8008,
        description="Port for Prometheus metrics."
    )

    # --- Queue settings ---
    QUEUE_MAX_SIZE: int = Field(
        100000,
        description="Maximum size of the signal queue."
    )
    
    # --- Signal Tracking settings ---
    SIGNAL_TRACKER_MAX_AGE_HOURS: int = Field(
        24,
        description="Maximum age in hours for signal trackers before cleanup."
    )
    
    SIGNAL_TRACKER_CLEANUP_INTERVAL_HOURS: int = Field(
        1,
        description="Interval in hours between signal tracker cleanup runs."
    )

    # --- Finviz Elite settings ---
    FINVIZ_USE_ELITE: bool = Field(
        False,
        description="Whether to use Finviz Elite with authentication."
    )
    FINVIZ_LOGIN_URL: str = Field(
        "https://finviz.com/login_submit.ashx",
        description="URL for Finviz Elite login."
    )
    FINVIZ_EMAIL: Optional[str] = Field(
        None,
        description="Email for Finviz Elite authentication."
    )
    FINVIZ_PASSWORD: Optional[str] = Field(
        None,
        description="Password for Finviz Elite authentication."
    )

    # --- Webhook Rate Limiting settings ---
    DEST_WEBHOOK_MAX_REQ_PER_MIN: int = Field(
        60,
        description="Maximum number of requests per minute to destination webhook."
    )
    DEST_WEBHOOK_RATE_LIMITING_ENABLED: bool = Field(
        True,
        description="Enable rate limiting for destination webhook."
    )

    # ------------------------------------------------------------------ #
    # Database Configuration                                              #
    # ------------------------------------------------------------------ #
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/trading_signals",
        description="PostgreSQL database URL for async connections."
    )
    
    DATABASE_ECHO: bool = Field(
        False,
        description="Enable SQLAlchemy query logging."
    )
    
    DATABASE_POOL_SIZE: int = Field(
        20,
        description="Database connection pool size."
    )
    
    DATABASE_MAX_OVERFLOW: int = Field(
        30,
        description="Maximum overflow connections in pool."
    )
    
    DATABASE_POOL_RECYCLE: int = Field(
        3600,
        description="Connection recycle time in seconds."
    )
    
    # Enable dual-write during migration (write to both memory and database)
    DUAL_WRITE_ENABLED: bool = Field(
        True,
        description="Enable dual-write to both memory and database during migration."
    )
    
    # Enable database-only mode (disable memory tracking)
    DATABASE_ONLY_MODE: bool = Field(
        False,
        description="Use only database for signal tracking (disable memory)."
    )
    
    # Data retention settings
    SIGNAL_RETENTION_DAYS: int = Field(
        30,
        description="Number of days to retain signal data."
    )
    
    METRICS_RETENTION_DAYS: int = Field(
        90,
        description="Number of days to retain system metrics."
    )

    # --- Reprocessing settings for FinvizEngine ---
    FINVIZ_REPROCESS_ENABLED: bool = Field(
        False,
        description="Enable reprocessing of recently rejected signals for new Top-N tickers."
    )
    FINVIZ_REPROCESS_WINDOW_SECONDS: int = Field(
        300,
        description="Time window in seconds to look back for rejected signals to reprocess."
    )

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"  # Ignore extra environment variables not defined in the model


# Singleton settings object used throughout the app
settings = Settings()

# Export individual config values for direct import (for legacy or convenience)
FINVIZ_UPDATE_TOKEN = settings.FINVIZ_UPDATE_TOKEN
FINVIZ_CONFIG_FILE = settings.FINVIZ_CONFIG_FILE
DEFAULT_TICKER_REFRESH_SEC = settings.DEFAULT_TICKER_REFRESH_SEC
QUEUE_MAX_SIZE = settings.QUEUE_MAX_SIZE
SERVER_PORT = settings.SERVER_PORT

# Dynamic constants based on Elite mode
def get_finviz_tickers_per_page() -> int:
    """Get the number of tickers per page based on Elite mode."""
    return 100 if settings.FINVIZ_USE_ELITE else 20

def get_max_req_per_min() -> int:
    """Get the maximum requests per minute based on Elite mode."""
    return 120 if settings.FINVIZ_USE_ELITE else 59

def get_max_concurrency() -> int:
    """Get the maximum concurrent requests based on Elite mode."""
    return 20 if settings.FINVIZ_USE_ELITE else 20

# Legacy constants (for backward compatibility)
FINVIZ_TICKERS_PER_PAGE = get_finviz_tickers_per_page()
MAX_REQ_PER_MIN = get_max_req_per_min()
MAX_CONCURRENCY = get_max_concurrency()
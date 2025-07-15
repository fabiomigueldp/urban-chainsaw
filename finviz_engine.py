import asyncio
import httpx
import logging
import math
import re
import time
import csv
import io
from datetime import datetime
from typing import Any, Dict, List, Set, Optional

from pydantic import BaseModel, validator, HttpUrl, Field

from config import (
    settings,
    get_finviz_tickers_per_page,
    get_max_req_per_min,
    get_max_concurrency,
    DEFAULT_TICKER_REFRESH_SEC,
    FINVIZ_CONFIG_FILE
)
from finviz import parse_tickers_from_html, load_finviz_config, persist_finviz_config_from_dict, normalise_url
# Placeholder for prometheus_client if you integrate it
# from prometheus_client import Counter, Gauge, Histogram

_logger = logging.getLogger("finviz_engine")

# --- Prometheus Metrics (placeholders) ---
# These would be initialized if prometheus_client is used
# finviz_requests_total = Counter("finviz_requests_total", "Total Finviz HTTP requests made by the engine")
# finviz_concurrency_current = Gauge("finviz_concurrency_current", "Current number of in-flight Finviz requests")
# finviz_update_duration_seconds = Histogram("finviz_update_duration_seconds", "Duration of Finviz ticker update cycles")
# finviz_update_success_total = Counter("finviz_update_success_total", "Total successful Finviz ticker updates")
# finviz_update_failure_total = Counter("finviz_update_failure_total", "Total failed Finviz ticker updates")

class FinvizConfig(BaseModel):
    url: HttpUrl
    top_n: int
    refresh: int = DEFAULT_TICKER_REFRESH_SEC # seconds
    reprocess_enabled: bool = Field(default=False, description="Enable reprocessing of recently rejected signals for new Top-N tickers.")
    reprocess_window_seconds: int = Field(default=300, description="Time window in seconds to look back for rejected signals to reprocess.")
    respect_sell_chronology_enabled: bool = Field(default=True, description="Skip reprocessing BUY signals if subsequent SELL signals exist.")
    sell_chronology_window_seconds: int = Field(default=300, description="Time window in seconds to look for subsequent SELL signals.")

    @validator('top_n')
    def top_n_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError('top_n must be positive')
        return v

    @validator('refresh')
    def refresh_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError('refresh interval must be positive')
        return v

    @validator('reprocess_window_seconds')
    def reprocess_window_must_be_non_negative(cls, v):
        if v < 0:
            raise ValueError('reprocess_window_seconds cannot be negative')
        return v

    @validator('sell_chronology_window_seconds')
    def sell_chronology_window_must_be_non_negative(cls, v):
        if v < 0:
            raise ValueError('sell_chronology_window_seconds cannot be negative')
        return v

class FinvizEngine:
    def __init__(self, shared_state: Dict[str, Any], admin_ws_broadcaster: callable):
        self.shared_state = shared_state
        self.shared_state["tickers"] = set()  # Initialize with an empty set
        self.last_known_good_tickers: Set[str] = set()
        self.admin_ws_broadcaster = admin_ws_broadcaster

        # Rate limiting and concurrency control - using dynamic values
        self.rate_limit_semaphore = asyncio.Semaphore(get_max_req_per_min()) # Tokens for 1 minute
        self.concurrency_semaphore = asyncio.Semaphore(get_max_concurrency())

        self.cfg_updated_event = asyncio.Event()
        self._current_config: Optional[FinvizConfig] = None
        self._config_lock = asyncio.Lock() # To protect config reads/writes
        self._running = False
        
        # Add pause/resume functionality
        self._paused = False
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # Initially not paused

        # For token bucket (rate_limit_semaphore)
        self._tokens_generated_time = time.monotonic()
        
        # Finviz Elite authentication
        self._auth_cookies: httpx.Cookies = httpx.Cookies()
        self._session_valid_until: float = 0  # unixtime
        
        # Status tracking metrics
        self.last_update_status: str = "Not Started"
        self.last_successful_update: Optional[str] = None
        self.last_failed_update: Optional[str] = None
        self.update_start_time: Optional[float] = None
        self.last_update_duration: Optional[float] = None

    def _format_timestamp(self, timestamp: Optional[float]) -> str:
        """Convert timestamp to readable format."""
        if timestamp is None:
            return "N/A"
        try:
            return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError):
            return "Invalid"
    
    def get_status_metrics(self) -> Dict[str, str]:
        """Get current status metrics for admin interface."""
        return {
            "last_update_status": self.last_update_status,
            "last_successful_update": self._format_timestamp(self.last_successful_update),
            "last_failed_update": self._format_timestamp(self.last_failed_update),
            "last_update_duration": f"{self.last_update_duration:.2f}s" if self.last_update_duration else "N/A",
            "is_paused": self._paused,
            "is_running": self._running,
            "max_concurrency": get_max_concurrency(),
            "max_requests_per_min": get_max_req_per_min(),
            "rows_per_page": get_finviz_tickers_per_page(),
            "finviz_elite_enabled": settings.FINVIZ_USE_ELITE,
            "auth_session_valid": time.time() < self._session_valid_until if self._session_valid_until > 0 else False,
            "rate_limit_tokens_available": self.rate_limit_semaphore._value,
            "concurrency_slots_available": self.concurrency_semaphore._value
        }

    async def _broadcast_status_update(self):
        """Broadcast complete status update to admin WebSocket clients."""
        try:
            from comm_engine import comm_engine
            from main import get_finviz_status_data, get_overview_data
            
            # Only trigger finviz_status_update - avoid cascading updates
            finviz_data = await get_finviz_status_data()
            await comm_engine.trigger_finviz_status_update(finviz_data)
            
            # Also send overview data with elite auth status
            overview_data = await get_overview_data()
            await comm_engine.trigger_overview_update(overview_data)
            
        except Exception as e:
            _logger.error(f"Error broadcasting status update: {e}")

    def _browser_headers(self) -> Dict[str, str]:
        """Return browser-like headers for HTTP requests."""
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1"
        }

    async def _login(self) -> None:
        """Authenticate with Finviz Elite and store session cookies."""
        if not settings.FINVIZ_EMAIL or not settings.FINVIZ_PASSWORD:
            raise RuntimeError("FINVIZ_EMAIL and FINVIZ_PASSWORD must be set for Elite access")
        
        payload = {
            "email": settings.FINVIZ_EMAIL,
            "password": settings.FINVIZ_PASSWORD,
            "remember": "1",
        }
        
        _logger.info("Attempting Finviz Elite login...")
        async with httpx.AsyncClient(
            timeout=20.0, 
            follow_redirects=True,
            headers=self._browser_headers()
        ) as client:
            try:
                response = await client.post(settings.FINVIZ_LOGIN_URL, data=payload)
                if response.status_code not in (200, 302):
                    raise RuntimeError(f"Login failed: HTTP {response.status_code}")
                
                self._auth_cookies = client.cookies
                self._session_valid_until = time.time() + 55 * 60  # 55 min TTL
                _logger.info("Finviz Elite login successful, session valid for 55 minutes")
                
            except Exception as e:
                _logger.error(f"Finviz Elite login failed: {e}")
                raise RuntimeError(f"Login failed: {e}")

    async def _get_elite_client(self) -> httpx.AsyncClient:
        """Get an authenticated HTTP client for Finviz Elite requests."""
        if time.time() >= self._session_valid_until or not self._auth_cookies:
            await self._login()
        
        # Create client with auth cookies
        client = httpx.AsyncClient(
            cookies=self._auth_cookies,
            headers=self._browser_headers(),
            follow_redirects=False,
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
        
        # Set cookie for 100 rows per page for Elite HTML mode
        client.cookies.set("screenerTableRows", "100", domain="elite.finviz.com")
        
        return client

    def _parse_csv_tickers(self, csv_content: str) -> List[str]:
        """Parse ticker symbols from CSV content returned by Finviz Elite export."""
        tickers = []
        try:
            # Create a CSV reader from the content
            csv_reader = csv.reader(io.StringIO(csv_content))
            
            # Read the first row to check if it's a header
            first_row = next(csv_reader, None)
            if first_row is None:
                _logger.warning("Empty CSV content received")
                return tickers
            
            # Check if first row is a header (contains column names like 'Ticker', 'Company', etc.)
            ticker_col_index = 0
            is_header = False
            
            # If first column contains common CSV header words, treat it as header
            if first_row and isinstance(first_row[0], str):
                first_col_lower = first_row[0].lower().strip()
                if first_col_lower in ['ticker', 'symbol', 'tick', 'company', 'name']:
                    is_header = True
                    # Find the ticker column in header
                    for i, col_name in enumerate(first_row):
                        if col_name.lower().strip() in ['ticker', 'symbol', 'tick']:
                            ticker_col_index = i
                            break
                else:
                    # First row doesn't look like header, so it's data
                    # Process this row as ticker data
                    if len(first_row) > ticker_col_index:
                        ticker = first_row[ticker_col_index].strip().upper()
                        if ticker and ticker not in ['N/A', '', '-']:
                            tickers.append(ticker)
            
            # Extract tickers from remaining rows
            for row in csv_reader:
                if row and len(row) > ticker_col_index:
                    ticker = row[ticker_col_index].strip().upper()
                    if ticker and ticker not in ['N/A', '', '-']:
                        tickers.append(ticker)
            
            _logger.debug(f"Parsed {len(tickers)} tickers from CSV content (header detected: {is_header})")
            
        except Exception as e:
            _logger.error(f"Error parsing CSV content: {e}")
            raise
        
        return tickers

    async def _generate_tokens_for_rate_limit(self):
        """Replenishes tokens for the rate_limit_semaphore every minute."""
        while self._running:
            now = time.monotonic()
            if now - self._tokens_generated_time >= 60:
                # Release all tokens that should have been generated in the last minute
                # This ensures that if the loop was busy, we catch up.
                max_req = get_max_req_per_min()
                num_to_release = max_req - self.rate_limit_semaphore._value
                for _ in range(num_to_release):
                    if self.rate_limit_semaphore._value < max_req:
                         self.rate_limit_semaphore.release()
                self._tokens_generated_time = now
                _logger.debug(f"Replenished {num_to_release} tokens for rate limiter. Current: {self.rate_limit_semaphore._value}")
            await asyncio.sleep(1) # Check every second

    async def get_config(self) -> FinvizConfig:
        async with self._config_lock:
            if self._current_config is None:
                await self._load_config_from_file()
            return self._current_config

    async def _load_config_from_file(self) -> None:
        """Loads configuration from finviz_config.json"""
        try:
            config_data = load_finviz_config() # finviz.py function
            if not config_data.get("finviz_url") or not config_data.get("top_n"):
                _logger.warning(f"{FINVIZ_CONFIG_FILE} is missing 'finviz_url' or 'top_n'. Using defaults or previous if available.")
                # Fallback to ensure engine can start even with a bad/missing initial config
                if self._current_config: # Keep using current if load fails
                    _logger.info(f"Kept existing config due to load failure: {self._current_config}")
                    return
                else: # Critical if no config at all on first load
                    _logger.error(f"CRITICAL: No valid config in {FINVIZ_CONFIG_FILE} and no prior config. Engine cannot start fetching.")
                    # A default safe config could be set here if desired
                    # For now, it will likely fail in run() if _current_config remains None
                    raise ValueError("Initial configuration load failed and no defaults set.")

            self._current_config = FinvizConfig(
                url=config_data["finviz_url"],
                top_n=config_data["top_n"],
                refresh=config_data.get("refresh_interval_sec", DEFAULT_TICKER_REFRESH_SEC),
                reprocess_enabled=config_data.get("reprocess_enabled", False),
                reprocess_window_seconds=config_data.get("reprocess_window_seconds", 300),
                respect_sell_chronology_enabled=config_data.get("respect_sell_chronology_enabled", True),
                sell_chronology_window_seconds=config_data.get("sell_chronology_window_seconds", 300)
            )
            _logger.info(f"Successfully loaded config: URL={self._current_config.url}, TopN={self._current_config.top_n}, Refresh={self._current_config.refresh}s, ReprocessEnabled={self._current_config.reprocess_enabled}, ReprocessWindow={self._current_config.reprocess_window_seconds}s, RespectSellChronology={self._current_config.respect_sell_chronology_enabled}, SellChronologyWindow={self._current_config.sell_chronology_window_seconds}s")
        except Exception as e:
            _logger.error(f"Error loading config from {FINVIZ_CONFIG_FILE}: {e}. Engine will use last known good config or defaults if available.")
            if not self._current_config: # If there's no config at all (e.g. first run)
                 _logger.critical(f"CRITICAL: Failed to load initial configuration from {FINVIZ_CONFIG_FILE}. Engine cannot operate without a base configuration.")
                 # Consider raising an exception here to halt startup if no config can be loaded
                 # For now, we'll let it try to run, but it will likely fail in the main loop.
                 # A more robust solution might involve setting a very basic default config.
                 raise # Re-raise to signal critical failure if no config at all

    async def update_config(self, new_config_data: Dict[str, Any]) -> None:
        async with self._config_lock:
            if self._current_config is None: # Should have been loaded by get_config or run
                await self._load_config_from_file()

            # Create a proposed new configuration, merging with current
            proposed_data = self._current_config.dict()
            if "url" in new_config_data and new_config_data["url"] is not None:
                proposed_data["url"] = new_config_data["url"]
            if "top_n" in new_config_data and new_config_data["top_n"] is not None:
                proposed_data["top_n"] = new_config_data["top_n"]
            if "refresh" in new_config_data and new_config_data["refresh"] is not None:
                proposed_data["refresh"] = new_config_data["refresh"]

            # Handle new reprocessing fields
            if "reprocess_enabled" in new_config_data and new_config_data["reprocess_enabled"] is not None:
                proposed_data["reprocess_enabled"] = new_config_data["reprocess_enabled"]
            elif "reprocess_enabled" not in proposed_data: # Ensure default if not present
                proposed_data["reprocess_enabled"] = self._current_config.reprocess_enabled if self._current_config else False

            if "reprocess_window_seconds" in new_config_data and new_config_data["reprocess_window_seconds"] is not None:
                proposed_data["reprocess_window_seconds"] = new_config_data["reprocess_window_seconds"]
            elif "reprocess_window_seconds" not in proposed_data: # Ensure default if not present
                proposed_data["reprocess_window_seconds"] = self._current_config.reprocess_window_seconds if self._current_config else 300

            # Handle new sell chronology fields
            if "respect_sell_chronology_enabled" in new_config_data and new_config_data["respect_sell_chronology_enabled"] is not None:
                proposed_data["respect_sell_chronology_enabled"] = new_config_data["respect_sell_chronology_enabled"]
            elif "respect_sell_chronology_enabled" not in proposed_data: # Ensure default if not present
                proposed_data["respect_sell_chronology_enabled"] = self._current_config.respect_sell_chronology_enabled if self._current_config else True

            if "sell_chronology_window_seconds" in new_config_data and new_config_data["sell_chronology_window_seconds"] is not None:
                proposed_data["sell_chronology_window_seconds"] = new_config_data["sell_chronology_window_seconds"]
            elif "sell_chronology_window_seconds" not in proposed_data: # Ensure default if not present
                proposed_data["reprocess_window_seconds"] = self._current_config.reprocess_window_seconds if self._current_config else 300


            try:
                new_cfg = FinvizConfig(**proposed_data)
            except Exception as e:
                _logger.error(f"Invalid config data provided for update: {e}")
                raise ValueError(f"Invalid config data: {e}")

            # Validate rate limits before applying - check current authentication state
            # Use current authentication status to determine actual rate limits
            is_currently_authenticated = (
                bool(self._auth_cookies) and 
                self._session_valid_until > 0 and 
                time.time() < self._session_valid_until
            )
            
            if is_currently_authenticated and settings.FINVIZ_USE_ELITE:
                # Currently authenticated Elite user
                tickers_per_page = 100
                max_req_per_min = 120
            else:
                # Free account or not authenticated
                tickers_per_page = 20
                max_req_per_min = 59
                
            pages_required = math.ceil(new_cfg.top_n / tickers_per_page)
            requests_per_cycle = pages_required
            # Ensure refresh is not zero to avoid division by zero
            if new_cfg.refresh <= 0:
                _logger.error("Refresh interval must be positive.")
                raise ValueError("Refresh interval must be positive.")
            
            expected_reqs_per_min = requests_per_cycle * (60 / new_cfg.refresh)

            if expected_reqs_per_min > max_req_per_min:
                auth_status = "Elite" if is_currently_authenticated and settings.FINVIZ_USE_ELITE else "Free"
                _logger.warning(
                    f"Proposed config exceeds rate limit for {auth_status} account: "
                    f"TopN={new_cfg.top_n}, Refresh={new_cfg.refresh}s => "
                    f"{expected_reqs_per_min:.2f} reqs/min (Max: {max_req_per_min})."
                )
                raise ValueError(
                    f"Configuration rejected: exceeds max requests per minute. "
                    f"Calculated: {expected_reqs_per_min:.2f}, Max: {max_req_per_min} ({auth_status} account)"
                )

            # Persist to file
            persist_finviz_config_from_dict({
                "finviz_url": str(new_cfg.url), # Pydantic HttpUrl to string
                "top_n": new_cfg.top_n,
                "refresh_interval_sec": new_cfg.refresh,
                "reprocess_enabled": new_cfg.reprocess_enabled,
                "reprocess_window_seconds": new_cfg.reprocess_window_seconds
            })
            self._current_config = new_cfg
            _logger.info(f"FinvizEngine config updated and persisted: {self._current_config}")
            self.cfg_updated_event.set()


    async def run(self):
        self._running = True
        _logger.info("FinvizEngine started.")
        # Start the token generation loop for the rate limiter
        asyncio.create_task(self._generate_tokens_for_rate_limit())
        # Start the periodic status broadcast loop
        asyncio.create_task(self._periodic_status_broadcast())

        while self._running:
            try:
                # Wait if paused with periodic status broadcasts
                if self._paused:
                    self.last_update_status = "Paused"
                    _logger.debug("Engine is paused, waiting to resume...")
                    
                    # Wait for resume signal (periodic broadcasts handled by _periodic_status_broadcast)
                    await self._pause_event.wait()
                    
                    if not self._running:  # Check if stopped while paused
                        break
                    _logger.info("Engine resumed from pause.")

                current_cfg = await self.get_config() # Loads from file if not already loaded
                if not current_cfg: # Should not happen if get_config is robust
                    _logger.error("No configuration available, sleeping before retry.")
                    await asyncio.sleep(DEFAULT_TICKER_REFRESH_SEC) # Default sleep
                    continue

                start_time = time.monotonic()
                self.update_start_time = start_time
                self.last_update_status = "In Progress"
                # finviz_concurrency_current.set(0) # Reset for the new cycle if using Prometheus

                await self._update_tickers_safely(current_cfg)

                duration = time.monotonic() - start_time
                self.last_update_duration = duration
                # finviz_update_duration_seconds.observe(duration) # If using Prometheus
                _logger.info(f"Ticker update cycle completed in {duration:.2f}s.")

                # Wait for the refresh interval or a config update signal
                try:
                    _logger.debug(f"Waiting for {current_cfg.refresh}s or config update signal...")
                    await asyncio.wait_for(self.cfg_updated_event.wait(), timeout=current_cfg.refresh)
                except asyncio.TimeoutError:
                    pass  # Regular refresh cycle
                finally:
                    self.cfg_updated_event.clear() # Clear event if it was set

            except ValueError as ve: # Catch config validation errors from get_config or update_config
                _logger.error(f"Configuration error in main loop: {ve}. Retrying after delay.")
                await asyncio.sleep(DEFAULT_TICKER_REFRESH_SEC) # Wait before retrying config load
            except Exception as e:
                _logger.exception(f"Unexpected error in FinvizEngine main loop: {e}. Retrying after delay.")
                # finviz_update_failure_total.inc() # If using Prometheus
                await asyncio.sleep(60) # Longer sleep for unexpected errors

    async def _update_tickers_safely(self, cfg: FinvizConfig):
        """Wraps _update_tickers with error handling to preserve last_known_good_tickers."""
        current_cfg = await self.get_config() # Get the most recent config, including reprocessing settings

        try:
            new_tickers = await self._fetch_all_tickers(cfg) # cfg here is the one passed to the run loop iteration
            if new_tickers is not None: # Check if fetch was successful (not None)

                # Identify newly added tickers for reprocessing logic
                previously_known_tickers = self.last_known_good_tickers.copy()
                entered_top_n_tickers = new_tickers - previously_known_tickers

                # Log detailed ticker analysis for debugging
                _logger.info(f"Ticker analysis - Previous: {len(previously_known_tickers)}, New: {len(new_tickers)}, Entered: {len(entered_top_n_tickers)}")
                _logger.debug(f"Previously known tickers: {sorted(list(previously_known_tickers))}")
                _logger.debug(f"New tickers: {sorted(list(new_tickers))}")
                if entered_top_n_tickers:
                    _logger.info(f"Newly entered tickers: {sorted(list(entered_top_n_tickers))}")

                self.shared_state["tickers"] = new_tickers
                self.last_known_good_tickers = new_tickers.copy() # Update last known good
                # finviz_update_success_total.inc() # If using Prometheus
                
                # Update status metrics
                self.last_update_status = "Success"
                self.last_successful_update = time.time()
                
                _logger.info(f"Successfully updated tickers: {len(new_tickers)} symbols. Shared state updated.")
                await self.admin_ws_broadcaster(event_type="finviz_update_ok", data={"count": len(new_tickers)})
                # Broadcast Top-N tickers update para WebSocket
                from comm_engine import comm_engine
                top_n_data = {
                    "tickers": sorted(list(new_tickers)),
                    "count": len(new_tickers),
                    "last_update": self.last_successful_update,
                }
                await comm_engine.trigger_top_n_tickers_update(top_n_data)
                # Send complete status update using centralized comm_engine
                await self._broadcast_status_update()

                # --- Reprocessing Logic ---
                _logger.debug(f"Reprocessing check - Enabled: {current_cfg.reprocess_enabled}, New tickers: {len(entered_top_n_tickers)}")
                if current_cfg.reprocess_enabled and entered_top_n_tickers:
                    _logger.info(f"Reprocessing enabled. Tickers newly entered Top-N: {entered_top_n_tickers}")
                    await self._reprocess_signals_for_new_tickers(entered_top_n_tickers, current_cfg.reprocess_window_seconds)
                elif current_cfg.reprocess_enabled:
                    _logger.debug(f"Reprocessing enabled but no new tickers detected. Window: {current_cfg.reprocess_window_seconds}s")

            else:
                # finviz_update_failure_total.inc() # If using Prometheus
                
                # Update status metrics
                self.last_update_status = "Failed (No Data)"
                self.last_failed_update = time.time()
                
                _logger.warning("Ticker update failed (fetch returned None). Retaining last known good tickers.")
                # Ensure shared_state["tickers"] is the last known good set
                self.shared_state["tickers"] = self.last_known_good_tickers.copy()
                await self.admin_ws_broadcaster(event_type="finviz_update_failed", data={"count": len(self.last_known_good_tickers)})
                # Send complete status update using centralized comm_engine
                await self._broadcast_status_update()

        except Exception as e:
            # finviz_update_failure_total.inc() # If using Prometheus
            
            # Update status metrics
            self.last_update_status = f"Failed ({str(e)[:50]}...)"
            self.last_failed_update = time.time()
            
            _logger.error(f"Exception during _update_tickers_safely: {e}. Retaining last known good tickers.")
            self.shared_state["tickers"] = self.last_known_good_tickers.copy() # Ensure safety on any exception
            await self.admin_ws_broadcaster(event_type="finviz_update_failed", data={"error": str(e), "count": len(self.last_known_good_tickers)})
            # Send complete status update
            await self._broadcast_status_update()

    async def _reprocess_signals_for_new_tickers(self, new_tickers: Set[str], window_seconds: int):
        """
        Enhanced reprocessing using the robust SignalReprocessingEngine.
        """
        _logger.info(f"Starting enhanced reprocessing for {len(new_tickers)} new tickers within {window_seconds}s window")
        
        # Import the robust reprocessing engine
        try:
            from signal_reprocessing_engine import SignalReprocessingEngine
            from main import db_manager, approved_signal_queue, shared_state
            
            # Create reprocessing engine instance
            reprocessing_engine = SignalReprocessingEngine(
                db_manager=db_manager,
                approved_signal_queue=approved_signal_queue
            )
            
            # Process the new tickers
            result = await reprocessing_engine.process_new_tickers(new_tickers, window_seconds)
            
            # Update shared state metrics
            if result.signals_reprocessed > 0:
                shared_state["signal_metrics"]["signals_approved"] += result.signals_reprocessed
                if shared_state["signal_metrics"]["signals_rejected"] >= result.signals_reprocessed:
                    shared_state["signal_metrics"]["signals_rejected"] -= result.signals_reprocessed
                else:
                    _logger.warning("Cannot decrement signals_rejected - would go below 0")
            
            # Log summary
            if result.success:
                _logger.info(f"✅ Reprocessing completed successfully: "
                           f"{result.signals_reprocessed} signals reprocessed from {result.signals_found} found, "
                           f"success rate: {result.metrics.get_success_rate():.1f}%")
            else:
                _logger.error(f"❌ Reprocessing completed with errors: "
                            f"{result.signals_reprocessed} successful, {result.signals_failed} failed, "
                            f"errors: {result.errors}")
            
            # Broadcast final metrics update
            try:
                from comm_engine import comm_engine
                from main import get_current_metrics
                await comm_engine.broadcast("metrics_update", get_current_metrics())
            except Exception as e:
                _logger.warning(f"Failed to broadcast final metrics update: {e}")
                
        except ImportError as e:
            _logger.error(f"Failed to import SignalReprocessingEngine: {e}. Falling back to legacy implementation.")
            await self._legacy_reprocess_signals_for_new_tickers(new_tickers, window_seconds)
        except Exception as e:
            _logger.error(f"Enhanced reprocessing failed: {e}. Falling back to legacy implementation.")
            await self._legacy_reprocess_signals_for_new_tickers(new_tickers, window_seconds)

    async def _legacy_reprocess_signals_for_new_tickers(self, new_tickers: Set[str], window_seconds: int):
        _logger.info(f"Attempting to reprocess signals for {len(new_tickers)} new tickers within a {window_seconds}s window.")
        from main import db_manager, approved_signal_queue, shared_state # Import necessary components
        from database.simple_models import SignalStatusEnum, SignalLocationEnum, Signal as DBSignalModel # Import Signal for type hint
        import time

        for ticker in new_tickers:
            try:
                _logger.debug(f"Checking for rejected BUY signals for new ticker: {ticker}")
                # Only reprocess BUY signals - SELL signals rejected means no position existed
                rejected_signals_payloads = await db_manager.get_rejected_signals_for_reprocessing(ticker, window_seconds)
                
                # Filter to only BUY signals
                buy_signals = []
                for signal_data in rejected_signals_payloads:
                    signal_side = (signal_data.get("side") or "").lower().strip()
                    signal_type = (signal_data.get("signal_type") or "").lower().strip()
                    
                    # Check original_signal action to ensure we don't reprocess SELL signals
                    original_signal = signal_data.get("original_signal", {})
                    original_action = (original_signal.get("action") or "").lower()
                    
                    # Enhanced BUY signal detection with more comprehensive triggers
                    buy_triggers = {"buy", "long", "enter", "open", "bull"}
                    sell_triggers = {"sell", "exit", "close"}
                    
                    # Check if it's truly a BUY signal (exclude SELL actions)
                    is_buy_signal = (
                        (signal_side in buy_triggers or 
                         signal_type in buy_triggers or
                         (not signal_side and signal_type == "buy") or  # Default case when side is empty
                         (not signal_side and not signal_type))  # Default to BUY when both are empty
                        and original_action not in sell_triggers  # Critical: exclude SELL actions
                    )
                    
                    if is_buy_signal:
                        buy_signals.append(signal_data)
                        _logger.debug(f"Including BUY signal {signal_data.get('signal_id')} for reprocessing (side: '{signal_side}', type: '{signal_type}', original_action: '{original_action}')")
                    else:
                        _logger.debug(f"Skipping non-BUY signal {signal_data.get('signal_id')} for reprocessing (side: '{signal_side}', type: '{signal_type}', original_action: '{original_action}')")
                
                
                rejected_signals_payloads = buy_signals

                if rejected_signals_payloads:
                    _logger.info(f"Found {len(rejected_signals_payloads)} rejected signals for ticker {ticker} to reprocess.")
                    for signal_payload_dict in rejected_signals_payloads:
                        signal_id = signal_payload_dict.get("signal_id")
                        if not signal_id:
                            _logger.error(f"Skipping reprocessing for signal with missing ID: {signal_payload_dict}")
                            continue

                        _logger.info(f"Reprocessing signal ID {signal_id} for ticker {ticker}.")

                        # 1. Change status to APPROVED in DB and log event
                        reapproved = await db_manager.reapprove_signal(signal_id, "Signal re-approved due to ticker entering Top-N list.")
                        if not reapproved:
                            _logger.error(f"Failed to re-approve signal {signal_id} in database.")
                            continue

                        # 2. Reconstruct the Signal object from the stored original_signal dict FIRST
                        # Reconstruct the Signal object from the stored original_signal dict
                        from models import Signal as SignalPydanticModel
                        try:
                            original_signal_data = signal_payload_dict.get("original_signal", {})
                            if not original_signal_data: # Fallback if original_signal is empty
                                original_signal_data = {
                                    "ticker": signal_payload_dict.get("ticker"),
                                    "side": signal_payload_dict.get("side"),
                                    "price": signal_payload_dict.get("price"),
                                    "time": signal_payload_dict.get("created_at").isoformat() if signal_payload_dict.get("created_at") else None,
                                    "signal_id": signal_id # Ensure signal_id is present
                                }

                            reprocessed_signal = SignalPydanticModel(**original_signal_data)
                            # Ensure the signal_id matches the one from the database record
                            reprocessed_signal.signal_id = signal_id

                        except Exception as pydantic_error:
                            _logger.error(f"Error reconstructing Signal object for {signal_id}: {pydantic_error}. Original data: {original_signal_data}")
                            continue

                        # 3. For BUY signals: Open position AND add to forwarding queue
                        # For SELL signals: Only add to forwarding queue (if position exists)
                        signal_side = (signal_payload_dict.get("side") or "").lower()
                        
                        # FIX: Use original_signal to get action instead of undefined reprocessed_signal
                        original_signal = signal_payload_dict.get("original_signal", {})
                        signal_action = (original_signal.get("action") or "").lower()
                        
                        # Determine if this is a BUY signal
                        buy_triggers = {"buy", "long", "enter"}
                        sell_triggers = {"sell", "exit", "close"}
                        
                        is_buy_signal = (signal_side in buy_triggers) or (signal_action in buy_triggers)
                        is_sell_signal = (signal_side in sell_triggers) or (signal_action in sell_triggers)
                        
                        # Log detailed classification information
                        _logger.info(f"[REPROCESSING] Signal {signal_id}: side='{signal_side}', "
                                   f"action='{signal_action}', is_buy={is_buy_signal}, is_sell={is_sell_signal}")
                        
                        if is_buy_signal:
                            # For BUY signals: Open position (just like _queue_worker does)
                            try:
                                await db_manager.open_position(ticker=ticker, entry_signal_id=signal_id)
                                _logger.info(f"Reprocessing: Position opened for {ticker} (signal {signal_id})")
                                
                                # Update sell_all list
                                from main import get_sell_all_list_data, comm_engine
                                sell_all_data = await get_sell_all_list_data()
                                await comm_engine.trigger_sell_all_list_update(sell_all_data)
                                
                            except Exception as position_error:
                                _logger.error(f"Reprocessing: Failed to open position for {ticker}: {position_error}")
                                continue
                                
                        elif is_sell_signal:
                            # For SELL signals: Check if position exists before reprocessing
                            position_exists = await db_manager.is_position_open(ticker)
                            if not position_exists:
                                _logger.warning(f"Reprocessing: Skipping SELL signal {signal_id} for {ticker} - no open position found")
                                continue
                            else:
                                # Mark position as closing
                                await db_manager.mark_position_as_closing(ticker, signal_id)
                                _logger.info(f"Reprocessing: Position marked as closing for {ticker} (signal {signal_id})")
                                
                                # Update sell_all list
                                from main import get_sell_all_list_data, comm_engine
                                sell_all_data = await get_sell_all_list_data()
                                await comm_engine.trigger_sell_all_list_update(sell_all_data)
                        else:
                            # IMPORTANT: Signal is neither clearly BUY nor SELL
                            # This should NOT happen with the improved filtering above
                            _logger.warning(f"Reprocessing: Unknown signal type for {signal_id} - side: '{signal_side}', action: '{signal_action}'. "
                                          f"This indicates a potential bug in signal classification.")
                            continue

                        # 4. Add to approved_signal_queue for forwarding
                        approved_signal_data = {
                            'signal': reprocessed_signal, # The Pydantic Signal model instance
                            'ticker': reprocessed_signal.normalised_ticker(),
                            'approved_at': time.time(),
                            'worker_id': 'reprocessing_engine',
                            'signal_id': signal_id
                        }
                        await approved_signal_queue.put(approved_signal_data)
                        _logger.info(f"Signal {signal_id} for {ticker} re-queued for forwarding.")

                        # 4. Update metrics safely
                        shared_state["signal_metrics"]["signals_approved"] += 1
                        if shared_state["signal_metrics"]["signals_rejected"] > 0:
                            shared_state["signal_metrics"]["signals_rejected"] -= 1
                        else:
                            _logger.warning(f"Reprocessing: Cannot decrement signals_rejected - already at 0")

                        _logger.info(f"✅ Reprocessing: Signal {signal_id} for {ticker} successfully reprocessed and queued for forwarding")
                else:
                    _logger.debug(f"No rejected signals found for ticker {ticker} in the last {window_seconds}s.")
            except Exception as e:
                _logger.error(f"Error during reprocessing for ticker {ticker}: {e}", exc_info=True)

        # Broadcast metrics once after all reprocessing for the cycle
        try:
            from comm_engine import comm_engine
            from main import get_current_metrics
            await comm_engine.broadcast("metrics_update", get_current_metrics())
            _logger.info("Metrics broadcasted after reprocessing cycle.")
        except Exception as e:
            _logger.error(f"Error broadcasting metrics after reprocessing: {e}")


    async def _fetch_all_tickers(self, cfg: FinvizConfig) -> Optional[Set[str]]:
        """
        Fetches all tickers from Finviz based on the configuration.
        Returns a set of tickers, or None if a full update fails critically.
        """
        if not cfg or not cfg.url or cfg.top_n <= 0:
            _logger.error(f"Invalid configuration for fetching: {cfg}")
            return None

        num_pages = math.ceil(cfg.top_n / get_finviz_tickers_per_page())
        _logger.info(f"Fetching {cfg.top_n} tickers across {num_pages} pages from {cfg.url}")

        # Headers to simulate a real browser
        browser_headers = self._browser_headers()
        tasks = []
        
        # Use Elite client or regular client based on configuration
        if settings.FINVIZ_USE_ELITE:
            # Elite mode: sequential processing to avoid rate limits
            client = await self._get_elite_client()
            
            try:
                results = []
                for page_num in range(num_pages):
                    # Finviz uses 1-based indexing for 'r' parameter, but it's an offset.
                    # r=1 is the first page (offset 0 items)
                    # r=101 is the second page (offset 100 items for Elite)
                    # r=201 is the third page (offset 200 items for Elite)
                    start_offset = page_num * get_finviz_tickers_per_page() + 1
                    try:
                        page_result = await self._fetch_single_page_with_retries(client, str(cfg.url), start_offset, page_num + 1)
                        results.append(page_result)
                        
                        # Add diagnostic check for first page in Elite mode
                        if page_num == 0 and len(page_result) < 80:
                            _logger.warning(f"Elite mode diagnostic: First page returned only {len(page_result)} tickers (expected ~100). This may indicate 100-row mode is not active or URL needs adjustment.")
                            
                    except Exception as e:
                        _logger.error(f"Elite sequential fetch failed for page {page_num + 1}: {e}")
                        results.append(e)
                        
            finally:
                await client.aclose()
        else:
            # Use a new client for each full update cycle to ensure fresh connections
            # and respect httpx's recommended usage pattern.
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=httpx.Timeout(30.0, connect=10.0),
                headers=browser_headers
            ) as client:
                for page_num in range(num_pages):
                    # Finviz uses 1-based indexing for 'r' parameter, but it's an offset.
                    # r=1 is the first page (offset 0 items)
                    # r=21 is the second page (offset 20 items for free, 101 for Elite)
                    # r=41 is the third page (offset 40 items for free, 201 for Elite)
                    start_offset = page_num * get_finviz_tickers_per_page() + 1
                    tasks.append(self._fetch_single_page_with_retries(client, str(cfg.url), start_offset, page_num + 1))

                results = await asyncio.gather(*tasks, return_exceptions=True)

        collected_tickers: List[str] = []  # Changed to list to preserve order
        successful_pages = 0
        failed_pages = 0

        for i, res in enumerate(results):
            page_num_for_log = i + 1
            if isinstance(res, list): # Successfully fetched page
                collected_tickers.extend(res)  # Extend to maintain order
                successful_pages += 1
            elif isinstance(res, Exception): # An exception occurred for this page
                _logger.error(f"Error fetching page {page_num_for_log}: {res}")
                failed_pages +=1
            else: # Should not happen if _fetch_single_page_with_retries returns list or raises
                _logger.error(f"Unexpected result type for page {page_num_for_log}: {type(res)}")
                failed_pages +=1
        
        if successful_pages == 0 and num_pages > 0:
            _logger.error(f"All {num_pages} page fetches failed. Cannot update ticker list.")
            return None # Indicate critical failure of the entire update

        if failed_pages > 0:
            _logger.warning(f"Partial success: {successful_pages}/{num_pages} pages fetched. {failed_pages} pages failed.")

        # Remove duplicates while preserving order (first occurrence wins)
        seen = set()
        unique_ordered_tickers = []
        for ticker in collected_tickers:
            if ticker not in seen:
                seen.add(ticker)
                unique_ordered_tickers.append(ticker)

        _logger.info(f"Collected {len(unique_ordered_tickers)} unique tickers from {successful_pages} pages.")
        
        # Trim to exact top_n as requested by user, preserving Finviz ranking order
        if len(unique_ordered_tickers) > cfg.top_n:
            trimmed_tickers = unique_ordered_tickers[:cfg.top_n]
            _logger.info(f"Trimmed ticker list from {len(unique_ordered_tickers)} to exactly {cfg.top_n} tickers as requested.")
            return set(trimmed_tickers)  # Convert back to set for compatibility
        
        return set(unique_ordered_tickers)  # Convert back to set for compatibility


    async def _fetch_single_page_with_retries(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        start_offset: int,
        page_number: int # For logging
    ) -> List[str]: # Returns list of tickers or raises exception if all retries fail
        """Fetches a single page of tickers with rate limiting, concurrency control, and retries."""
        
        # Ensure base_url is normalized before appending parameters
        normalised_base_url = normalise_url(base_url)
        # Append the 'r' parameter for pagination. 'v=150' or other view params should be in base_url.
        # Example: https://finviz.com/screener.ashx?v=150&f=...&r=21
        paginated_url = f"{normalised_base_url}&r={start_offset}"

        max_retries = 5 # Exponential backoff up to 2^4 = 16s, then 2^5 = 32s
        current_retry_delay = 1  # Initial delay in seconds

        for attempt in range(max_retries + 1):
            # Acquire semaphores before making the request
            # finviz_concurrency_current.inc() # If using Prometheus
            async with self.concurrency_semaphore: # Limit concurrent active requests
                # Token bucket for overall rate limiting (requests per minute)
                # This will block if too many requests were made in the last minute window
                # The _generate_tokens_for_rate_limit task replenishes this.
                await self.rate_limit_semaphore.acquire()
                # finviz_requests_total.inc() # If using Prometheus
                _logger.debug(f"Attempt {attempt + 1}/{max_retries + 1} for page {page_number} (offset {start_offset}), URL: {paginated_url}. Concurrency: {self.concurrency_semaphore._value+1}/{get_max_concurrency()}, RateTokens: {self.rate_limit_semaphore._value}")

                try:
                    response = await client.get(paginated_url) # `client` has follow_redirects=False

                    if response.status_code == 301 or response.status_code == 302:
                        redirect_location = response.headers.get('location', 'N/A')
                        
                        # For Elite users, check if redirect is to login page
                        if settings.FINVIZ_USE_ELITE and '/login.ashx' in redirect_location:
                            _logger.warning(f"Session expired for Elite user, attempting re-login for page {page_number}")
                            try:
                                await self._login()
                                # Retry the request immediately with new session
                                response = await client.get(paginated_url)
                                if response.status_code == 200:
                                    # Update the client cookies if needed
                                    client.cookies.update(self._auth_cookies)
                                else:
                                    raise httpx.HTTPStatusError(
                                        f"Login retry failed with status {response.status_code}",
                                        request=response.request,
                                        response=response
                                    )
                            except Exception as login_error:
                                _logger.error(f"Failed to re-login after session expiry: {login_error}")
                                raise httpx.HTTPStatusError(
                                    f"Elite session expired and re-login failed: {login_error}",
                                    request=response.request,
                                    response=response
                                )
                        else:
                            _logger.warning(
                                f"Page {page_number} (offset {start_offset}) request resulted in redirect "
                                f"({response.status_code}) to {redirect_location}. URL: {paginated_url}. "
                                f"This may indicate an issue with URL normalization or Finviz changes."
                            )
                            # This is treated as an error because redirects are disabled.
                            raise httpx.HTTPStatusError(
                                f"Redirect ({response.status_code}) encountered for {paginated_url}",
                                request=response.request,
                                response=response
                            )

                    response.raise_for_status() # Raises for 4xx/5xx errors
                    
                    # Check if this is a CSV export URL for Elite users
                    if settings.FINVIZ_USE_ELITE and 'export.ashx' in paginated_url:
                        # Handle CSV response
                        try:
                            csv_content = response.text
                            tickers_on_page = self._parse_csv_tickers(csv_content)
                            _logger.debug(f"Successfully parsed CSV for page {page_number} (offset {start_offset}): {len(tickers_on_page)} tickers.")
                            return tickers_on_page
                        except Exception as csv_error:
                            _logger.error(f"Failed to parse CSV content for page {page_number}: {csv_error}")
                            # Fall back to HTML parsing
                    
                    # Use httpx's built-in text handling which automatically handles 
                    # all compression types (gzip, brotli, deflate) transparently
                    html = response.text
                    
                    tickers_on_page = parse_tickers_from_html(html)
                    _logger.debug(f"Successfully fetched page {page_number} (offset {start_offset}): {len(tickers_on_page)} tickers.")
                    return tickers_on_page # Success

                except httpx.TimeoutException as e:
                    _logger.warning(f"Timeout on attempt {attempt + 1} for page {page_number} (offset {start_offset}): {e}")
                except httpx.HTTPStatusError as e:
                    _logger.warning(
                        f"HTTP error on attempt {attempt + 1} for page {page_number} (offset {start_offset}): "
                        f"{e.response.status_code} {e.response.reason_phrase}. URL: {e.request.url}"
                    )
                    if e.response.status_code == 403: # Forbidden, often due to bot detection
                        _logger.error(f"Access forbidden (403) for page {page_number}. This might be a block from Finviz.")
                        # Consider a longer, specific delay or stopping for 403s
                    if e.response.status_code == 429: # Too Many Requests
                         _logger.warning(f"Rate limited (429) by Finviz on page {page_number}. Increasing delay.")
                         # Exponential backoff will handle delay, but token bucket should prevent this.
                         # If this happens, MAX_REQ_PER_MIN might be too high or bucket logic flawed.
                except Exception as e: # Catch other errors like custom RuntimeError for redirects, network issues
                    _logger.warning(f"Generic error on attempt {attempt + 1} for page {page_number} (offset {start_offset}): {e}")

                # If not the last attempt, sleep and retry
                if attempt < max_retries:
                    _logger.info(f"Retrying page {page_number} (offset {start_offset}) in {current_retry_delay}s...")
                    await asyncio.sleep(current_retry_delay)
                    current_retry_delay = min(current_retry_delay * 2, 60) # Exponential backoff, cap at 60s
                # else: # All retries failed for this page
                    # finviz_concurrency_current.dec() # If using Prometheus
                    # _logger.error(f"All {max_retries + 1} retries failed for page {page_number} (offset {start_offset}). URL: {paginated_url}")
                    # raise # Re-raise the last caught exception to be gathered by _fetch_all_tickers

            # finviz_concurrency_current.dec() # Ensure decrement happens even if loop breaks or error before client.get
        
        # If loop finishes without returning/raising (i.e., all retries exhausted)
        _logger.error(f"All {max_retries + 1} retries failed for page {page_number} (offset {start_offset}). URL: {paginated_url}")
        # This specific exception will be caught by asyncio.gather in _fetch_all_tickers
        raise RuntimeError(f"Failed to fetch page {page_number} (offset {start_offset}) after {max_retries + 1} attempts.")


    async def stop(self):
        self._running = False
        self.cfg_updated_event.set() # Wake up the main loop if it's sleeping
        _logger.info("FinvizEngine stopping...")
        # Wait for tasks to finish if necessary, e.g., token generator
        # For simplicity, we're not explicitly waiting for the token generator task here.
        # It will exit on the next iteration of its `while self._running` loop.

    async def pause(self):
        """Pause the FinvizEngine refresh cycles."""
        if not self._paused:
            self._paused = True
            self._pause_event.clear()
            _logger.info("FinvizEngine paused.")

    async def resume(self):
        """Resume the FinvizEngine refresh cycles."""
        if self._paused:
            self._paused = False
            self._pause_event.set()
            _logger.info("FinvizEngine resumed.")

    def is_paused(self) -> bool:
        """Check if the engine is currently paused."""
        return self._paused

    def is_running(self) -> bool:
        """Check if the engine is currently running."""
        return self._running

    async def trigger_manual_refresh(self):
        """Trigger a manual refresh of tickers."""
        if self._running:
            if self._paused:
                # If paused, perform a one-time refresh without resuming
                _logger.info("Manual refresh triggered while paused - performing one-time update.")
                try:
                    current_cfg = await self.get_config()
                    await self._update_tickers_safely(current_cfg)
                    _logger.info("Manual refresh completed while engine is paused.")
                except Exception as e:
                    _logger.error(f"Manual refresh failed while paused: {e}")
            else:
                # If not paused, trigger normal refresh cycle
                _logger.info("Manual refresh triggered.")
                self.cfg_updated_event.set()  # Wake up the main loop
        else:
            _logger.warning("Cannot trigger manual refresh: engine is not running.")

    async def logout_elite_session(self) -> None:
        """Logout from Finviz Elite session and revert to free account limits."""
        _logger.info("Logging out from Finviz Elite session...")
        
        # Clear authentication cookies and session
        self._auth_cookies = httpx.Cookies()
        self._session_valid_until = 0
        
        # Update rate limiting and concurrency for free account
        # This will take effect on the next configuration reload
        self.rate_limit_semaphore = asyncio.Semaphore(get_max_req_per_min())
        self.concurrency_semaphore = asyncio.Semaphore(get_max_concurrency())
        
        _logger.info("Finviz Elite session logged out, reverted to free account limits")
        
        # Broadcast status update to reflect changes
        await self._broadcast_status_update()

    async def _periodic_status_broadcast(self):
        """Broadcasts status updates every second regardless of engine state."""
        while self._running:
            try:
                await asyncio.sleep(1)  # Broadcast every second
                if self._current_config:  # Only broadcast if config is loaded
                    await self._broadcast_status_update()
                    _logger.debug("Periodic status broadcast sent")
            except Exception as e:
                _logger.error(f"Error in periodic status broadcast: {e}")

# --- Test stubs / examples (can be moved to a test file) ---
async def mock_admin_ws_broadcaster(event_type: str, data: Any):
    print(f"[Mock WS Broadcast] Event: {event_type}, Data: {data}")

async def main_test():
    # Create a dummy finviz_config.json for testing
    initial_config_for_file = {
        "finviz_url": "https://finviz.com/screener.ashx?v=111", # Basic URL
        "top_n": 25, # Test with a small number requiring 2 pages
        "refresh_interval_sec": 15 # Test refresh
    }
    persist_finviz_config_from_dict(initial_config_for_file)


    shared_state_test = {}
    engine = FinvizEngine(shared_state_test, mock_admin_ws_broadcaster)

    # Start the engine in the background
    engine_task = asyncio.create_task(engine.run())

    await asyncio.sleep(5) # Let it run for an initial fetch

    print(f"Initial tickers: {shared_state_test.get('tickers')}")

    # Test config update
    try:
        print("Attempting to update config...")
        await engine.update_config({
            "url": "https://finviz.com/screener.ashx?v=150&f=sh_avgvol_o500,sh_price_o10", # More complex URL
            "top_n": 5,  # Reduce top_n
            "refresh": 10 # Change refresh
        })
        print("Config update submitted. Waiting for next cycle...")
    except ValueError as e:
        print(f"Config update rejected: {e}")
    except Exception as e:
        print(f"Error during config update: {e}")


    await asyncio.sleep(20) # Let it run a few more cycles with new config
    print(f"Tickers after config update: {shared_state_test.get('tickers')}")

    # Test rate limit validation
    try:
        print("Attempting to update config to violate rate limits...")
        await engine.update_config({ "top_n": 200, "refresh": 5}) # ~240 req/min
    except ValueError as e:
        print(f"Config update (rate limit test) correctly rejected: {e}")

    await asyncio.sleep(10)

    # Test pause/resume
    print("Pausing engine...")
    await engine.pause()
    await asyncio.sleep(5) # Wait to observe pause effect

    print("Resuming engine...")
    await engine.resume()

    # Stop the engine
    await engine.stop()
    try:
        await asyncio.wait_for(engine_task, timeout=10) # Wait for engine to shut down
        print("Engine stopped gracefully.")
    except asyncio.TimeoutError:
        print("Engine did not stop in time.")
    except Exception as e:
        print(f"Exception while waiting for engine to stop: {e}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # asyncio.run(main_test())
    # To run the test:
    # 1. Ensure finviz.py has `parse_tickers_from_html`, `load_finviz_config`, `persist_finviz_config_from_dict`
    # 2. Create a dummy finviz_config.json or let `persist_finviz_config_from_dict` create it.
    # 3. Uncomment asyncio.run(main_test()) and run `python finviz_engine.py`
    # Note: This test will make actual HTTP requests to finviz.com.
    # For unit tests, mock httpx.AsyncClient and file operations.
    pass

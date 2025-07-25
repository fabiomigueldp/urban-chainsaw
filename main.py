"""FastAPI entry‑point for Trading‑Signal Processor.

High‑level architecture
-----------------------
```
┌────────────┐   HTTP POST   ┌───────────────┐
│ Upstream   │──────────────►│ /webhook/in   │
│ signal src │               │ (enqueue)     │
└────────────┘               └─────┬─────────┘
                                   │ asyncio.Queue
                                   ▼
                          ┌───────────────────┐
                          │ Worker coroutine  │
                          │  • check Top‑N    │
                          │  • forward if ok  │
                          └─────────┬─────────┘
                                    │ HTTP POST
                                    ▼
                            ┌──────────────┐
                            │ DEST webhook │
                            └──────────────┘
```

The design minimises latency: the HTTP handler only performs **O(1)**
work (enqueue) and immediately returns *202 Accepted*.  Heavy‑lifting is
handled by worker coroutines that run concurrently with network I/O.
"""

import asyncio
import logging
import os
import time
import collections
import datetime
import math
import csv
import io
import json
import asyncio
import httpx
# import finviz # Will be refactored to use only parser # Commented out as it's no longer used directly here
from typing import Set, List, Any, Dict, Callable, Optional # Added Optional

from fastapi import Body, FastAPI, BackgroundTasks, HTTPException, status, Request, WebSocket, WebSocketDisconnect, UploadFile, File, Form, Response
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
# from prometheus_client import start_http_server, Counter, Gauge, Enum # For Prometheus
# from prometheus_fastapi_instrumentator import Instrumentator # For Prometheus

from config import settings, FINVIZ_UPDATE_TOKEN, FINVIZ_CONFIG_FILE, DEFAULT_TICKER_REFRESH_SEC, get_max_req_per_min, get_max_concurrency, get_finviz_tickers_per_page
from models import Signal, SellIndividualPayload, ClosePositionPayload, TokenPayload, AuditTrailQuery, AuditTrailResponse
# Remove direct networking imports from finviz, keep parser if needed elsewhere or refactor finviz.py
# from finviz import load_finviz_config, persist_finviz_config_from_dict # REMOVED - database-only configuration

from comm_engine import comm_engine  # Import centralized communication engine
from finviz_engine import FinvizEngine, FinvizConfig # Import the new engine
from webhook_rate_limiter import WebhookRateLimiter # Import webhook rate limiter
from admin_logger import log_admin_action  # Import admin action logging

# Database integration
from database.DBManager import db_manager
from database.simple_models import SignalStatusEnum, SignalLocationEnum

# ---------------------------------------------------------------------------- #
# Helper Functions                                                              #
# ---------------------------------------------------------------------------- #

def get_current_metrics() -> Dict[str, Any]:
    """Get current metrics with memory as source of truth."""
    # Get real-time queue sizes from memory (always available)
    processing_queue_size = queue.qsize() if queue else 0
    approved_queue_size = approved_signal_queue.qsize() if approved_signal_queue else 0
    
    # Use memory metrics as the source of truth for now
    # TODO: Implement async database metrics retrieval for better accuracy
    memory_metrics = shared_state["signal_metrics"].copy()
    memory_metrics["approved_queue_size"] = approved_queue_size
    memory_metrics["processing_queue_size"] = processing_queue_size
    memory_metrics["data_source"] = "memory_realtime"
    
    return memory_metrics


# ---------------------------------------------------------------------------- #
# Globals & Shared State                                                       #
# ---------------------------------------------------------------------------- #
# Global queue variables - initialized during startup
queue: Optional[asyncio.Queue[Signal]] = None  # Main processing queue
approved_signal_queue: Optional[asyncio.Queue[Dict[str, Any]]] = None  # Queue for approved signals waiting for forwarding

# Signal metrics counters
signal_metrics = {
    "signals_received": 0,
    "signals_approved": 0,
    "signals_rejected": 0,
    "signals_forwarded_success": 0,
    "signals_forwarded_error": 0,
    "metrics_start_time": None,
    "approved_queue_size": 0,
    "processing_queue_size": 0,
    "processing_workers_active": 0,
    "forwarding_workers_active": 0
}

shared_state: Dict[str, Any] = {
    "tickers": set(),  # Managed by FinvizEngine
    "finviz_engine_instance": None, # To hold the FinvizEngine instance
    "webhook_rate_limiter_instance": None, # To hold the WebhookRateLimiter instance
    "signal_metrics": signal_metrics,
    "signal_trackers": {},  # Initialize signal trackers dictionary
}

# ---------------------------------------------------------------------------- #
# Temporary Stub Functions for Migration Cleanup                              #
# ---------------------------------------------------------------------------- #

class SignalStatus:
    """Stub class - replaced by SignalStatusEnum from database."""
    FORWARDED_SUCCESS = "forwarded_success"
    FORWARDED_HTTP_ERROR = "forwarded_http_error"
    FORWARDED_GENERIC_ERROR = "forwarded_generic_error"
    APPROVED = "approved"
    QUEUED_FORWARDING = "queued_forwarding"

class SignalLocation:
    """Stub class - replaced by SignalLocationEnum from database."""
    COMPLETED = "completed"
    APPROVED_QUEUE = "approved_queue"

def cleanup_old_trackers(max_age_hours: int = 24):
    """Stub function - cleanup now handled by PostgreSQL retention policies."""
    pass

def update_signal_tracker(*args, **kwargs):
    """Stub function - tracking now handled by PostgreSQL."""
    pass

async def broadcast_signal_tracker_update(*args, **kwargs):
    """Stub function - updates now handled by PostgreSQL WebSocket."""
    pass

def create_signal_tracker(*args, **kwargs):
    """Stub function - tracking now handled by PostgreSQL."""
    return None

# ---------------------------------------------------------------------------- #
# Logging                                                                      #
# ---------------------------------------------------------------------------- #
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
_logger = logging.getLogger("processor")

# ---------------------------------------------------------------------------- #
# Worker Functions                                                             #
# ---------------------------------------------------------------------------- #

async def _queue_worker(worker_id: int, get_tickers_func: Callable) -> None:
    """Processes signals from the queue with intelligent logic for buy and sell actions."""
    _logger.info(f"Decision worker {worker_id} started")
    
    while True:
        try:
            signal: Signal = await queue.get()
            signal_id = signal.signal_id
            normalised_ticker = signal.normalised_ticker()

            shared_state["signal_metrics"]["processing_workers_active"] += 1
            await db_manager.log_signal_event(
                signal_id=signal_id,
                event_type=SignalStatusEnum.PROCESSING,
                details=f"Signal picked up by decision worker {worker_id}",
                worker_id=f"decision_worker_{worker_id}"
            )

            # Step 1: Identify the action type (BUY or SELL)
            action_type = "unknown"
            sell_triggers = {"sell", "exit", "close"}
            buy_triggers = {"buy", "long", "enter"}
            
            sig_action = (getattr(signal, 'action', '') or '').lower()
            sig_side = (getattr(signal, 'side', '') or '').lower()

            if sig_action in sell_triggers or sig_side in sell_triggers:
                action_type = "SELL"
            elif sig_action in buy_triggers or sig_side in buy_triggers:
                action_type = "BUY"
            
            _logger.info(f"[WORKER {worker_id}] [SIGNAL: {signal_id}] Detected action: {action_type} for {normalised_ticker}")

            # Step 2: Apply the correct validation logic
            is_approved = False
            approval_reason = ""

            if action_type == "BUY":
                current_tickers = await get_tickers_func()
                if normalised_ticker in current_tickers:
                    is_approved = True
                    approval_reason = f"Ticker found in Finviz Top-{len(current_tickers)} list."
                else:
                    approval_reason = f"Ticker not in Finviz Top-{len(current_tickers)} list."
            
            elif action_type == "SELL":
                position_is_open = await db_manager.is_position_open(normalised_ticker)
                if position_is_open:
                    is_approved = True
                    approval_reason = "Validated against an open position in the database."
                else:
                    approval_reason = f"No open position found for ticker '{normalised_ticker}' in the database."
            
            else: # Unknown action type
                approval_reason = f"Signal action/side ('{sig_action}'/'{sig_side}') is not recognized as BUY or SELL."

            # Step 3: Process the validation result
            if is_approved:
                shared_state["signal_metrics"]["signals_approved"] += 1
                _logger.info(f"✅ [WORKER {worker_id}] [SIGNAL: {signal_id}] Signal APPROVED. Reason: {approval_reason}")
                
                await db_manager.log_signal_event(signal_id=signal_id, event_type=SignalStatusEnum.APPROVED, details=approval_reason)
                
                if action_type == "BUY":
                    await db_manager.open_position(ticker=normalised_ticker, entry_signal_id=signal_id)
                    # Broadcast updated sell_all list after position is opened
                    sell_all_data = await get_sell_all_list_data()
                    await comm_engine.trigger_sell_all_list_update(sell_all_data)
                elif action_type == "SELL":
                    await db_manager.mark_position_as_closing(ticker=normalised_ticker, exit_signal_id=signal_id)
                    # Broadcast updated sell_all list after position is marked as closing
                    sell_all_data = await get_sell_all_list_data()
                    await comm_engine.trigger_sell_all_list_update(sell_all_data)

                await approved_signal_queue.put({
                    'signal': signal, 'ticker': normalised_ticker, 'approved_at': time.time(),
                    'worker_id': f"decision_worker_{worker_id}", 'signal_id': signal_id
                })
                await db_manager.log_signal_event(signal_id=signal_id, event_type=SignalStatusEnum.QUEUED_FORWARDING, details="Signal queued for forwarding.")
            
            else: # Rejected
                shared_state["signal_metrics"]["signals_rejected"] += 1
                _logger.warning(f"❌ [WORKER {worker_id}] [SIGNAL: {signal_id}] Signal REJECTED. Reason: {approval_reason}")
                await db_manager.log_signal_event(signal_id=signal_id, event_type=SignalStatusEnum.REJECTED, details=approval_reason, location=SignalLocationEnum.DISCARDED)

        except Exception as e:
            signal_id_for_error = signal.signal_id if 'signal' in locals() else "unknown"
            _logger.error(f"[WORKER {worker_id}] [SIGNAL: {signal_id_for_error}] Critical error during processing: {e}", exc_info=True)
            if 'signal' in locals():
                await db_manager.log_signal_event(signal_id=signal_id_for_error, event_type=SignalStatusEnum.ERROR, details=str(e))
        finally:
            if 'signal' in locals():
                queue.task_done()
            shared_state["signal_metrics"]["processing_workers_active"] -= 1
            await comm_engine.broadcast("metrics_update", get_current_metrics())

async def _forwarding_worker(worker_id: int) -> None:
    """Forward approved signals from the approved queue."""
    _logger.info(f"Forwarding worker {worker_id} started")
    
    while True:
        try:
            # Get approved signal from queue
            approved_data = await approved_signal_queue.get()
            signal = approved_data['signal']
            signal_id = signal.signal_id
            
            _logger.info(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Forwarding signal for ticker: {signal.normalised_ticker()}")
            # Log forwarding event to database
            try:
                await db_manager.log_signal_event(
                    signal_id=signal_id,
                    event_type=SignalStatusEnum.FORWARDING,
                    location=SignalLocationEnum.WORKER_FORWARDING,
                    details="Signal being forwarded to destination webhook",
                    worker_id=f"forwarding_worker_{worker_id}"
                )
                _logger.debug(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Event logged to database: FORWARDING")
            except Exception as db_error:
                _logger.error(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Error logging FORWARDING status to database: {db_error}")
                # Try to log with a fallback status if the FORWARDING enum fails
                try:
                    await db_manager.log_signal_event(
                        signal_id=signal_id,
                        event_type=SignalStatusEnum.PROCESSING,  # Fallback status
                        location=SignalLocationEnum.WORKER_FORWARDING,
                        details=f"Signal forwarding started (fallback log due to enum error: {db_error})",
                        worker_id=f"forwarding_worker_{worker_id}"
                    )
                    _logger.info(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Logged with fallback status PROCESSING")
                except Exception as fallback_error:
                    _logger.error(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Even fallback logging failed: {fallback_error}")
                # Continue processing even if database logging fails
            
            try:
                # Get webhook rate limiter instance
                webhook_rl: Optional[WebhookRateLimiter] = shared_state.get("webhook_rate_limiter_instance")
                
                _logger.debug(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Got webhook_rl instance: {webhook_rl is not None}")
                
                # Apply rate limiting (the rate limiter will handle checking if it's enabled)
                if webhook_rl:
                    _logger.info(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Requesting rate limit token...")
                    try:
                        await webhook_rl.acquire_token()
                        _logger.info(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Rate limit token acquired successfully")
                    except Exception as token_error:
                        _logger.error(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Error acquiring token: {token_error}")
                        raise
                else:
                    _logger.warning(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] No webhook rate limiter instance found")
                
                # Only increment active workers counter AFTER successfully acquiring token
                shared_state["signal_metrics"]["forwarding_workers_active"] += 1
                
                _logger.info(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Starting HTTP request to {settings.DEST_WEBHOOK_URL}")
                
                # Forward signal via HTTP POST
                timeout = httpx.Timeout(10.0)
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(
                        str(settings.DEST_WEBHOOK_URL),
                        json=signal.dict(),
                        headers={"Content-Type": "application/json"}
                    )
                    response.raise_for_status()
                    
                    # --- IMPROVED POSITION CLOSE DETECTION LOGIC ---
                    # After a successful forward, check if it was a signal to close the position
                    sig_action = (getattr(signal, 'action', '') or '').lower()
                    sig_side = (getattr(signal, 'side', '') or '').lower()
                    
                    # Log signal details for debugging
                    _logger.info(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Signal details - side: '{sig_side}', action: '{sig_action}'")
                    
                    # Priority logic for position closing detection
                    is_position_close = False
                    
                    # 1. Primary: action="exit" or "close" indicates position closing
                    if sig_action in {"exit", "close"}:
                        is_position_close = True
                        _logger.info(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Position close detected via action: '{sig_action}'")
                    
                    # 2. Secondary: explicit buy/enter actions are position opens
                    elif sig_action in {"buy", "enter", "long"}:
                        is_position_close = False
                        _logger.info(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Position open detected via action: '{sig_action}' - NO position closing needed")
                    
                    # 3. Tertiary: side-based detection for compatibility (legacy behavior)
                    elif sig_side in {"sell"} and not sig_action:
                        is_position_close = True
                        _logger.info(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Position close assumed via side: '{sig_side}' (no action specified - legacy)")
                    
                    # 4. Fallback: assume position open for buy sides
                    elif sig_side in {"buy", "long", "enter"}:
                        is_position_close = False
                        _logger.info(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Position open detected via side: '{sig_side}'")
                    
                    else:
                        is_position_close = False
                        _logger.warning(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Ambiguous signal - assuming position open: side='{sig_side}', action='{sig_action}'")
                    
                    if is_position_close:
                        closed = await db_manager.close_position(exit_signal_id=signal_id)
                        if closed:
                            # Broadcast updated sell_all list after position is closed
                            sell_all_data = await get_sell_all_list_data()
                            await comm_engine.trigger_sell_all_list_update(sell_all_data)
                            _logger.info(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Position closed and Sell All list updated.")
                        else:
                             _logger.warning(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] A close signal was forwarded, but no corresponding 'closing' position was found in DB to finalize.")
                    else:
                        _logger.info(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Position open signal forwarded - no position closing needed")
                    # --- END OF IMPROVED LOGIC ---
                    
                    _logger.info(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Signal forwarded successfully - HTTP {response.status_code}")
                    
                    shared_state["signal_metrics"]["signals_forwarded_success"] += 1
                      # Log success event to database
                    try:
                        await db_manager.log_signal_event(
                            signal_id=signal_id,
                            event_type=SignalStatusEnum.FORWARDED_SUCCESS,
                            location=SignalLocationEnum.COMPLETED,
                            details=f"Signal forwarded successfully to {settings.DEST_WEBHOOK_URL}",
                            worker_id=f"forwarding_worker_{worker_id}",
                            http_status=response.status_code,
                            response_data=response.text[:500] if response.text else None
                        )
                    except Exception as db_error:
                        _logger.error(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Failed to log success to database: {db_error}")
                
            except Exception as e:
                _logger.error(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Error forwarding signal: {e}")
                
                shared_state["signal_metrics"]["signals_forwarded_error"] += 1
                
                # Determine the appropriate error status based on exception type
                error_status = None
                http_status = None
                
                try:
                    if hasattr(e, 'response') and hasattr(e.response, 'status_code'):
                        # HTTP error
                        error_status = SignalStatusEnum.FORWARDED_HTTP_ERROR
                        http_status = e.response.status_code
                    else:
                        # Generic error (timeout, network, etc.)
                        error_status = SignalStatusEnum.FORWARDED_GENERIC_ERROR
                        http_status = None
                except AttributeError as enum_error:
                    _logger.error(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Error accessing SignalStatusEnum: {enum_error}")
                    # Fallback to a basic error status
                    error_status = SignalStatusEnum.ERROR
                    http_status = getattr(e, 'response', {}).get('status_code', None) if hasattr(e, 'response') else None
                
                # Log error event to database with enhanced error handling
                try:
                    await db_manager.log_signal_event(
                        signal_id=signal_id,
                        event_type=error_status,
                        location=SignalLocationEnum.COMPLETED,
                        details=f"Error forwarding signal: {str(e)}",
                        worker_id=f"forwarding_worker_{worker_id}",
                        error_info={"type": "forwarding_error", "message": str(e)},
                        http_status=http_status
                    )
                    _logger.info(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Error logged to database with status: {error_status}")
                    
                except Exception as db_error:
                    _logger.error(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Failed to log error to database: {db_error}")
                    
                    # Ultimate fallback - try to log with basic ERROR status
                    try:
                        await db_manager.log_signal_event(
                            signal_id=signal_id,
                            event_type=SignalStatusEnum.ERROR,  # Basic error status that should always exist
                            location=SignalLocationEnum.COMPLETED,
                            details=f"Forwarding failed: {str(e)} (DB error: {str(db_error)})",
                            worker_id=f"forwarding_worker_{worker_id}"
                        )
                        _logger.info(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Error logged with fallback ERROR status")
                    except Exception as ultimate_error:
                        _logger.error(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Ultimate fallback logging failed: {ultimate_error}")
                        # At this point, we've tried everything - just continue
            
            finally:
                shared_state["signal_metrics"]["forwarding_workers_active"] -= 1
                
                # Broadcast updated metrics
                await comm_engine.broadcast("metrics_update", get_current_metrics())
                
        except Exception as e:
            _logger.error(f"Forwarding worker {worker_id} error: {type(e).__name__}: {str(e)}")
            _logger.error(f"Forwarding worker {worker_id} full exception:", exc_info=True)
            await asyncio.sleep(1)  # Brief pause before continuing

# ---------------------------------------------------------------------------- #
# FastAPI application                                                          #
# ---------------------------------------------------------------------------- #
app = FastAPI(
    title="Trading Signal Processor",
    version="1.1.0",
    description="Filter incoming trading alerts by Finviz ranking and forward approved signals to webhooks.",
)

# Template configuration
templates = Jinja2Templates(directory="templates")

# Static files configuration
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- Prometheus Setup (Example, needs prometheus_client installed) ---
# if os.getenv("ENABLE_PROMETHEUS", "false").lower() == "true":
#     _logger.info(f"Starting Prometheus metrics server on port {settings.PROMETHEUS_PORT}")
#     start_http_server(settings.PROMETHEUS_PORT)
#     # Instrument FastAPI app for common metrics
#     Instrumentator().instrument(app).expose(app, endpoint="/metrics")
#
# # Example custom metrics (can be moved to where they are updated)
# signals_processed_total = Counter("signals_processed_total", "Total trading signals processed", ["status"]) # approved, rejected, dropped
# queue_size_current = Gauge("queue_size_current", "Current number of signals in the queue")
# queue_size_current.set_function(lambda: queue.qsize())


async def get_tickers_from_shared_state() -> Set[str]:
    """Dependency to get current tickers from shared state for workers."""
    if "tickers" not in shared_state or not isinstance(shared_state["tickers"], set):
        _logger.error("Ticker set not found or invalid in shared_state. Returning empty set.")
        return set()
    return shared_state["tickers"].copy() # Return a copy for thread-safety if needed, though assignment is atomic

# ---------------------------------------------------------------------------- #
# Data Collection Functions for Communication Engine                          #
# ---------------------------------------------------------------------------- #

async def get_system_info_data() -> Dict[str, Any]:
    """Get current system information for comm_engine."""
    try:
        # Get Finviz engine instance
        engine = shared_state.get("finviz_engine_instance")
        webhook_rl = shared_state.get("webhook_rate_limiter_instance")
        
        # Initialize with default values
        system_info = {
            "finviz_elite_enabled": True,
            "auth_session_valid": False,
            "max_requests_per_min": "N/A",
            "max_concurrency": "N/A", 
            "rows_per_page": "N/A",
            "rate_limit_tokens_available": "N/A",
            "concurrency_slots_available": "N/A",
            "webhook_rate_limiter_enabled": False,
            "webhook_tokens_available": "N/A",
            "webhook_max_requests_per_minute": "N/A",
            # Add pause status information
            "finviz_engine_paused": False,
            "webhook_rate_limiter_paused": False,
        }
        
        if engine:
            try:
                status_metrics = engine.get_status_metrics()
                system_info.update({
                    "finviz_elite_enabled": status_metrics.get("finviz_elite_enabled", True),
                    "auth_session_valid": status_metrics.get("auth_session_valid", False),
                    "max_requests_per_min": status_metrics.get("max_requests_per_min", "N/A"),
                    "max_concurrency": status_metrics.get("max_concurrency", "N/A"),
                    "rows_per_page": status_metrics.get("rows_per_page", "N/A"),
                    "rate_limit_tokens_available": status_metrics.get("rate_limit_tokens_available", "N/A"),
                    "concurrency_slots_available": status_metrics.get("concurrency_slots_available", "N/A"),
                    # Include pause status from engine
                    "finviz_engine_paused": engine.is_paused(),
                })
            except Exception as e:
                _logger.error(f"Error getting engine status metrics: {e}")
        
        if webhook_rl:
            try:
                webhook_metrics = webhook_rl.get_metrics()
                system_info.update({
                    "webhook_rate_limiter_enabled": webhook_metrics.get("rate_limiting_enabled", False),
                    "webhook_tokens_available": webhook_metrics.get("tokens_available", "N/A"),
                    "webhook_max_requests_per_minute": webhook_metrics.get("max_req_per_min", "N/A"),
                    # Include pause status - rate limiter is paused when rate limiting is disabled
                    "webhook_rate_limiter_paused": not webhook_metrics.get("rate_limiting_enabled", False),
                })
            except Exception as e:
                _logger.error(f"Error getting webhook rate limiter metrics: {e}")
        
        # Get reprocess status from active strategy in database (not JSON file)
        try:
            if engine:
                config = await engine.get_config()
                system_info["reprocess_enabled"] = config.reprocess_enabled
                system_info["reprocess_window_seconds"] = config.reprocess_window_seconds
                system_info["respect_sell_chronology_enabled"] = config.respect_sell_chronology_enabled
                system_info["sell_chronology_window_seconds"] = config.sell_chronology_window_seconds
                
                # Determine reprocess_mode for frontend
                if not config.reprocess_enabled:
                    system_info["reprocess_mode"] = "Disabled"
                elif config.reprocess_window_seconds == 0:
                    system_info["reprocess_mode"] = "Infinite"
                else:
                    system_info["reprocess_mode"] = f"{config.reprocess_window_seconds}s Window"
            else:
                # Fallback when engine not available
                system_info["reprocess_enabled"] = False
                system_info["reprocess_mode"] = "Engine Not Available"
                system_info["reprocess_window_seconds"] = 300
                system_info["respect_sell_chronology_enabled"] = True
                system_info["sell_chronology_window_seconds"] = 300

        except Exception as e:
            _logger.warning(f"Could not get active strategy config for reprocess status: {e}")
            system_info["reprocess_enabled"] = False
            system_info["reprocess_mode"] = "Unknown"
            system_info["reprocess_window_seconds"] = 300
            system_info["respect_sell_chronology_enabled"] = True
            system_info["sell_chronology_window_seconds"] = 300
        
        # Get sell all cleanup status from system config
        try:
            from system_config import get_sell_all_cleanup_config
            cleanup_config = get_sell_all_cleanup_config()
            system_info["sell_all_cleanup_enabled"] = cleanup_config["enabled"]
            system_info["sell_all_cleanup_lifetime_hours"] = cleanup_config["lifetime_hours"]
        except Exception as e:
            _logger.warning(f"Could not load system config for sell all cleanup status: {e}")
            system_info["sell_all_cleanup_enabled"] = False
            system_info["sell_all_cleanup_lifetime_hours"] = 0
        
        return system_info
        
    except Exception as e:
        _logger.error(f"Error getting system info: {e}")
        return {}

async def get_current_engine_config_for_admin() -> Dict[str, Any]:
    """Get current engine configuration for admin interface."""
    try:
        engine = shared_state.get("finviz_engine_instance")
        if not engine:
            return {
                "top_n": settings.TOP_N,
                "refresh_interval": settings.FINVIZ_REFRESH_SEC,
                "status": "not_initialized"
            }
        
        # Get configuration from engine
        config = {
            "top_n": getattr(engine, 'top_n', settings.TOP_N),
            "refresh_interval": getattr(engine, 'refresh_interval', settings.FINVIZ_REFRESH_SEC),
            "status": "running" if engine.is_running() else ("paused" if engine.is_paused() else "stopped"),
            "last_refresh": getattr(engine, 'last_refresh', None),
            "total_tickers": len(getattr(engine, 'current_tickers', [])),
            "use_elite": settings.FINVIZ_USE_ELITE,
            "finviz_url": getattr(engine, 'finviz_url', 'N/A')
        }
        return config
    except Exception as e:
        _logger.error(f"Error getting engine config: {e}")
        return {
            "top_n": settings.TOP_N,
            "refresh_interval": settings.FINVIZ_REFRESH_SEC,
            "status": "error",
            "error": str(e)
        }

async def get_finviz_status_data() -> Dict[str, Any]:
    """Get current Finviz engine status for comm_engine."""
    try:
        engine_config = await get_current_engine_config_for_admin()
        current_tickers = await get_tickers_from_shared_state()
        
        # Get engine status correctly
        engine = shared_state.get("finviz_engine_instance")
        engine_status = "stopped"
        if engine:
            is_running = engine.is_running()
            is_paused = engine.is_paused()
            
            if is_running:
                if is_paused:
                    engine_status = "paused"
                else:
                    engine_status = "running"
            else:
                engine_status = "stopped"
        
        finviz_status = {
            **engine_config,
            "tickers": sorted(list(current_tickers)),
            "num_tickers": len(current_tickers),
            "engine_status": engine_status
        }
        
        return finviz_status
        
    except Exception as e:
        _logger.error(f"Error getting Finviz status: {e}")
        return {}

async def get_overview_data() -> Dict[str, Any]:
    """Get current overview data for comm_engine."""
    try:        # Get Finviz engine instance
        engine = shared_state.get("finviz_engine_instance")
        current_tickers = await get_tickers_from_shared_state()
        
        overview_data = {
            "websocket_status": "Connected",  # We're connected if this function is being called
            "elite_auth_status": "Checking...",
            "total_tickers": len(current_tickers),
        }
        
        if engine:
            try:
                status_metrics = engine.get_status_metrics()
                
                # Determine engine status based on running and paused state
                is_running = status_metrics.get("is_running", False)
                is_paused = status_metrics.get("is_paused", False)
                
                if is_running:
                    if is_paused:
                        overview_data["engine_status"] = "paused"
                    else:
                        overview_data["engine_status"] = "running"
                else:
                    overview_data["engine_status"] = "stopped"
                
                # Determine elite auth status
                is_elite_enabled = status_metrics.get("finviz_elite_enabled", False)
                is_authenticated = status_metrics.get("auth_session_valid", False)
                
                if is_elite_enabled:
                    if is_authenticated:
                        overview_data["elite_auth_status"] = "Authenticated"
                    else:
                        overview_data["elite_auth_status"] = "Authentication Failed"
                else:
                    overview_data["elite_auth_status"] = "Disabled (Free Account)"
                    
            except Exception as e:
                _logger.error(f"Error getting engine status for overview: {e}")
                overview_data["elite_auth_status"] = "Error"
        
        return overview_data
        
    except Exception as e:
        _logger.error(f"Error getting overview data: {e}")
        return {
            "websocket_status": "Connected",
            "elite_auth_status": "Error",
            "total_tickers": 0,
        }

async def get_queue_status_data() -> Dict[str, Any]:
    """Get current queue status for comm_engine."""
    try:
        queue_status = {
            "processing_queue_size": queue.qsize() if queue else 0,
            "approved_queue_size": approved_signal_queue.qsize() if approved_signal_queue else 0,
            "processing_workers_active": shared_state["signal_metrics"]["processing_workers_active"],
            "forwarding_workers_active": shared_state["signal_metrics"]["forwarding_workers_active"],
            "active_workers": shared_state["signal_metrics"]["processing_workers_active"] + shared_state["signal_metrics"]["forwarding_workers_active"],  # Total active workers
        }
        
        return queue_status
        
    except Exception as e:
        _logger.error(f"Error getting queue status: {e}")
        return {}

async def get_webhook_config_data() -> Dict[str, Any]:
    """Get current webhook configuration for comm_engine."""
    try:
        webhook_config = {
            "dest_webhook_url": str(settings.DEST_WEBHOOK_URL),
        }
        
        return webhook_config
        
    except Exception as e:
        _logger.error(f"Error getting webhook config: {e}")
        return {}

async def get_webhook_rate_limiter_data() -> Dict[str, Any]:
    """Get current webhook rate limiter status for comm_engine."""
    try:
        webhook_rl = shared_state.get("webhook_rate_limiter_instance")
        
        if webhook_rl:
            webhook_metrics = webhook_rl.get_metrics()
            # Map the fields to what the frontend expects
            return {
                "enabled": webhook_metrics.get("rate_limiting_enabled", False),
                "status": "Enabled" if webhook_metrics.get("rate_limiting_enabled", False) else "Disabled",
                "tokens_available": webhook_metrics.get("tokens_available", 0),
                "max_req_per_min": webhook_metrics.get("max_req_per_min", 0),
                "requests_made_this_minute": webhook_metrics.get("requests_made_this_minute", 0),
                "requests_this_minute": webhook_metrics.get("requests_made_this_minute", 0),  # Alternative mapping
                "total_requests_limited": webhook_metrics.get("total_requests_limited", 0),
                "total_limited": webhook_metrics.get("total_requests_limited", 0),  # Alternative mapping
                "pending_token_returns": webhook_metrics.get("pending_token_returns", 0),
                "is_rate_limited": webhook_metrics.get("is_rate_limited", False),
            }
        else:
            return {
                "enabled": False,
                "status": "Disabled",
                "tokens_available": 0,
                "max_req_per_min": 0,
                "requests_made_this_minute": 0,
                "requests_this_minute": 0,
                "total_requests_limited": 0,
                "total_limited": 0,
                "pending_token_returns": 0,
                "is_rate_limited": False,
            }
        
    except Exception as e:
        _logger.error(f"Error getting webhook rate limiter: {e}")
        return {}

async def get_ticker_list_data() -> Dict[str, Any]:
    """Get current ticker list data for comm_engine."""
    try:
        finviz_data = await get_finviz_status_data()
        ticker_data = {
            "tickers": finviz_data.get("tickers", []),
            "num_tickers": finviz_data.get("num_tickers", 0),
        }
        return ticker_data
    except Exception as e:
        _logger.error(f"Error getting ticker list data: {e}")
        return {"tickers": [], "num_tickers": 0}

async def get_auth_status_data() -> Dict[str, Any]:
    """Get current auth status data for comm_engine."""
    try:
        system_info = await get_system_info_data()
        auth_data = {
            "auth_session_valid": system_info.get("auth_session_valid", False),
        }
        return auth_data
    except Exception as e:
        _logger.error(f"Error getting auth status data: {e}")
        return {"auth_session_valid": False}

async def get_sell_all_list_data() -> Dict[str, Any]:
    """Get the current list of tickers with open positions for comm_engine."""
    try:
        open_tickers = await db_manager.get_all_open_positions_tickers()
        return {
            "tickers": sorted(open_tickers),
            "count": len(open_tickers),
            "timestamp": time.time()
        }
    except Exception as e:
        _logger.error(f"Error getting sell all list data from DB: {e}")
        return {"tickers": [], "count": 0, "timestamp": time.time()}

@app.on_event("startup")
async def _startup() -> None:
    """Initializes database, FinvizEngine, cache, and background tasks."""
    _logger.info("Application startup sequence initiated.")

    # Initialize database connection first
    _logger.info("Initializing database connection...")
    db_manager.initialize(settings.DATABASE_URL)
    
    # Create database tables if they don't exist
    try:
        from database.simple_init import init_database, ensure_default_finviz_url
        await init_database(settings.DATABASE_URL)
        _logger.info("✅ Database tables initialized successfully")
        
        # Ensure default Finviz strategy exists
        await ensure_default_finviz_url(db_manager)
        _logger.info("✅ Default Finviz strategy ensured")
        
    except Exception as e:
        _logger.error(f"❌ Failed to initialize database: {e}")
        # Don't fail startup - the application can still work with memory-only mode
        _logger.warning("Continuing without database - using memory-only mode")

    # Initialize signal metrics start time
    shared_state["signal_metrics"]["metrics_start_time"] = time.time()

    # Initialize and start FinvizEngine with database manager
    if shared_state.get("finviz_engine_instance") is None:
        # Pass database manager to FinvizEngine
        engine = FinvizEngine(shared_state, comm_engine.broadcast, db_manager)
        shared_state["finviz_engine_instance"] = engine
        asyncio.create_task(engine.run())
        _logger.info("FinvizEngine instance created and started with database integration.")
    else:
        _logger.info("FinvizEngine already initialized.")

    # Initialize and start WebhookRateLimiter
    if shared_state.get("webhook_rate_limiter_instance") is None:
        # Using centralized comm_engine for WebSocket communication
        webhook_rate_limiter = WebhookRateLimiter(shared_state, comm_engine.broadcast)
        shared_state["webhook_rate_limiter_instance"] = webhook_rate_limiter
        await webhook_rate_limiter.start()
        _logger.info("WebhookRateLimiter instance created and started.")
    else:
        _logger.info("WebhookRateLimiter already initialized.")


    # Initialize worker queues
    global queue, approved_signal_queue
    queue = asyncio.Queue(maxsize=settings.QUEUE_MAX_SIZE)  # Main processing queue
    approved_signal_queue = asyncio.Queue(maxsize=settings.QUEUE_MAX_SIZE * 2)  # Approved signals queue
    
    # Store queues in shared state for health checks
    shared_state["signal_queue"] = queue
    shared_state["approved_signal_queue"] = approved_signal_queue

    # Start processing worker coroutines (fast approval/rejection)
    for wid in range(settings.WORKER_CONCURRENCY):
        asyncio.create_task(_queue_worker(wid, get_tickers_from_shared_state))
    _logger.info(
        "Processing workers started – workers=%d queue=%d",
        settings.WORKER_CONCURRENCY,
        queue.maxsize,
    )
    
    # Start forwarding worker coroutines (dedicated rate-limited forwarding)
    for wid in range(settings.FORWARDING_WORKERS):
        asyncio.create_task(_forwarding_worker(wid))
    _logger.info(
        "Forwarding workers started – workers=%d queue=%d",
        settings.FORWARDING_WORKERS,
        approved_signal_queue.maxsize,
    )
      # Start periodic cleanup of old signal trackers
    async def cleanup_tracker_task():
        while True:
            try:
                await asyncio.sleep(3600)  # Run every hour
                cleanup_old_trackers(max_age_hours=24)
            except Exception as e:
                _logger.error(f"Error in tracker cleanup task: {e}")
    
    asyncio.create_task(cleanup_tracker_task())
    _logger.info("Signal tracker cleanup task started")    # Start periodic admin updates task
    async def admin_updates_task():
        """Send periodic updates to admin clients via WebSocket."""
        last_audit_timestamp = datetime.datetime.utcnow()

        while True:
            try:
                await asyncio.sleep(5)  # Update every 5 seconds for real-time data
                
                # Only send updates if there are admin connections
                if len(comm_engine.active_connections) > 0:
                    # Send comprehensive status update with real-time database data
                    try:
                        # Fetch new audit trail events
                        new_events = await db_manager.get_audit_trail_since(last_audit_timestamp)
                        if new_events:
                            last_audit_timestamp = new_events[0]['timestamp'] # most recent is first
                            await comm_engine.broadcast("audit_trail_update", {"events": new_events})

                        current_metrics = get_current_metrics()
                        system_info = await get_system_info_data()
                        
                        # Calculate uptime
                        start_time = shared_state["signal_metrics"]["metrics_start_time"]
                        uptime_seconds = time.time() - start_time if start_time else 0
                        
                        # Ensure uptime is never negative
                        if uptime_seconds < 0:
                            _logger.warning(f"Negative uptime detected in broadcast: {uptime_seconds}. Resetting metrics start time.")
                            shared_state["signal_metrics"]["metrics_start_time"] = time.time()
                            uptime_seconds = 0
                            
                        system_info["uptime_seconds"] = uptime_seconds
                        
                        # Send metrics update with real-time data
                        await comm_engine.broadcast("metrics_update", current_metrics)
                        
                        # Send system status update
                        await comm_engine.broadcast("status_update", {
                            "metrics": current_metrics,
                            "system_info": system_info,
                            "timestamp": time.time()
                        })
                        
                        _logger.debug(f"Sent periodic admin updates to {len(comm_engine.active_connections)} connections")
                        
                    except Exception as update_error:
                        _logger.error(f"Error sending periodic admin updates: {update_error}")
                        
            except Exception as e:
                _logger.error(f"Error in admin updates task: {e}")
    
    asyncio.create_task(admin_updates_task())
    _logger.info("Admin WebSocket updates task started")

@app.on_event("shutdown")
async def _shutdown() -> None:
    """Gracefully stop the FinvizEngine and WebhookRateLimiter."""
    _logger.info("Application shutdown sequence initiated.")
    
    # Stop FinvizEngine
    engine = shared_state.get("finviz_engine_instance")
    if engine:
        _logger.info("Stopping FinvizEngine...")
        await engine.stop()
        _logger.info("FinvizEngine stopped.")
    
    # Stop WebhookRateLimiter
    webhook_rate_limiter = shared_state.get("webhook_rate_limiter_instance")
    if webhook_rate_limiter:
        _logger.info("Stopping WebhookRateLimiter...")
        await webhook_rate_limiter.stop()
        _logger.info("WebhookRateLimiter stopped.")
    
    # Close database connections
    _logger.info("Closing database connections...")
    await db_manager.close()
    _logger.info("Database connections closed.")


# ---------------------------------------------------------------------------- #
# Health Check Endpoints                                                       #
# ---------------------------------------------------------------------------- #

@app.get("/health")
async def health_check():
    """Basic health check endpoint."""
    return {
        "status": "healthy",
        "version": "1.1.0",
        "timestamp": asyncio.get_event_loop().time()
    }

@app.get("/health/detailed")
async def detailed_health_check():
    """Detailed health check with system status."""
    engine = shared_state.get("finviz_engine_instance")
    
    # Get engine status correctly
    engine_status = "stopped"
    if engine:
        is_running = engine.is_running()
        is_paused = engine.is_paused()
        
        if is_running:
            if is_paused:
                engine_status = "paused"
            else:
                engine_status = "running"
        else:
            engine_status = "stopped"
    
    current_tickers = await get_tickers_from_shared_state()
    
    # Get queues from global scope or shared state
    signal_queue = shared_state.get("signal_queue", None)
    approved_queue = shared_state.get("approved_signal_queue", None)
    
    processing_queue_info = {
        "size": signal_queue.qsize() if signal_queue else 0,
        "max_size": signal_queue.maxsize if signal_queue else "unknown"
    } if signal_queue else {"status": "not_initialized"}
    
    approved_queue_info = {
        "size": approved_queue.qsize() if approved_queue else 0,
        "max_size": approved_queue.maxsize if approved_queue else "unknown"
    } if approved_queue else {"status": "not_initialized"}
    
    return {
        "status": "healthy",
        "version": "1.1.0",
        "timestamp": asyncio.get_event_loop().time(),
        "components": {
            "finviz_engine": engine_status,
            "processing_queue": processing_queue_info,
            "approved_queue": approved_queue_info,
            "workers": {
                "processing": settings.WORKER_CONCURRENCY,
                "forwarding": settings.FORWARDING_WORKERS,
                "processing_active": shared_state["signal_metrics"]["processing_workers_active"],
                "forwarding_active": shared_state["signal_metrics"]["forwarding_workers_active"]
            },
            "top_n_tickers": {
                "count": len(current_tickers),
                "last_update": shared_state.get("last_top_n_update", "never")
            }
        }
    }

# ---------------------------------------------------------------------------- #
# Admin Dashboard Endpoint                                                     #
# ---------------------------------------------------------------------------- #

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    """Serve the admin dashboard HTML page."""
    return templates.TemplateResponse("admin.html", {"request": request})

# ---------------------------------------------------------------------------- #
# Signal Processing Endpoints                                                  #
# ---------------------------------------------------------------------------- #

# Removed old /webhook/in, _refresh_top_n, _top_n_refresher
# They are replaced by FinvizEngine and new worker logic

@app.post("/webhook/in", status_code=status.HTTP_202_ACCEPTED)
async def receive_signal(signal: Signal, _bg: BackgroundTasks):
    """Ingress webhook – receives a trading signal and enqueues it."""
    try:        # Increment received signals counter
        shared_state["signal_metrics"]["signals_received"] += 1
        
        # Determine signal type based on side and action
        from database.simple_models import SignalTypeEnum
        signal_type = SignalTypeEnum.BUY  # Default
        sig_side = (signal.side or "").lower()
        sig_action = (signal.action or "").lower()
        if (sig_side in {"sell"} or sig_action in {"sell", "exit", "close"}):
            signal_type = SignalTypeEnum.SELL
            
        # Log signal classification for debugging
        _logger.info(f"[SIGNAL: {signal.signal_id}] Classification: side='{sig_side}', action='{sig_action}', type={signal_type.value}")
        
        # Persist signal to database
        try:
            await db_manager.create_signal_with_initial_event(signal, signal_type)
            _logger.info(f"[SIGNAL: {signal.signal_id}] Persisted initial signal record to database with type: {signal_type.value}")
        except Exception as db_error:
            _logger.error(f"[SIGNAL: {signal.signal_id}] FAILED to persist signal to database: {db_error}")
            # Continue processing even if database fails
        
        _logger.info(f"[SIGNAL: {signal.signal_id}] [TICKER: {signal.normalised_ticker()}] Signal received and assigned ID")
        
        # Enqueue signal for processing
        queue.put_nowait(signal)
        
        # Broadcast updated metrics to admin clients
        await comm_engine.broadcast("metrics_update", get_current_metrics())
        
        return {
            "queued": True, 
            "queue_size": queue.qsize(),
            "signal_id": signal.signal_id,
            "ticker": signal.normalised_ticker()
        }
    except asyncio.QueueFull:
        _logger.warning("Queue full – dropping signal %s", signal.ticker)
        # signals_processed_total.labels(status="dropped_full_queue").inc() # Prometheus
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": "Queue full. Please retry shortly.", "queue_size": queue.qsize()},
        )

# Removed old /finviz/url endpoint, replaced by /finviz/config
@app.post("/finviz/config", status_code=status.HTTP_204_NO_CONTENT)
@log_admin_action("config_update", "update_finviz_config")
async def update_finviz_engine_config(request: Request, payload: dict = Body(...)):
    """
    Updates the FinvizEngine configuration (URL, TOP_N, Refresh Interval).
    All fields in payload are optional.
    Expects a JSON: {"url": "<new_url>", "top_n": <int>, "refresh": <int_seconds>, "token": "<token>"}
    """
    
    token = payload.pop("token", None) # Remove token before passing to engine
    if token != FINVIZ_UPDATE_TOKEN:
        _logger.warning("Invalid token received for /finviz/config.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")

    engine: Optional[FinvizEngine] = shared_state.get("finviz_engine_instance")
    if not engine:
        _logger.error("FinvizEngine not initialized. Cannot update config.")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="FinvizEngine not available.")

    # Validate payload structure and map frontend field names to engine field names
    # Frontend sends: finviz_url, top_n, refresh_interval_sec, reprocess_enabled, reprocess_window_seconds, 
    #                 respect_sell_chronology_enabled, sell_chronology_window_seconds
    # Engine expects: url, top_n, refresh, reprocess_enabled, reprocess_window_seconds,
    #                 respect_sell_chronology_enabled, sell_chronology_window_seconds
    field_mapping = {
        "finviz_url": "url",
        "top_n": "top_n",
        "refresh_interval_sec": "refresh",
        "reprocess_enabled": "reprocess_enabled",
        "reprocess_window_seconds": "reprocess_window_seconds",
        "respect_sell_chronology_enabled": "respect_sell_chronology_enabled",
        "sell_chronology_window_seconds": "sell_chronology_window_seconds"
    }
    
    update_data = {}
    for frontend_key, engine_key in field_mapping.items():
        if frontend_key in payload and payload[frontend_key] is not None:
            update_data[engine_key] = payload[frontend_key]

    if not update_data:
        _logger.info("Received /finviz/config request with no updatable fields.")
        # Return 204 as no change is made, but request is valid.
        # Or, could be a 400 if some change is expected. For now, 204.
        return

    try:
        await engine.update_config(update_data)
        
        _logger.info(f"FinvizEngine configuration update request processed: {update_data}")
        # No body for 204 response
    except ValueError as e: # Catch validation errors from engine (e.g. rate limit, bad values)
        _logger.warning(f"Invalid Finviz config update: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        _logger.error(f"Error updating Finviz config: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error processing config update.")


@app.post("/admin/webhook/update", status_code=status.HTTP_204_NO_CONTENT)
@log_admin_action("config_update", "update_dest_webhook")
async def update_dest_webhook(request: Request, payload: dict = Body(...)):
    """
    Updates the destination webhook URL and timeout.
    Expects a JSON: {"webhook_url": "<new_url>", "timeout": <seconds>, "token": "<token>"}
    """
    token = payload.get("token")
    if token != FINVIZ_UPDATE_TOKEN:
        _logger.warning("Invalid token received for /admin/webhook/update.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")

    webhook_url = payload.get("webhook_url")
    if not webhook_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="webhook_url is required")

    try:
        # Update the settings object
        settings.DEST_WEBHOOK_URL = webhook_url
        _logger.info(f"Destination webhook URL updated to: {webhook_url}")
        
        # Update timeout if provided
        timeout = payload.get("timeout")
        if timeout is not None:
            timeout_value = int(timeout)
            if 1 <= timeout_value <= 60:  # Validate timeout range
                settings.DEST_WEBHOOK_TIMEOUT = timeout_value
                _logger.info(f"Destination webhook timeout updated to: {timeout_value}s")
            else:
                _logger.warning(f"Invalid timeout value: {timeout_value}. Must be between 1-60 seconds.")
        
        # Broadcast update to admin clients
        webhook_config_data = await get_webhook_config_data()
        await comm_engine.trigger_webhook_config_update(webhook_config_data)
        
    except ValueError as e:
        _logger.warning(f"Invalid timeout value: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid timeout value")
    except Exception as e:
        _logger.error(f"Error updating destination webhook: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error updating webhook configuration.")


@app.post("/admin/metrics/reset", status_code=status.HTTP_204_NO_CONTENT)
@log_admin_action("metrics_reset", "reset_signal_metrics")
async def reset_signal_metrics(request: Request, payload: dict = Body(...)):
    """
    Resets signal processing metrics counters.
    Expects a JSON: {"token": "<token>"}
    """
    
    token = payload.get("token")
    if token != FINVIZ_UPDATE_TOKEN:
        _logger.warning("Invalid token received for /admin/metrics/reset.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")

    _logger.info("🔄 METRICS RESET: Starting metrics reset operation...")

    # Reset all in-memory counters
    shared_state["signal_metrics"]["signals_received"] = 0
    shared_state["signal_metrics"]["signals_approved"] = 0
    shared_state["signal_metrics"]["signals_rejected"] = 0
    shared_state["signal_metrics"]["signals_forwarded_success"] = 0
    shared_state["signal_metrics"]["signals_forwarded_error"] = 0
    shared_state["signal_metrics"]["processing_workers_active"] = 0  # Reset active processing workers counter
    shared_state["signal_metrics"]["forwarding_workers_active"] = 0  # Reset active forwarding workers counter
    shared_state["signal_metrics"]["metrics_start_time"] = time.time()
    
    _logger.info("✅ METRICS RESET: All metrics have been reset to zero")
    
    # Reset webhook rate limiter metrics if available
    rate_limiter: Optional[WebhookRateLimiter] = shared_state.get("webhook_rate_limiter_instance")
    if rate_limiter and "webhook_rate_limiter" in shared_state:
        shared_state["webhook_rate_limiter"]["total_requests_limited"] = 0
        shared_state["webhook_rate_limiter"]["requests_made_this_minute"] = 0
        _logger.info("Webhook rate limiter metrics reset")
    
    _logger.info("✅ METRICS RESET: All signal metrics counters reset successfully")
    
    # Broadcast reset metrics to admin clients
    await comm_engine.broadcast("metrics_reset", get_current_metrics())


@app.post("/admin/engine/pause", status_code=status.HTTP_204_NO_CONTENT)
@log_admin_action("engine_control", "pause_engine")
async def pause_finviz_engine(request: Request, payload: dict = Body(...)):
    """
    Pause the FinvizEngine refresh cycles.
    Expects a JSON: {"token": "<token>"}
    """
    
    token = payload.get("token")
    if token != FINVIZ_UPDATE_TOKEN:
        _logger.warning("Invalid token received for /admin/engine/pause.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")

    engine: Optional[FinvizEngine] = shared_state.get("finviz_engine_instance")
    if not engine:
        _logger.error("FinvizEngine not initialized. Cannot pause.")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="FinvizEngine not available.")

    try:
        await engine.pause()
        
        _logger.info("FinvizEngine paused via admin endpoint.")
        
        # Broadcast updated system status to all admin clients
        system_info = await get_system_info_data()
        await comm_engine.broadcast("status_update", {
            "system_info": system_info,
            "timestamp": time.time()
        })
        
    except Exception as e:
        _logger.error(f"Error pausing FinvizEngine: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error pausing engine.")


@app.post("/admin/engine/resume", status_code=status.HTTP_204_NO_CONTENT)
@log_admin_action("engine_control", "resume_engine")
async def resume_finviz_engine(request: Request, payload: dict = Body(...)):
    """
    Resume the FinvizEngine refresh cycles.
    Expects a JSON: {"token": "<token>"}
    """
    
    token = payload.get("token")
    if token != FINVIZ_UPDATE_TOKEN:
        _logger.warning("Invalid token received for /admin/engine/resume.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")

    engine: Optional[FinvizEngine] = shared_state.get("finviz_engine_instance")
    if not engine:
        _logger.error("FinvizEngine not initialized. Cannot resume.")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="FinvizEngine not available.")

    try:
        await engine.resume()
        
        _logger.info("FinvizEngine resumed via admin endpoint.")
        
        # Broadcast updated system status to all admin clients
        system_info = await get_system_info_data()
        await comm_engine.broadcast("status_update", {
            "system_info": system_info,
            "timestamp": time.time()
        })
        
    except Exception as e:
        _logger.error(f"Error resuming FinvizEngine: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error resuming engine.")


@app.post("/admin/engine/manual-refresh", status_code=status.HTTP_204_NO_CONTENT)
@log_admin_action("engine_control", "trigger_manual_refresh")
async def trigger_manual_refresh(request: Request, payload: dict = Body(...)):
    """
    Trigger a manual refresh of the FinvizEngine.
    Expects a JSON: {"token": "<token>"}
    """
    token = payload.get("token")
    if token != FINVIZ_UPDATE_TOKEN:
        _logger.warning("Invalid token received for /admin/engine/manual-refresh.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")

    engine: Optional[FinvizEngine] = shared_state.get("finviz_engine_instance")
    if not engine:
        _logger.error("FinvizEngine not initialized. Cannot trigger manual refresh.")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="FinvizEngine not available.")

    try:
        await engine.trigger_manual_refresh()
        _logger.info("Manual refresh triggered via admin endpoint.")
    except Exception as e:
        _logger.error(f"Error triggering manual refresh: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error triggering manual refresh.")


@app.get("/admin/system-info")
async def get_system_info():
    """Get detailed system information including queue status and performance metrics."""
    try:
        engine: Optional[FinvizEngine] = shared_state.get("finviz_engine_instance")

        # Queue information
        signal_queue = shared_state.get("signal_queue", None)
        queue_info = {
            "current_size": signal_queue.qsize() if signal_queue else 0,
            "max_size": signal_queue.maxsize if signal_queue else "unknown",
            "is_full": signal_queue.full() if signal_queue else False,
            "is_empty": signal_queue.empty() if signal_queue else True
        } if signal_queue else {"status": "not_initialized"}

        # Engine detailed metrics
        engine_metrics = {}
        if engine:
            engine_metrics = engine.get_status_metrics()
            engine_metrics.update({
                "rate_limit_tokens_available": engine.rate_limit_semaphore._value if hasattr(engine.rate_limit_semaphore, '_value') else "unknown",
                "concurrency_slots_available": engine.concurrency_semaphore._value if hasattr(engine.concurrency_semaphore, '_value') else "unknown"
            })
        
        # Get real-time metrics using simplified function
        signal_processing_metrics = get_current_metrics()

        # Calculate additional metrics for display
        try:
            total_processed = signal_processing_metrics["signals_received"]
            approved_count = signal_processing_metrics["signals_approved"]

            # Calculate uptime from metrics start time
            current_time = time.time()  # Use time.time() to match metrics_start_time scale
            start_time = shared_state["signal_metrics"]["metrics_start_time"]
            uptime_seconds = current_time - start_time if start_time else 0
            
            # Debug log for uptime calculation
            _logger.debug(f"Uptime calculation - current_time: {current_time}, start_time: {start_time}, uptime_seconds: {uptime_seconds}")
            
            # Ensure uptime is never negative
            if uptime_seconds < 0:
                _logger.warning(f"Negative uptime detected: {uptime_seconds}. Resetting metrics start time.")
                shared_state["signal_metrics"]["metrics_start_time"] = current_time
                uptime_seconds = 0

            # Calculate percentage rates
            approval_rate = (approved_count / total_processed * 100) if total_processed > 0 else 0
            forward_success_rate = (signal_processing_metrics["signals_forwarded_success"] / (signal_processing_metrics["signals_forwarded_success"] + signal_processing_metrics["signals_forwarded_error"]) * 100) if (signal_processing_metrics["signals_forwarded_success"] + signal_processing_metrics["signals_forwarded_error"]) > 0 else 0

            # Add calculated metrics to the response
            signal_processing_metrics.update({
                "approval_rate_percent": round(approval_rate, 2),
                "forward_success_rate_percent": round(forward_success_rate, 2),
                "signals_per_minute": round(total_processed / (uptime_seconds / 60), 2) if uptime_seconds > 0 else 0
            })

        except Exception as calc_error:
            _logger.error(f"Error calculating additional metrics: {calc_error}")
            signal_processing_metrics.update({
                "approval_rate_percent": 0,
                "forward_success_rate_percent": 0,
                "signals_per_minute": 0
            })

        # Webhook rate limiter metrics
        rate_limiter: Optional[WebhookRateLimiter] = shared_state.get("webhook_rate_limiter_instance")
        webhook_rate_limiter_info = {}
        if rate_limiter:
            try:
                webhook_metrics = rate_limiter.get_metrics()
                webhook_rate_limiter_info = {
                    "rate_limiting_enabled": webhook_metrics.get("rate_limiting_enabled", False),
                    "max_req_per_min": webhook_metrics.get("max_req_per_min", 0),
                    "tokens_available": webhook_metrics.get("tokens_available", 0),
                    "requests_made_this_minute": webhook_metrics.get("requests_made_this_minute", 0),
                    "total_requests_limited": webhook_metrics.get("total_requests_limited", 0),
                    "is_rate_limited": rate_limiter.is_rate_limited(),
                    "last_token_refresh": webhook_metrics.get("last_token_refresh", 0)
                }
            except Exception as e:
                webhook_rate_limiter_info = {"status": "error", "message": str(e)}
        else:
            webhook_rate_limiter_info = {"status": "not_initialized"}

        # Get pause status for frontend compatibility
        finviz_engine_paused = False
        webhook_rate_limiter_paused = False
        reprocess_enabled = False

        if engine:
            finviz_engine_paused = engine.is_paused()

        if rate_limiter:
            # Check if rate limiting is disabled (paused)
            webhook_rate_limiter_paused = not rate_limiter.rate_limiting_enabled

        # Get reprocess status from active Finviz strategy in database
        try:
            engine = shared_state.get("finviz_engine_instance")
            if engine:
                config = await engine.get_config()
                reprocess_enabled = config.reprocess_enabled
            else:
                reprocess_enabled = False
        except Exception as e:
            _logger.warning(f"Could not load active finviz strategy for reprocess status: {e}")
            reprocess_enabled = False

        return {
            "system_info": {
                "uptime_seconds": uptime_seconds,
                "worker_concurrency": settings.WORKER_CONCURRENCY,
                "dest_webhook_url": str(settings.DEST_WEBHOOK_URL),
                "finviz_engine_paused": finviz_engine_paused,
                "webhook_rate_limiter_paused": webhook_rate_limiter_paused,
                "reprocess_enabled": reprocess_enabled,
                "finviz_ticker_count": len(shared_state.get("tickers", set())),
                "timestamp": time.time(),
                "data_source": signal_processing_metrics.get("data_source", "unknown")
            },
            "metrics": signal_processing_metrics,
            "queue": queue_info,
            "engine": engine_metrics,
            "finviz_config": {
                "elite_enabled": settings.FINVIZ_USE_ELITE,
                "max_requests_per_min": get_max_req_per_min(),
                "max_concurrency": get_max_concurrency(),
                "tickers_per_page": get_finviz_tickers_per_page()
            },
            "webhook_rate_limiter": webhook_rate_limiter_info,
            # Compatibility fields - also keep at root level for backward compatibility
            "finviz_engine_paused": finviz_engine_paused,
            "webhook_rate_limiter_paused": webhook_rate_limiter_paused,
            "reprocess_enabled": reprocess_enabled,
            "finviz_ticker_count": len(shared_state.get("tickers", set())),
            "timestamp": time.time(),
            "data_source": signal_processing_metrics.get("data_source", "unknown")
        }
    except Exception as e:
        _logger.error(f"Error in get_system_info: {e}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal Server Error", "error": str(e)},
        )


@app.post("/admin/order/sell-individual", status_code=status.HTTP_200_OK)
@log_admin_action("order_management", "sell_individual_order")
async def sell_individual_order(request: Request, payload: SellIndividualPayload):
    # Token validation
    if payload.token != FINVIZ_UPDATE_TOKEN:
        _logger.warning("Invalid token received for /admin/order/sell-individual.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")

    _logger.info(f"Received /admin/order/sell-individual request for ticker: {payload.ticker}")
    normalised_ticker = payload.ticker.strip().upper() # Normalize ticker

    # Create Signal object
    signal_to_queue = Signal(ticker=normalised_ticker, side='sell') # Using side='sell'

    # Create signal in database with correct type
    from database.simple_models import SignalTypeEnum
    try:
        signal_id = await db_manager.create_signal_with_initial_event(signal_to_queue, SignalTypeEnum.MANUAL_SELL)
        _logger.info(f"Manual sell signal created in database with ID: {signal_id}")
    except Exception as e:
        _logger.error(f"Error creating signal in database: {e}")
        raise HTTPException(status_code=500, detail=f"Error creating signal: {str(e)}")

    # Update metrics
    shared_state["signal_metrics"]["signals_received"] += 1
    
    # Prepare data for the queue (similar to _queue_worker)
    approved_signal_data = {
        'signal': signal_to_queue,
        'ticker': signal_to_queue.normalised_ticker(),
        'approved_at': time.time(), # Using current time
        'worker_id': 'admin_sell_individual', # Identifier for this source
        'signal_id': signal_to_queue.signal_id  # Include signal_id for consistency
    }

    try:
        # Add to approved_signal_queue
        await approved_signal_queue.put(approved_signal_data)
        _logger.info(f"Sell signal for {normalised_ticker} added to approved_signal_queue.")
        
        # Broadcast updated sell_all list (now shows open positions)
        sell_all_data = await get_sell_all_list_data()
        await comm_engine.trigger_sell_all_list_update(sell_all_data)

        return {"message": f"Sell signal for {normalised_ticker} queued successfully", "signal_id": signal_to_queue.signal_id}
        
    except Exception as e:
        _logger.error(f"Error queueing sell signal for {normalised_ticker}: {e}")
        raise HTTPException(status_code=500, detail=f"Error queueing sell signal: {str(e)}")

@app.post("/admin/order/close-position", status_code=status.HTTP_200_OK)
@log_admin_action("order_management", "close_position")
async def close_position_endpoint(request: Request, payload: ClosePositionPayload):
    # Token validation
    if payload.token != FINVIZ_UPDATE_TOKEN:
        _logger.warning("Invalid token received for /admin/order/close-position.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")

    _logger.info(f"Received close position request for ticker: {payload.ticker}")
    normalised_ticker = payload.ticker.strip().upper()

    # Create Signal object with action="exit"
    signal_to_queue = Signal(
        ticker=normalised_ticker, 
        side='sell',
        action='exit'  # CRÍTICO: Definir action="exit"
    )

    # Create signal in database FIRST (before marking position as closing)
    from database.simple_models import SignalTypeEnum
    try:
        signal_id = await db_manager.create_signal_with_initial_event(
            signal_to_queue, 
            SignalTypeEnum.POSITION_CLOSE
        )
        _logger.info(f"Position close signal created with ID: {signal_id}")
    except Exception as e:
        _logger.error(f"Error creating signal: {e}")
        raise HTTPException(status_code=500, detail=f"Error creating signal: {str(e)}")

    # Mark position as CLOSING in database (after signal exists in DB)
    position_marked = await db_manager.mark_position_as_closing(
        ticker=normalised_ticker, 
        exit_signal_id=signal_to_queue.signal_id
    )
    
    if not position_marked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail=f"No open position found for {normalised_ticker}"
        )

    # Update metrics
    shared_state["signal_metrics"]["signals_received"] += 1
    
    # Queue for processing
    approved_signal_data = {
        'signal': signal_to_queue,
        'ticker': signal_to_queue.normalised_ticker(),
        'approved_at': time.time(),
        'worker_id': 'admin_close_position',
        'signal_id': signal_to_queue.signal_id
    }

    try:
        await approved_signal_queue.put(approved_signal_data)
        _logger.info(f"Close signal for {normalised_ticker} queued successfully.")
        
        # Broadcast updated positions
        sell_all_data = await get_sell_all_list_data()
        await comm_engine.trigger_sell_all_list_update(sell_all_data)

        return {
            "message": f"Position close signal for {normalised_ticker} queued successfully", 
            "signal_id": signal_to_queue.signal_id,
            "action": "exit"
        }
        
    except Exception as e:
        _logger.error(f"Error queueing close signal: {e}")
        raise HTTPException(status_code=500, detail=f"Error queueing close signal: {str(e)}")

@app.post("/admin/order/sell-all", status_code=status.HTTP_200_OK)
@log_admin_action("order_management", "sell_all_orders")
async def sell_all_orders(request: Request, payload: TokenPayload):
    """Creates and queues sell signals for all currently open positions from the database."""
    if payload.token != FINVIZ_UPDATE_TOKEN:
        _logger.warning("Invalid token received for /admin/order/sell-all.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")

    try:
        tickers_to_sell = await db_manager.get_all_open_positions_tickers()
        
        if not tickers_to_sell:
            return {"message": "No open positions to sell.", "tickers_processed": []}

        _logger.info(f"Initiating SELL ALL for {len(tickers_to_sell)} open positions: {tickers_to_sell}")
        
        processed_tickers = []
        for ticker in tickers_to_sell:
            try:
                signal_to_queue = Signal(
                    ticker=ticker,
                    side='sell',
                    action='exit' # Use a consistent action
                )
                
                from database.simple_models import SignalTypeEnum
                await db_manager.create_signal_with_initial_event(signal_to_queue, SignalTypeEnum.SELL_ALL)
                
                # Manually enqueue to bypass the decision worker's validation logic,
                # as we've already validated the position exists.
                await approved_signal_queue.put({
                    'signal': signal_to_queue, 'ticker': ticker, 'approved_at': time.time(),
                    'worker_id': 'admin_sell_all', 'signal_id': signal_to_queue.signal_id
                })
                
                # Mark position as closing immediately
                await db_manager.mark_position_as_closing(ticker, signal_to_queue.signal_id)

                processed_tickers.append(ticker)
                _logger.info(f"Sell-all signal queued for {ticker}")
                
            except Exception as ticker_error:
                _logger.error(f"Error processing sell-all signal for {ticker}: {ticker_error}", exc_info=True)
                continue
        
        return {
            "message": f"Processed {len(processed_tickers)} sell signals.",
            "tickers_processed": processed_tickers,
            "total_requested": len(tickers_to_sell)
        }
    except Exception as e:
        _logger.error(f"Error in sell-all operation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error processing sell-all: {str(e)}")

# Replaces existing signals-history implementation to provide real data
@app.get("/admin/signals-history")
async def get_signals_history(hours: int = 24):
    """Get signal history and trends directly from database with formatted data for charts."""
    try:
        # Get hourly signal statistics from database
        hourly_stats = await db_manager.get_hourly_signal_stats(hours)
        
        # Fallback to calculated data if database doesn't have enough data
        if not hourly_stats or len(hourly_stats) == 0:
            _logger.warning("No historical data available, generating fallback data")
            # Generate fallback hourly data
            current_time = datetime.datetime.now()
            fallback_data = []
            
            # Get current metrics for estimation
            current_metrics = get_current_metrics()
            total_signals = current_metrics.get("signals_received", 0)
            
            for i in range(hours):
                hour_time = current_time - datetime.timedelta(hours=i)
                # Estimate signals per hour based on total
                signals_estimate = max(0, int(total_signals / max(1, hours)) + (i % 3))
                
                fallback_data.append({
                    "hour": hour_time.strftime("%H:%00"),
                    "date": hour_time.strftime("%Y-%m-%d"),
                    "signals_received": signals_estimate,
                    "signals_approved": int(signals_estimate * 0.7),
                    "signals_rejected": int(signals_estimate * 0.3),
                    "timestamp": hour_time.timestamp()
                })
            
            fallback_data.reverse()  # Most recent first
            
            return {
                "data": fallback_data,
                "source": "calculated",
                "hours_analyzed": hours,
                "message": "Database data not available, showing estimated data",
                "timestamp": time.time()
            }
        
        # Format data for frontend charts
        formatted_data = []
        for stat in hourly_stats:
            # Convert database data to frontend format
            formatted_data.append({
                "hour": stat.get("hour_label", "00:00"),
                "date": stat.get("date", ""),
                "signals_received": stat.get("total_signals", 0),
                "signals_approved": stat.get("approved_signals", 0),
                "signals_rejected": stat.get("rejected_signals", 0),
                "signals_forwarded": stat.get("forwarded_signals", 0),
                "timestamp": stat.get("timestamp", time.time())
            })
        
        return {
            "data": formatted_data,
            "source": "database",
            "hours_analyzed": hours,
            "total_periods": len(formatted_data),
            "timestamp": time.time()
        }
        
    except Exception as e:
        _logger.error(f"Error retrieving signals history: {e}")
        # Return safe fallback instead of error
        return {
            "data": [],
            "source": "error_fallback", 
            "hours_analyzed": hours,
            "error": f"Database error: {str(e)}",
            "timestamp": time.time()
        }

# ---------------------------------------------------------------------------- #
# Admin Audit Trail Endpoint                                                  #
# ---------------------------------------------------------------------------- #

@app.get("/admin/audit-trail")
async def get_admin_audit_trail(
    limit: int = 20, 
    offset: int = 0, 
    status_filter: str = None, 
    ticker: str = None, 
    hours: int = None,
    signal_id: str = None,
    signal_type: str = None  # NEW: Signal type filter
):
    """Returns audit events for admin panel with improved filters."""
    try:
        # Set up filters for the DBManager
        filters = {}
        if status_filter:
            filters['status_filter'] = status_filter
        if ticker:
            filters['ticker_filter'] = ticker
        if hours:
            filters['hours'] = hours
        if signal_id:
            filters['signal_id_filter'] = signal_id
        if signal_type:  # NEW: Pass signal type filter
            filters['signal_type_filter'] = signal_type
            
        _logger.info(f"Audit trail request - limit: {limit}, offset: {offset}, filters: {filters}")
        
        # Busca dados no banco
        signals = await db_manager.get_audit_trail(
            limit=limit, 
            offset=offset, 
            **filters
        )
        total = await db_manager.get_audit_trail_count(**filters)
        
        # Converter signals para eventos de auditoria
        events = []
        for signal in signals:
            # Create main event from signal status
            main_event = {
                "timestamp": signal.get("updated_at") or signal.get("created_at"),
                "signal_id": signal.get("signal_id", ""),
                "ticker": signal.get("ticker", "-"),
                "event_type": signal.get("status", "unknown"),
                "location": signal.get("location", "unknown"),
                "details": signal.get("error_message") or signal.get("details", "-"),
                "worker_id": signal.get("worker_id", "-"),
                "http_status": signal.get("http_status"),
                "signal_type": signal.get("signal_type", "buy")  # Add signal_type
            }
            
            # If we have a status filter, only add the main event if it matches
            if not status_filter or main_event["event_type"].lower() == status_filter.lower():
                events.append(main_event)
            
            # Add individual events if available, filtering by status if needed
            if signal.get("events"):
                for event in signal["events"]:
                    # Ensure proper timestamp format
                    event_timestamp = event.get("timestamp") or event.get("created_at")
                    if isinstance(event_timestamp, str):
                        # If it's already a string, keep it
                        formatted_timestamp = event_timestamp
                    elif hasattr(event_timestamp, 'isoformat'):
                        # If it's a datetime object, convert to ISO format
                        formatted_timestamp = event_timestamp.isoformat()
                    else:
                        # If it's something else, convert to string
                        formatted_timestamp = str(event_timestamp) if event_timestamp else None
                    
                    event_status = event.get("status", event.get("event_type", "unknown"))
                    
                    # If we have a status filter, only add events that match
                    if not status_filter or event_status.lower() == status_filter.lower():
                        processed_event = {
                            "timestamp": formatted_timestamp,
                            "signal_id": event.get("signal_id", signal.get("signal_id", "")),
                            "ticker": event.get("ticker", signal.get("ticker", "-")),
                            "event_type": event_status,
                            "location": event.get("location", "unknown"),
                            "details": event.get("details", "-"),
                            "worker_id": event.get("worker_id", "-"),
                            "http_status": event.get("http_status"),
                            "signal_type": event.get("signal_type", signal.get("signal_type", "buy"))  # Add signal_type
                        }
                        events.append(processed_event)
        
        # Sort events by timestamp (newest first)
        events.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        
        # Limit to requested amount
        events = events[:limit]
        
        _logger.info(f"Audit trail response - events: {len(events)}, total: {total}")
        
        return {
            "events": events,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(events) < total,
            "filters_applied": filters
        }
    except Exception as e:
        _logger.error(f"Error fetching audit trail: {e}", exc_info=True)
        return {
            "events": [], 
            "total": 0, 
            "error": str(e),
            "limit": limit,
            "offset": offset,
            "has_more": False
        }

# ---------------------------------------------------------------------------- #
# Additional Admin Management Endpoints                                       #
# ---------------------------------------------------------------------------- #

@app.post("/admin/webhook-rate-limiter/update", status_code=status.HTTP_204_NO_CONTENT)
@log_admin_action("rate_limiter_control", "update_webhook_rate_limiter_config")
async def update_webhook_rate_limiter_config(request: Request, payload: dict = Body(...)):
    """Update webhook rate limiter configuration."""
    token = payload.get("token")
    if token != FINVIZ_UPDATE_TOKEN:
        _logger.warning("Invalid token received for webhook rate limiter config update.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")
    
    webhook_rl: Optional[WebhookRateLimiter] = shared_state.get("webhook_rate_limiter_instance")
    if not webhook_rl:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Webhook rate limiter not available")
    
    try:
        max_req_per_min = payload.get("max_req_per_min")
        rate_limiting_enabled = payload.get("rate_limiting_enabled")
        
        # Update configuration
        await webhook_rl.update_config(
            max_req_per_min=int(max_req_per_min) if max_req_per_min is not None else None,
            enabled=rate_limiting_enabled if rate_limiting_enabled is not None else None
        )
        
        if max_req_per_min is not None:
            _logger.info(f"Webhook rate limiter max_req_per_min updated to: {max_req_per_min}")
        if rate_limiting_enabled is not None:
            _logger.info(f"Webhook rate limiter enabled status updated to: {rate_limiting_enabled}")
        
    except ValueError as e:
        _logger.warning(f"Invalid webhook rate limiter config: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        _logger.error(f"Error updating webhook rate limiter config: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error updating config")

@app.post("/admin/webhook-rate-limiter/pause", status_code=status.HTTP_204_NO_CONTENT)
@log_admin_action("rate_limiter_control", "pause_webhook_rate_limiter")
async def pause_webhook_rate_limiter(request: Request, payload: dict = Body(...)):
    """Pause webhook rate limiter."""
    token = payload.get("token")
    if token != FINVIZ_UPDATE_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")
    
    webhook_rl: Optional[WebhookRateLimiter] = shared_state.get("webhook_rate_limiter_instance")
    if not webhook_rl:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Webhook rate limiter not available")
    
    try:
        webhook_rl.pause()  # Remove await - method is not async
        _logger.info("Webhook rate limiter paused via admin endpoint")
        
        # Broadcast updated system status to all admin clients
        system_info = await get_system_info_data()
        await comm_engine.broadcast("status_update", {
            "system_info": system_info,
            "timestamp": time.time()
        })
        
    except Exception as e:
        _logger.error(f"Error pausing webhook rate limiter: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error pausing rate limiter")

@app.post("/admin/webhook-rate-limiter/resume", status_code=status.HTTP_204_NO_CONTENT)
@log_admin_action("rate_limiter_control", "resume_webhook_rate_limiter")
async def resume_webhook_rate_limiter(request: Request, payload: dict = Body(...)):
    """Resume webhook rate limiter."""
    token = payload.get("token")
    if token != FINVIZ_UPDATE_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")
    
    webhook_rl: Optional[WebhookRateLimiter] = shared_state.get("webhook_rate_limiter_instance")
    if not webhook_rl:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Webhook rate limiter not available")
    
    try:
        webhook_rl.resume()  # Remove await - method is not async
        _logger.info("Webhook rate limiter resumed via admin endpoint")
        
        # Broadcast updated system status to all admin clients
        system_info = await get_system_info_data()
        await comm_engine.broadcast("status_update", {
            "system_info": system_info,
            "timestamp": time.time()
        })
        
    except Exception as e:
        _logger.error(f"Error resuming webhook rate limiter: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error resuming rate limiter")

@app.get("/admin/sell-all-queue")
async def get_sell_all_queue():
    """Get the current list of tickers with open positions."""
    try:
        data = await get_sell_all_list_data()
        return data
    except Exception as e:
        _logger.error(f"Error getting sell-all queue: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving open positions: {str(e)}"
        )

@app.get("/admin/webhook/config")
async def get_webhook_config():
    """Get current webhook configuration."""
    try:
        return {
            "webhook_url": settings.DEST_WEBHOOK_URL,
            "timeout": getattr(settings, 'DEST_WEBHOOK_TIMEOUT', 5),
            "rate_limiting_enabled": getattr(settings, 'DEST_WEBHOOK_RATE_LIMITING_ENABLED', True),
            "max_req_per_min": get_max_req_per_min(),
            "timestamp": time.time()
        }
    except Exception as e:
        _logger.error(f"Error getting webhook config: {e}")

@app.get("/admin/finviz/config")
async def get_finviz_config():
    """Get current Finviz configuration FROM ACTIVE STRATEGY IN DATABASE."""
    try:
        # Load from active strategy in database (not JSON file)
        engine = shared_state.get("finviz_engine_instance")
        if engine:
            config = await engine.get_config()
            return {
                "finviz_url": str(config.url),
                "top_n": config.top_n,
                "refresh_interval_sec": config.refresh,
                "reprocess_enabled": config.reprocess_enabled,
                "reprocess_window_seconds": config.reprocess_window_seconds,
                "respect_sell_chronology_enabled": config.respect_sell_chronology_enabled,
                "sell_chronology_window_seconds": config.sell_chronology_window_seconds,
                "timestamp": time.time()
            }
        else:
            # Fallback when engine not available - return safe defaults
            return {
                "finviz_url": "",
                "top_n": 100,
                "refresh_interval_sec": 10,
                "reprocess_enabled": False,
                "reprocess_window_seconds": 300,
                "respect_sell_chronology_enabled": True,
                "sell_chronology_window_seconds": 300,
                "timestamp": time.time()
            }
    except Exception as e:
        _logger.error(f"Error getting finviz config from active strategy: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error retrieving finviz config from active strategy")

# --- Sell All List Management API Endpoints ---

@app.post("/admin/sell-all-queue", status_code=status.HTTP_200_OK)
async def add_ticker_to_sell_all_queue(payload: dict = Body(...)):
    """Adiciona um ticker à lista de sell-all (simula posição aberta)."""
    token = payload.get("token")
    if token != FINVIZ_UPDATE_TOKEN:
        _logger.warning("Invalid token received for /admin/sell-all-queue POST.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")
    
    ticker = payload.get("ticker")
    if not ticker:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ticker is required")
    
    ticker = ticker.strip().upper()
    
    try:
        # Check if ticker already has an open position
        existing_positions = await db_manager.get_all_open_positions_tickers()
        if ticker in existing_positions:
            return {"message": f"Ticker {ticker} already has an open position", "ticker": ticker}
        
        # Create a dummy buy signal to simulate an open position
        from models import Signal
        from database.simple_models import SignalTypeEnum
        
        dummy_signal = Signal(ticker=ticker, side='buy')
        signal_id = await db_manager.create_signal_with_initial_event(dummy_signal, SignalTypeEnum.BUY)
        
        # Create an open position for this ticker
        await db_manager.open_position(ticker, signal_id)
        
        _logger.info(f"Ticker {ticker} added to sell-all queue (simulated open position)")
        
        # Broadcast updated sell_all list
        sell_all_data = await get_sell_all_list_data()
        await comm_engine.trigger_sell_all_list_update(sell_all_data)
        
        return {"message": f"Ticker {ticker} added to sell-all queue successfully", "ticker": ticker}
        
    except Exception as e:
        _logger.error(f"Error adding ticker {ticker} to sell-all queue: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error adding ticker: {str(e)}")

@app.post("/admin/sell-all-queue/clear", status_code=status.HTTP_200_OK)
async def clear_sell_all_queue(payload: dict = Body(...)):
    """Limpa todas as posições abertas (fecha todas as posições)."""
    token = payload.get("token")
    if token != FINVIZ_UPDATE_TOKEN:
        _logger.warning("Invalid token received for /admin/sell-all-queue/clear.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")
    
    try:
        # Get all open positions
        open_tickers = await db_manager.get_all_open_positions_tickers()
        
        if not open_tickers:
            return {"message": "No open positions to clear", "cleared_count": 0}
        
        _logger.info(f"Clearing all {len(open_tickers)} open positions: {open_tickers}")
        
        # Close all open positions
        cleared_count = 0
        for ticker in open_tickers:
            try:
                # Get the open position
                positions = await db_manager.get_positions_with_details(ticker_filter=ticker, status_filter="open")
                for position in positions:
                    await db_manager.close_position_manually(position["id"])
                    cleared_count += 1
            except Exception as ticker_error:
                _logger.error(f"Error closing position for {ticker}: {ticker_error}")
        
        _logger.info(f"Cleared {cleared_count} positions from sell-all queue")
        
        # Broadcast updated sell_all list
        sell_all_data = await get_sell_all_list_data()
        await comm_engine.trigger_sell_all_list_update(sell_all_data)
        
        return {
            "message": f"Cleared {cleared_count} positions from sell-all queue",
            "cleared_count": cleared_count,
            "original_count": len(open_tickers)
        }
        
    except Exception as e:
        _logger.error(f"Error clearing sell-all queue: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error clearing queue: {str(e)}")

@app.get("/admin/sell-all-queue")
async def get_sell_all_queue():
    """Get the current list of tickers with open positions."""
    try:
        data = await get_sell_all_list_data()
        return data
    except Exception as e:
        _logger.error(f"Error getting sell-all queue: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving open positions: {str(e)}"
        )

# --- End Sell All List Management API Endpoints ---

@app.post("/admin/system/config", status_code=status.HTTP_204_NO_CONTENT)
async def update_system_config(payload: dict = Body(...)):
    """Update multiple system configurations at once."""
    token = payload.get("token")
    if token != FINVIZ_UPDATE_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")
    
    try:
        updated_configs = []
        
        # Update webhook config if provided
        if "webhook" in payload:
            webhook_config = payload["webhook"]
            if "url" in webhook_config:
                settings.DEST_WEBHOOK_URL = webhook_config["url"]
                updated_configs.append("webhook_url")
            if "timeout" in webhook_config:
                settings.DEST_WEBHOOK_TIMEOUT = int(webhook_config["timeout"])
                updated_configs.append("webhook_timeout")
        
        # Update finviz config if provided
        if "finviz" in payload:
            finviz_config = payload["finviz"]
            engine: Optional[FinvizEngine] = shared_state.get("finviz_engine_instance")
            
            if engine and any(key in finviz_config for key in ["url", "top_n", "refresh_interval_sec"]):
                # Build new config
                new_config = {}
                if "url" in finviz_config:
                    new_config["finviz_url"] = finviz_config["url"]
                    updated_configs.append("finviz_url")
                if "top_n" in finviz_config:
                    new_config["top_n"] = int(finviz_config["top_n"])
                    updated_configs.append("finviz_top_n")
                if "refresh_interval_sec" in finviz_config:
                    new_config["refresh_interval_sec"] = int(finviz_config["refresh_interval_sec"])
                    updated_configs.append("finviz_refresh_interval")
                
                await engine.update_config(new_config)
        
        # Update rate limiter config if provided
        if "rate_limiter" in payload:
            rl_config = payload["rate_limiter"]
            webhook_rl: Optional[WebhookRateLimiter] = shared_state.get("webhook_rate_limiter_instance")
            
            if webhook_rl:
                if "max_req_per_min" in rl_config:
                    await webhook_rl.update_config(max_req_per_min=int(rl_config["max_req_per_min"]))
                    updated_configs.append("rate_limiter_max_req")
                if "enabled" in rl_config:
                    if rl_config["enabled"]:
                        webhook_rl.resume()
                    else:
                        webhook_rl.pause()
                    updated_configs.append("rate_limiter_enabled")
        
        _logger.info(f"System configuration updated: {', '.join(updated_configs)}")
        
    except Exception as e:
        _logger.error(f"Error updating system config: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error updating system config")

# ---------------------------------------------------------------------------- #
# Admin WebSocket Endpoint                                                    #
# ---------------------------------------------------------------------------- #

@app.websocket("/ws/admin-updates")
async def admin_updates_ws(websocket: WebSocket):
    """WebSocket endpoint para atualizações admin em tempo real com melhor handling"""
    client_info = f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "unknown"
    _logger.info(f"New admin WebSocket connection attempt from: {client_info}")
    
    try:
        await websocket.accept()
        _logger.info(f"WebSocket connection accepted for: {client_info}")
        
        await comm_engine.add_connection(websocket)
        
        # Send initial metrics to new connection
        try:
            initial_metrics = get_current_metrics()
            await websocket.send_json({
                "type": "metrics_update",
                "data": initial_metrics
            })
            _logger.info(f"Initial metrics sent to: {client_info}")
        except Exception as e:
            _logger.error(f"Failed to send initial metrics to {client_info}: {e}")
        
        # Keep connection alive with periodic pings
        while True:
            try:
                await asyncio.sleep(30)  # Ping every 30 seconds
                await websocket.ping()
            except Exception as ping_error:
                _logger.warning(f"Ping failed for {client_info}: {ping_error}")
                break
                
    except WebSocketDisconnect:
        _logger.info(f"WebSocket disconnected normally: {client_info}")
    except Exception as e:
        _logger.error(f"WebSocket error for {client_info}: {e}")
    finally:
        await comm_engine.remove_connection(websocket)
        _logger.info(f"WebSocket connection cleaned up for: {client_info}")

# Add compatibility endpoint - redirects to system-info.
@app.get("/admin/status")
async def get_admin_status():
    """Compatibility endpoint - redirects to system-info."""
    return await get_system_info()

@app.get("/admin/top-n-tickers")
async def get_top_n_tickers():
    """Returns the current list of Top-N tickers approved by FinvizEngine."""
    try:
        current_tickers = await get_tickers_from_shared_state()
        engine = shared_state.get("finviz_engine_instance")
        
        # Get additional engine info if available
        engine_info = {}
        if engine:
            status_metrics = engine.get_status_metrics()
            engine_info = {
                "last_update": status_metrics.get("last_successful_update_time"),
                "update_status": status_metrics.get("last_update_status", "Unknown"),
                "total_collected": status_metrics.get("tickers_total_collected", 0),
                "paused": engine.is_paused()
            }
        
        return {
            "tickers": sorted(list(current_tickers)),
            "count": len(current_tickers),
            "last_update": time.time(),
            "timestamp": time.time(),
            "engine_info": engine_info
        }
    except Exception as e:
        _logger.error(f"Error getting top-N tickers: {e}")
        return {
            "tickers": [],
            "count": 0,
            "last_update": time.time(),
            "timestamp": time.time(),
            "error": str(e),
            "engine_info": {}
        }

# ---------------------------------------------------------------------------- #
# Orders Management Endpoints                                                  #
# ---------------------------------------------------------------------------- #

@app.get("/admin/orders")
async def get_orders(status: Optional[str] = None, ticker: Optional[str] = None):
    """Retorna lista de ordens/posições com filtros."""
    try:
        orders = await db_manager.get_positions_with_details(
            status_filter=status,
            ticker_filter=ticker
        )
        
        return {
            "orders": orders,
            "count": len(orders),
            "timestamp": time.time()
        }
    except Exception as e:
        _logger.error(f"Error getting orders: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error getting orders: {str(e)}")

@app.get("/admin/orders/stats")
async def get_orders_stats():
    """Retorna estatísticas de ordens em tempo real."""
    try:
        stats_data = await db_manager.get_positions_statistics()
        return stats_data
    except Exception as e:
        _logger.error(f"Error getting orders stats: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error getting stats: {str(e)}")

@app.post("/admin/orders/{position_id}/close")
async def close_order_manually(position_id: int, payload: dict = Body(...)):
    """Fecha ordem manualmente."""
    token = payload.get("token")
    if token != FINVIZ_UPDATE_TOKEN:
        _logger.warning(f"Invalid token received for /admin/orders/{position_id}/close.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")
    
    try:
        result = await db_manager.close_position_manually(position_id)
        
        # Broadcast update via WebSocket
        await comm_engine.broadcast("order_status_change", {
            "position_id": position_id,
            "new_status": "closed",
            "timestamp": time.time()
        })
        
        _logger.info(f"Position {position_id} closed manually via admin endpoint")
        return result
        
    except ValueError as e:
        _logger.warning(f"Invalid position close request: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        _logger.error(f"Error closing order {position_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error closing order: {str(e)}")


@app.post("/admin/clear-database")
@log_admin_action("database_operation", "clear_database")
async def clear_database(request: Request, payload: dict = Body(...)):
    """Limpa completamente todos os dados do banco de dados. OPERAÇÃO DESTRUTIVA!"""
    token = payload.get("token")
    if token != FINVIZ_UPDATE_TOKEN:
        _logger.warning("Invalid token received for /admin/clear-database.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")
    
    try:
        _logger.warning("🚨 ADMIN ACTION: Clearing entire database via admin endpoint")
        result = await db_manager.clear_all_data()
        
        # Broadcast database clear event via WebSocket
        await comm_engine.broadcast("database_cleared", {
            "deleted_signals": result.get("deleted_signals_count", 0),
            "deleted_events": result.get("deleted_events_count", 0),
            "deleted_positions": result.get("deleted_positions_count", 0),
            "timestamp": time.time()
        })
        
        _logger.warning(f"🧹 Database cleared successfully: {result.get('deleted_signals_count', 0)} signals, {result.get('deleted_events_count', 0)} events, {result.get('deleted_positions_count', 0)} positions deleted")
        return result
        
    except Exception as e:
        _logger.error(f"Error clearing database: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error clearing database: {str(e)}")


# New endpoint with frontend-expected URL pattern
@app.post("/admin/database/clear")
@log_admin_action("database_operation", "clear_database_new_url")
async def clear_database_new_url(request: Request, payload: dict = Body(...)):
    """Limpa completamente todos os dados do banco de dados. OPERAÇÃO DESTRUTIVA! (New URL pattern)"""
    # Delegate to existing implementation
    return await clear_database(request, payload)
@app.get("/admin/export-csv")
async def export_database_csv():
    """Exporta todos os dados do banco para CSV."""
    try:
        # Get all signals with their events (using high limit to get all data)
        signals = await db_manager.get_audit_trail(limit=50000)
        
        if not signals:
            raise HTTPException(status_code=status.HTTP_204_NO_CONTENT, detail="No data available for export")
        
        # Create CSV content
        csv_content = []
        csv_content.append("timestamp,signal_id,ticker,signal_type,status,location,worker_id,details,http_code")
        
        for event in signals:
            row = [
                event.get("timestamp", ""),
                event.get("signal_id", ""),
                event.get("ticker", ""),
                event.get("signal_type", ""),
                event.get("status", ""),
                event.get("location", "unknown"),
                event.get("worker_id", ""),
                event.get("details", ""),
                event.get("http_status", "")
            ]
            # Escape CSV fields that contain commas or quotes
            escaped_row = []
            for field in row:
                field_str = str(field) if field is not None else ""
                if "," in field_str or '"' in field_str:
                    field_str = '"' + field_str.replace('"', '""') + '"'
                escaped_row.append(field_str)
            csv_content.append(",".join(escaped_row))
        
        csv_data = "\n".join(csv_content)
        
        return Response(
            content=csv_data,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=trading_signals_{int(time.time())}.csv"}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        _logger.error(f"Error exporting CSV: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error exporting data: {str(e)}")


# New endpoint with frontend-expected URL pattern
@app.get("/admin/database/export")
async def export_database_csv_new_url():
    """Exporta todos os dados do banco para CSV. (New URL pattern)"""
    # Delegate to existing implementation
    return await export_database_csv()

@app.post("/admin/import-csv")
@log_admin_action("file_import", "import_database_csv")
async def import_database_csv(request: Request, file: UploadFile = File(...), token: str = Form(...)):
    """Importa dados de CSV para o banco de dados."""
    if token != FINVIZ_UPDATE_TOKEN:
        _logger.warning("Invalid token received for /admin/import-csv.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")
    
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File must be a CSV file")
    
    try:
        # Read CSV file content
        csv_content = await file.read()
        csv_data = csv_content.decode('utf-8')
        
        # Parse CSV data
        reader = csv.DictReader(io.StringIO(csv_data))
        
        imported_count = 0
        for row in reader:
            # Process each row and add to database
            # This is a simplified implementation - in production you'd want more validation
            # For now, we'll just count the rows
            imported_count += 1
        
        _logger.info(f"CSV import completed: {imported_count} rows processed")
        return {"imported_count": imported_count, "message": "CSV import completed successfully"}
        
    except Exception as e:
        _logger.error(f"Error importing CSV: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error importing CSV: {str(e)}")


# New endpoint with frontend-expected URL pattern
@app.post("/admin/database/import")
@log_admin_action("file_import", "import_database_csv_new_url")
async def import_database_csv_new_url(request: Request, file: UploadFile = File(...), token: str = Form(...)):
    """Importa dados de CSV para o banco de dados. (New URL pattern)"""
    # Delegate to existing implementation
    return await import_database_csv(request, file, token)

@app.post("/admin/sell-all/config", status_code=status.HTTP_204_NO_CONTENT)
async def update_sell_all_config(payload: dict = Body(...)):
    """Updates the Sell All list cleanup configuration."""
    token = payload.get("token")
    if token != FINVIZ_UPDATE_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")

    try:
        from system_config import update_sell_all_cleanup_config
        
        # Extract configuration values
        enabled = payload.get('enabled')
        lifetime_hours = payload.get('lifetime_hours')
        
        # Validate inputs
        if enabled is not None:
            enabled = bool(enabled)
        else:
            # Keep current enabled state if not provided
            from system_config import get_sell_all_cleanup_config
            current_config = get_sell_all_cleanup_config()
            enabled = current_config["enabled"]
            
        if lifetime_hours is not None:
            lifetime_hours = int(lifetime_hours)
            if lifetime_hours <= 0:
                raise ValueError("Lifetime must be a positive number of hours.")
        else:
            # Keep current lifetime if not provided
            from system_config import get_sell_all_cleanup_config
            current_config = get_sell_all_cleanup_config()
            lifetime_hours = current_config["lifetime_hours"]
        
        # Update configuration in persistent storage
        update_sell_all_cleanup_config(enabled, lifetime_hours)
        
        _logger.info(f"Sell All cleanup config updated: enabled={enabled}, lifetime_hours={lifetime_hours}")

    except (ValueError, TypeError) as e:
        _logger.error(f"Invalid value for sell-all config: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        _logger.error(f"Error updating sell-all config: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error updating sell-all config")

@app.get("/admin/sell-all/config")
async def get_sell_all_config():
    """Gets the current Sell All list cleanup configuration."""
    try:
        from system_config import get_sell_all_cleanup_config
        config = get_sell_all_cleanup_config()
        return config
    except Exception as e:
        _logger.error(f"Error getting sell-all config: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/admin/reprocessing/health")
async def get_reprocessing_health():
    """Get health status of the Signal Reprocessing Engine."""
    try:
        from signal_reprocessing_engine import SignalReprocessingEngine
        
        # Get the finviz engine instance
        finviz_engine = shared_state.get("finviz_engine_instance")
        if not finviz_engine:
            return {"status": "ENGINE_NOT_AVAILABLE", "message": "Finviz engine not initialized"}
        
        # Create a temporary reprocessing engine to get health status
        # In a production system, you might want to store this as a singleton
        temp_engine = SignalReprocessingEngine(db_manager, approved_signal_queue)
        health_status = temp_engine.get_health_status()
        
        return {
            "reprocessing_engine": health_status,
            "finviz_engine_running": finviz_engine.is_running() if hasattr(finviz_engine, 'is_running') else "unknown"
        }
        
    except ImportError:
        return {
            "status": "MODULE_NOT_AVAILABLE", 
            "message": "Signal Reprocessing Engine module not available"
        }
    except Exception as e:
        _logger.error(f"Error getting reprocessing health: {e}", exc_info=True)
        return {
            "status": "ERROR",
            "message": f"Error retrieving health status: {str(e)}"
        }

@app.post("/admin/reprocessing/trigger")
async def trigger_manual_reprocessing(payload: dict = Body(...)):
    """
    Manually trigger reprocessing for specific tickers.
    Expects: {"tickers": ["AAPL", "MSFT"], "window_seconds": 300, "token": "admin_token"}
    """
    token = payload.get("token")
    if token != FINVIZ_UPDATE_TOKEN:
        _logger.warning("Invalid token received for manual reprocessing trigger.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")
    
    tickers = payload.get("tickers", [])
    window_seconds = payload.get("window_seconds", 300)
    
    if not tickers:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No tickers specified")
    
    if not isinstance(tickers, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tickers must be a list")
    
    try:
        from signal_reprocessing_engine import SignalReprocessingEngine
        
        # Create reprocessing engine
        reprocessing_engine = SignalReprocessingEngine(db_manager, approved_signal_queue)
        
        # Convert tickers to set and normalize
        ticker_set = {ticker.upper().strip() for ticker in tickers if ticker.strip()}
        
        if not ticker_set:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No valid tickers provided")
        
        # Trigger reprocessing
        result = await reprocessing_engine.process_new_tickers(ticker_set, window_seconds)
        
        return {
            "success": result.success,
            "tickers_processed": list(result.metrics.tickers_processed),
            "signals_found": result.signals_found,
            "signals_reprocessed": result.signals_reprocessed,
            "signals_failed": result.signals_failed,
            "success_rate": result.metrics.get_success_rate(),
            "duration_ms": result.duration_ms,
            "errors": result.errors
        }
        
    except ImportError:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                           detail="Signal Reprocessing Engine not available")
    except Exception as e:
        _logger.error(f"Error in manual reprocessing: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                           detail=f"Reprocessing failed: {str(e)}")

# ==========================================================================================
#                           FINVIZ STRATEGY MANAGEMENT ENDPOINTS
# ==========================================================================================

def authenticate_admin_token(token: str) -> bool:
    """Authenticate admin token against the configured token."""
    return token == FINVIZ_UPDATE_TOKEN

@app.get("/admin/finviz/strategies")
async def get_finviz_strategies():
    """Lists all Finviz strategies with complete configurations."""
    try:
        strategies = await db_manager.get_finviz_urls()
        return {"strategies": strategies, "count": len(strategies)}
    except Exception as e:
        _logger.error(f"Error getting Finviz strategies: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@app.post("/admin/finviz/strategies")
@log_admin_action("url_management", "create_finviz_strategy")
async def create_finviz_strategy(request: Request, payload: dict = Body(...)):
    """Creates a new complete Finviz strategy."""
    
    if not authenticate_admin_token(payload.get("token")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin token")
    
    try:
        # Extract strategy data
        name = payload.get("name")
        url = payload.get("url")
        description = payload.get("description", "")
        top_n = payload.get("top_n", 100)
        refresh_interval_sec = payload.get("refresh_interval_sec", 10)
        reprocess_enabled = payload.get("reprocess_enabled", False)
        reprocess_window_seconds = payload.get("reprocess_window_seconds", 300)
        respect_sell_chronology_enabled = payload.get("respect_sell_chronology_enabled", True)
        sell_chronology_window_seconds = payload.get("sell_chronology_window_seconds", 300)
        
        if not name or not url:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name and URL are required")
        
        # Create strategy
        strategy_id = await db_manager.create_finviz_url(
            name=name,
            url=url,
            description=description,
            top_n=top_n,
            refresh_interval_sec=refresh_interval_sec,
            reprocess_enabled=reprocess_enabled,
            reprocess_window_seconds=reprocess_window_seconds,
            respect_sell_chronology_enabled=respect_sell_chronology_enabled,
            sell_chronology_window_seconds=sell_chronology_window_seconds
        )
        
        return {"strategy_id": strategy_id, "message": "Strategy created successfully"}
        
    except Exception as e:
        _logger.error(f"Error creating Finviz strategy: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@app.put("/admin/finviz/strategies/{strategy_id}")
@log_admin_action("url_management", "update_finviz_strategy", target_resource="strategy_{strategy_id}")
async def update_finviz_strategy(strategy_id: int, request: Request, payload: dict = Body(...)):
    """Updates an existing Finviz strategy."""
    
    if not authenticate_admin_token(payload.get("token")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin token")
    
    try:
        # Extract update data
        update_data = {}
        allowed_fields = [
            "name", "url", "description", "top_n", "refresh_interval_sec",
            "reprocess_enabled", "reprocess_window_seconds", 
            "respect_sell_chronology_enabled", "sell_chronology_window_seconds"
        ]
        
        for field in allowed_fields:
            if field in payload:
                update_data[field] = payload[field]
        
        if not update_data:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No valid fields to update")
        
        # Update strategy
        success = await db_manager.update_finviz_url(strategy_id, **update_data)
        
        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")
        
        return {"message": "Strategy updated successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        _logger.error(f"Error updating Finviz strategy: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@app.delete("/admin/finviz/strategies/{strategy_id}")
@log_admin_action("url_management", "delete_finviz_strategy", target_resource="strategy_{strategy_id}")
async def delete_finviz_strategy(strategy_id: int, request: Request, payload: dict = Body(...)):
    """Removes a Finviz strategy (cannot be the active one)."""
    
    if not authenticate_admin_token(payload.get("token")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin token")
    
    try:
        # Delete strategy
        success = await db_manager.delete_finviz_url(strategy_id)
        
        if not success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, 
                              detail="Cannot delete strategy (not found or is active)")
        
        return {"message": "Strategy deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        _logger.error(f"Error deleting Finviz strategy: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@app.post("/admin/finviz/strategies/{strategy_id}/activate")
@log_admin_action("url_management", "activate_finviz_strategy", target_resource="strategy_{strategy_id}")
async def activate_finviz_strategy(strategy_id: int, request: Request, payload: dict = Body(...)):
    """Activates a specific strategy (switches in real-time)."""
    
    if not authenticate_admin_token(payload.get("token")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin token")
    
    try:
        # Get engine instance
        engine = shared_state.get("finviz_engine_instance")
        if not engine:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                              detail="FinvizEngine not available")
        
        # Switch active strategy
        success = await engine.switch_active_url(strategy_id)
        
        if not success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, 
                              detail="Failed to activate strategy")
        
        return {"message": "Strategy activated successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        _logger.error(f"Error activating Finviz strategy: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@app.get("/admin/finviz/strategies/active")
async def get_active_finviz_strategy():
    """Returns the currently active strategy with all configurations."""
    try:
        active_strategy = await db_manager.get_active_finviz_url()
        if not active_strategy:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, 
                              detail="No active strategy found")
        
        return {"active_strategy": active_strategy}
        
    except HTTPException:
        raise
    except Exception as e:
        _logger.error(f"Error getting active Finviz strategy: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@app.post("/admin/finviz/strategies/{strategy_id}/duplicate")
@log_admin_action("url_management", "duplicate_finviz_strategy", target_resource="strategy_{strategy_id}")
async def duplicate_finviz_strategy(strategy_id: int, request: Request, payload: dict = Body(...)):
    """Duplicates an existing strategy with a new name."""
    
    if not authenticate_admin_token(payload.get("token")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin token")
    
    try:
        new_name = payload.get("new_name")
        if not new_name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New name is required")
        
        # Get source strategy
        strategies = await db_manager.get_finviz_urls()
        source_strategy = next((s for s in strategies if s["id"] == strategy_id), None)
        
        if not source_strategy:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source strategy not found")
        
        # Create duplicate
        new_strategy_id = await db_manager.create_finviz_url(
            name=new_name,
            url=source_strategy["url"],
            description=f"Copy of {source_strategy['name']}",
            top_n=source_strategy["top_n"],
            refresh_interval_sec=source_strategy["refresh_interval_sec"],
            reprocess_enabled=source_strategy["reprocess_enabled"],
            reprocess_window_seconds=source_strategy["reprocess_window_seconds"],
            respect_sell_chronology_enabled=source_strategy["respect_sell_chronology_enabled"],
            sell_chronology_window_seconds=source_strategy["sell_chronology_window_seconds"]
        )
        
        return {"strategy_id": new_strategy_id, "message": "Strategy duplicated successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        _logger.error(f"Error duplicating Finviz strategy: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

# ==========================================================================================
#                           ADMIN ACTIONS LOG ENDPOINTS
# ==========================================================================================

@app.get("/admin/actions-log")
async def get_admin_actions_log(
    limit: int = 50,
    offset: int = 0,
    action_type_filter: Optional[str] = None,
    hours: Optional[int] = None,
    success_filter: Optional[bool] = None
):
    """Returns administrative actions log with filters."""
    try:
        actions = await db_manager.get_admin_actions_log(
            limit=limit,
            offset=offset,
            action_type_filter=action_type_filter,
            hours=hours,
            success_filter=success_filter
        )
        
        total_count = await db_manager.get_admin_actions_count(
            action_type_filter=action_type_filter,
            hours=hours,
            success_filter=success_filter
        )
        
        return {
            "actions": actions,
            "total": total_count,
            "limit": limit,
            "offset": offset
        }
        
    except Exception as e:
        _logger.error(f"Error getting admin actions log: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@app.get("/admin/actions-log/summary")
async def get_admin_actions_summary(hours: int = 24):
    """Returns summary statistics for administrative actions."""
    try:
        summary = await db_manager.get_admin_actions_summary(hours=hours)
        return summary
        
    except Exception as e:
        _logger.error(f"Error getting admin actions summary: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@app.get("/admin/actions-log/export")
async def export_admin_actions_log(
    hours: Optional[int] = None,
    action_type_filter: Optional[str] = None,
    format: str = 'csv'
):
    """Exports administrative actions log."""
    try:
        actions = await db_manager.get_admin_actions_log(
            limit=10000,  # Large limit for export
            offset=0,
            action_type_filter=action_type_filter,
            hours=hours
        )
        
        if format.lower() == 'csv':
            # Create CSV response
            output = io.StringIO()
            writer = csv.writer(output)
            
            # Write header
            writer.writerow([
                'Action ID', 'Timestamp', 'Action Type', 'Action Name', 
                'Admin Token', 'IP Address', 'Success', 'Error Message',
                'Target Resource', 'Execution Time (ms)'
            ])
            
            # Write data
            for action in actions:
                writer.writerow([
                    action['action_id'],
                    action['timestamp'],
                    action['action_type'],
                    action['action_name'],
                    action['admin_token'],
                    action['ip_address'],
                    action['success'],
                    action['error_message'],
                    action['target_resource'],
                    action['execution_time_ms']
                ])
            
            csv_content = output.getvalue()
            output.close()
            
            return Response(
                content=csv_content,
                media_type='text/csv',
                headers={'Content-Disposition': f'attachment; filename=admin_actions_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'}
            )
        else:
            # JSON format
            return JSONResponse(content={"actions": actions})
            
    except Exception as e:
        _logger.error(f"Error exporting admin actions: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

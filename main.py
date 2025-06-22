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
# import finviz # Will be refactored to use only parser # Commented out as it's no longer used directly here
from typing import Set, List, Any, Dict, Callable, Optional # Added Optional
import json

import httpx
from fastapi import Body, FastAPI, BackgroundTasks, HTTPException, status, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
# from prometheus_client import start_http_server, Counter, Gauge, Enum # For Prometheus
# from prometheus_fastapi_instrumentator import Instrumentator # For Prometheus

from config import settings, FINVIZ_UPDATE_TOKEN, FINVIZ_CONFIG_FILE, DEFAULT_TICKER_REFRESH_SEC, get_max_req_per_min, get_max_concurrency, get_finviz_tickers_per_page
from models import Signal, SellIndividualPayload, TokenPayload, SignalTracker, SignalStatus, SignalLocation, AuditTrailQuery, AuditTrailResponse
# Remove direct networking imports from finviz, keep parser if needed elsewhere or refactor finviz.py
from finviz import load_finviz_config, persist_finviz_config_from_dict # Keep config helpers

from comm_engine import comm_engine  # Import centralized communication engine
from finviz_engine import FinvizEngine, FinvizConfig # Import the new engine
from webhook_rate_limiter import WebhookRateLimiter # Import webhook rate limiter

# Database integration
from database.DBManager import db_manager
from database.models import SignalStatusEnum, SignalLocationEnum

# ---------------------------------------------------------------------------- #
# Helper Functions                                                              #
# ---------------------------------------------------------------------------- #

def get_current_metrics() -> Dict[str, Any]:
    """Get current metrics with real-time queue sizes."""
    try:
        metrics = shared_state["signal_metrics"].copy()
        
        # Update queue sizes with real-time data
        metrics["approved_queue_size"] = approved_signal_queue.qsize() if approved_signal_queue else 0
        metrics["processing_queue_size"] = queue.qsize() if queue else 0
        
        return metrics
    except Exception as e:
        print(f"Error getting current metrics: {e}")
        return shared_state.get("signal_metrics", {})

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
    "forwarding_workers_active": 0
}

shared_state: Dict[str, Any] = {
    "tickers": set(),  # Managed by FinvizEngine
    "finviz_engine_instance": None, # To hold the FinvizEngine instance
    "webhook_rate_limiter_instance": None, # To hold the WebhookRateLimiter instance
    "signal_metrics": signal_metrics,
    "audit_log": collections.deque(maxlen=50000),  # Increased from 5000 to 50000 for comprehensive audit trail
    "signal_trackers": {},  # Dict[signal_id, SignalTracker] - New tracking system
    "sell_all_accumulator": set(),
    # Monitoramento de sinais em PROCESSING
    "processing_monitor": {
        "active_processing": {},  # signal_id -> start_time
        "processing_timeout": 30.0,  # segundos
        "stuck_signals": []
    }
}

# Funções de monitoramento de PROCESSING

def start_processing_monitor(signal_id: str):
    """Marca início do processamento de um sinal."""
    shared_state["processing_monitor"]["active_processing"][signal_id] = time.time()

def end_processing_monitor(signal_id: str):
    """Marca fim do processamento de um sinal."""
    shared_state["processing_monitor"]["active_processing"].pop(signal_id, None)

def check_stuck_processing_signals():
    """Verifica sinais presos em PROCESSING."""
    current_time = time.time()
    timeout = shared_state["processing_monitor"]["processing_timeout"]
    stuck = []
    for signal_id, start_time in list(shared_state["processing_monitor"]["active_processing"].items()):
        if current_time - start_time > timeout:
            stuck.append({
                "signal_id": signal_id,
                "stuck_duration": current_time - start_time,
                "started_at": start_time
            })
    shared_state["processing_monitor"]["stuck_signals"] = stuck
    return stuck

async def processing_monitor_task():
    """Task de background para monitorar sinais presos em PROCESSING."""
    while True:
        try:
            stuck_signals = check_stuck_processing_signals()
            if stuck_signals:
                for signal in stuck_signals:
                    # Aqui pode-se implementar lógica de recuperação automática
                    # Exemplo: marcar como discarded
                    update_signal_tracker(
                        signal["signal_id"],
                        SignalStatus.DISCARDED,
                        SignalLocation.DISCARDED,
                        details=f"Descartado por timeout em PROCESSING ({signal['stuck_duration']:.1f}s)"
                    )
                    end_processing_monitor(signal["signal_id"])
        except Exception as e:
            _logger.error(f"Erro no monitor de processing: {e}")
        await asyncio.sleep(10)  # Checa a cada 10s

# ---------------------------------------------------------------------------- #
# Signal Tracking Functions                                                    #
# ---------------------------------------------------------------------------- #

def create_signal_tracker(signal: Signal) -> SignalTracker:
    """Create a new signal tracker for the given signal."""
    tracker = SignalTracker(
        signal_id=signal.signal_id,
        ticker=signal.ticker,
        normalised_ticker=signal.normalised_ticker(),
        original_signal=signal.dict(),
        current_status=SignalStatus.RECEIVED,
        current_location=SignalLocation.PROCESSING_QUEUE
    )
    
    # Add initial event
    tracker.add_event(
        event_type=SignalStatus.RECEIVED,
        location=SignalLocation.PROCESSING_QUEUE,
        details="Signal received and assigned unique ID"
    )
    
    return tracker

def update_signal_tracker(
    signal_id: str, 
    event_type: SignalStatus, 
    location: SignalLocation,
    worker_id: Optional[str] = None,
    details: Optional[str] = None,
    error_info: Optional[Dict[str, Any]] = None,
    http_status: Optional[int] = None,
    response_data: Optional[str] = None
) -> Optional[SignalTracker]:
    """Update signal tracker with new event."""
    tracker = shared_state["signal_trackers"].get(signal_id)
    if not tracker:
        _logger.warning(f"Signal tracker not found for ID: {signal_id}")
        return None
    
    tracker.add_event(
        event_type=event_type,
        location=location,
        worker_id=worker_id,
        details=details,
        error_info=error_info,
        http_status=http_status,
        response_data=response_data
    )
    
    # FIXED: Remove dual storage system - only use signal_trackers
    # The audit_log is now deprecated and unified into signal_trackers
    # Frontend gets data via WebSocket broadcasts and tracker-based API calls
    
    return tracker

async def broadcast_signal_tracker_update(tracker: SignalTracker):
    """Broadcast signal tracker update to frontend."""
    audit_entry = tracker.to_audit_entry()
    await comm_engine.trigger_new_audit_entry(audit_entry)

def cleanup_old_trackers(max_age_hours: int = 24):
    """Clean up old signal trackers to prevent memory issues."""
    current_time = time.time()
    cutoff_time = current_time - (max_age_hours * 3600)
    
    trackers_to_remove = [
        signal_id for signal_id, tracker in shared_state["signal_trackers"].items()
        if tracker.created_at < cutoff_time
    ]
    
    for signal_id in trackers_to_remove:
        del shared_state["signal_trackers"][signal_id]
    
    # FIXED: Also clean up legacy audit_log entries to prevent memory leaks
    # Remove old entries from audit_log (if any legacy entries exist)
    if 'audit_log' in shared_state:
        original_count = len(shared_state['audit_log'])
        # Keep only recent entries (convert to list to avoid deque modification issues)
        recent_entries = []
        for entry in list(shared_state['audit_log']):
            entry_time = entry.get('created_at', entry.get('timestamp', 0))
            if isinstance(entry_time, str):
                try:
                    entry_time = datetime.fromisoformat(entry_time.replace('Z', '+00:00')).timestamp()
                except:
                    entry_time = 0
            
            if entry_time > cutoff_time:
                recent_entries.append(entry)
        
        # Clear and repopulate
        shared_state['audit_log'].clear()
        shared_state['audit_log'].extend(recent_entries)
        
        removed_audit_entries = original_count - len(recent_entries)
        if removed_audit_entries > 0:
            _logger.info(f"Cleaned up {removed_audit_entries} old audit log entries")
    
    if trackers_to_remove:
        _logger.info(f"Cleaned up {len(trackers_to_remove)} old signal trackers")

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
    """Process signals from the main queue."""
    _logger.info(f"Queue worker {worker_id} started")
    
    while True:
        try:
            # Get signal from queue
            signal = await queue.get()
            signal_id = signal.signal_id
            
            _logger.info(f"[WORKER {worker_id}] [SIGNAL: {signal_id}] Processing signal for ticker: {signal.normalised_ticker()}")
            
            # Start processing monitor
            start_processing_monitor(signal_id)
            
            # Update tracker - signal is being processed
            update_signal_tracker(
                signal_id,
                SignalStatus.PROCESSING,
                SignalLocation.PROCESSING_QUEUE,
                worker_id=f"queue_worker_{worker_id}",
                details="Signal being processed by worker"
            )
            
            try:
                # Get current tickers
                current_tickers = await get_tickers_func()
                normalised_ticker = signal.normalised_ticker()
                
                # Check if ticker is in top-N list
                if normalised_ticker in current_tickers:
                    # Approve signal
                    _logger.info(f"[WORKER {worker_id}] [SIGNAL: {signal_id}] Signal APPROVED - ticker {normalised_ticker} is in top-N list")
                    
                    shared_state["signal_metrics"]["signals_approved"] += 1
                    
                    # Update tracker - signal approved
                    update_signal_tracker(
                        signal_id,
                        SignalStatus.APPROVED,
                        SignalLocation.PROCESSING_QUEUE,
                        worker_id=f"queue_worker_{worker_id}",
                        details=f"Signal approved - ticker {normalised_ticker} found in top-{len(current_tickers)} list"
                    )
                    
                    # Prepare data for approved queue
                    approved_signal_data = {
                        'signal': signal,
                        'ticker': normalised_ticker,
                        'approved_at': time.time(),
                        'worker_id': f"queue_worker_{worker_id}",
                        'signal_id': signal_id
                    }
                    
                    # Add to approved queue
                    await approved_signal_queue.put(approved_signal_data)
                    
                    # Update tracker - signal queued for forwarding
                    update_signal_tracker(
                        signal_id,
                        SignalStatus.QUEUED_FORWARDING,
                        SignalLocation.APPROVED_QUEUE,
                        worker_id=f"queue_worker_{worker_id}",
                        details="Signal queued for forwarding"
                    )
                    
                    # Add to sell_all_accumulator if it's a buy signal
                    if signal.side.lower() == 'buy':
                        shared_state['sell_all_accumulator'].add(normalised_ticker)
                        _logger.info(f"[WORKER {worker_id}] [SIGNAL: {signal_id}] Added {normalised_ticker} to sell_all_accumulator")
                        
                        # Broadcast updated sell_all_accumulator
                        sell_all_data = await get_sell_all_list_data()
                        await comm_engine.trigger_sell_all_list_update(sell_all_data)
                    
                else:
                    # Reject signal
                    _logger.info(f"[WORKER {worker_id}] [SIGNAL: {signal_id}] Signal REJECTED - ticker {normalised_ticker} not in top-N list")
                    
                    shared_state["signal_metrics"]["signals_rejected"] += 1
                    
                    # Log rejection event to database
                    event_details = f"Signal rejected - ticker {normalised_ticker} not in top-{len(current_tickers)} list"
                    try:
                        await db_manager.log_signal_event(
                            signal_id=signal_id,
                            event_type=SignalStatusEnum.REJECTED,
                            location=SignalLocationEnum.DISCARDED,
                            details=event_details,
                            worker_id=f"queue_worker_{worker_id}"
                        )
                    except Exception as db_error:
                        _logger.error(f"[WORKER {worker_id}] [SIGNAL: {signal_id}] Failed to log rejection to database: {db_error}")
                    
                    # Keep memory tracking if dual-write enabled
                    if settings.DUAL_WRITE_ENABLED:
                        update_signal_tracker(
                            signal_id,
                            SignalStatus.REJECTED,
                            SignalLocation.DISCARDED,
                            worker_id=f"queue_worker_{worker_id}",
                            details=event_details
                        )
                
                # Broadcast tracker update
                tracker = shared_state["signal_trackers"].get(signal_id)
                if tracker:
                    await broadcast_signal_tracker_update(tracker)
                
                # Broadcast updated metrics
                await comm_engine.broadcast("metrics_update", get_current_metrics())
                
            except Exception as e:
                _logger.error(f"[WORKER {worker_id}] [SIGNAL: {signal_id}] Error processing signal: {e}")
                
                # Update tracker with error
                update_signal_tracker(
                    signal_id,
                    SignalStatus.ERROR,
                    SignalLocation.PROCESSING_QUEUE,
                    worker_id=f"queue_worker_{worker_id}",
                    details=f"Error processing signal: {str(e)}",
                    error_info={"type": "processing_error", "message": str(e)}
                )
                
                # Broadcast tracker update
                tracker = shared_state["signal_trackers"].get(signal_id)
                if tracker:
                    await broadcast_signal_tracker_update(tracker)
            
            finally:
                # End processing monitor
                end_processing_monitor(signal_id)
                
        except Exception as e:
            _logger.error(f"Queue worker {worker_id} error: {e}")
            await asyncio.sleep(1)  # Brief pause before continuing

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
            
            # Update tracker - signal is being forwarded
            try:
                update_signal_tracker(
                    signal_id,
                    SignalStatus.FORWARDING,
                    SignalLocation.WORKER_FORWARDING,
                    worker_id=f"forwarding_worker_{worker_id}",
                    details="Signal being forwarded to destination webhook"
                )
                _logger.debug(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Tracker updated to FORWARDING")
            except Exception as tracker_error:
                _logger.error(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Error updating tracker: {tracker_error}")
                raise
            
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
                    
                    # Keep memory tracking if dual-write enabled
                    if settings.DUAL_WRITE_ENABLED:
                        update_signal_tracker(
                            signal_id,
                            SignalStatus.FORWARDED_SUCCESS,
                            SignalLocation.COMPLETED,
                            worker_id=f"forwarding_worker_{worker_id}",
                            details=f"Signal forwarded successfully to {settings.DEST_WEBHOOK_URL}",
                            http_status=response.status_code,
                            response_data=response.text[:500] if response.text else None
                        )
                
            except Exception as e:
                _logger.error(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Error forwarding signal: {e}")
                
                shared_state["signal_metrics"]["signals_forwarded_error"] += 1
                
                # Determine the appropriate error status based on exception type
                if hasattr(e, 'response') and hasattr(e.response, 'status_code'):
                    # HTTP error
                    error_status = SignalStatusEnum.FORWARDED_HTTP_ERROR
                    http_status = e.response.status_code
                else:
                    # Generic error (timeout, network, etc.)
                    error_status = SignalStatusEnum.FORWARDED_GENERIC_ERROR
                    http_status = None
                
                # Log error event to database
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
                except Exception as db_error:
                    _logger.error(f"[FORWARDING WORKER {worker_id}] [SIGNAL: {signal_id}] Failed to log error to database: {db_error}")
                
                # Keep memory tracking if dual-write enabled
                if settings.DUAL_WRITE_ENABLED:
                    update_signal_tracker(
                        signal_id,
                        SignalStatus.FORWARDED_HTTP_ERROR if http_status else SignalStatus.FORWARDED_GENERIC_ERROR,
                        SignalLocation.COMPLETED,
                        worker_id=f"forwarding_worker_{worker_id}",
                        details=f"Error forwarding signal: {str(e)}",
                        error_info={"type": "forwarding_error", "message": str(e)},
                        http_status=http_status
                    )
            
            finally:
                shared_state["signal_metrics"]["forwarding_workers_active"] -= 1
                
                # Broadcast tracker update
                tracker = shared_state["signal_trackers"].get(signal_id)
                if tracker:
                    await broadcast_signal_tracker_update(tracker)
                
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
                })
            except Exception as e:
                _logger.error(f"Error getting webhook rate limiter metrics: {e}")
        
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
            "use_elite": settings.FINVIZ_USE_ELITE
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
    try:
        # Get Finviz engine instance
        engine = shared_state.get("finviz_engine_instance")
        
        overview_data = {
            "websocket_status": "Connected",  # We're connected if this function is being called
            "elite_auth_status": "Checking...",
            "total_tickers": len(shared_state.get("current_tickers", [])),
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
            "active_workers": 0,  # TODO: Implement actual worker count
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

async def get_sell_all_list_data() -> List[str]:
    """Get current sell all list data for comm_engine."""
    try:
        sell_all_data = sorted(list(shared_state.get('sell_all_accumulator', [])))
        return sell_all_data
    except Exception as e:
        _logger.error(f"Error getting sell all list data: {e}")
        return []

@app.on_event("startup")
async def _startup() -> None:
    """Initializes FinvizEngine, cache, and background tasks."""
    _logger.info("Application startup sequence initiated.")

    # Initialize database connection first
    _logger.info("Initializing database connection...")
    db_manager.initialize(settings.DATABASE_URL)
    
    # Create database tables if they don't exist
    try:
        from database.simple_init import init_database
        await init_database(settings.DATABASE_URL)
        _logger.info("✅ Database tables initialized successfully")
    except Exception as e:
        _logger.error(f"❌ Failed to initialize database: {e}")
        # Don't fail startup - the application can still work with memory-only mode
        _logger.warning("Continuing without database - using memory-only mode")

    # Initialize signal metrics start time
    shared_state["signal_metrics"]["metrics_start_time"] = time.time()

    # Initialize and start FinvizEngine
    if shared_state.get("finviz_engine_instance") is None:
        # Using centralized comm_engine for WebSocket communication
        engine = FinvizEngine(shared_state, comm_engine.broadcast)
        shared_state["finviz_engine_instance"] = engine
        asyncio.create_task(engine.run())
        _logger.info("FinvizEngine instance created and started.")
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
    _logger.info("Signal tracker cleanup task started")

    # Task para monitorar sinais presos em PROCESSING
    asyncio.create_task(processing_monitor_task())
    _logger.info("Processing monitor task started")

    # Initial broadcast to any early admin connections
    # This might be better after engine's first run, but good for initial state
    # The engine itself will broadcast after its first successful fetch.
    # Consider if an initial "loading" state is needed for admin UI.
    # await broadcast_admin_state_update() # Renamed from broadcast_admin_update


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
                "forwarding_active": shared_state["signal_metrics"]["forwarding_workers_active"]
            },
            "top_n_tickers": {
                "count": len(current_tickers),
                "last_update": shared_state.get("last_top_n_update", "never")
            }
        }
    }

# ---------------------------------------------------------------------------- #
# Signal Processing Endpoints                                                  #
# ---------------------------------------------------------------------------- #

# Removed old /webhook/in, _refresh_top_n, _top_n_refresher
# They are replaced by FinvizEngine and new worker logic

@app.post("/webhook/in", status_code=status.HTTP_202_ACCEPTED)
async def receive_signal(signal: Signal, _bg: BackgroundTasks):
    """Ingress webhook – receives a trading signal and enqueues it."""
    try:
        # Increment received signals counter
        shared_state["signal_metrics"]["signals_received"] += 1
        
        # Persist signal to database first (new approach)
        try:
            await db_manager.create_signal_with_initial_event(signal)
            _logger.info(f"[SIGNAL: {signal.signal_id}] Persisted initial signal record to database.")
        except Exception as db_error:
            _logger.error(f"[SIGNAL: {signal.signal_id}] FAILED to persist signal to database: {db_error}")
            # Continue with memory-only mode if database fails
        
        # Keep memory tracking for backward compatibility and speed (if dual-write enabled)
        if settings.DUAL_WRITE_ENABLED:
            tracker = create_signal_tracker(signal)
            shared_state["signal_trackers"][signal.signal_id] = tracker
            update_signal_tracker(
                signal.signal_id,
                SignalStatus.QUEUED_PROCESSING,
                SignalLocation.PROCESSING_QUEUE,
                details="Signal queued for processing"
            )
            # Broadcast tracker update to admin clients
            await broadcast_signal_tracker_update(tracker)
        
        _logger.info(f"[SIGNAL: {signal.signal_id}] [TICKER: {signal.normalised_ticker()}] Signal received and assigned ID")
        
        # Enqueue signal for processing (memory queue still used for speed)
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
async def update_finviz_engine_config(payload: dict = Body(...)):
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

    # Validate payload structure before passing to engine (optional, engine also validates)
    allowed_keys = {"url", "top_n", "refresh"}
    update_data = {k: v for k, v in payload.items() if k in allowed_keys and v is not None}

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
async def update_dest_webhook(payload: dict = Body(...)):
    """
    Updates the destination webhook URL.
    Expects a JSON: {"webhook_url": "<new_url>", "token": "<token>"}
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
        
        # Broadcast update to admin clients
        webhook_config_data = await get_webhook_config_data()
        await comm_engine.trigger_webhook_config_update(webhook_config_data)
        
    except Exception as e:
        _logger.error(f"Error updating destination webhook: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error updating webhook URL.")


@app.post("/admin/metrics/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_signal_metrics(payload: dict = Body(...)):
    """
    Resets signal processing metrics counters.
    Expects a JSON: {"token": "<token>"}
    """
    token = payload.get("token")
    if token != FINVIZ_UPDATE_TOKEN:
        _logger.warning("Invalid token received for /admin/metrics/reset.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")

    # Reset all counters
    shared_state["signal_metrics"]["signals_received"] = 0
    shared_state["signal_metrics"]["signals_approved"] = 0
    shared_state["signal_metrics"]["signals_rejected"] = 0
    shared_state["signal_metrics"]["signals_forwarded_success"] = 0
    shared_state["signal_metrics"]["signals_forwarded_error"] = 0
    shared_state["signal_metrics"]["forwarding_workers_active"] = 0  # Reset active workers counter
    shared_state["signal_metrics"]["metrics_start_time"] = time.time()
    
    # Reset webhook rate limiter metrics if available
    rate_limiter: Optional[WebhookRateLimiter] = shared_state.get("webhook_rate_limiter_instance")
    if rate_limiter and "webhook_rate_limiter" in shared_state:
        shared_state["webhook_rate_limiter"]["total_requests_limited"] = 0
        shared_state["webhook_rate_limiter"]["requests_made_this_minute"] = 0
        _logger.info("Webhook rate limiter metrics reset")
    
    _logger.info("Signal metrics counters reset")
    
    # Broadcast reset metrics to admin clients
    await comm_engine.broadcast("metrics_reset", get_current_metrics())


@app.post("/admin/engine/pause", status_code=status.HTTP_204_NO_CONTENT)
async def pause_finviz_engine(payload: dict = Body(...)):
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
    except Exception as e:
        _logger.error(f"Error pausing FinvizEngine: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error pausing engine.")


@app.post("/admin/engine/resume", status_code=status.HTTP_204_NO_CONTENT)
async def resume_finviz_engine(payload: dict = Body(...)):
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
    except Exception as e:
        _logger.error(f"Error resuming FinvizEngine: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error resuming engine.")


@app.post("/admin/engine/manual-refresh", status_code=status.HTTP_204_NO_CONTENT)
async def trigger_manual_refresh(payload: dict = Body(...)):
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
    
    # Calculate metrics rates
    current_time = asyncio.get_event_loop().time()
    start_time = shared_state["signal_metrics"]["metrics_start_time"]
    uptime_seconds = current_time - start_time if start_time else 0
    
    metrics = shared_state["signal_metrics"]
    total_processed = metrics["signals_received"]
    
    # Use the simple approved count from metrics
    approved_count = metrics["signals_approved"]
    
    approval_rate = (approved_count / total_processed * 100) if total_processed > 0 else 0
    forward_success_rate = (metrics["signals_forwarded_success"] / (metrics["signals_forwarded_success"] + metrics["signals_forwarded_error"]) * 100) if (metrics["signals_forwarded_success"] + metrics["signals_forwarded_error"]) > 0 else 0
    
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
    
    return {
        "system": {
            "uptime_seconds": uptime_seconds,
            "worker_concurrency": settings.WORKER_CONCURRENCY,
            "dest_webhook_url": str(settings.DEST_WEBHOOK_URL)
        },
        "queue": queue_info,
        "engine": engine_metrics,
        "signal_processing": {
            **metrics,
            "approval_rate_percent": round(approval_rate, 2),
            "forward_success_rate_percent": round(forward_success_rate, 2),
            "signals_per_minute": round(total_processed / (uptime_seconds / 60), 2) if uptime_seconds > 0 else 0
        },
        "finviz_config": {
            "elite_enabled": settings.FINVIZ_USE_ELITE,
            "max_requests_per_min": get_max_req_per_min(),
            "max_concurrency": get_max_concurrency(),
            "tickers_per_page": get_finviz_tickers_per_page()
        },
        "webhook_rate_limiter": webhook_rate_limiter_info
    }


@app.post("/admin/order/sell-individual", status_code=status.HTTP_200_OK)
async def sell_individual_order(payload: SellIndividualPayload):
    # Token validation
    if payload.token != FINVIZ_UPDATE_TOKEN:
        _logger.warning("Invalid token received for /admin/order/sell-individual.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")

    _logger.info(f"Received /admin/order/sell-individual request for ticker: {payload.ticker}")

    # Create Signal object
    signal_to_queue = Signal(ticker=payload.ticker, side='sell') # Using side='sell'

    # Create signal tracker for administrative signals
    tracker = create_signal_tracker(signal_to_queue)
    shared_state["signal_trackers"][signal_to_queue.signal_id] = tracker
    
    # Update metrics
    shared_state["signal_metrics"]["signals_received"] += 1
    
    # Update tracker - signal approved via admin interface
    update_signal_tracker(
        signal_to_queue.signal_id,
        SignalStatus.APPROVED,
        SignalLocation.APPROVED_QUEUE,
        worker_id='admin_sell_individual',
        details="Signal approved via admin sell-individual interface"
    )

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
        _logger.info(f"Sell signal for {payload.ticker} added to approved_signal_queue.")
        
        # Update tracker - signal queued for forwarding
        update_signal_tracker(
            signal_to_queue.signal_id,
            SignalStatus.QUEUED_FORWARDING,
            SignalLocation.APPROVED_QUEUE,
            worker_id='admin_sell_individual',
            details="Signal queued for forwarding via admin interface"
        )
        
        # Broadcast tracker update
        await broadcast_signal_tracker_update(tracker)

        # Update shared_state['sell_all_accumulator']
        shared_state['sell_all_accumulator'].add(payload.ticker.strip().upper())
        
        return {"message": f"Sell signal for {payload.ticker} queued successfully", "signal_id": signal_to_queue.signal_id}
        
    except Exception as e:
        _logger.error(f"Error queueing sell signal for {payload.ticker}: {e}")
        raise HTTPException(status_code=500, detail=f"Error queueing sell signal: {str(e)}")

@app.post("/admin/order/sell-all", status_code=status.HTTP_200_OK)
async def sell_all_orders(payload: TokenPayload):
    """Create and queue sell signals for all tickers in the sell_all_accumulator."""
    try:
        token = payload.token
        if token != FINVIZ_UPDATE_TOKEN:
            _logger.warning("Invalid token received for /admin/order/sell-all.")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")

        tickers_to_sell = list(shared_state.get('sell_all_accumulator', []))
        
        if not tickers_to_sell:
            return {"message": "No tickers to sell", "tickers_processed": []}

        processed_tickers = []
        for ticker in tickers_to_sell:
            try:
                # Create sell signal
                signal_to_queue = Signal(
                    ticker=ticker,
                    side='sell',
                    action='sell',
                    price=None,
                    time=datetime.utcnow().isoformat() + 'Z'
                )
                
                # Create tracker for this signal
                tracker = create_signal_tracker(signal_to_queue)
                shared_state["signal_trackers"][signal_to_queue.signal_id] = tracker
                
                # Prepare data for approved queue
                approved_signal_data = {
                    'signal': signal_to_queue,
                    'ticker': ticker,
                    'approved_at': time.time(),
                    'worker_id': 'admin_sell_all',
                    'signal_id': signal_to_queue.signal_id
                }
                
                # Add to approved queue
                await approved_signal_queue.put(approved_signal_data)
                
                # Update tracker
                update_signal_tracker(
                    signal_to_queue.signal_id,
                    SignalStatus.QUEUED_FORWARDING,
                    SignalLocation.APPROVED_QUEUE,
                    worker_id='admin_sell_all',
                    details=f"Sell-all signal queued for {ticker}"
                )
                
                processed_tickers.append(ticker)
                _logger.info(f"Sell-all signal queued for {ticker}")
                
            except Exception as ticker_error:
                _logger.error(f"Error processing sell signal for {ticker}: {ticker_error}")
                continue
        
        # Clear the accumulator
        shared_state['sell_all_accumulator'].clear()
        
        # Broadcast sell_all_list update
        sell_all_data = await get_sell_all_list_data()
        await comm_engine.trigger_sell_all_list_update(sell_all_data)
        
        return {
            "message": f"Processed {len(processed_tickers)} sell signals",
            "tickers_processed": processed_tickers,
            "total_requested": len(tickers_to_sell)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        _logger.error(f"Error in sell-all operation: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing sell-all: {str(e)}")

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
            "use_elite": settings.FINVIZ_USE_ELITE
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

@app.get("/admin", response_class=HTMLResponse)
async def admin_interface(request: Request):
    """Serve the admin interface."""
    return templates.TemplateResponse("admin.html", {"request": request})

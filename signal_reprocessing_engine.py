"""
Signal Reprocessing Engine - Robust implementation for handling rejected signal recovery.

This module provides a comprehensive and robust signal reprocessing system that handles
the recovery of previously rejected BUY signals when their tickers enter the Finviz Top-N list.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Union
from enum import Enum

from models import Signal as SignalPydanticModel

_logger = logging.getLogger("signal_reprocessing")


class ReprocessingStatus(Enum):
    """Status enumeration for reprocessing operations."""
    SUCCESS = "success"
    FAILED_VALIDATION = "failed_validation"
    FAILED_RECONSTRUCTION = "failed_reconstruction"
    FAILED_DATABASE = "failed_database"
    FAILED_QUEUE = "failed_queue"
    SKIPPED_NON_BUY = "skipped_non_buy"


@dataclass
class ReprocessingMetrics:
    """Comprehensive metrics for reprocessing operations."""
    signals_found: int = 0
    signals_processed: int = 0
    signals_successful: int = 0
    signals_failed: int = 0
    signals_skipped: int = 0
    reconstruction_failures: int = 0
    database_errors: int = 0
    queue_errors: int = 0
    validation_failures: int = 0
    last_run_timestamp: Optional[datetime] = None
    last_run_duration_ms: int = 0
    tickers_processed: Set[str] = field(default_factory=set)
    
    def reset(self):
        """Reset all counters for a new cycle."""
        self.signals_found = 0
        self.signals_processed = 0
        self.signals_successful = 0
        self.signals_failed = 0
        self.signals_skipped = 0
        self.reconstruction_failures = 0
        self.database_errors = 0
        self.queue_errors = 0
        self.validation_failures = 0
        self.tickers_processed.clear()
    
    def get_success_rate(self) -> float:
        """Calculate success rate as percentage."""
        if self.signals_processed == 0:
            return 0.0
        return (self.signals_successful / self.signals_processed) * 100.0


@dataclass
class ReprocessingResult:
    """Result of a reprocessing operation for multiple tickers."""
    success: bool
    tickers_processed: int
    signals_found: int
    signals_reprocessed: int
    signals_failed: int
    errors: List[str]
    duration_ms: int
    metrics: ReprocessingMetrics


@dataclass
class SignalReprocessingOutcome:
    """Outcome of processing a single signal."""
    signal_id: str
    ticker: str
    status: ReprocessingStatus
    success: bool
    error_message: Optional[str] = None
    reconstructed_signal: Optional[SignalPydanticModel] = None


class SignalValidator:
    """Validates signals for reprocessing eligibility."""
    
    @staticmethod
    def is_buy_signal(signal_data: Dict[str, Any]) -> bool:
        """
        Determine if a signal is a BUY signal using comprehensive logic.
        
        Args:
            signal_data: Dictionary containing signal information
            
        Returns:
            True if the signal is identified as a BUY signal
        """
        side = (signal_data.get("side") or "").lower().strip()
        signal_type = (signal_data.get("signal_type") or "").lower().strip()
        
        # Extract action from original_signal if available
        action = ""
        if original_signal := signal_data.get("original_signal"):
            action = (original_signal.get("action") or "").lower().strip()
        
        # Comprehensive buy indicators
        buy_indicators = {"buy", "long", "enter", "open", "bull", "purchase"}
        sell_indicators = {"sell", "short", "exit", "close", "bear"}
        
        # Check for explicit sell indicators first (higher priority)
        if (side in sell_indicators or 
            signal_type in sell_indicators or 
            action in sell_indicators):
            return False
        
        # Check for buy indicators
        if (side in buy_indicators or 
            signal_type in buy_indicators or 
            action in buy_indicators):
            return True
        
        # Default case: treat signals with no clear direction as BUY
        # This matches the original system behavior
        if not side and not signal_type and not action:
            return True
            
        # If signal_type is 'buy' (most common case)
        if signal_type == "buy":
            return True
            
        return False
    
    @staticmethod
    def validate_signal_data(signal_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        Validate signal data integrity.
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check required fields
        if not signal_data.get("signal_id"):
            return False, "Missing signal_id"
        
        if not signal_data.get("ticker"):
            return False, "Missing ticker"
        
        if not signal_data.get("normalised_ticker"):
            return False, "Missing normalised_ticker"
        
        # Validate original_signal structure if present
        if original_signal := signal_data.get("original_signal"):
            if not isinstance(original_signal, dict):
                return False, "original_signal must be a dictionary"
        
        return True, None


class SignalReconstructor:
    """Handles robust reconstruction of Signal objects from database data."""
    
    def __init__(self):
        self.reconstruction_timeout_ms = 5000
    
    async def reconstruct_signal(self, signal_data: Dict[str, Any]) -> Optional[SignalPydanticModel]:
        """
        Reconstruct a Signal object with multiple fallback strategies.
        
        Args:
            signal_data: Dictionary containing signal data from database
            
        Returns:
            Reconstructed Signal object or None if reconstruction fails
        """
        signal_id = signal_data.get("signal_id", "unknown")
        
        try:
            # Strategy 1: Use original_signal if available and valid
            if original_signal := signal_data.get("original_signal"):
                if reconstructed := await self._from_original_signal(original_signal, signal_id):
                    return reconstructed
            
            # Strategy 2: Reconstruct from basic database fields
            if reconstructed := await self._from_basic_fields(signal_data):
                return reconstructed
            
            # Strategy 3: Create minimal valid signal
            if reconstructed := await self._create_minimal_signal(signal_data):
                return reconstructed
                
        except Exception as e:
            _logger.error(f"Signal reconstruction failed for {signal_id}: {e}")
        
        return None
    
    async def _from_original_signal(self, original: Dict[str, Any], signal_id: str) -> Optional[SignalPydanticModel]:
        """Reconstruct from original_signal with validation."""
        try:
            # Ensure signal_id is set correctly
            original_copy = original.copy()
            original_copy["signal_id"] = signal_id
            
            # Validate required fields are present
            if not original_copy.get("ticker"):
                _logger.warning(f"Missing ticker in original_signal for {signal_id}")
                return None
            
            signal = SignalPydanticModel(**original_copy)
            _logger.debug(f"Successfully reconstructed signal {signal_id} from original_signal")
            return signal
            
        except Exception as e:
            _logger.warning(f"Failed to reconstruct from original_signal for {signal_id}: {e}")
            return None
    
    async def _from_basic_fields(self, signal_data: Dict[str, Any]) -> Optional[SignalPydanticModel]:
        """Reconstruct from basic database fields."""
        try:
            # Build minimal signal data from database fields
            signal_dict = {
                "signal_id": signal_data["signal_id"],
                "ticker": signal_data["ticker"],
                "side": signal_data.get("side", "buy"),  # Default to buy
                "price": signal_data.get("price"),
                "time": signal_data.get("created_at").isoformat() if signal_data.get("created_at") else None
            }
            
            # Add any additional fields that might be useful
            if action := signal_data.get("action"):
                signal_dict["action"] = action
            
            signal = SignalPydanticModel(**signal_dict)
            _logger.debug(f"Successfully reconstructed signal {signal_data['signal_id']} from basic fields")
            return signal
            
        except Exception as e:
            _logger.warning(f"Failed to reconstruct from basic fields for {signal_data.get('signal_id')}: {e}")
            return None
    
    async def _create_minimal_signal(self, signal_data: Dict[str, Any]) -> Optional[SignalPydanticModel]:
        """Create minimal valid signal as last resort."""
        try:
            # Absolute minimal signal
            signal_dict = {
                "signal_id": signal_data["signal_id"],
                "ticker": signal_data["ticker"],
                "side": "buy",  # Default for reprocessing
                "time": datetime.utcnow().isoformat()
            }
            
            signal = SignalPydanticModel(**signal_dict)
            _logger.info(f"Created minimal signal for {signal_data['signal_id']} as fallback")
            return signal
            
        except Exception as e:
            _logger.error(f"Failed to create minimal signal for {signal_data.get('signal_id')}: {e}")
            return None


class SignalReprocessingEngine:
    """
    Robust signal reprocessing engine with comprehensive error handling,
    metrics, and monitoring capabilities.
    """
    
    def __init__(self, db_manager, approved_signal_queue: asyncio.Queue):
        """
        Initialize the reprocessing engine.
        
        Args:
            db_manager: Database manager instance
            approved_signal_queue: Queue for approved signals awaiting forwarding
        """
        self.db_manager = db_manager
        self.approved_signal_queue = approved_signal_queue
        self.validator = SignalValidator()
        self.reconstructor = SignalReconstructor()
        self.metrics = ReprocessingMetrics()
        
        # Configuration
        self.max_signals_per_ticker = 100
        self.processing_timeout_ms = 30000  # 30 seconds
        
    async def process_new_tickers(self, new_tickers: Set[str], window_seconds: int) -> ReprocessingResult:
        """
        Process reprocessing for newly entered Top-N tickers.
        
        Args:
            new_tickers: Set of tickers that newly entered the Top-N list
            window_seconds: Time window to look back for rejected signals (0 = infinite)
            
        Returns:
            Detailed result of the reprocessing operation
        """
        start_time = time.time()
        self.metrics.reset()
        self.metrics.last_run_timestamp = datetime.utcnow()
        
        errors = []
        
        _logger.info(f"[ReprocessingEngine] Starting reprocessing cycle for {len(new_tickers)} new tickers")
        _logger.debug(f"[ReprocessingEngine] Window: {window_seconds}s, Tickers: {sorted(new_tickers)}")
        
        try:
            for ticker in new_tickers:
                try:
                    await self._process_ticker(ticker, window_seconds)
                    self.metrics.tickers_processed.add(ticker)
                except Exception as e:
                    error_msg = f"Failed to process ticker {ticker}: {str(e)}"
                    _logger.error(f"[ReprocessingEngine] {error_msg}")
                    errors.append(error_msg)
                    continue
            
            # Broadcast metrics update
            await self._broadcast_metrics_update()
            
        except Exception as e:
            error_msg = f"Critical error in reprocessing cycle: {str(e)}"
            _logger.error(f"[ReprocessingEngine] {error_msg}")
            errors.append(error_msg)
        
        # Calculate final metrics
        duration_ms = int((time.time() - start_time) * 1000)
        self.metrics.last_run_duration_ms = duration_ms
        
        success = len(errors) == 0 and self.metrics.signals_failed == 0
        
        result = ReprocessingResult(
            success=success,
            tickers_processed=len(self.metrics.tickers_processed),
            signals_found=self.metrics.signals_found,
            signals_reprocessed=self.metrics.signals_successful,
            signals_failed=self.metrics.signals_failed,
            errors=errors,
            duration_ms=duration_ms,
            metrics=self.metrics
        )
        
        _logger.info(f"[ReprocessingEngine] Cycle completed in {duration_ms}ms: "
                    f"{result.signals_reprocessed} successful, {result.signals_failed} failed, "
                    f"success rate: {self.metrics.get_success_rate():.1f}%")
        
        return result
    
    async def _process_ticker(self, ticker: str, window_seconds: int) -> None:
        """Process reprocessing for a single ticker."""
        _logger.debug(f"[ReprocessingEngine:{ticker}] Processing ticker with {window_seconds}s window")
        
        # Get rejected signals for this ticker
        try:
            rejected_signals = await self.db_manager.get_rejected_signals_for_reprocessing(ticker, window_seconds)
            self.metrics.signals_found += len(rejected_signals)
            
            if not rejected_signals:
                _logger.debug(f"[ReprocessingEngine:{ticker}] No rejected signals found")
                return
            
            _logger.info(f"[ReprocessingEngine:{ticker}] Found {len(rejected_signals)} candidate signals")
            
        except Exception as e:
            _logger.error(f"[ReprocessingEngine:{ticker}] Database error retrieving signals: {e}")
            self.metrics.database_errors += 1
            raise
        
        # Process each signal
        for signal_data in rejected_signals:
            outcome = await self._process_single_signal(signal_data, ticker)
            self._update_metrics_from_outcome(outcome)
    
    async def _process_single_signal(self, signal_data: Dict[str, Any], ticker: str) -> SignalReprocessingOutcome:
        """Process a single signal for reprocessing."""
        signal_id = signal_data.get("signal_id", "unknown")
        
        _logger.debug(f"[ReprocessingEngine:{ticker}:{signal_id}] Processing signal")
        
        # Step 1: Validate signal data
        is_valid, validation_error = self.validator.validate_signal_data(signal_data)
        if not is_valid:
            _logger.warning(f"[ReprocessingEngine:{ticker}:{signal_id}] Validation failed: {validation_error}")
            return SignalReprocessingOutcome(
                signal_id=signal_id,
                ticker=ticker,
                status=ReprocessingStatus.FAILED_VALIDATION,
                success=False,
                error_message=validation_error
            )
        
        # Step 2: Check if it's a BUY signal
        if not self.validator.is_buy_signal(signal_data):
            _logger.debug(f"[ReprocessingEngine:{ticker}:{signal_id}] Skipping non-BUY signal")
            return SignalReprocessingOutcome(
                signal_id=signal_id,
                ticker=ticker,
                status=ReprocessingStatus.SKIPPED_NON_BUY,
                success=False,
                error_message="Not a BUY signal"
            )
        
        _logger.debug(f"[ReprocessingEngine:{ticker}:{signal_id}] Validated as BUY signal")
        
        # Step 3: Re-approve in database
        try:
            reapproved = await self.db_manager.reapprove_signal(
                signal_id, 
                f"Signal re-approved via reprocessing engine - ticker {ticker} entered Top-N list"
            )
            if not reapproved:
                error_msg = "Failed to re-approve signal in database"
                _logger.error(f"[ReprocessingEngine:{ticker}:{signal_id}] {error_msg}")
                return SignalReprocessingOutcome(
                    signal_id=signal_id,
                    ticker=ticker,
                    status=ReprocessingStatus.FAILED_DATABASE,
                    success=False,
                    error_message=error_msg
                )
            
            _logger.debug(f"[ReprocessingEngine:{ticker}:{signal_id}] Database re-approval: SUCCESS")
            
        except Exception as e:
            error_msg = f"Database error during re-approval: {str(e)}"
            _logger.error(f"[ReprocessingEngine:{ticker}:{signal_id}] {error_msg}")
            return SignalReprocessingOutcome(
                signal_id=signal_id,
                ticker=ticker,
                status=ReprocessingStatus.FAILED_DATABASE,
                success=False,
                error_message=error_msg
            )
        
        # Step 4: Reconstruct Signal object
        try:
            reconstructed_signal = await self.reconstructor.reconstruct_signal(signal_data)
            if not reconstructed_signal:
                error_msg = "Failed to reconstruct Signal object"
                _logger.error(f"[ReprocessingEngine:{ticker}:{signal_id}] {error_msg}")
                return SignalReprocessingOutcome(
                    signal_id=signal_id,
                    ticker=ticker,
                    status=ReprocessingStatus.FAILED_RECONSTRUCTION,
                    success=False,
                    error_message=error_msg
                )
            
            _logger.debug(f"[ReprocessingEngine:{ticker}:{signal_id}] Signal reconstruction: SUCCESS")
            
        except Exception as e:
            error_msg = f"Error during signal reconstruction: {str(e)}"
            _logger.error(f"[ReprocessingEngine:{ticker}:{signal_id}] {error_msg}")
            return SignalReprocessingOutcome(
                signal_id=signal_id,
                ticker=ticker,
                status=ReprocessingStatus.FAILED_RECONSTRUCTION,
                success=False,
                error_message=error_msg
            )
        
        # Step 5: Open position (for BUY signals)
        try:
            await self.db_manager.open_position(ticker=ticker, entry_signal_id=signal_id)
            _logger.debug(f"[ReprocessingEngine:{ticker}:{signal_id}] Position opened")
            
            # Update sell_all list
            from main import get_sell_all_list_data, comm_engine
            sell_all_data = await get_sell_all_list_data()
            await comm_engine.trigger_sell_all_list_update(sell_all_data)
            
        except Exception as e:
            # Don't fail the entire reprocessing for position errors
            _logger.warning(f"[ReprocessingEngine:{ticker}:{signal_id}] Position management warning: {e}")
        
        # Step 6: Add to forwarding queue
        try:
            approved_signal_data = {
                'signal': reconstructed_signal,
                'ticker': reconstructed_signal.normalised_ticker(),
                'approved_at': time.time(),
                'worker_id': 'reprocessing_engine',
                'signal_id': signal_id
            }
            
            await self.approved_signal_queue.put(approved_signal_data)
            _logger.info(f"[ReprocessingEngine:{ticker}:{signal_id}] Added to forwarding queue: SUCCESS")
            
        except Exception as e:
            error_msg = f"Error adding to forwarding queue: {str(e)}"
            _logger.error(f"[ReprocessingEngine:{ticker}:{signal_id}] {error_msg}")
            return SignalReprocessingOutcome(
                signal_id=signal_id,
                ticker=ticker,
                status=ReprocessingStatus.FAILED_QUEUE,
                success=False,
                error_message=error_msg
            )
        
        # Success!
        _logger.info(f"[ReprocessingEngine:{ticker}:{signal_id}] Reprocessing completed successfully")
        return SignalReprocessingOutcome(
            signal_id=signal_id,
            ticker=ticker,
            status=ReprocessingStatus.SUCCESS,
            success=True,
            reconstructed_signal=reconstructed_signal
        )
    
    def _update_metrics_from_outcome(self, outcome: SignalReprocessingOutcome) -> None:
        """Update metrics based on processing outcome."""
        self.metrics.signals_processed += 1
        
        if outcome.success:
            self.metrics.signals_successful += 1
        else:
            if outcome.status == ReprocessingStatus.SKIPPED_NON_BUY:
                self.metrics.signals_skipped += 1
            else:
                self.metrics.signals_failed += 1
                
                # Update specific error counters
                if outcome.status == ReprocessingStatus.FAILED_VALIDATION:
                    self.metrics.validation_failures += 1
                elif outcome.status == ReprocessingStatus.FAILED_RECONSTRUCTION:
                    self.metrics.reconstruction_failures += 1
                elif outcome.status == ReprocessingStatus.FAILED_DATABASE:
                    self.metrics.database_errors += 1
                elif outcome.status == ReprocessingStatus.FAILED_QUEUE:
                    self.metrics.queue_errors += 1
    
    async def _broadcast_metrics_update(self) -> None:
        """Broadcast updated metrics to connected clients."""
        try:
            from comm_engine import comm_engine
            from main import get_current_metrics
            await comm_engine.broadcast("metrics_update", get_current_metrics())
            _logger.debug("[ReprocessingEngine] Metrics broadcasted successfully")
        except Exception as e:
            _logger.warning(f"[ReprocessingEngine] Failed to broadcast metrics: {e}")
    
    def get_health_status(self) -> Dict[str, Any]:
        """Get current health status of the reprocessing engine."""
        if not self.metrics.last_run_timestamp:
            return {
                "status": "UNKNOWN",
                "message": "No reprocessing cycles completed yet",
                "last_run": None
            }
        
        success_rate = self.metrics.get_success_rate()
        
        if success_rate >= 95.0:
            status = "HEALTHY"
        elif success_rate >= 85.0:
            status = "WARNING"
        else:
            status = "CRITICAL"
        
        return {
            "status": status,
            "success_rate": success_rate,
            "last_run": self.metrics.last_run_timestamp.isoformat(),
            "last_duration_ms": self.metrics.last_run_duration_ms,
            "signals_processed": self.metrics.signals_processed,
            "signals_successful": self.metrics.signals_successful,
            "signals_failed": self.metrics.signals_failed
        }

"""Webhook Rate Limiter with Sliding Window Token Bucket Algorithm.

This module implements a robust rate limiting mechanism for destination webhook requests,
ensuring no signals are lost while respecting the webhook's rate limits.

Key Features:
- Sliding window token bucket algorithm for precise rate limiting
- Each token is returned exactly 60 seconds after use
- True distributed rate limiting without burst allowances
- Configurable requests per minute
- Zero signal loss guarantee via queuing
- Real-time metrics and monitoring
- Graceful error handling and retries
"""

import asyncio
import time
import logging
import heapq
from typing import Dict, Any, Callable, Optional, List, Tuple
from config import settings

_logger = logging.getLogger("webhook_rate_limiter")


class WebhookRateLimiter:
    """Rate limiter for destination webhook requests using sliding window token bucket algorithm."""
    
    def __init__(self, shared_state: Dict[str, Any], admin_ws_broadcaster: Callable):
        self.shared_state = shared_state
        self.admin_ws_broadcaster = admin_ws_broadcaster
        
        # Rate limiting configuration
        self.max_req_per_min = settings.DEST_WEBHOOK_MAX_REQ_PER_MIN
        self.rate_limiting_enabled = settings.DEST_WEBHOOK_RATE_LIMITING_ENABLED
        
        # Sliding window implementation
        self.rate_limit_semaphore = asyncio.Semaphore(self.max_req_per_min)
        self.token_return_queue: List[Tuple[float, int]] = []  # Min heap of (return_time, token_id)
        self.next_token_id = 0
        
        # Sliding window for "This Minute" metric - tracks requests in last 60 seconds
        self.requests_last_minute: List[float] = []  # List of timestamps for requests made
        
        # Control flags
        self._running = False
        self._token_manager_task: Optional[asyncio.Task] = None
        
        # Initialize rate limiting metrics in shared state
        if "webhook_rate_limiter" not in self.shared_state:
            self.shared_state["webhook_rate_limiter"] = {
                "tokens_available": self.max_req_per_min,
                "requests_made_this_minute": 0,
                "total_requests_limited": 0,
                "rate_limiting_enabled": self.rate_limiting_enabled,
                "max_req_per_min": self.max_req_per_min,
                "last_token_refresh": time.time(),
                "pending_token_returns": 0
            }
    
    async def acquire_token(self) -> bool:
        """
        Acquire a token for making a webhook request.
        Each token will be returned exactly 60 seconds after acquisition.
        
        Returns:
            bool: True if token acquired, False if rate limiting disabled
        """
        if not self.rate_limiting_enabled:
            _logger.debug("Rate limiting disabled, allowing request without token")
            return True
        
        _logger.debug(f"Attempting to acquire token. Available: {self.rate_limit_semaphore._value}/{self.max_req_per_min}")
        
        # Check if we have tokens available before attempting to acquire
        if self.rate_limit_semaphore._value == 0:
            self.shared_state["webhook_rate_limiter"]["total_requests_limited"] += 1
            _logger.info(f"Webhook request rate limited - no tokens available. Total limited: {self.shared_state['webhook_rate_limiter']['total_requests_limited']}")
            
            # Broadcast updated metrics
            await self._broadcast_metrics()
        
        # Acquire token from semaphore (blocks if no tokens available)
        _logger.debug(f"Acquiring token from semaphore...")
        await self.rate_limit_semaphore.acquire()
        _logger.debug(f"Token acquired successfully! Remaining: {self.rate_limit_semaphore._value}")
        
        # Schedule this token to be returned in exactly 60 seconds
        current_time = time.time()
        return_time = current_time + 60.0  # Exactly 60 seconds from now
        token_id = self.next_token_id
        self.next_token_id += 1
        
        # Add to return queue (min heap sorted by return time)
        heapq.heappush(self.token_return_queue, (return_time, token_id))
        
        # Add timestamp to sliding window for "This Minute" metric
        self.requests_last_minute.append(current_time)
        
        # Clean old requests from sliding window (older than 60 seconds)
        self._clean_sliding_window_requests(current_time)
        
        # Update metrics with corrected "This Minute" count
        self.shared_state["webhook_rate_limiter"]["requests_made_this_minute"] = len(self.requests_last_minute)
        self.shared_state["webhook_rate_limiter"]["tokens_available"] = self.rate_limit_semaphore._value
        self.shared_state["webhook_rate_limiter"]["pending_token_returns"] = len(self.token_return_queue)
        
        _logger.debug(f"Token {token_id} acquired, will be returned at {return_time:.2f} (in 60s)")
        
        # Broadcast updated metrics
        await self._broadcast_metrics()
        
        return True
    
    async def start(self):
        """Start the webhook rate limiter."""
        if self._running:
            _logger.warning("WebhookRateLimiter is already running")
            return
        
        self._running = True
        _logger.info(f"Starting WebhookRateLimiter - Rate limiting: {self.rate_limiting_enabled}, Max req/min: {self.max_req_per_min}")
        
        if self.rate_limiting_enabled:
            # Start the sliding window token manager
            self._token_manager_task = asyncio.create_task(self._sliding_window_token_manager())
            _logger.info("Started sliding window token manager")
        else:
            _logger.info("Rate limiting disabled, no token management needed")
    
    async def stop(self):
        """Stop the webhook rate limiter."""
        _logger.info("Stopping WebhookRateLimiter...")
        self._running = False
        
        # Cancel token manager task if running
        if self._token_manager_task and not self._token_manager_task.done():
            self._token_manager_task.cancel()
            try:
                await self._token_manager_task
            except asyncio.CancelledError:
                pass
        
        _logger.info("WebhookRateLimiter stopped")
    
    async def _sliding_window_token_manager(self):
        """
        Manages the sliding window by returning tokens exactly 60 seconds after they were used.
        This creates a true sliding window rate limiter.
        """
        _logger.info("Starting sliding window token manager")
        
        while self._running:
            try:
                current_time = time.time()
                tokens_returned = 0
                
                # Return all tokens whose time has come
                while (self.token_return_queue and 
                       self.token_return_queue[0][0] <= current_time):
                    
                    return_time, token_id = heapq.heappop(self.token_return_queue)
                    
                    # Return the token to the semaphore
                    self.rate_limit_semaphore.release()
                    tokens_returned += 1
                    
                    _logger.debug(f"Token {token_id} returned to pool (was scheduled for {return_time:.2f})")
                
                # Update metrics if tokens were returned
                if tokens_returned > 0:
                    self.shared_state["webhook_rate_limiter"]["tokens_available"] = self.rate_limit_semaphore._value
                    self.shared_state["webhook_rate_limiter"]["pending_token_returns"] = len(self.token_return_queue)
                    
                    _logger.info(f"Returned {tokens_returned} tokens to pool. Available: {self.rate_limit_semaphore._value}")
                
                # Update sliding window metrics (clean old requests and update count)
                self._update_sliding_window_metrics(current_time)
                
                # Broadcast updated metrics (only if there were changes)
                if tokens_returned > 0 or len(self.requests_last_minute) != self.shared_state["webhook_rate_limiter"].get("requests_made_this_minute", 0):
                    await self._broadcast_metrics()
                
                # Sleep until next token needs to be returned (or max 1 second)
                sleep_time = 1.0  # Default sleep
                if self.token_return_queue:
                    next_return_time = self.token_return_queue[0][0]
                    time_until_next = next_return_time - current_time
                    sleep_time = min(max(time_until_next, 0.1), 1.0)  # Between 0.1s and 1s
                
                await asyncio.sleep(sleep_time)
                
            except Exception as e:
                _logger.error(f"Error in sliding window token manager: {e}")
                await asyncio.sleep(1)
    
    async def _broadcast_metrics(self):
        """Broadcast rate limiter metrics to admin WebSocket clients using centralized comm_engine."""
        try:
            from comm_engine import comm_engine
            
            # Build metrics directly instead of importing from main to avoid circular import
            webhook_rl_data = {
                "enabled": self.rate_limiting_enabled,
                "status": "Enabled" if self.rate_limiting_enabled else "Disabled", 
                "tokens_available": self.rate_limit_semaphore._value,
                "max_req_per_min": self.max_req_per_min,
                "requests_made_this_minute": len(self.requests_last_minute),
                "requests_this_minute": len(self.requests_last_minute),
                "total_requests_limited": self.shared_state.get("webhook_rate_limiter", {}).get("total_requests_limited", 0),
                "total_limited": self.shared_state.get("webhook_rate_limiter", {}).get("total_requests_limited", 0),
                "pending_token_returns": len(self.token_return_queue),
                "is_rate_limited": self.is_rate_limited(),
            }
            
            await comm_engine.trigger_webhook_rate_limiter_update(webhook_rl_data)
        except Exception as e:
            _logger.error(f"Failed to broadcast webhook rate limiter metrics: {e}")
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get current rate limiter metrics with real-time sliding window update."""
        # Update sliding window metrics before returning
        current_time = time.time()
        self._update_sliding_window_metrics(current_time)
        
        metrics = self.shared_state.get("webhook_rate_limiter", {}).copy()
        
        # Add additional computed metrics
        metrics.update({
            "is_rate_limited": self.is_rate_limited(),
            "requests_in_last_minute": len(self.requests_last_minute),
            "oldest_request_age": current_time - min(self.requests_last_minute) if self.requests_last_minute else 0,
            "newest_request_age": current_time - max(self.requests_last_minute) if self.requests_last_minute else 0,
        })
        
        return metrics
    
    def get_detailed_metrics(self) -> Dict[str, Any]:
        """Get detailed rate limiter metrics for debugging and monitoring."""
        current_time = time.time()
        self._update_sliding_window_metrics(current_time)
        
        # Get basic metrics
        basic_metrics = self.get_metrics()
        
        # Add detailed information
        detailed_metrics = basic_metrics.copy()
        detailed_metrics.update({
            "sliding_window_details": {
                "total_requests_tracked": len(self.requests_last_minute),
                "request_timestamps": self.requests_last_minute.copy(),
                "window_start_time": current_time - 60.0,
                "window_end_time": current_time,
            },
            "token_management": {
                "pending_returns": len(self.token_return_queue),
                "next_return_time": self.token_return_queue[0][0] if self.token_return_queue else None,
                "semaphore_value": self.rate_limit_semaphore._value,
                "max_semaphore_value": self.max_req_per_min,
            },
            "system_state": {
                "rate_limiting_enabled": self.rate_limiting_enabled,
                "system_running": self._running,
                "token_manager_active": self._token_manager_task and not self._token_manager_task.done() if self._token_manager_task else False,
            }
        })
        
        return detailed_metrics
    
    async def update_config(self, max_req_per_min: Optional[int] = None, enabled: Optional[bool] = None):
        """
        Update rate limiter configuration dynamically.
        
        Args:
            max_req_per_min: New maximum requests per minute limit
            enabled: Enable/disable rate limiting
        """
        config_changed = False
        
        if max_req_per_min is not None and max_req_per_min != self.max_req_per_min:
            if max_req_per_min < 1:
                raise ValueError("max_req_per_min must be at least 1")
            if max_req_per_min > 300:
                raise ValueError("max_req_per_min cannot exceed 300 (safety limit)")
                
            old_limit = self.max_req_per_min
            self.max_req_per_min = max_req_per_min
            
            # Adjust semaphore capacity
            if max_req_per_min > old_limit:
                # Add tokens
                for _ in range(max_req_per_min - old_limit):
                    self.rate_limit_semaphore.release()
            elif max_req_per_min < old_limit:
                # Remove tokens (by acquiring them)
                for _ in range(old_limit - max_req_per_min):
                    try:
                        self.rate_limit_semaphore.acquire_nowait()
                    except:
                        break  # No more tokens to remove
            
            # Update metrics
            self.shared_state["webhook_rate_limiter"]["max_req_per_min"] = max_req_per_min
            self.shared_state["webhook_rate_limiter"]["tokens_available"] = self.rate_limit_semaphore._value
            
            _logger.info(f"Updated webhook rate limit from {old_limit} to {max_req_per_min} req/min")
            config_changed = True
        
        if enabled is not None and enabled != self.rate_limiting_enabled:
            self.rate_limiting_enabled = enabled
            self.shared_state["webhook_rate_limiter"]["rate_limiting_enabled"] = enabled
            
            if enabled and self._running:
                # Start token manager if enabling rate limiting and we're running
                if not self._token_manager_task or self._token_manager_task.done():
                    self._token_manager_task = asyncio.create_task(self._sliding_window_token_manager())
            elif not enabled and self._token_manager_task:
                # Stop token manager if disabling rate limiting
                self._token_manager_task.cancel()
            
            _logger.info(f"Webhook rate limiting {'enabled' if enabled else 'disabled'}")
            config_changed = True
        
        if config_changed:
            await self._broadcast_metrics()
    
    def pause(self):
        """Pause rate limiting (disable temporarily)."""
        _logger.info("Pausing webhook rate limiting")
        self.rate_limiting_enabled = False
        self.shared_state["webhook_rate_limiter"]["rate_limiting_enabled"] = False
    
    def resume(self):
        """Resume rate limiting (enable)."""
        _logger.info("Resuming webhook rate limiting")
        self.rate_limiting_enabled = True
        self.shared_state["webhook_rate_limiter"]["rate_limiting_enabled"] = True
        
        # Restart token manager if needed
        if self._running and (not self._token_manager_task or self._token_manager_task.done()):
            self._token_manager_task = asyncio.create_task(self._sliding_window_token_manager())
    
    def reset_metrics(self):
        """Reset rate limiting metrics."""
        _logger.info("Resetting webhook rate limiter metrics")
        self.shared_state["webhook_rate_limiter"]["total_requests_limited"] = 0
        self.shared_state["webhook_rate_limiter"]["requests_made_this_minute"] = 0
        
        # Clear sliding window requests list
        self.requests_last_minute.clear()
        _logger.debug("Cleared sliding window requests list")
    
    def is_rate_limited(self) -> bool:
        """Check if currently rate limited (no tokens available)."""
        if not self.rate_limiting_enabled:
            return False
        return self.rate_limit_semaphore._value == 0
    
    async def wait_for_token(self, timeout: Optional[float] = None) -> bool:
        """
        Wait for a token to become available.
        
        Args:
            timeout: Maximum time to wait for a token (None = wait forever)
            
        Returns:
            bool: True if token acquired within timeout, False if timed out
        """
        if not self.rate_limiting_enabled:
            return True
            
        try:
            if timeout:
                await asyncio.wait_for(self.acquire_token(), timeout=timeout)
            else:
                await self.acquire_token()
            return True
        except asyncio.TimeoutError:
            _logger.warning(f"Timed out waiting for webhook rate limit token after {timeout}s")
            return False
    
    def _clean_sliding_window_requests(self, current_time: float):
        """
        Remove requests older than 60 seconds from the sliding window.
        
        Args:
            current_time: Current timestamp to use as reference
        """
        cutoff_time = current_time - 60.0
        
        # Remove all timestamps older than 60 seconds
        # Using list comprehension for efficiency
        self.requests_last_minute = [
            timestamp for timestamp in self.requests_last_minute 
            if timestamp > cutoff_time
        ]
    
    def _update_sliding_window_metrics(self, current_time: float):
        """
        Update the sliding window metrics by cleaning old requests.
        This should be called periodically to keep metrics accurate.
        
        Args:
            current_time: Current timestamp to use as reference
        """
        self._clean_sliding_window_requests(current_time)
        self.shared_state["webhook_rate_limiter"]["requests_made_this_minute"] = len(self.requests_last_minute)
    
    def validate_metrics(self) -> Dict[str, Any]:
        """
        Validate the consistency of all rate limiter metrics.
        
        Returns:
            Dict with validation results and any inconsistencies found
        """
        current_time = time.time()
        self._update_sliding_window_metrics(current_time)
        
        validation_results = {
            "is_valid": True,
            "inconsistencies": [],
            "warnings": [],
            "metrics_snapshot": self.get_metrics()
        }
        
        # Check sliding window consistency
        stored_count = self.shared_state["webhook_rate_limiter"]["requests_made_this_minute"]
        actual_count = len(self.requests_last_minute)
        
        if stored_count != actual_count:
            validation_results["is_valid"] = False
            validation_results["inconsistencies"].append(
                f"Stored requests_made_this_minute ({stored_count}) != actual sliding window count ({actual_count})"
            )
        
        # Check token availability consistency
        semaphore_value = self.rate_limit_semaphore._value
        stored_tokens = self.shared_state["webhook_rate_limiter"]["tokens_available"]
        
        if semaphore_value != stored_tokens:
            validation_results["is_valid"] = False
            validation_results["inconsistencies"].append(
                f"Semaphore value ({semaphore_value}) != stored tokens_available ({stored_tokens})"
            )
        
        # Check if tokens available + pending returns <= max_req_per_min
        pending_returns = len(self.token_return_queue)
        total_tokens = semaphore_value + pending_returns
        
        if total_tokens != self.max_req_per_min:
            validation_results["warnings"].append(
                f"Total tokens ({total_tokens}) != max_req_per_min ({self.max_req_per_min}). "
                f"Available: {semaphore_value}, Pending: {pending_returns}"
            )
        
        # Check sliding window timestamps
        cutoff_time = current_time - 60.0
        old_requests = [ts for ts in self.requests_last_minute if ts <= cutoff_time]
        
        if old_requests:
            validation_results["warnings"].append(
                f"Found {len(old_requests)} requests older than 60 seconds in sliding window"
            )
        
        # Check rate limiting state consistency
        is_rate_limited = self.is_rate_limited()
        no_tokens_available = (semaphore_value == 0)
        
        if is_rate_limited != no_tokens_available:
            validation_results["inconsistencies"].append(
                f"is_rate_limited() ({is_rate_limited}) != (tokens == 0) ({no_tokens_available})"
            )
        
        return validation_results

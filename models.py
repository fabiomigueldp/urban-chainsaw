"""Pydantic models that formalise the shape of inbound trading signals.

We purposefully allow *extra* keys so that the service is tolerant to
future changes in payload structure without immediate redeployment.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from enum import Enum
from datetime import datetime
import uuid
import time
import math

class Signal(BaseModel):
    """Generic trading signal.

    Only the *ticker* is mandatory for filtering.
    """
    model_config = {"extra": "allow"}
    
    ticker: str = Field(..., example="AAPL")
    side: Optional[str] = Field(None, example="BUY")
    action: Optional[str] = Field(None, example="buy")
    price: Optional[float] = Field(None, example=187.12)
    time: Optional[str] = Field(
        None, description="ISO‑8601 timestamp of market event.")
    
    # New fields for tracking
    signal_id: Optional[str] = Field(None, description="Unique identifier for this signal")
    received_at: Optional[float] = Field(None, description="Unix timestamp when signal was received")

    def __init__(self, **data):
        """Initialize signal with unique ID and timestamp if not provided."""
        if 'signal_id' not in data or not data['signal_id']:
            data['signal_id'] = str(uuid.uuid4())
        if 'received_at' not in data or not data['received_at']:
            data['received_at'] = time.time()
        super().__init__(**data)

    def normalised_ticker(self) -> str:
        """Return upper‑case symbol without leading/trailing whitespace."""
        return self.ticker.strip().upper()


class SellIndividualPayload(BaseModel):
    model_config = {"extra": "allow"}
    
    ticker: str
    token: str


class TokenPayload(BaseModel):
    model_config = {"extra": "allow"}
    
    token: str


class SignalStatus(str, Enum):
    """Signal status enumeration for tracking."""
    RECEIVED = "received"
    QUEUED_PROCESSING = "queued_processing"
    PROCESSING = "processing"
    APPROVED = "approved"
    REJECTED = "rejected"
    QUEUED_FORWARDING = "queued_forwarding"
    FORWARDING = "forwarding"
    FORWARDED_SUCCESS = "forwarded_success"
    FORWARDED_TIMEOUT = "forwarded_timeout"
    FORWARDED_HTTP_ERROR = "forwarded_http_error"
    FORWARDED_GENERIC_ERROR = "forwarded_generic_error"
    DISCARDED = "discarded"

class SignalLocation(str, Enum):
    """Signal location enumeration for tracking where the signal is."""
    PROCESSING_QUEUE = "processing_queue"
    APPROVED_QUEUE = "approved_queue"
    WORKER_PROCESSING = "worker_processing"
    WORKER_FORWARDING = "worker_forwarding"
    COMPLETED = "completed"
    DISCARDED = "discarded"

class SignalEvent(BaseModel):
    """Individual event in signal lifecycle."""
    model_config = {"extra": "allow"}
    
    timestamp: float = Field(default_factory=time.time)
    event_type: SignalStatus
    location: SignalLocation
    worker_id: Optional[str] = None
    details: Optional[str] = None
    error_info: Optional[Dict[str, Any]] = None
    http_status: Optional[int] = None
    response_data: Optional[str] = None
    
class SignalTracker(BaseModel):
    """Complete signal tracking information."""
    model_config = {"extra": "allow"}
    
    signal_id: str
    ticker: str
    normalised_ticker: str
    original_signal: Dict[str, Any]
    current_status: SignalStatus
    current_location: SignalLocation
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    events: List[SignalEvent] = Field(default_factory=list)
    
    # New fields for enhanced tracking
    total_processing_time: Optional[float] = None
    error_count: int = 0
    retry_count: int = 0
    tags: List[str] = Field(default_factory=list)
    
    def add_event(self, event_type: SignalStatus, location: SignalLocation, 
                  worker_id: Optional[str] = None, details: Optional[str] = None,
                  error_info: Optional[Dict[str, Any]] = None, 
                  http_status: Optional[int] = None, response_data: Optional[str] = None):
        """Add a new event to the signal tracking."""
        # Count errors for metrics
        if error_info or (http_status and http_status >= 400):
            self.error_count += 1
        
        # Count retries
        if event_type in [SignalStatus.FORWARDING, SignalStatus.PROCESSING]:
            existing_same_events = [e for e in self.events if e.event_type == event_type]
            if len(existing_same_events) > 0:
                self.retry_count += 1
        
        event = SignalEvent(
            event_type=event_type,
            location=location,
            worker_id=worker_id,
            details=details,
            error_info=error_info,
            http_status=http_status,
            response_data=response_data
        )
        self.events.append(event)
        self.current_status = event_type
        self.current_location = location
        self.updated_at = time.time()
        
        # Calculate total processing time if completed
        if event_type in [SignalStatus.FORWARDED_SUCCESS, SignalStatus.FORWARDED_HTTP_ERROR, 
                         SignalStatus.FORWARDED_TIMEOUT, SignalStatus.FORWARDED_GENERIC_ERROR, 
                         SignalStatus.REJECTED, SignalStatus.DISCARDED]:
            self.total_processing_time = self.updated_at - self.created_at
    
    def get_current_status_display(self) -> str:
        """Get human-readable current status."""
        status_map = {
            SignalStatus.RECEIVED: "📨 Received",
            SignalStatus.QUEUED_PROCESSING: "⏳ Queued for Processing",
            SignalStatus.PROCESSING: "⚙️ Processing",
            SignalStatus.APPROVED: "✅ Approved",
            SignalStatus.REJECTED: "❌ Rejected",
            SignalStatus.QUEUED_FORWARDING: "📤 Queued for Forwarding",
            SignalStatus.FORWARDING: "🚀 Forwarding",
            SignalStatus.FORWARDED_SUCCESS: "✅ Successfully Forwarded",
            SignalStatus.FORWARDED_TIMEOUT: "⏰ Forwarding Timeout",
            SignalStatus.FORWARDED_HTTP_ERROR: "❌ HTTP Error",
            SignalStatus.FORWARDED_GENERIC_ERROR: "💥 Generic Error",
            SignalStatus.DISCARDED: "🗑️ Discarded"
        }
        return status_map.get(self.current_status, f"Unknown: {self.current_status}")
    
    def get_journey_summary(self) -> str:
        """Get a summary of the signal's journey."""
        if not self.events:
            return "No events recorded"
        
        duration = self.updated_at - self.created_at
        return f"Journey: {len(self.events)} events over {duration:.2f}s"
    
    def get_performance_metrics(self) -> Dict[str, Any]:
        """Get detailed performance metrics for this signal."""
        duration = self.updated_at - self.created_at
        
        # Calculate time spent in each stage
        stage_durations = {}
        for i, event in enumerate(self.events):
            if i == 0:
                continue
            prev_event = self.events[i-1]
            stage_name = f"{prev_event.event_type.value}_to_{event.event_type.value}"
            stage_duration = event.timestamp - prev_event.timestamp
            stage_durations[stage_name] = stage_duration
        
        return {
            "total_duration": duration,
            "stage_durations": stage_durations,
            "error_count": self.error_count,
            "retry_count": self.retry_count,
            "events_count": len(self.events),
            "is_completed": self.current_location in [SignalLocation.COMPLETED, SignalLocation.DISCARDED],
            "final_status": self.current_status.value,
            "processing_efficiency": 1.0 - (self.error_count / max(len(self.events), 1))
        }
    
    def matches_filter(self, ticker_filter: str = None, status_filter: str = None, 
                      location_filter: str = None, start_time: float = None, 
                      end_time: float = None, error_only: bool = False,
                      min_duration: float = None, max_duration: float = None) -> bool:
        """Check if this signal matches the given filters."""
        # Ticker filter
        if ticker_filter:
            ticker_filter = ticker_filter.upper()
            if (ticker_filter not in self.ticker.upper() and 
                ticker_filter not in self.normalised_ticker.upper() and
                ticker_filter != self.signal_id):
                return False
        
        # Status filter
        if status_filter and status_filter != 'all':
            if self.current_status.value != status_filter:
                return False
        
        # Location filter
        if location_filter and location_filter != 'all':
            if self.current_location.value != location_filter:
                return False
        
        # Time range filter
        if start_time and self.created_at < start_time:
            return False
        if end_time and self.created_at > end_time:
            return False
            
        # Error filter
        if error_only and self.error_count == 0:
            return False
            
        # Duration filter
        if min_duration is not None or max_duration is not None:
            duration = self.updated_at - self.created_at
            if min_duration is not None and duration < min_duration:
                return False
            if max_duration is not None and duration > max_duration:
                return False
        
        return True
    
    def add_tag(self, tag: str):
        """Add a tag to this signal."""
        if tag not in self.tags:
            self.tags.append(tag)
    
    def remove_tag(self, tag: str):
        """Remove a tag from this signal."""
        if tag in self.tags:
            self.tags.remove(tag)
    
    def to_audit_entry(self) -> Dict[str, Any]:
        """Convert to audit entry format for frontend."""
        return {
            "signal_id": self.signal_id,
            "ticker": self.ticker,
            "normalised_ticker": self.normalised_ticker,
            "timestamp": datetime.fromtimestamp(self.updated_at).isoformat() + 'Z',
            "status": self.current_status.value,
            "status_display": self.get_current_status_display(),
            "location": self.current_location.value,
            "action": "signal_tracking",
            "details": self.get_journey_summary(),
            "events_count": len(self.events),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "total_processing_time": self.total_processing_time,
            "error_count": self.error_count,
            "retry_count": self.retry_count,
            "tags": self.tags,
            "performance_metrics": self.get_performance_metrics(),
            "events": [
                {
                    "timestamp": event.timestamp,
                    "event_type": event.event_type.value,
                    "location": event.location.value,
                    "worker_id": event.worker_id,
                    "details": event.details,
                    "error_info": event.error_info,
                    "http_status": event.http_status,
                    "response_data": event.response_data,
                    "formatted_timestamp": datetime.fromtimestamp(event.timestamp).isoformat() + 'Z'
                } for event in self.events
            ],
            **self.original_signal  # Include original signal data
        }

class AuditTrailQuery(BaseModel):
    """Advanced query parameters for audit trail filtering."""
    model_config = {"extra": "allow"}
    
    ticker: Optional[str] = None
    signal_id: Optional[str] = None
    status: Optional[str] = None
    location: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    error_only: bool = False
    min_duration: Optional[float] = None
    max_duration: Optional[float] = None
    sort_by: str = "updated_at"
    sort_order: str = "desc"
    page: int = 1
    page_size: int = 50
    include_events: bool = True
    tags: Optional[List[str]] = None

class AuditTrailResponse(BaseModel):
    """Response model for audit trail queries."""
    model_config = {"extra": "allow"}
    
    entries: List[Dict[str, Any]]
    total_count: int
    page: int
    page_size: int
    total_pages: int
    filters_applied: Dict[str, Any]
    summary: Dict[str, Any]

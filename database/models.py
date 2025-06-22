"""Database models for Signal Audit Trail system using SQLAlchemy with async support."""

from sqlalchemy import (
    Column, String, Integer, Float, Text, Boolean, DateTime, Interval,
    Enum, JSON, ARRAY, BigInteger, ForeignKey, Index, UniqueConstraint
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.dialects.postgresql import UUID, JSONB, TIMESTAMP
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from datetime import datetime
import enum
import uuid

Base = declarative_base()

# Enums for database
class SignalStatusEnum(enum.Enum):
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
    ERROR = "error"
    DISCARDED = "discarded"

class SignalLocationEnum(enum.Enum):
    PROCESSING_QUEUE = "processing_queue"
    APPROVED_QUEUE = "approved_queue"
    WORKER_PROCESSING = "worker_processing"
    WORKER_FORWARDING = "worker_forwarding"
    COMPLETED = "completed"
    DISCARDED = "discarded"

class MetricPeriodEnum(enum.Enum):
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"

class Signal(Base):
    """Main signal records table."""
    __tablename__ = "signals"
    
    # Primary key
    signal_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Core signal data
    ticker = Column(String(20), nullable=False, index=True)
    normalised_ticker = Column(String(20), nullable=False, index=True)
    side = Column(String(10))
    action = Column(String(10))
    price = Column(Float)
    signal_time = Column(String(50))  # Original time from signal
    
    # Original signal as JSON
    original_signal = Column(JSONB, nullable=False)
    
    # Current state
    current_status = Column(Enum(SignalStatusEnum), nullable=False, index=True)
    current_location = Column(Enum(SignalLocationEnum), nullable=False, index=True)
    
    # Timestamps
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now(), index=True)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now(), onupdate=func.now(), index=True)
    received_at = Column(Float)  # Unix timestamp when signal was received
    
    # Performance metrics
    total_processing_time = Column(Interval)
    error_count = Column(Integer, default=0, nullable=False)
    retry_count = Column(Integer, default=0, nullable=False)
    
    # Metadata
    tags = Column(ARRAY(String), default=list)
    
    # Relationships
    events = relationship("SignalEvent", back_populates="signal", cascade="all, delete-orphan")
    
    # Indexes for common queries
    __table_args__ = (
        Index('idx_signals_ticker_status', 'ticker', 'current_status'),
        Index('idx_signals_created_status', 'created_at', 'current_status'),
        Index('idx_signals_normalised_ticker_location', 'normalised_ticker', 'current_location'),
        Index('idx_signals_updated_at_desc', updated_at.desc()),
    )

class SignalEvent(Base):
    """Signal events tracking table."""
    __tablename__ = "signal_events"
    
    # Primary key
    event_id = Column(BigInteger, primary_key=True, autoincrement=True)
    
    # Foreign key to signal
    signal_id = Column(UUID(as_uuid=True), ForeignKey('signals.signal_id'), nullable=False, index=True)
    
    # Event data
    timestamp = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now(), index=True)
    event_type = Column(Enum(SignalStatusEnum), nullable=False, index=True)
    location = Column(Enum(SignalLocationEnum), nullable=False)
    
    # Context information
    worker_id = Column(String(100))
    details = Column(Text)
    error_info = Column(JSONB)
    http_status = Column(Integer)
    response_data = Column(Text)
    
    # Relationships
    signal = relationship("Signal", back_populates="events")
    
    # Indexes for common queries
    __table_args__ = (
        Index('idx_events_signal_timestamp', 'signal_id', 'timestamp'),
        Index('idx_events_type_timestamp', 'event_type', 'timestamp'),
        Index('idx_events_timestamp_desc', timestamp.desc()),
    )

class SignalMetricsSummary(Base):
    """Pre-computed metrics for faster analytics."""
    __tablename__ = "signal_metrics_summary"
    
    # Primary key
    metric_id = Column(BigInteger, primary_key=True, autoincrement=True)
    
    # Time period
    period_start = Column(TIMESTAMP(timezone=True), nullable=False, index=True)
    period_end = Column(TIMESTAMP(timezone=True), nullable=False, index=True)
    period_type = Column(Enum(MetricPeriodEnum), nullable=False, index=True)
    
    # Metrics
    total_signals = Column(Integer, default=0, nullable=False)
    approved_signals = Column(Integer, default=0, nullable=False)
    rejected_signals = Column(Integer, default=0, nullable=False)
    forwarded_success = Column(Integer, default=0, nullable=False)
    forwarded_errors = Column(Integer, default=0, nullable=False)
    error_signals = Column(Integer, default=0, nullable=False)
    discarded_signals = Column(Integer, default=0, nullable=False)
    
    # Performance metrics
    avg_processing_time = Column(Interval)
    min_processing_time = Column(Interval)
    max_processing_time = Column(Interval)
    total_processing_time = Column(Interval)
    
    # Additional metrics
    unique_tickers = Column(Integer, default=0)
    total_retries = Column(Integer, default=0)
    total_errors = Column(Integer, default=0)
    
    # Metadata
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now())
    
    # Constraints
    __table_args__ = (
        UniqueConstraint('period_start', 'period_end', 'period_type', name='uq_period_metrics'),
        Index('idx_metrics_period_type_start', 'period_type', 'period_start'),
    )

class SystemMetrics(Base):
    """System-wide metrics and health indicators."""
    __tablename__ = "system_metrics"
    
    # Primary key
    metric_id = Column(BigInteger, primary_key=True, autoincrement=True)
    
    # Timestamp
    timestamp = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now(), index=True)
    
    # Queue metrics
    processing_queue_size = Column(Integer, default=0)
    approved_queue_size = Column(Integer, default=0)
    
    # Worker metrics
    active_processing_workers = Column(Integer, default=0)
    active_forwarding_workers = Column(Integer, default=0)
    
    # Rate limiting metrics
    webhook_tokens_available = Column(Integer)
    webhook_requests_this_minute = Column(Integer, default=0)
    finviz_rate_limit_tokens = Column(Integer)
    
    # System health
    finviz_engine_status = Column(String(20))
    webhook_rate_limiter_enabled = Column(Boolean, default=False)
    
    # Additional metrics as JSON
    additional_metrics = Column(JSONB)
    
    __table_args__ = (
        Index('idx_system_metrics_timestamp_desc', timestamp.desc()),
    )

class TickerAnalytics(Base):
    """Analytics per ticker for detailed insights."""
    __tablename__ = "ticker_analytics"
    
    # Primary key
    analytics_id = Column(BigInteger, primary_key=True, autoincrement=True)
    
    # Ticker info
    ticker = Column(String(20), nullable=False, index=True)
    normalised_ticker = Column(String(20), nullable=False, index=True)
    
    # Time period
    period_start = Column(TIMESTAMP(timezone=True), nullable=False, index=True)
    period_end = Column(TIMESTAMP(timezone=True), nullable=False, index=True)
    period_type = Column(Enum(MetricPeriodEnum), nullable=False)
    
    # Signal counts
    total_signals = Column(Integer, default=0)
    approved_signals = Column(Integer, default=0)
    rejected_signals = Column(Integer, default=0)
    forwarded_success = Column(Integer, default=0)
    forwarded_errors = Column(Integer, default=0)
    
    # Performance
    avg_processing_time = Column(Interval)
    success_rate = Column(Float)  # Percentage
    error_rate = Column(Float)    # Percentage
    
    # Metadata
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now())
    
    __table_args__ = (
        UniqueConstraint('ticker', 'period_start', 'period_end', 'period_type', name='uq_ticker_period'),
        Index('idx_ticker_analytics_period', 'period_type', 'period_start'),
        Index('idx_ticker_analytics_success_rate', success_rate.desc()),
    )

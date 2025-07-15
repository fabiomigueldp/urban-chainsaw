"""Simplified database models for signal tracking - NEW SIGNALS ONLY."""

from sqlalchemy import Column, String, Integer, Float, Text, DateTime, BigInteger, ForeignKey, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.dialects.postgresql import UUID, JSONB, TIMESTAMP, INET
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
import uuid

Base = declarative_base()

class SignalStatusEnum(enum.Enum):
    """Signal status during processing - HYBRID: Enum in Python, String in DB."""
    RECEIVED = "received"
    APPROVED = "approved"
    REJECTED = "rejected"
    FORWARDED_SUCCESS = "forwarded_success"
    FORWARDED_ERROR = "forwarded_error"
    FORWARDED_HTTP_ERROR = "forwarded_http_error"
    FORWARDED_GENERIC_ERROR = "forwarded_generic_error"
    FORWARDED_TIMEOUT = "forwarded_timeout"
    ERROR = "error"
    PROCESSING = "processing"
    QUEUED_FORWARDING = "queued_forwarding"
    FORWARDING = "forwarding"
    DISCARDED = "discarded"

class SignalTypeEnum(enum.Enum):
    """Type of signal - distinguishes between different signal sources and types."""
    BUY = "buy"
    SELL = "sell" 
    MANUAL_SELL = "manual_sell"
    SELL_ALL = "sell_all"

class SignalLocationEnum(enum.Enum):
    """Current signal location in the system."""
    PROCESSING_QUEUE = "processing_queue"
    APPROVED_QUEUE = "approved_queue"
    WORKER_PROCESSING = "worker_processing"
    WORKER_FORWARDING = "worker_forwarding"
    COMPLETED = "completed"
    DISCARDED = "discarded"

class MetricPeriodEnum(enum.Enum):
    """Periods for metrics and analysis."""
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"

class PositionStatusEnum(enum.Enum):
    """Lifecycle status of a trade position."""
    OPEN = "open"
    CLOSING = "closing" # A sell order has been approved and sent, awaiting confirmation
    CLOSED = "closed"

class AdminActionTypeEnum(enum.Enum):
    """Types of administrative actions for audit trail."""
    CONFIG_UPDATE = "config_update"
    ENGINE_CONTROL = "engine_control"
    URL_MANAGEMENT = "url_management"
    METRICS_RESET = "metrics_reset"
    DATABASE_OPERATION = "database_operation"
    RATE_LIMITER_CONTROL = "rate_limiter_control"
    ORDER_MANAGEMENT = "order_management"
    FILE_IMPORT = "file_import"
    MANUAL_OVERRIDE = "manual_override"
    SYSTEM_MAINTENANCE = "system_maintenance"

class Signal(Base):
    """Simple signal tracking table - HYBRID: String fields in DB, enums in Python."""
    __tablename__ = "signals"
    
    # Primary key
    signal_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Signal data
    ticker = Column(String(20), nullable=False, index=True)
    normalised_ticker = Column(String(20), nullable=False, index=True)
    side = Column(String(10))
    price = Column(Float)
    
    # Status tracking - STRING in database, ENUM in Python
    status = Column(String(30), nullable=False, index=True)
    
    # Signal type - NEW: distinguish between buy, sell, manual_sell, sell_all
    signal_type = Column(String(20), nullable=False, default='buy', index=True)
    
    # Timestamps
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now(), index=True)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now(), onupdate=func.now())
    
    # Original signal as JSON
    original_signal = Column(JSONB, nullable=False)
    
    # Performance tracking
    processing_time_ms = Column(Integer)  # milliseconds
    error_message = Column(Text)
    retry_count = Column(Integer, default=0)  # For retry control
    
    # Events
    events = relationship("SignalEvent", back_populates="signal", cascade="all, delete-orphan")

class SignalEvent(Base):
    """Simple event tracking - HYBRID: String fields in DB, enums in Python."""
    __tablename__ = "signal_events"
    
    event_id = Column(BigInteger, primary_key=True, autoincrement=True)
    signal_id = Column(UUID(as_uuid=True), ForeignKey('signals.signal_id'), nullable=False, index=True)
    
    timestamp = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now())
    status = Column(String(30), nullable=False)  # STRING in database
    details = Column(Text)
    worker_id = Column(String(50))
    
    # Relationship
    signal = relationship("Signal", back_populates="events")

class Position(Base):
    """Represents a single trade position in the database."""
    __tablename__ = "positions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, index=True)
    status = Column(String(20), nullable=False, default=PositionStatusEnum.OPEN.value, index=True)
    
    entry_signal_id = Column(UUID(as_uuid=True), ForeignKey('signals.signal_id'), nullable=False)
    exit_signal_id = Column(UUID(as_uuid=True), ForeignKey('signals.signal_id'), nullable=True)

    opened_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now())
    closed_at = Column(TIMESTAMP(timezone=True), nullable=True)

    # Relationships to the Signal table for easy joins if needed in the future
    entry_signal = relationship("Signal", foreign_keys=[entry_signal_id])
    exit_signal = relationship("Signal", foreign_keys=[exit_signal_id])

class FinvizUrl(Base):
    """Complete Finviz strategy presets with all configuration parameters."""
    __tablename__ = "finviz_urls"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True, index=True)
    url = Column(Text, nullable=False)
    description = Column(Text)
    
    # Complete strategy configuration parameters
    top_n = Column(Integer, nullable=False, default=100)
    refresh_interval_sec = Column(Integer, nullable=False, default=10)
    reprocess_enabled = Column(Boolean, default=False)
    reprocess_window_seconds = Column(Integer, default=300)
    respect_sell_chronology_enabled = Column(Boolean, default=True)
    sell_chronology_window_seconds = Column(Integer, default=300)
    
    # Activation control
    is_active = Column(Boolean, default=False, index=True)
    created_at = Column(TIMESTAMP(timezone=True), default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), default=func.now(), onupdate=func.now())
    last_used_at = Column(TIMESTAMP(timezone=True))

class AdminAction(Base):
    """Administrative actions audit trail for complete transparency."""
    __tablename__ = "admin_actions"
    
    action_id = Column(BigInteger, primary_key=True, autoincrement=True)
    timestamp = Column(TIMESTAMP(timezone=True), default=func.now(), nullable=False, index=True)
    action_type = Column(String(50), nullable=False, index=True)
    action_name = Column(String(100), nullable=False)
    admin_token = Column(Text, index=True)  # Direct admin token for audit
    ip_address = Column(INET)
    user_agent = Column(Text)
    details = Column(JSONB)
    target_resource = Column(String(100))
    success = Column(Boolean, default=True, nullable=False, index=True)
    error_message = Column(Text)
    execution_time_ms = Column(Integer)

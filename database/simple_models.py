"""Simplified database models for signal tracking - NEW SIGNALS ONLY."""

from sqlalchemy import Column, String, Integer, Float, Text, DateTime, BigInteger, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.dialects.postgresql import UUID, JSONB, TIMESTAMP
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
import uuid

Base = declarative_base()

class SignalStatusEnum(enum.Enum):
    """Status do sinal durante o processamento - HÍBRIDO: Enum no Python, String no DB."""
    RECEIVED = "received"
    APPROVED = "approved"
    REJECTED = "rejected"
    FORWARDED_SUCCESS = "forwarded_success"
    FORWARDED_ERROR = "forwarded_error"
    ERROR = "error"
    PROCESSING = "processing"
    QUEUED_FORWARDING = "queued_forwarding"
    DISCARDED = "discarded"

class SignalLocationEnum(enum.Enum):
    """Localização atual do sinal no sistema."""
    PROCESSING_QUEUE = "processing_queue"
    APPROVED_QUEUE = "approved_queue"
    WORKER_PROCESSING = "worker_processing"
    WORKER_FORWARDING = "worker_forwarding"
    COMPLETED = "completed"
    DISCARDED = "discarded"

class MetricPeriodEnum(enum.Enum):
    """Períodos para métricas e análises."""
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"

class Signal(Base):
    """Simple signal tracking table - HÍBRIDO: Campos string no DB, enums no Python."""
    __tablename__ = "signals"
    
    # Primary key
    signal_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Signal data
    ticker = Column(String(20), nullable=False, index=True)
    normalised_ticker = Column(String(20), nullable=False, index=True)
    side = Column(String(10))
    price = Column(Float)
    
    # Status tracking - STRING no banco, ENUM no Python
    status = Column(String(30), nullable=False, index=True)
    
    # Timestamps
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now(), index=True)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now(), onupdate=func.now())
    
    # Original signal as JSON
    original_signal = Column(JSONB, nullable=False)
    
    # Performance tracking
    processing_time_ms = Column(Integer)  # milliseconds
    error_message = Column(Text)
    retry_count = Column(Integer, default=0)  # Para controle de retentativas
    
    # Events
    events = relationship("SignalEvent", back_populates="signal", cascade="all, delete-orphan")

class SignalEvent(Base):
    """Simple event tracking - HÍBRIDO: Campos string no DB, enums no Python."""
    __tablename__ = "signal_events"
    
    event_id = Column(BigInteger, primary_key=True, autoincrement=True)
    signal_id = Column(UUID(as_uuid=True), ForeignKey('signals.signal_id'), nullable=False, index=True)
    
    timestamp = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now())
    status = Column(String(30), nullable=False)  # STRING no banco
    details = Column(Text)
    worker_id = Column(String(50))
    
    # Relationship
    signal = relationship("Signal", back_populates="events")

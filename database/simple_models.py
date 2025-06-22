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
    """Simplified status tracking."""
    RECEIVED = "received"
    APPROVED = "approved"
    REJECTED = "rejected"
    FORWARDED_SUCCESS = "forwarded_success"
    FORWARDED_ERROR = "forwarded_error"
    ERROR = "error"

class Signal(Base):
    """Simple signal tracking table."""
    __tablename__ = "signals"
    
    # Primary key
    signal_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Signal data
    ticker = Column(String(20), nullable=False, index=True)
    normalised_ticker = Column(String(20), nullable=False, index=True)
    side = Column(String(10))
    price = Column(Float)
    
    # Status tracking
    status = Column(String(30), nullable=False, index=True)
    
    # Timestamps
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now(), index=True)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now(), onupdate=func.now())
    
    # Original signal as JSON
    original_signal = Column(JSONB, nullable=False)
    
    # Performance tracking
    processing_time_ms = Column(Integer)  # milliseconds
    error_message = Column(Text)
    
    # Events
    events = relationship("SignalEvent", back_populates="signal", cascade="all, delete-orphan")

class SignalEvent(Base):
    """Simple event tracking."""
    __tablename__ = "signal_events"
    
    event_id = Column(BigInteger, primary_key=True, autoincrement=True)
    signal_id = Column(UUID(as_uuid=True), ForeignKey('signals.signal_id'), nullable=False, index=True)
    
    timestamp = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now())
    status = Column(String(30), nullable=False)
    details = Column(Text)
    worker_id = Column(String(50))
    
    # Relationship
    signal = relationship("Signal", back_populates="events")

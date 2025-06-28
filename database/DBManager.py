# database/DBManager.py

# ==========================================================================================
#                         Trading Signal Processor - DBManager
#
# HYBRID APPROACH IMPLEMENTED:
# - Enums in Python for type safety and clarity
# - Strings in database for simplicity and performance
# - Automatic conversion in persistence layer
# ==========================================================================================

import logging
import json
from contextlib import asynccontextmanager
from typing import List, Optional, Dict, Any, AsyncGenerator
from datetime import datetime, timedelta
import datetime as dt

from sqlalchemy import select, func, and_, or_, desc, asc, text, String
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

# Project imports
from config import settings
from models import Signal as SignalPayload, SignalTracker, AuditTrailQuery, AuditTrailResponse
from database.simple_models import Base, Signal, SignalEvent, SignalStatusEnum, SignalLocationEnum, MetricPeriodEnum, SignalTypeEnum

_logger = logging.getLogger("DBManager")

class DBManager:
    """Centralized manager for all database operations."""

    def __init__(self):
        self.engine = None
        self.async_session_factory = None
        self._initialized = False

    def initialize(self, database_url: str):
        """Initializes the database engine and session factory. Must be called at application startup."""
        if self._initialized:
            _logger.warning("DatabaseManager already initialized.")
            return

        engine_config = {
            'echo': settings.DATABASE_ECHO,
            'pool_size': settings.DATABASE_POOL_SIZE,
            'max_overflow': settings.DATABASE_MAX_OVERFLOW,
            'pool_pre_ping': True,
            'pool_recycle': settings.DATABASE_POOL_RECYCLE,
        }

        self.engine = create_async_engine(database_url, **engine_config)
        self.async_session_factory = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

        self._initialized = True
        _logger.info(f"DatabaseManager initialized successfully. Pool size: {engine_config['pool_size']}")

    async def close(self):
        """Closes database connections. Must be called at application shutdown."""
        if self.engine:
            await self.engine.dispose()
            _logger.info("Database connections closed.")

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Provides a database session with transaction management."""
        if not self._initialized:
            raise RuntimeError("DatabaseManager not initialized. Call initialize() first.")

        session: AsyncSession = self.async_session_factory()
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            _logger.error(f"Database session error, transaction rolled back: {e}", exc_info=True)
            raise
        finally:
            await session.close()

    # --- Business Logic Methods (Manager Public API) ---
    
    async def create_signal_with_initial_event(self, signal_payload: SignalPayload, signal_type: SignalTypeEnum = SignalTypeEnum.BUY) -> str:
        """
        Creates a new signal record and its first 'RECEIVED' event.
        This is the main function to be called when a new signal arrives.
        HYBRID: Uses enums in code, saves strings to database.
        """
        async with self.get_session() as session:
            db_signal = Signal(
                signal_id=signal_payload.signal_id,
                ticker=signal_payload.ticker,
                normalised_ticker=signal_payload.normalised_ticker(),
                side=signal_payload.side,
                price=signal_payload.price,
                original_signal=signal_payload.dict(),
                status=SignalStatusEnum.RECEIVED.value,  # Enum -> string para DB
                signal_type=signal_type.value  # NEW: Set signal type
            )

            initial_event = SignalEvent(
                signal_id=db_signal.signal_id,
                status=SignalStatusEnum.RECEIVED.value,  # Enum -> string for DB
                details="Signal received and queued for processing."
            )

            session.add(db_signal)
            session.add(initial_event)
            await session.flush()
            _logger.debug(f"Signal {db_signal.signal_id} created in database.")
            return str(db_signal.signal_id)

    async def log_signal_event(
        self,
        signal_id: str,
        event_type: SignalStatusEnum,  # Accepts enum in code
        location: Optional[SignalLocationEnum] = None,
        details: Optional[str] = None,
        worker_id: Optional[str] = None,
        error_info: Optional[Dict[str, Any]] = None,
        http_status: Optional[int] = None,
        response_data: Optional[str] = None
    ) -> bool:
        """
        Logs a new event for an existing signal and updates the current signal state.
        HYBRID: Accepts enums in Python, converts to string in database.
        """
        async with self.get_session() as session:
            # Find the main signal
            result = await session.execute(select(Signal).where(Signal.signal_id == signal_id))
            db_signal = result.scalar_one_or_none()

            if not db_signal:
                _logger.warning(f"Attempt to log event for non-existent signal: {signal_id}")
                return False

            # Create new event - convert enum to string
            event_type_str = event_type.value if hasattr(event_type, 'value') else str(event_type)
            
            new_event = SignalEvent(
                signal_id=signal_id,
                status=event_type_str,  # String in database
                details=details,
                worker_id=worker_id
            )
            session.add(new_event)

            # Update current state of main signal
            db_signal.status = event_type_str  # String in database
            
            await session.flush()
            _logger.debug(f"Event '{event_type_str}' logged for signal {signal_id}.")
            return True

    async def increment_signal_retry_count(self, signal_id: str) -> Optional[int]:
        """Increments the retry count for a signal."""
        async with self.get_session() as session:
            result = await session.execute(select(Signal).where(Signal.signal_id == signal_id))
            db_signal = result.scalar_one_or_none()
            if db_signal:
                # Add retry_count field if it doesn't exist in the model
                if not hasattr(db_signal, 'retry_count'):
                    _logger.warning("retry_count field does not exist in Signal model. Future implementation.")
                    return None
                db_signal.retry_count = (db_signal.retry_count or 0) + 1
                await session.flush()
                return db_signal.retry_count
            return None
            
    async def get_signal_retry_count(self, signal_id: str) -> int:
        """Gets the current retry count for a signal."""
        async with self.get_session() as session:
            result = await session.execute(select(Signal).where(Signal.signal_id == signal_id))
            db_signal = result.scalar_one_or_none()
            if db_signal and hasattr(db_signal, 'retry_count'):
                return db_signal.retry_count or 0
            return 0

    async def get_system_analytics(self) -> Dict[str, Any]:
        """Gets general system metrics and analytics."""
        async with self.get_session() as session:
            # Total signal count
            total_signals_res = await session.execute(select(func.count(Signal.signal_id)))
            total_signals = total_signals_res.scalar_one()
            
            if total_signals == 0:
                return {
                    "total_signals": 0,
                    "approved_signals": 0,
                    "rejected_signals": 0,
                    "forwarded_success": 0,
                    "forwarded_error": 0,
                    "status_distribution": {}
                }
                  
            # Status distribution (now strings in database)
            status_query = select(Signal.status, func.count(Signal.signal_id)).group_by(Signal.status)
            status_res = await session.execute(status_query)
            status_distribution = {status: count for status, count in status_res}

            # Other metrics
            avg_duration_res = await session.execute(
                select(func.avg(Signal.processing_time_ms)).where(Signal.processing_time_ms.isnot(None))
            )
            avg_duration_ms = avg_duration_res.scalar_one() or 0
            
            return {
                "total_signals": total_signals,
                "approved_signals": status_distribution.get("approved", 0),
                "rejected_signals": status_distribution.get("rejected", 0),
                "forwarded_success": status_distribution.get("forwarded_success", 0),
                "forwarded_error": status_distribution.get("forwarded_error", 0) + status_distribution.get("forwarded_http_error", 0) + status_distribution.get("forwarded_generic_error", 0),
                "average_processing_time_ms": avg_duration_ms,
                "status_distribution": status_distribution
            }

    async def get_hourly_signal_stats(self, hours: int = 24) -> List[Dict[str, Any]]:
        """
        Gets signal statistics by hour for the last N hours.
        Returns data formatted for charts, always filling missing hours with zeros.
        """
        try:
            async with self.get_session() as session:
                # Calculate time interval - use timezone-aware datetime
                end_time = datetime.utcnow()
                start_time = end_time - timedelta(hours=hours)
                
                _logger.info(f"Getting hourly stats from {start_time} to {end_time} ({hours} hours)")
                
                # Query to get data aggregated by hour
                query = text("""
                    SELECT 
                        DATE_TRUNC('hour', created_at) as hour,
                        COUNT(*) as total_signals,
                        COUNT(CASE WHEN status = 'approved' THEN 1 END) as approved_signals,
                        COUNT(CASE WHEN status = 'rejected' THEN 1 END) as rejected_signals,
                        COUNT(CASE WHEN status = 'forwarded_success' THEN 1 END) as forwarded_signals
                    FROM signals s
                    WHERE s.created_at >= :start_time AND s.created_at <= :end_time
                    GROUP BY DATE_TRUNC('hour', created_at)
                    ORDER BY hour ASC
                """)
                
                result = await session.execute(query, {
                    'start_time': start_time,
                    'end_time': end_time
                })
                
                # Map database data by hour
                data_by_hour = {}
                for row in result:
                    # Ensure hour is timezone-aware
                    hour_key = row.hour.replace(tzinfo=None) if row.hour.tzinfo else row.hour
                    data_by_hour[hour_key] = row
                
                _logger.info(f"Found data for {len(data_by_hour)} hours in database")
                
                hourly_data = []
                
                # Fill ALL hours in the interval, even if they have no data
                for i in range(hours):
                    hour_time = end_time - timedelta(hours=hours-1-i)
                    hour_key = hour_time.replace(minute=0, second=0, microsecond=0)
                    
                    if hour_key in data_by_hour:
                        row = data_by_hour[hour_key]
                        hourly_data.append({
                            "hour": hour_time.strftime("%H:00"),
                            "hour_label": hour_time.strftime("%H:00"),
                            "date": hour_time.strftime("%Y-%m-%d"),
                            "timestamp": hour_time.timestamp(),
                            "total_signals": row.total_signals,
                            "signals_received": row.total_signals,  # Alias for frontend compatibility
                            "approved_signals": row.approved_signals,
                            "signals_approved": row.approved_signals,  # Alias for frontend compatibility
                            "rejected_signals": row.rejected_signals,
                            "signals_rejected": row.rejected_signals,  # Alias for frontend compatibility
                            "forwarded_signals": row.forwarded_signals,
                            "signals_forwarded": row.forwarded_signals  # Alias for frontend compatibility
                        })
                    else:
                        # Fill hour without data with zeros
                        hourly_data.append({
                            "hour": hour_time.strftime("%H:00"),
                            "hour_label": hour_time.strftime("%H:00"),
                            "date": hour_time.strftime("%Y-%m-%d"),
                            "timestamp": hour_time.timestamp(),
                            "total_signals": 0,
                            "signals_received": 0,
                            "approved_signals": 0,
                            "signals_approved": 0,
                            "rejected_signals": 0,
                            "signals_rejected": 0,
                            "forwarded_signals": 0,
                            "signals_forwarded": 0
                        })
                
                _logger.info(f"Returning {len(hourly_data)} hourly data points")
                return hourly_data
                
        except Exception as e:
            _logger.error(f"Error getting hourly signal statistics: {e}", exc_info=True)
            
            # Return fallback data in case of error
            hourly_data = []
            end_time = datetime.utcnow()
            for i in range(hours):
                hour_time = end_time - timedelta(hours=hours-1-i)
                hourly_data.append({
                    "hour": hour_time.strftime("%H:00"),
                    "hour_label": hour_time.strftime("%H:00"),
                    "date": hour_time.strftime("%Y-%m-%d"),
                    "timestamp": hour_time.timestamp(),
                    "total_signals": 0,
                    "signals_received": 0,
                    "approved_signals": 0,
                    "signals_approved": 0,
                    "rejected_signals": 0,
                    "signals_rejected": 0,
                    "forwarded_signals": 0,
                    "signals_forwarded": 0
                })
            
            _logger.warning(f"Returning fallback data with {len(hourly_data)} zero-filled hours")
            return hourly_data

    async def query_signals(self, query_params: AuditTrailQuery) -> AuditTrailResponse:
        """Advanced signal query with filters, pagination and sorting."""
        async with self.get_session() as session:
            base_query = select(Signal)
            count_query = select(func.count(Signal.signal_id))
            
            filters = self._build_filters(query_params)
            if filters:
                filter_condition = and_(*filters)
                base_query = base_query.where(filter_condition)
                count_query = count_query.where(filter_condition)

            # Total records
            total_count = (await session.execute(count_query)).scalar_one()

            # Sorting
            sort_column_map = {
                "created_at": Signal.created_at,
                "updated_at": Signal.updated_at,
                "processing_time": Signal.processing_time_ms,
            }
            sort_column = sort_column_map.get(query_params.sort_by, Signal.updated_at)
            if query_params.sort_order == "desc":
                base_query = base_query.order_by(desc(sort_column))
            else:
                base_query = base_query.order_by(asc(sort_column))

            # Pagination
            offset = (query_params.page - 1) * query_params.page_size
            base_query = base_query.offset(offset).limit(query_params.page_size)

            if query_params.include_events:
                base_query = base_query.options(selectinload(Signal.events))

            # Execution
            result = await session.execute(base_query)
            signals = result.scalars().all()
            
            entries = [self._signal_to_dict(s, query_params.include_events) for s in signals]
            total_pages = (total_count + query_params.page_size - 1) // query_params.page_size

            return AuditTrailResponse(
                entries=entries,
                total_count=total_count,
                page=query_params.page,
                page_size=query_params.page_size,
                total_pages=total_pages,
                filters_applied=query_params.dict(exclude_unset=True),
                summary=await self._calculate_query_summary(session, filters)
            )

    async def run_data_cleanup(self, retention_days: int) -> Dict[str, Any]:
        """Removes signal and event data older than the retention period."""
        async with self.get_session() as session:
            cutoff_date = datetime.utcnow() - timedelta(days=retention_days)
            
            # Use text() for performance in bulk deletes
            # Cascade delete in relationship will handle events.
            delete_stmt = text("DELETE FROM signals WHERE created_at < :cutoff")
            
            result = await session.execute(delete_stmt, {"cutoff": cutoff_date})
            deleted_count = result.rowcount

            _logger.info(f"Data cleanup completed. {deleted_count} old signals removed.")
            return {
                "deleted_signals_count": deleted_count,
                "retention_period_days": retention_days,
                "cutoff_date": cutoff_date.isoformat()
            }

    async def clear_all_data(self) -> Dict[str, Any]:
        """Completely clears all signals and events from the database. DESTRUCTIVE OPERATION!"""
        async with self.get_session() as session:
            # First delete all events (due to foreign key constraints)
            events_delete_stmt = text("DELETE FROM signal_events")
            events_result = await session.execute(events_delete_stmt)
            deleted_events = events_result.rowcount
            
            # Then delete all signals
            signals_delete_stmt = text("DELETE FROM signals")
            signals_result = await session.execute(signals_delete_stmt)
            deleted_signals = signals_result.rowcount

            _logger.warning(f"Database completely cleared. Deleted {deleted_signals} signals and {deleted_events} events.")
            return {
                "deleted_signals_count": deleted_signals,
                "deleted_events_count": deleted_events,
                "operation": "clear_all_data"
            }

    # --- Compatibility Methods (Legacy API) ---
    
    async def get_audit_trail(
        self,
        limit: int = 100,
        offset: int = 0,
        status_filter: Optional[str] = None,
        ticker_filter: Optional[str] = None,
        signal_id_filter: Optional[str] = None,
        signal_type_filter: Optional[str] = None,  # NEW: Signal type filter
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        hours: Optional[int] = None,
        **kwargs  # Accept additional parameters for backward compatibility
    ) -> List[Dict[str, Any]]:
        """Compatibility method for get_audit_trail (legacy API) with improved filters."""
        from models import AuditTrailQuery
        
        # Apply hours filter if provided
        if hours and not start_time:
            start_time = datetime.utcnow() - timedelta(hours=hours)
        
        # Map legacy parameters to new API
        query_params = AuditTrailQuery(
            page=offset // limit + 1,
            page_size=limit,
            status=status_filter,
            ticker=ticker_filter,
            signal_id=signal_id_filter,
            signal_type=signal_type_filter,  # NEW: Pass signal type filter
            start_time=start_time.isoformat() + 'Z' if start_time else None,
            end_time=end_time.isoformat() + 'Z' if end_time else None,
            include_events=True,
            sort_by="created_at",
            sort_order="desc"
        )
        
        response = await self.query_signals(query_params)
        return response.entries

    async def get_rejected_signals_for_reprocessing(self, ticker: str, window_seconds: int) -> List[Dict[str, Any]]:
        """
        Retrieves signals for a specific ticker that were rejected within a given time window.
        If window_seconds is 0, it retrieves all rejected signals for the ticker (infinite window).
        Returns a list of dictionaries, where each dictionary represents a signal.
        """
        async with self.get_session() as session:
            
            # Base query
            stmt = (
                select(Signal)
                .where(
                    Signal.normalised_ticker == ticker.upper(),
                    Signal.status == SignalStatusEnum.REJECTED.value
                )
            )

            # Conditionally add time window filter
            if window_seconds > 0:
                cutoff_time = datetime.utcnow() - timedelta(seconds=window_seconds)
                stmt = stmt.where(Signal.created_at >= cutoff_time)
            
            # Add ordering
            stmt = stmt.order_by(desc(Signal.created_at)) # Process newer ones first if multiple

            result = await session.execute(stmt)
            signals = result.scalars().all()

            # Convert to dictionary, including original_signal if needed for reconstruction
            signal_list = []
            for sig in signals:
                signal_data = {
                    "signal_id": str(sig.signal_id),
                    "ticker": sig.ticker,
                    "normalised_ticker": sig.normalised_ticker,
                    "side": sig.side,
                    "price": sig.price,
                    "status": sig.status,
                    "created_at": sig.created_at,
                    "updated_at": sig.updated_at,
                    "original_signal": sig.original_signal, # Important for reconstructing Signal Pydantic model
                    "signal_type": sig.signal_type
                }
                signal_list.append(signal_data)

            log_msg = f"Found {len(signal_list)} rejected signals for ticker {ticker}"
            if window_seconds > 0:
                log_msg += f" within {window_seconds}s for reprocessing."
            else:
                log_msg += " (infinite window) for reprocessing."
            _logger.debug(log_msg)
            
            return signal_list

    async def reapprove_signal(self, signal_id: str, details: str) -> bool:
        """
        Changes a signal's status to APPROVED and logs a reprocessing event.
        """
        async with self.get_session() as session:
            # Find the signal
            result = await session.execute(select(Signal).where(Signal.signal_id == signal_id))
            db_signal = result.scalar_one_or_none()

            if not db_signal:
                _logger.warning(f"Signal {signal_id} not found for re-approval.")
                return False

            # Update signal status
            db_signal.status = SignalStatusEnum.APPROVED.value # Enum -> string for DB
            db_signal.updated_at = datetime.utcnow()

            # Log reprocessing event
            reprocessing_event = SignalEvent(
                signal_id=signal_id,
                status=SignalStatusEnum.APPROVED.value, # Log event as approved
                details=details or "Signal re-approved via reprocessing mechanism.",
                worker_id="reprocessing_engine" # Identify the source of this event
            )
            session.add(reprocessing_event)

            await session.flush() # Persist changes
            _logger.info(f"Signal {signal_id} re-approved. Status: {db_signal.status}. Event logged.")
            return True

    async def get_audit_trail_count(
        self, 
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        signal_id: Optional[str] = None,
        signal_id_filter: Optional[str] = None,
        event_types: Optional[List[str]] = None,
        status_filter: Optional[str] = None,
        ticker_filter: Optional[str] = None,
        signal_type_filter: Optional[str] = None,  # NEW: Signal type filter
        hours: Optional[int] = None,
        **kwargs  # Accept additional parameters for backward compatibility
    ) -> int:
        """Counts audit records based on improved filters."""
        async with self.get_session() as session:
            # Base query - always use count on Signal table
            query = select(func.count(Signal.signal_id))
            
            # Apply time filters (prefer hours if provided)
            if hours and not start_time:
                start_time = datetime.utcnow() - timedelta(hours=hours)
            
            if start_time:
                query = query.where(Signal.created_at >= start_time)
            if end_time:
                query = query.where(Signal.created_at <= end_time)
            if signal_id:
                query = query.where(Signal.signal_id == signal_id)
            if signal_id_filter:
                # Convert UUID to string for ILIKE search
                query = query.where(Signal.signal_id.cast(String).ilike(f'%{signal_id_filter}%'))
            if status_filter:
                # Filter by current signal status OR any event with that status
                # This allows finding signals that passed through a specific status at any point
                query = query.where(or_(
                    Signal.status == status_filter,  # Current status
                    Signal.events.any(SignalEvent.status == status_filter)  # Any event with that status
                ))
            if signal_type_filter:  # NEW: Filter by signal type
                query = query.where(Signal.signal_type == signal_type_filter)
            if ticker_filter:
                # Filter by ticker field in Signal table
                query = query.where(Signal.ticker.ilike(f'%{ticker_filter}%'))
            
            result = await session.execute(query)
            return result.scalar_one()

    async def get_signal_status_distribution(self) -> Dict[str, Any]:
        """Returns signal status distribution with metadata."""
        async with self.get_session() as session:
            query = select(Signal.status, func.count()).group_by(Signal.status)
            result = await session.execute(query)
            distribution = {status: count for status, count in result.all()}
            
            return {
                "data": distribution,
                "data_source": "database_realtime",
                "timestamp": datetime.now().isoformat(),
                "total_entries": sum(distribution.values())
            }

    async def get_recent_signals(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns the most recent signals."""
        async with self.get_session() as session:
            query = select(Signal).order_by(desc(Signal.created_at)).limit(limit)
            result = await session.execute(query)
            signals = result.scalars().all()
            
            return [
                {
                    "signal_id": str(signal.signal_id),
                    "ticker": signal.ticker,
                    "normalised_ticker": signal.normalised_ticker,
                    "status": signal.status,
                    "side": signal.side,
                    "price": signal.price,
                    "created_at": signal.created_at.isoformat(),
                    "updated_at": signal.updated_at.isoformat(),
                    "original_signal": signal.original_signal,
                    "processing_time_ms": signal.processing_time_ms,
                    "error_message": signal.error_message,
                    "retry_count": signal.retry_count
                }
                for signal in signals
            ]

    async def get_ticker_performance(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Returns performance statistics by ticker."""
        async with self.get_session() as session:
            query = select(
                Signal.ticker,
                func.count().label('total_signals'),
                func.count(Signal.status == SignalStatusEnum.APPROVED.value).label('approved'),
                func.count(Signal.status == SignalStatusEnum.REJECTED.value).label('rejected'),
                func.count(Signal.status == SignalStatusEnum.FORWARDED_SUCCESS.value).label('forwarded')
            ).group_by(Signal.ticker).order_by(desc(func.count())).limit(limit)
            
            result = await session.execute(query)
            return [
                {
                    "ticker": row.ticker,
                    "total_signals": row.total_signals,
                    "approved": row.approved,
                    "rejected": row.rejected,
                    "forwarded": row.forwarded,
                    "approval_rate": (row.approved / row.total_signals * 100) if row.total_signals > 0 else 0
                }
                for row in result.all()
            ]

    async def get_live_metrics(self) -> Dict[str, Any]:
        """Returns real-time system metrics."""
        async with self.get_session() as session:
            # General statistics
            total_signals = await session.execute(select(func.count(Signal.signal_id)))
            total_signals = total_signals.scalar_one()
            
            # Last 24 hours
            last_24h = datetime.now() - timedelta(hours=24)
            recent_signals = await session.execute(
                select(func.count(Signal.signal_id)).where(Signal.created_at >= last_24h)
            )
            recent_signals = recent_signals.scalar_one()
            
            # Status distribution
            status_dist = await self.get_signal_status_distribution()
            
            # Recent events
            recent_events_query = select(SignalEvent).order_by(desc(SignalEvent.timestamp)).limit(10)
            recent_events_result = await session.execute(recent_events_query)
            recent_events = recent_events_result.scalars().all()
            
            return {
                "total_signals": total_signals,
                "recent_signals_24h": recent_signals,
                "status_distribution": status_dist,
                "recent_events": [
                    {
                        "event_id": event.event_id,
                        "signal_id": str(event.signal_id),
                        "status": event.status,
                        "timestamp": event.timestamp.isoformat(),
                        "details": event.details
                    }
                    for event in recent_events
                ],
                "data_source": "database_realtime",
                "timestamp": datetime.now().isoformat()
            }

    # --- Internal Helper Methods ---

    def _build_filters(self, p: AuditTrailQuery) -> list:
        """Builds the SQLAlchemy filter list from query parameters."""
        filters = []
        if p.ticker:
            # Only filter by ticker and normalised_ticker, NOT by signal_id to avoid UUID conversion errors
            filters.append(or_(
                Signal.ticker.ilike(f"%{p.ticker}%"), 
                Signal.normalised_ticker.ilike(f"%{p.ticker}%")
            ))
        if p.signal_id:
            # Convert UUID to string for ILIKE search
            filters.append(Signal.signal_id.cast(String).ilike(f"%{p.signal_id}%"))
        if p.status and p.status != 'all':
            # Filter by current signal status OR any event with that status
            # This allows finding signals that passed through a specific status at any point
            filters.append(or_(
                Signal.status == p.status.lower(),  # Current status
                Signal.events.any(SignalEvent.status == p.status.lower())  # Any event with that status
            ))
        if p.signal_type and p.signal_type != 'all':
            # NEW: Filter by signal type
            filters.append(Signal.signal_type == p.signal_type)
        if p.start_time:
            filters.append(Signal.created_at >= datetime.fromisoformat(p.start_time.replace('Z', '+00:00')))
        if p.end_time:
            filters.append(Signal.created_at <= datetime.fromisoformat(p.end_time.replace('Z', '+00:00')))
        if p.error_only:
            filters.append(or_(
                Signal.status == 'error',
                Signal.status == 'forwarded_error',
                Signal.error_message.isnot(None)
            ))
        return filters
        
    async def _calculate_query_summary(self, session: AsyncSession, filters: list) -> Dict[str, Any]:
        """Calculates summary statistics for a set of filters."""
        summary_query = select(
            func.count(Signal.signal_id).label('total'),
            func.avg(Signal.processing_time_ms).label('avg_processing_time')
        )
        if filters:
            summary_query = summary_query.where(and_(*filters))
        
        result = (await session.execute(summary_query)).first()
        avg_time_ms = result.avg_processing_time if result and result.avg_processing_time else 0
        
        return {
            "total_signals_in_query": result.total if result else 0,
            "average_processing_time_ms": avg_time_ms
        }

    def _signal_to_dict(self, signal: Signal, include_events: bool) -> Dict[str, Any]:
        """Converts a SQLAlchemy Signal object to a dictionary for the API."""
        from datetime import datetime
        
        def safe_datetime_format(dt):
            """Safely format datetime to ISO string."""
            if dt is None:
                return None
            
            # If it's already a datetime object with timezone info
            if hasattr(dt, 'isoformat'):
                try:
                    # If it has timezone info, format correctly
                    if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
                        return dt.isoformat()
                    else:
                        # Assume UTC if no timezone info
                        return dt.isoformat() + 'Z'
                except:
                    pass
            
            # Try to convert from timestamp
            try:
                timestamp_float = float(dt)
                # Check if it's in milliseconds (very large number) or seconds
                if timestamp_float > 1000000000000:  # Milliseconds
                    timestamp_float = timestamp_float / 1000
                return datetime.utcfromtimestamp(timestamp_float).isoformat() + 'Z'
            except (ValueError, TypeError, OSError):
                pass
            
            # Last resort - convert to string
            return str(dt) if dt else None

        # Primary signal data
        data = {
            "signal_id": str(signal.signal_id),
            "ticker": signal.ticker or '-',
            "normalised_ticker": getattr(signal, 'normalised_ticker', None) or '-',
            "timestamp": safe_datetime_format(signal.updated_at),
            "status": getattr(signal, 'status', None) or 'unknown',
            "status_display": (getattr(signal, 'status', '') or '').replace('_', ' ').title() or 'Unknown',
            "signal_type": getattr(signal, 'signal_type', None) or 'buy',
            "signal_type_display": (getattr(signal, 'signal_type', '') or '').replace('_', ' ').title() or 'Buy',
            "created_at": safe_datetime_format(signal.created_at),
            "updated_at": safe_datetime_format(signal.updated_at),
            "processing_time_ms": getattr(signal, 'processing_time_ms', None),
            "error_message": getattr(signal, 'error_message', None),
            "original_signal": getattr(signal, 'original_signal', None),
            
            # Add audit trail specific fields for compatibility
            "event_type": getattr(signal, 'status', 'unknown'),
            "location": self._derive_location_from_status(getattr(signal, 'status', '')),
            "details": getattr(signal, 'error_message', None) or '-',
            "worker_id": '-',
            "http_status": None
        }
        
        if include_events and hasattr(signal, 'events'):
            data["events"] = []
            for event in sorted(signal.events, key=lambda e: getattr(e, 'timestamp', datetime.min)):
                event_data = {
                    "timestamp": safe_datetime_format(getattr(event, 'timestamp', None)),
                    "event_type": getattr(event, 'status', 'unknown'),
                    "location": self._derive_location_from_status(getattr(event, 'status', '')),
                    "details": getattr(event, 'details', '') or '-',
                    "worker_id": getattr(event, 'worker_id', '') or '-',
                    "http_status": getattr(event, 'http_status', None),
                    "signal_id": str(signal.signal_id),
                    "ticker": signal.ticker or '-'
                }
                data["events"].append(event_data)
        return data
    
    def _derive_location_from_status(self, status: str) -> str:
        """Derive location from status for backward compatibility."""
        status = status.lower()
        if status in ['received']:
            return 'PROCESSING_QUEUE'
        elif status in ['processing']:
            return 'WORKER_PROCESSING'
        elif status in ['approved']:
            return 'APPROVED_QUEUE'
        elif status in ['forwarded_success', 'forwarded_error']:
            return 'WORKER_FORWARDING'
        elif status in ['rejected', 'error']:
            return 'DISCARDED'
        else:
            return 'COMPLETED' if status in ['completed'] else 'PROCESSING_QUEUE'

    async def get_all_signals_for_export(self) -> List[Dict[str, Any]]:
        """
        Retrieves all signals and their associated events for CSV export.
        Each event for a signal will result in a separate row, duplicating signal data.
        """
        async with self.get_session() as session:
            query = select(Signal).options(selectinload(Signal.events)).order_by(Signal.created_at)
            result = await session.execute(query)
            signals = result.scalars().all()

            export_data = []
            for signal in signals:
                signal_base_data = {
                    "signal_id": str(signal.signal_id),
                    "ticker": signal.ticker,
                    "normalised_ticker": signal.normalised_ticker,
                    "side": signal.side,
                    "price": signal.price,
                    "status": signal.status,
                    "signal_type": signal.signal_type,
                    "created_at": signal.created_at.isoformat() if signal.created_at else None,
                    "updated_at": signal.updated_at.isoformat() if signal.updated_at else None,
                    "processing_time_ms": signal.processing_time_ms,
                    "error_message": signal.error_message,
                    "retry_count": signal.retry_count,
                    "original_signal_json": json.dumps(signal.original_signal) if signal.original_signal else "{}"
                }

                if signal.events:
                    for event in signal.events:
                        event_data = {
                            "event_id": event.event_id,
                            "event_timestamp": event.timestamp.isoformat() if event.timestamp else None,
                            "event_status": event.status,
                            "event_details": event.details,
                            "event_worker_id": event.worker_id
                        }
                        export_data.append({**signal_base_data, **event_data})
                else:
                    # Include signals without events as well
                    export_data.append({
                        **signal_base_data,
                        "event_id": None,
                        "event_timestamp": None,
                        "event_status": None,
                        "event_details": None,
                        "event_worker_id": None
                    })
            return export_data

    async def import_signals_from_csv(self, data: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Imports signal and event data from a list of dictionaries (parsed from CSV).
        Updates existing signals/events or creates new ones, ensuring consistency.
        
        Strategy for handling auto-increment event_id:
        - For signals: Use signal_id as unique identifier for upsert
        - For events: Use (signal_id, timestamp, status) as composite key for duplicate detection
        - Let event_id auto-increment naturally during import
        """
        signals_created = 0
        signals_updated = 0
        events_created = 0
        events_updated = 0
        rows_skipped = 0

        async with self.get_session() as session:
            for row_index, row in enumerate(data):
                try:
                    signal_id = row.get("signal_id")
                    event_id = row.get("event_id")

                    if not signal_id:
                        _logger.warning(f"Row {row_index + 1}: Skipping row due to missing signal_id")
                        rows_skipped += 1
                        continue

                    # --- Process Signal Data ---
                    signal_data = {
                        "ticker": row.get("ticker"),
                        "normalised_ticker": row.get("normalised_ticker"),
                        "side": row.get("side"),
                        "price": float(row["price"]) if row.get("price") and str(row.get("price")).strip() else None,
                        "status": row.get("status"),
                        "signal_type": row.get("signal_type"),
                        "created_at": datetime.fromisoformat(row["created_at"].replace('Z', '+00:00')) if row.get("created_at") else datetime.utcnow(),
                        "updated_at": datetime.fromisoformat(row["updated_at"].replace('Z', '+00:00')) if row.get("updated_at") else datetime.utcnow(),
                        "processing_time_ms": int(row["processing_time_ms"]) if row.get("processing_time_ms") and str(row.get("processing_time_ms")).strip() else None,
                        "error_message": row.get("error_message"),
                        "retry_count": int(row["retry_count"]) if row.get("retry_count") and str(row.get("retry_count")).strip() else 0,
                        "original_signal": json.loads(row["original_signal_json"]) if row.get("original_signal_json") and str(row.get("original_signal_json")).strip() != '{}' else {}
                    }

                    # Try to find existing signal
                    existing_signal = await session.execute(select(Signal).where(Signal.signal_id == signal_id))
                    db_signal = existing_signal.scalar_one_or_none()

                    if db_signal:
                        # Update existing signal
                        for key, value in signal_data.items():
                            setattr(db_signal, key, value)
                        signals_updated += 1
                    else:
                        # Create new signal
                        db_signal = Signal(signal_id=signal_id, **signal_data)
                        session.add(db_signal)
                        signals_created += 1
                    
                    # --- Process Event Data (if event_id is present and valid) ---
                    if event_id and row.get("event_timestamp"):
                        event_data = {
                            "signal_id": signal_id,
                            "timestamp": datetime.fromisoformat(row["event_timestamp"].replace('Z', '+00:00')) if row.get("event_timestamp") else datetime.utcnow(),
                            "status": row.get("event_status"),
                            "details": row.get("event_details"),
                            "worker_id": row.get("event_worker_id")
                        }
                        
                        # For import, we look for duplicate events based on signal_id, timestamp, and status
                        # instead of event_id, since event_id is auto-generated
                        existing_event = await session.execute(select(SignalEvent).where(
                            SignalEvent.signal_id == signal_id,
                            SignalEvent.timestamp == event_data["timestamp"],
                            SignalEvent.status == event_data["status"]
                        ))
                        db_event = existing_event.scalar_one_or_none()

                        if db_event:
                            # Update existing event (found by signal_id, timestamp, and status)
                            for key, value in event_data.items():
                                if key != "signal_id":  # Don't update the foreign key
                                    setattr(db_event, key, value)
                            events_updated += 1
                        else:
                            # Create new event (let event_id auto-increment)
                            db_event = SignalEvent(**event_data)
                            session.add(db_event)
                            events_created += 1
                            
                except Exception as e:
                    _logger.error(f"Row {row_index + 1}: Error processing row: {e}")
                    _logger.debug(f"Problematic row data: {row}")
                    rows_skipped += 1
                    continue
            
            await session.flush() # Flush to ensure IDs are generated/updated before commit
            await session.commit() # Commit all changes in one transaction

        _logger.info(f"CSV Import Summary: Signals Created: {signals_created}, Signals Updated: {signals_updated}, Events Created: {events_created}, Events Updated: {events_updated}, Rows Skipped: {rows_skipped}")
        return {
            "signals_created": signals_created,
            "signals_updated": signals_updated,
            "events_created": events_created,
            "events_updated": events_updated,
            "rows_skipped": rows_skipped
        }

# Global Singleton Instance - to be imported throughout the application
db_manager = DBManager()

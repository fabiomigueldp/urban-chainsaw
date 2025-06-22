"""Data access layer for Signal Audit Trail with async PostgreSQL operations."""

from typing import List, Optional, Dict, Any, Tuple
from sqlalchemy import select, func, and_, or_, desc, asc, text
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta
import time
import logging

from .connection import db_manager
from .models import Signal, SignalEvent, SignalMetricsSummary, SystemMetrics, TickerAnalytics
from .models import SignalStatusEnum, SignalLocationEnum, MetricPeriodEnum
from models import SignalTracker, SignalStatus, SignalLocation, AuditTrailQuery, AuditTrailResponse

_logger = logging.getLogger("audit_service")

class SignalAuditService:
    """Service layer for signal audit trail operations."""
    
    async def create_signal(self, signal_tracker: SignalTracker) -> str:
        """Create a new signal record in the database."""
        async with db_manager.get_session() as session:
            # Convert SignalTracker to database model
            db_signal = Signal(
                signal_id=signal_tracker.signal_id,
                ticker=signal_tracker.ticker,
                normalised_ticker=signal_tracker.normalised_ticker,
                side=signal_tracker.original_signal.get('side'),
                action=signal_tracker.original_signal.get('action'),
                price=signal_tracker.original_signal.get('price'),
                signal_time=signal_tracker.original_signal.get('time'),
                original_signal=signal_tracker.original_signal,
                current_status=SignalStatusEnum(signal_tracker.current_status.value),
                current_location=SignalLocationEnum(signal_tracker.current_location.value),
                received_at=signal_tracker.original_signal.get('received_at'),
                error_count=signal_tracker.error_count,
                retry_count=signal_tracker.retry_count,
                tags=signal_tracker.tags
            )
            
            session.add(db_signal)
            await session.flush()
            
            # Add initial events
            for event in signal_tracker.events:
                db_event = SignalEvent(
                    signal_id=signal_tracker.signal_id,
                    timestamp=datetime.fromtimestamp(event.timestamp),
                    event_type=SignalStatusEnum(event.event_type.value),
                    location=SignalLocationEnum(event.location.value),
                    worker_id=event.worker_id,
                    details=event.details,
                    error_info=event.error_info,
                    http_status=event.http_status,
                    response_data=event.response_data
                )
                session.add(db_event)
            
            await session.commit()
            _logger.debug(f"Created signal {signal_tracker.signal_id} in database")
            return signal_tracker.signal_id
    
    async def update_signal(self, signal_id: str, signal_tracker: SignalTracker) -> bool:
        """Update an existing signal record."""
        async with db_manager.get_session() as session:
            # Get existing signal
            result = await session.execute(
                select(Signal).where(Signal.signal_id == signal_id)
            )
            db_signal = result.scalar_one_or_none()
            
            if not db_signal:
                _logger.warning(f"Signal {signal_id} not found for update")
                return False
            
            # Update signal fields
            db_signal.current_status = SignalStatusEnum(signal_tracker.current_status.value)
            db_signal.current_location = SignalLocationEnum(signal_tracker.current_location.value)
            db_signal.error_count = signal_tracker.error_count
            db_signal.retry_count = signal_tracker.retry_count
            db_signal.tags = signal_tracker.tags
            
            if signal_tracker.total_processing_time:
                db_signal.total_processing_time = timedelta(seconds=signal_tracker.total_processing_time)
            
            await session.commit()
            _logger.debug(f"Updated signal {signal_id} in database")
            return True
    
    async def add_signal_event(self, signal_id: str, event_data: Dict[str, Any]) -> bool:
        """Add a new event to a signal."""
        async with db_manager.get_session() as session:
            db_event = SignalEvent(
                signal_id=signal_id,
                timestamp=datetime.fromtimestamp(event_data.get('timestamp', time.time())),
                event_type=SignalStatusEnum(event_data['event_type']),
                location=SignalLocationEnum(event_data['location']),
                worker_id=event_data.get('worker_id'),
                details=event_data.get('details'),
                error_info=event_data.get('error_info'),
                http_status=event_data.get('http_status'),
                response_data=event_data.get('response_data')
            )
            
            session.add(db_event)
            await session.commit()
            _logger.debug(f"Added event to signal {signal_id}")
            return True
    
    async def get_signal(self, signal_id: str, include_events: bool = True) -> Optional[Dict[str, Any]]:
        """Get a signal by ID with optional events."""
        async with db_manager.get_session() as session:
            query = select(Signal).where(Signal.signal_id == signal_id)
            
            if include_events:
                query = query.options(selectinload(Signal.events))
            
            result = await session.execute(query)
            db_signal = result.scalar_one_or_none()
            
            if not db_signal:
                return None
            
            return self._signal_to_dict(db_signal, include_events)
    
    async def query_signals(self, query_params: AuditTrailQuery) -> AuditTrailResponse:
        """Advanced signal querying with filters and pagination."""
        async with db_manager.get_session() as session:
            # Build base query
            base_query = select(Signal)
            count_query = select(func.count(Signal.signal_id))
            
            # Apply filters
            filters = []
            
            if query_params.ticker:
                filters.append(
                    or_(
                        Signal.ticker.ilike(f"%{query_params.ticker}%"),
                        Signal.normalised_ticker.ilike(f"%{query_params.ticker}%"),
                        Signal.signal_id == query_params.ticker
                    )
                )
            
            if query_params.signal_id:
                filters.append(Signal.signal_id == query_params.signal_id)
            
            if query_params.status and query_params.status != 'all':
                filters.append(Signal.current_status == SignalStatusEnum(query_params.status))
            
            if query_params.location and query_params.location != 'all':
                filters.append(Signal.current_location == SignalLocationEnum(query_params.location))
            
            if query_params.start_time:
                start_dt = datetime.fromisoformat(query_params.start_time.replace('Z', '+00:00'))
                filters.append(Signal.created_at >= start_dt)
            
            if query_params.end_time:
                end_dt = datetime.fromisoformat(query_params.end_time.replace('Z', '+00:00'))
                filters.append(Signal.created_at <= end_dt)
            
            if query_params.error_only:
                filters.append(Signal.error_count > 0)
            
            if query_params.min_duration is not None:
                filters.append(Signal.total_processing_time >= timedelta(seconds=query_params.min_duration))
            
            if query_params.max_duration is not None:
                filters.append(Signal.total_processing_time <= timedelta(seconds=query_params.max_duration))
            
            if query_params.tags:
                for tag in query_params.tags:
                    filters.append(Signal.tags.contains([tag]))
            
            # Apply filters to queries
            if filters:
                filter_condition = and_(*filters)
                base_query = base_query.where(filter_condition)
                count_query = count_query.where(filter_condition)
            
            # Get total count
            count_result = await session.execute(count_query)
            total_count = count_result.scalar()
            
            # Apply sorting
            sort_column = Signal.updated_at  # default
            if query_params.sort_by == "created_at":
                sort_column = Signal.created_at
            elif query_params.sort_by == "duration":
                sort_column = Signal.total_processing_time
            elif query_params.sort_by == "error_count":
                sort_column = Signal.error_count
            
            if query_params.sort_order == "desc":
                sort_column = desc(sort_column)
            else:
                sort_column = asc(sort_column)
            
            base_query = base_query.order_by(sort_column)
            
            # Apply pagination
            offset = (query_params.page - 1) * query_params.page_size
            base_query = base_query.offset(offset).limit(query_params.page_size)
            
            # Include events if requested
            if query_params.include_events:
                base_query = base_query.options(selectinload(Signal.events))
            
            # Execute query
            result = await session.execute(base_query)
            signals = result.scalars().all()
            
            # Convert to response format
            entries = [self._signal_to_dict(signal, query_params.include_events) for signal in signals]
            
            # Calculate summary (using the filtered data)
            summary = await self._calculate_query_summary(session, filters)
            
            # Calculate pagination info
            total_pages = (total_count + query_params.page_size - 1) // query_params.page_size
            
            return AuditTrailResponse(
                entries=entries,
                total_count=total_count,
                page=query_params.page,
                page_size=query_params.page_size,
                total_pages=total_pages,
                filters_applied=query_params.dict(exclude_unset=True),
                summary=summary
            )
    
    async def get_analytics(self) -> Dict[str, Any]:
        """Get comprehensive analytics for the audit trail."""
        async with db_manager.get_session() as session:
            # Overall statistics
            total_signals_result = await session.execute(select(func.count(Signal.signal_id)))
            total_signals = total_signals_result.scalar()
            
            if total_signals == 0:
                return {"message": "No data available"}
            
            # Status distribution
            status_query = select(
                Signal.current_status,
                func.count(Signal.signal_id).label('count')
            ).group_by(Signal.current_status)
            status_result = await session.execute(status_query)
            status_counts = {status.value: count for status, count in status_result}
            
            # Location distribution
            location_query = select(
                Signal.current_location,
                func.count(Signal.signal_id).label('count')
            ).group_by(Signal.current_location)
            location_result = await session.execute(location_query)
            location_counts = {location.value: count for location, count in location_result}
            
            # Performance metrics
            perf_query = select(
                func.avg(Signal.total_processing_time).label('avg_duration'),
                func.avg(Signal.error_count * 1.0 / func.greatest(func.array_length(Signal.tags, 1), 1)).label('avg_error_rate'),
                func.count(Signal.signal_id).filter(Signal.current_location.in_([
                    SignalLocationEnum.COMPLETED, SignalLocationEnum.DISCARDED
                ])).label('completed_signals')
            )
            perf_result = await session.execute(perf_query)
            perf_data = perf_result.first()
            
            # Recent activity (last 24 hours)
            last_24h = datetime.utcnow() - timedelta(hours=24)
            recent_query = select(func.count(Signal.signal_id)).where(Signal.created_at >= last_24h)
            recent_result = await session.execute(recent_query)
            recent_count = recent_result.scalar()
            
            # Top error tickers
            error_query = select(
                Signal.ticker,
                func.sum(Signal.error_count).label('total_errors')
            ).where(Signal.error_count > 0).group_by(Signal.ticker).order_by(desc('total_errors')).limit(10)
            error_result = await session.execute(error_query)
            top_error_tickers = [(ticker, errors) for ticker, errors in error_result]
            
            return {
                "overview": {
                    "total_signals": total_signals,
                    "completed_signals": perf_data.completed_signals or 0,
                    "completion_rate": ((perf_data.completed_signals or 0) / total_signals * 100) if total_signals > 0 else 0,
                    "average_processing_time": perf_data.avg_duration.total_seconds() if perf_data.avg_duration else 0,
                    "average_error_rate": (perf_data.avg_error_rate or 0) * 100
                },
                "status_distribution": status_counts,
                "location_distribution": location_counts,
                "last_24h": {
                    "total_signals": recent_count,
                },
                "error_analysis": {
                    "top_error_tickers": top_error_tickers,
                    "total_errors": sum(status_counts.get(status, 0) for status in ['forwarded_http_error', 'forwarded_generic_error', 'error']),
                    "signals_with_errors": len([s for s in status_counts if 'error' in s and status_counts[s] > 0])
                }
            }
    
    async def record_system_metrics(self, metrics_data: Dict[str, Any]) -> bool:
        """Record system-wide metrics."""
        async with db_manager.get_session() as session:
            db_metrics = SystemMetrics(
                processing_queue_size=metrics_data.get('processing_queue_size', 0),
                approved_queue_size=metrics_data.get('approved_queue_size', 0),
                active_processing_workers=metrics_data.get('active_processing_workers', 0),
                active_forwarding_workers=metrics_data.get('active_forwarding_workers', 0),
                webhook_tokens_available=metrics_data.get('webhook_tokens_available'),
                webhook_requests_this_minute=metrics_data.get('webhook_requests_this_minute', 0),
                finviz_rate_limit_tokens=metrics_data.get('finviz_rate_limit_tokens'),
                finviz_engine_status=metrics_data.get('finviz_engine_status'),
                webhook_rate_limiter_enabled=metrics_data.get('webhook_rate_limiter_enabled', False),
                additional_metrics=metrics_data.get('additional_metrics', {})
            )
            
            session.add(db_metrics)
            await session.commit()
            return True
    
    async def cleanup_old_data(self, max_age_days: int = 30) -> Dict[str, int]:
        """Clean up old signal data beyond retention period."""
        async with db_manager.get_session() as session:
            cutoff_date = datetime.utcnow() - timedelta(days=max_age_days)
            
            # Count signals to be deleted
            count_query = select(func.count(Signal.signal_id)).where(Signal.created_at < cutoff_date)
            count_result = await session.execute(count_query)
            signals_to_delete = count_result.scalar()
            
            # Delete old signals (events will be cascade deleted)
            delete_query = text("DELETE FROM signals WHERE created_at < :cutoff_date")
            await session.execute(delete_query, {"cutoff_date": cutoff_date})
            
            # Delete old system metrics
            metrics_delete_query = text("DELETE FROM system_metrics WHERE timestamp < :cutoff_date")
            await session.execute(metrics_delete_query, {"cutoff_date": cutoff_date})
            
            await session.commit()
            
            return {
                "signals_deleted": signals_to_delete,
                "cutoff_date": cutoff_date.isoformat()
            }
    
    def _signal_to_dict(self, signal: Signal, include_events: bool = True) -> Dict[str, Any]:
        """Convert database Signal model to dictionary."""
        result = {
            "signal_id": str(signal.signal_id),
            "ticker": signal.ticker,
            "normalised_ticker": signal.normalised_ticker,
            "timestamp": signal.updated_at.isoformat() + 'Z',
            "status": signal.current_status.value,
            "status_display": self._get_status_display(signal.current_status),
            "location": signal.current_location.value,
            "action": "signal_tracking",
            "details": f"Signal tracking for {signal.ticker}",
            "created_at": signal.created_at.timestamp(),
            "updated_at": signal.updated_at.timestamp(),
            "total_processing_time": signal.total_processing_time.total_seconds() if signal.total_processing_time else None,
            "error_count": signal.error_count,
            "retry_count": signal.retry_count,
            "tags": signal.tags or [],
            **signal.original_signal
        }
        
        if include_events and hasattr(signal, 'events'):
            result["events"] = [
                {
                    "timestamp": event.timestamp.timestamp(),
                    "event_type": event.event_type.value,
                    "location": event.location.value,
                    "worker_id": event.worker_id,
                    "details": event.details,
                    "error_info": event.error_info,
                    "http_status": event.http_status,
                    "response_data": event.response_data,
                    "formatted_timestamp": event.timestamp.isoformat() + 'Z'
                }
                for event in sorted(signal.events, key=lambda e: e.timestamp)
            ]
            result["events_count"] = len(signal.events)
        
        return result
    
    def _get_status_display(self, status: SignalStatusEnum) -> str:
        """Get human-readable status display."""
        status_map = {
            SignalStatusEnum.RECEIVED: "📨 Received",
            SignalStatusEnum.QUEUED_PROCESSING: "⏳ Queued for Processing",
            SignalStatusEnum.PROCESSING: "⚙️ Processing",
            SignalStatusEnum.APPROVED: "✅ Approved",
            SignalStatusEnum.REJECTED: "❌ Rejected",
            SignalStatusEnum.QUEUED_FORWARDING: "📤 Queued for Forwarding",
            SignalStatusEnum.FORWARDING: "🚀 Forwarding",
            SignalStatusEnum.FORWARDED_SUCCESS: "✅ Successfully Forwarded",
            SignalStatusEnum.FORWARDED_TIMEOUT: "⏰ Forwarding Timeout",
            SignalStatusEnum.FORWARDED_HTTP_ERROR: "❌ HTTP Error",
            SignalStatusEnum.FORWARDED_GENERIC_ERROR: "💥 Generic Error",
            SignalStatusEnum.ERROR: "❌ Error",
            SignalStatusEnum.DISCARDED: "🗑️ Discarded"
        }
        return status_map.get(status, f"Unknown: {status.value}")
    
    async def _calculate_query_summary(self, session, filters) -> Dict[str, Any]:
        """Calculate summary statistics for filtered results."""
        # Build summary query with same filters
        summary_query = select(
            func.count(Signal.signal_id).label('total'),
            func.count(Signal.signal_id).filter(Signal.current_status == SignalStatusEnum.APPROVED).label('approved'),
            func.count(Signal.signal_id).filter(Signal.current_status == SignalStatusEnum.REJECTED).label('rejected'),
            func.count(Signal.signal_id).filter(Signal.current_status == SignalStatusEnum.FORWARDED_SUCCESS).label('forwarded_success'),
            func.count(Signal.signal_id).filter(Signal.error_count > 0).label('with_errors'),
            func.avg(Signal.total_processing_time).label('avg_processing_time')
        )
        
        if filters:
            summary_query = summary_query.where(and_(*filters))
        
        result = await session.execute(summary_query)
        summary_data = result.first()
        
        return {
            "total_signals": summary_data.total or 0,
            "approved_signals": summary_data.approved or 0,
            "rejected_signals": summary_data.rejected or 0,
            "forwarded_success": summary_data.forwarded_success or 0,
            "signals_with_errors": summary_data.with_errors or 0,
            "average_processing_time": summary_data.avg_processing_time.total_seconds() if summary_data.avg_processing_time else 0
        }

# Global service instance
audit_service = SignalAuditService()

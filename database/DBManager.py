# database/DBManager.py

# ==========================================================================================
#                         Trading Signal Processor - DBManager
#
# ABORDAGEM HÍBRIDA IMPLEMENTADA:
# - Enums no Python para type safety e clareza
# - Strings no banco para simplicidade e performance
# - Conversão automática na camada de persistência
# ==========================================================================================

import logging
from contextlib import asynccontextmanager
from typing import List, Optional, Dict, Any, AsyncGenerator
from datetime import datetime, timedelta
import datetime as dt

from sqlalchemy import select, func, and_, or_, desc, asc, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

# Importações do seu projeto
from config import settings
from models import Signal as SignalPayload, SignalTracker, AuditTrailQuery, AuditTrailResponse
from database.simple_models import Base, Signal, SignalEvent, SignalStatusEnum, SignalLocationEnum, MetricPeriodEnum

_logger = logging.getLogger("DBManager")

class DBManager:
    """Gerenciador centralizado para todas as operações de banco de dados."""

    def __init__(self):
        self.engine = None
        self.async_session_factory = None
        self._initialized = False

    def initialize(self, database_url: str):
        """Inicializa o motor do banco de dados e a fábrica de sessões. Deve ser chamado no startup da aplicação."""
        if self._initialized:
            _logger.warning("DatabaseManager já foi inicializado.")
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
        _logger.info(f"DatabaseManager inicializado com sucesso. Pool size: {engine_config['pool_size']}")

    async def close(self):
        """Fecha as conexões do banco de dados. Deve ser chamado no shutdown da aplicação."""
        if self.engine:
            await self.engine.dispose()
            _logger.info("Conexões do banco de dados fechadas.")

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Providencia uma sessão de banco de dados com gerenciamento de transação."""
        if not self._initialized:
            raise RuntimeError("DatabaseManager não foi inicializado. Chame initialize() primeiro.")

        session: AsyncSession = self.async_session_factory()
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            _logger.error(f"Erro na sessão do banco de dados, transação revertida: {e}", exc_info=True)
            raise
        finally:
            await session.close()

    # --- Métodos de Lógica de Negócio (API Pública do Manager) ---
    
    async def create_signal_with_initial_event(self, signal_payload: SignalPayload) -> str:
        """
        Cria um novo registro de sinal e seu primeiro evento 'RECEIVED'.
        Esta é a principal função a ser chamada quando um novo sinal chega.
        HÍBRIDO: Usa enums no código, salva strings no banco.
        """
        async with self.get_session() as session:
            db_signal = Signal(
                signal_id=signal_payload.signal_id,
                ticker=signal_payload.ticker,
                normalised_ticker=signal_payload.normalised_ticker(),
                side=signal_payload.side,
                price=signal_payload.price,
                original_signal=signal_payload.dict(),
                status=SignalStatusEnum.RECEIVED.value  # Enum -> string para DB
            )

            initial_event = SignalEvent(
                signal_id=db_signal.signal_id,
                status=SignalStatusEnum.RECEIVED.value,  # Enum -> string para DB
                details="Sinal recebido e enfileirado para processamento."
            )

            session.add(db_signal)
            session.add(initial_event)
            await session.flush()
            _logger.debug(f"Sinal {db_signal.signal_id} criado no banco de dados.")
            return str(db_signal.signal_id)

    async def log_signal_event(
        self,
        signal_id: str,
        event_type: SignalStatusEnum,  # Aceita enum no código
        location: Optional[SignalLocationEnum] = None,
        details: Optional[str] = None,
        worker_id: Optional[str] = None,
        error_info: Optional[Dict[str, Any]] = None,
        http_status: Optional[int] = None,
        response_data: Optional[str] = None
    ) -> bool:
        """
        Registra um novo evento para um sinal existente e atualiza o estado atual do sinal.
        HÍBRIDO: Aceita enums no Python, converte para string no banco.
        """
        async with self.get_session() as session:
            # Encontra o sinal principal
            result = await session.execute(select(Signal).where(Signal.signal_id == signal_id))
            db_signal = result.scalar_one_or_none()

            if not db_signal:
                _logger.warning(f"Tentativa de logar evento para sinal inexistente: {signal_id}")
                return False

            # Cria o novo evento - converte enum para string
            event_type_str = event_type.value if hasattr(event_type, 'value') else str(event_type)
            
            new_event = SignalEvent(
                signal_id=signal_id,
                status=event_type_str,  # String no banco
                details=details,
                worker_id=worker_id
            )
            session.add(new_event)

            # Atualiza o estado atual do sinal principal
            db_signal.status = event_type_str  # String no banco
            
            await session.flush()
            _logger.debug(f"Evento '{event_type_str}' logado para o sinal {signal_id}.")
            return True

    async def increment_signal_retry_count(self, signal_id: str) -> Optional[int]:
        """Incrementa a contagem de retentativas para um sinal."""
        async with self.get_session() as session:
            result = await session.execute(select(Signal).where(Signal.signal_id == signal_id))
            db_signal = result.scalar_one_or_none()
            if db_signal:
                # Adicionar campo retry_count se não existir no modelo
                if not hasattr(db_signal, 'retry_count'):
                    _logger.warning("Campo retry_count não existe no modelo Signal. Implementação futura.")
                    return None
                db_signal.retry_count = (db_signal.retry_count or 0) + 1
                await session.flush()
                return db_signal.retry_count
            return None
            
    async def get_signal_retry_count(self, signal_id: str) -> int:
        """Obtém a contagem atual de retentativas para um sinal."""
        async with self.get_session() as session:
            result = await session.execute(select(Signal).where(Signal.signal_id == signal_id))
            db_signal = result.scalar_one_or_none()
            if db_signal and hasattr(db_signal, 'retry_count'):
                return db_signal.retry_count or 0
            return 0

    async def get_system_analytics(self) -> Dict[str, Any]:
        """Obtém métricas e análises gerais do sistema."""
        async with self.get_session() as session:
            # Contagem total de sinais
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
                  
            # Distribuição de status (agora strings no banco)
            status_query = select(Signal.status, func.count(Signal.signal_id)).group_by(Signal.status)
            status_res = await session.execute(status_query)
            status_distribution = {status: count for status, count in status_res}

            # Outras métricas
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
        Obtém estatísticas de sinal por hora para as últimas N horas.
        Retorna os dados formatados para gráficos, sempre preenchendo horas faltantes com zeros.
        """
        try:
            async with self.get_session() as session:
                # Calcular intervalo de tempo - usar timezone-aware datetime
                end_time = datetime.utcnow()
                start_time = end_time - timedelta(hours=hours)
                
                _logger.info(f"Getting hourly stats from {start_time} to {end_time} ({hours} hours)")
                
                # Consulta para obter dados agregados por hora
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
                
                # Mapear dados do banco por hora
                data_by_hour = {}
                for row in result:
                    # Garantir que a hora seja timezone-aware
                    hour_key = row.hour.replace(tzinfo=None) if row.hour.tzinfo else row.hour
                    data_by_hour[hour_key] = row
                
                _logger.info(f"Found data for {len(data_by_hour)} hours in database")
                
                hourly_data = []
                
                # Preencher TODAS as horas no intervalo, mesmo que não tenham dados
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
                        # Preencher hora sem dados com zeros
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
            _logger.error(f"Erro ao obter estatísticas horárias de sinais: {e}", exc_info=True)
            
            # Retornar dados de fallback em caso de erro
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
        """Consulta avançada de sinais com filtros, paginação e ordenação."""
        async with self.get_session() as session:
            base_query = select(Signal)
            count_query = select(func.count(Signal.signal_id))
            
            filters = self._build_filters(query_params)
            if filters:
                filter_condition = and_(*filters)
                base_query = base_query.where(filter_condition)
                count_query = count_query.where(filter_condition)

            # Total de registros
            total_count = (await session.execute(count_query)).scalar_one()

            # Ordenação
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

            # Paginação
            offset = (query_params.page - 1) * query_params.page_size
            base_query = base_query.offset(offset).limit(query_params.page_size)

            if query_params.include_events:
                base_query = base_query.options(selectinload(Signal.events))

            # Execução
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
        """Remove dados de sinais e eventos mais antigos que o período de retenção."""
        async with self.get_session() as session:
            cutoff_date = datetime.utcnow() - timedelta(days=retention_days)
            
            # Usar text() para performance em deletes em massa
            # O cascade delete no relacionamento cuidará dos eventos.
            delete_stmt = text("DELETE FROM signals WHERE created_at < :cutoff")
            
            result = await session.execute(delete_stmt, {"cutoff": cutoff_date})
            deleted_count = result.rowcount

            _logger.info(f"Limpeza de dados concluída. {deleted_count} sinais antigos removidos.")
            return {
                "deleted_signals_count": deleted_count,
                "retention_period_days": retention_days,
                "cutoff_date": cutoff_date.isoformat()
            }

    # --- Métodos de Compatibilidade (Legacy API) ---
    
    async def get_audit_trail(
        self,
        limit: int = 100,
        offset: int = 0,
        status_filter: Optional[str] = None,
        ticker_filter: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        hours: Optional[int] = None,
        **kwargs  # Accept additional parameters for backward compatibility
    ) -> List[Dict[str, Any]]:
        """Método de compatibilidade para get_audit_trail (legacy API) com filtros melhorados."""
        from models import AuditTrailQuery
        
        # Apply hours filter if provided
        if hours and not start_time:
            start_time = datetime.utcnow() - timedelta(hours=hours)
        
        # Mapear parâmetros legacy para nova API
        query_params = AuditTrailQuery(
            page=offset // limit + 1,
            page_size=limit,
            status=status_filter,
            ticker=ticker_filter,
            start_time=start_time.isoformat() + 'Z' if start_time else None,
            end_time=end_time.isoformat() + 'Z' if end_time else None,
            include_events=True,
            sort_by="created_at",
            sort_order="desc"
        )
        
        response = await self.query_signals(query_params)
        return response.entries

    async def get_audit_trail_count(
        self, 
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        signal_id: Optional[str] = None,
        event_types: Optional[List[str]] = None,
        status_filter: Optional[str] = None,
        ticker_filter: Optional[str] = None,
        hours: Optional[int] = None,
        **kwargs  # Accept additional parameters for backward compatibility
    ) -> int:
        """Conta os registros de auditoria com base nos filtros melhorados."""
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
            if status_filter:
                query = query.where(Signal.status == status_filter)
            if ticker_filter:
                # Filter by ticker field in Signal table
                query = query.where(Signal.ticker.ilike(f'%{ticker_filter}%'))
            
            result = await session.execute(query)
            return result.scalar_one()

    async def get_signal_status_distribution(self) -> Dict[str, Any]:
        """Retorna a distribuição de status dos sinais com metadados."""
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
        """Retorna os sinais mais recentes."""
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
        """Retorna estatísticas de performance por ticker."""
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
        """Retorna métricas em tempo real do sistema."""
        async with self.get_session() as session:
            # Estatísticas gerais
            total_signals = await session.execute(select(func.count(Signal.signal_id)))
            total_signals = total_signals.scalar_one()
            
            # Últimas 24 horas
            last_24h = datetime.now() - timedelta(hours=24)
            recent_signals = await session.execute(
                select(func.count(Signal.signal_id)).where(Signal.created_at >= last_24h)
            )
            recent_signals = recent_signals.scalar_one()
            
            # Status distribution
            status_dist = await self.get_signal_status_distribution()
            
            # Últimos eventos
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

    # --- Métodos Auxiliares Internos ---

    def _build_filters(self, p: AuditTrailQuery) -> list:
        """Constrói a lista de filtros SQLAlchemy a partir dos parâmetros de consulta."""
        filters = []
        if p.ticker:
            # Only filter by ticker and normalised_ticker, NOT by signal_id to avoid UUID conversion errors
            filters.append(or_(
                Signal.ticker.ilike(f"%{p.ticker}%"), 
                Signal.normalised_ticker.ilike(f"%{p.ticker}%")
            ))
        if p.status and p.status != 'all':
            # Agora usando strings diretamente
            filters.append(Signal.status == p.status)
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
        """Calcula estatísticas de resumo para um conjunto de filtros."""
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
        """Converte um objeto SQLAlchemy Signal em um dicionário para a API."""
        from datetime import datetime
        
        def safe_datetime_format(dt):
            """Safely format datetime to ISO string."""
            if dt is None:
                return None
            if hasattr(dt, 'isoformat'):
                return dt.isoformat() + 'Z'
            try:
                return datetime.utcfromtimestamp(float(dt)).isoformat() + 'Z'
            except (ValueError, TypeError, OSError):
                return str(dt) if dt else None

        # Primary signal data
        data = {
            "signal_id": str(signal.signal_id),
            "ticker": signal.ticker or '-',
            "normalised_ticker": getattr(signal, 'normalised_ticker', None) or '-',
            "timestamp": safe_datetime_format(signal.updated_at),
            "status": getattr(signal, 'status', None) or 'unknown',
            "status_display": (getattr(signal, 'status', '') or '').replace('_', ' ').title() or 'Unknown',
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

# Instância Singleton Global - para ser importada em toda a aplicação
db_manager = DBManager()

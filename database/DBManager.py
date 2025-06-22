# database/DBManager.py

# ==========================================================================================
#                         Trading Signal Processor - DBManager
#
# Este arquivo é o seu centro de controle completo para todas as interações com o banco de
# dados. Ele foi projetado para ser a única interface que sua aplicação precisa para
# persistir, auditar e consultar dados de sinais.
#
# COMO INTEGRAR ESTE MÓDULO NO SEU PROJETO:
#
# 1. PRÉ-REQUISITOS (FAÇA ISSO PRIMEIRO):
#    a. Instale o PostgreSQL (a forma mais fácil é via Docker:
#       `docker run --name trading-db -e POSTGRES_PASSWORD=sua_senha -p 5432:5432 -d postgres`)
#    b. Crie o banco de dados dentro do PostgreSQL (usando o Docker do passo anterior):
#       `docker exec -it trading-db createdb -U postgres trading_signals`
#    c. Atualize a variável `DATABASE_URL` no seu arquivo `.env` com os dados corretos.
#       Exemplo: DATABASE_URL="postgresql+asyncpg://postgres:sua_senha@localhost:5432/trading_signals"
#    d. Execute as migrações do Alembic para criar as tabelas no banco de dados:
#       - `alembic revision --autogenerate -m "Cria schema inicial de auditoria"`
#       - `alembic upgrade head`
#
# 2. LIMPEZA:
#    - Exclua os arquivos antigos `database/connection.py` e `database/audit_service.py`.
#      Este arquivo `DBManager.py` substitui ambos.
#
# 3. INTEGRAÇÃO NO `main.py`:
#
#    a. Importe o `db_manager`:
#       `from database.DBManager import db_manager`
#
#    b. Na função `_startup` do seu `main.py`, inicialize o DBManager:
#       @app.on_event("startup")
#       async def _startup() -> None:
#           _logger.info("Application startup sequence initiated.")
#           db_manager.initialize(settings.DATABASE_URL) # Adicione esta linha
#           # ... resto do seu código de startup ...
#
#    c. Na função `_shutdown` do seu `main.py`, feche a conexão:
#       @app.on_event("shutdown")
#       async def _shutdown() -> None:
#           await db_manager.close() # Adicione esta linha
#           # ... resto do seu código de shutdown ...
#
#    d. No endpoint `/webhook/in`, substitua a criação do tracker em memória pela chamada ao DBManager:
#       # ANTES:
#       # tracker = create_signal_tracker(signal)
#       # shared_state["signal_trackers"][signal.signal_id] = tracker
#       # ...
#
#       # DEPOIS:
#       try:
#           await db_manager.create_signal_with_initial_event(signal)
#           _logger.info(f"[SIGNAL: {signal.signal_id}] Persisted initial signal record to database.")
#       except Exception as db_error:
#           _logger.error(f"[SIGNAL: {signal.signal_id}] FAILED to persist signal to database: {db_error}")
#
#       # Coloque o sinal na fila para processamento (a lógica em memória continua útil para velocidade)
#       queue.put_nowait(signal)
#
#    e. Nos workers (`_queue_worker` e `_forwarding_worker`), substitua as chamadas `update_signal_tracker`
#       e `broadcast_signal_tracker_update` por uma única chamada ao `log_signal_event`.
#
#       # EXEMPLO EM `_queue_worker` PARA SINAL REJEITADO:
#       # ANTES:
#       # update_signal_tracker(signal_id, SignalStatus.REJECTED, ...)
#       # await broadcast_signal_tracker_update(tracker)
#
#       # DEPOIS:
#       event_details = f"Signal rejected - ticker {normalised_ticker} not in top-N list"
#       await db_manager.log_signal_event(
#           signal_id=signal.signal_id,
#           event_type=SignalStatusEnum.REJECTED,
#           location=SignalLocationEnum.DISCARDED,
#           details=event_details,
#           worker_id=f"queue_worker_{worker_id}"
#       )
#
#    f. Implemente a lógica de retentativas ("vidas") no `_forwarding_worker` usando o DBManager:
#
#       except Exception as e:
#           # ...
#           current_retries = await db_manager.get_signal_retry_count(signal_id)
#           MAX_RETRIES = 5 # Defina no config.py
#
#           if current_retries < MAX_RETRIES:
#               await db_manager.increment_signal_retry_count(signal_id)
#               # Re-enfileira o sinal...
#           else:
#               # Descarta o sinal...
#
#    g. Para a nova UI, crie endpoints de API que chamem os métodos de consulta deste manager:
#       - `POST /api/v1/audit/query` -> `db_manager.query_signals(query)`
#       - `GET /api/v1/metrics/summary` -> `db_manager.get_system_analytics()`
#
# ==========================================================================================

import logging
from contextlib import asynccontextmanager
from typing import List, Optional, Dict, Any, AsyncGenerator
from datetime import datetime, timedelta

from sqlalchemy import select, func, and_, or_, desc, asc, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

# Importações do seu projeto
from config import settings
from models import Signal as SignalPayload, SignalTracker, AuditTrailQuery, AuditTrailResponse
from database.models import Base, Signal, SignalEvent, SignalStatusEnum, SignalLocationEnum, MetricPeriodEnum

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
        """
        async with self.get_session() as session:
            db_signal = Signal(
                signal_id=signal_payload.signal_id,
                ticker=signal_payload.ticker,
                normalised_ticker=signal_payload.normalised_ticker(),
                side=signal_payload.side,
                action=signal_payload.action,
                price=signal_payload.price,
                signal_time=signal_payload.time,
                original_signal=signal_payload.dict(),
                current_status=SignalStatusEnum.RECEIVED,
                current_location=SignalLocationEnum.PROCESSING_QUEUE,
                received_at=signal_payload.received_at
            )

            initial_event = SignalEvent(
                signal_id=db_signal.signal_id,
                event_type=SignalStatusEnum.RECEIVED,
                location=SignalLocationEnum.PROCESSING_QUEUE,
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
        event_type: SignalStatusEnum,
        location: SignalLocationEnum,
        details: Optional[str] = None,
        worker_id: Optional[str] = None,
        error_info: Optional[Dict[str, Any]] = None,
        http_status: Optional[int] = None,
        response_data: Optional[str] = None
    ) -> bool:
        """
        Registra um novo evento para um sinal existente e atualiza o estado atual do sinal.
        """
        async with self.get_session() as session:
            # Encontra o sinal principal
            result = await session.execute(select(Signal).where(Signal.signal_id == signal_id))
            db_signal = result.scalar_one_or_none()

            if not db_signal:
                _logger.warning(f"Tentativa de logar evento para sinal inexistente: {signal_id}")
                return False

            # Cria o novo evento
            new_event = SignalEvent(
                signal_id=signal_id,
                event_type=event_type,
                location=location,
                details=details,
                worker_id=worker_id,
                error_info=error_info,
                http_status=http_status,
                response_data=response_data
            )
            session.add(new_event)

            # Atualiza o estado atual do sinal principal
            db_signal.current_status = event_type
            db_signal.current_location = location
            if error_info or (http_status and http_status >= 400):
                db_signal.error_count = (db_signal.error_count or 0) + 1
            
            # Se o sinal foi finalizado, calcula o tempo total
            if location in [SignalLocationEnum.COMPLETED, SignalLocationEnum.DISCARDED]:
                db_signal.total_processing_time = datetime.utcnow() - db_signal.created_at

            await session.flush()
            _logger.debug(f"Evento '{event_type.value}' logado para o sinal {signal_id}.")
            return True

    async def increment_signal_retry_count(self, signal_id: str) -> Optional[int]:
        """Incrementa a contagem de retentativas para um sinal."""
        async with self.get_session() as session:
            result = await session.execute(select(Signal).where(Signal.signal_id == signal_id))
            db_signal = result.scalar_one_or_none()
            if db_signal:
                db_signal.retry_count = (db_signal.retry_count or 0) + 1
                await session.flush()
                return db_signal.retry_count
            return None
            
    async def get_signal_retry_count(self, signal_id: str) -> int:
        """Obtém a contagem atual de retentativas para um sinal."""
        async with self.get_session() as session:
            result = await session.execute(select(Signal.retry_count).where(Signal.signal_id == signal_id))
            count = result.scalar_one_or_none()
            return count or 0

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
                "duration": Signal.total_processing_time,
                "error_count": Signal.error_count,
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
            
    async def get_system_analytics(self) -> Dict[str, Any]:
        """Obtém métricas e análises gerais do sistema."""
        async with self.get_session() as session:
            # Contagem total de sinais
            total_signals_res = await session.execute(select(func.count(Signal.signal_id)))
            total_signals = total_signals_res.scalar_one()
            
            if total_signals == 0:
                return {"overview": {"total_signals": 0}, "status_distribution": {}}
                
            # Distribuição de status
            status_query = select(Signal.current_status, func.count(Signal.signal_id)).group_by(Signal.current_status)
            status_res = await session.execute(status_query)
            status_distribution = {status.value: count for status, count in status_res}

            # Outras métricas
            avg_duration_res = await session.execute(select(func.avg(Signal.total_processing_time)).where(Signal.total_processing_time.isnot(None)))
            avg_duration = avg_duration_res.scalar_one()
            
            return {
                "overview": {
                    "total_signals": total_signals,
                    "signals_with_errors": sum(status_distribution.get(s.value, 0) for s in SignalStatusEnum if 'error' in s.value),
                    "average_processing_time_seconds": avg_duration.total_seconds() if avg_duration else 0,
                },
                "status_distribution": status_distribution
            }

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

    # --- Métodos Auxiliares Internos ---

    def _build_filters(self, p: AuditTrailQuery) -> list:
        """Constrói a lista de filtros SQLAlchemy a partir dos parâmetros de consulta."""
        filters = []
        if p.ticker:
            filters.append(or_(Signal.ticker.ilike(f"%{p.ticker}%"), Signal.normalised_ticker.ilike(f"%{p.ticker}%"), Signal.signal_id == p.ticker))
        if p.status and p.status != 'all':
            filters.append(Signal.current_status == SignalStatusEnum(p.status))
        if p.location and p.location != 'all':
            filters.append(Signal.current_location == SignalLocationEnum(p.location))
        if p.start_time:
            filters.append(Signal.created_at >= datetime.fromisoformat(p.start_time.replace('Z', '+00:00')))
        if p.end_time:
            filters.append(Signal.created_at <= datetime.fromisoformat(p.end_time.replace('Z', '+00:00')))
        if p.error_only:
            filters.append(Signal.error_count > 0)
        return filters
        
    async def _calculate_query_summary(self, session: AsyncSession, filters: list) -> Dict[str, Any]:
        """Calcula estatísticas de resumo para um conjunto de filtros."""
        summary_query = select(
            func.count(Signal.signal_id).label('total'),
            func.avg(Signal.total_processing_time).label('avg_processing_time')
        )
        if filters:
            summary_query = summary_query.where(and_(*filters))
        
        result = (await session.execute(summary_query)).first()
        avg_time = result.avg_processing_time.total_seconds() if result and result.avg_processing_time else 0
        
        return {
            "total_signals_in_query": result.total if result else 0,
            "average_processing_time_seconds": avg_time
        }

    def _signal_to_dict(self, signal: Signal, include_events: bool) -> Dict[str, Any]:
        """Converte um objeto SQLAlchemy Signal em um dicionário para a API."""
        data = {
            "signal_id": str(signal.signal_id),
            "ticker": signal.ticker,
            "normalised_ticker": signal.normalised_ticker,
            "timestamp": signal.updated_at.isoformat() + 'Z',
            "status": signal.current_status.value,
            "status_display": signal.current_status.name.replace('_', ' ').title(),
            "location": signal.current_location.value,
            "created_at": signal.created_at.isoformat() + 'Z',
            "updated_at": signal.updated_at.isoformat() + 'Z',
            "total_processing_time": signal.total_processing_time.total_seconds() if signal.total_processing_time else None,
            "error_count": signal.error_count,
            "retry_count": signal.retry_count,
            "tags": signal.tags or [],
            "original_signal": signal.original_signal,
        }
        if include_events and hasattr(signal, 'events'):
            data["events"] = [
                {
                    "timestamp": event.timestamp.isoformat() + 'Z',
                    "event_type": event.event_type.value,
                    "location": event.location.value,
                    "details": event.details,
                    "worker_id": event.worker_id,
                    "error_info": event.error_info,
                    "http_status": event.http_status,
                } for event in sorted(signal.events, key=lambda e: e.timestamp)
            ]
        return data

# Instância Singleton Global - para ser importada em toda a aplicação
db_manager = DBManager()
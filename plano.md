# Plano de Robustez Operacional
## Trading Signal Processor

### Data do Plano: 8 de julho de 2025

---

## 🎯 OBJETIVO

Criar um sistema robusto operacionalmente focado em **fonte de verdade única no PostgreSQL**, eliminando inconsistências e melhorando a experiência operacional. Este plano não inclui backup - apenas soluções para robustez durante operação normal.

---

## 🔍 PROBLEMAS CRÍTICOS IDENTIFICADOS

### 1. **PROBLEMA: Botão "Limpar Banco" Falhando**

**Status Atual**: ❌ **QUEBRADO**

**Erro Atual**:
```
Error clearing database: (sqlalchemy.dialects.postgresql.asyncpg.IntegrityError) : 
update or delete on table "signals" violates foreign key constraint 
"positions_entry_signal_id_fkey" on table "positions"
DETAIL: Key (signal_id)=(8d744817-382f-47bc-b536-dcc4219e43f8) is still referenced from table "positions".
```

**Causa Raiz**: A função `clear_all_data()` em `DBManager.py` tenta deletar signals antes de deletar positions que as referenciam.

**Localização**: 
- `database/DBManager.py` linha 402-418
- `main.py` linha 2053-2080

**Ordem Atual (INCORRETA)**:
1. DELETE FROM signal_events ✅
2. DELETE FROM signals ❌ (falha aqui - positions ainda referencia)

**Ordem Correta Necessária**:
1. DELETE FROM signal_events
2. **DELETE FROM positions** ← FALTANDO
3. DELETE FROM signals

### 2. **PROBLEMA: Impossibilidade de Adicionar Tickers Manualmente à Sell All List**

**Status Atual**: ❌ **QUEBRADO** 

**Causa Raiz**: Após migração para usar banco como fonte de verdade, o sistema `get_sell_all_list_data()` apenas retorna posições abertas do banco. Não existe mecanismo para criar "posições fictícias" para tickers adicionados manualmente.

**Sistema Atual**:
- Frontend: Chama `addTopNTickerToSellAll(ticker)` → POST `/admin/sell-all-queue`
- Backend: **ENDPOINT NÃO EXISTE** 
- Sell All List: Deriva apenas de `get_all_open_positions_tickers()`

**Problema**: Não há como adicionar tickers que não tenham posições reais abertas.

### 3. **PROBLEMA: Falta de Acompanhamento de Ordens em Tempo Real**

**Status Atual**: ❌ **INEXISTENTE**

**Necessidades Identificadas**:
- Interface para visualizar ordens abertas/fechadas
- Atualização em tempo real via WebSocket
- Fonte de verdade: tabela `positions` no banco
- UX intuitiva para acompanhar mudanças de status

### 4. **PROBLEMA: Estado Híbrido Memória + Banco**

**Status Atual**: ⚠️ **INCONSISTENTE**

**Pontos Usando Memória que Deveriam Usar Banco**:
- `shared_state["signal_metrics"]` - Contadores de sinais
- `shared_state["tickers"]` - Lista de tickers do Finviz
- `shared_state["webhook_rate_limiter"]` - Métricas de rate limiting
- Filas em memória (`approved_signal_queue`, `forwarding_signal_queue`)

---

## 🛠️ SOLUÇÕES PROPOSTAS

### **FASE 1: CORREÇÕES CRÍTICAS (1-2 dias)**

#### **1.1 Corrigir Botão "Limpar Banco"**

**Ação**: Modificar `clear_all_data()` para respeitar constraints

```python
# Novo ordem em database/DBManager.py
async def clear_all_data(self) -> Dict[str, Any]:
    async with self.get_session() as session:
        # 1. Deletar events primeiro (sem FK constraints)
        events_delete_stmt = text("DELETE FROM signal_events")
        events_result = await session.execute(events_delete_stmt)
        deleted_events = events_result.rowcount
        
        # 2. Deletar positions (que referenciam signals)
        positions_delete_stmt = text("DELETE FROM positions")
        positions_result = await session.execute(positions_delete_stmt)
        deleted_positions = positions_result.rowcount
        
        # 3. Agora podemos deletar signals sem violar constraints
        signals_delete_stmt = text("DELETE FROM signals")
        signals_result = await session.execute(signals_delete_stmt)
        deleted_signals = signals_result.rowcount

        return {
            "deleted_signals_count": deleted_signals,
            "deleted_events_count": deleted_events,
            "deleted_positions_count": deleted_positions,
            "operation": "clear_all_data"
        }
```

**Impacto**: ✅ Botão funcionará corretamente, limpando tudo sem erros

#### **1.2 Criar Endpoint para Adicionar Tickers Manualmente**

**Ação**: Implementar POST `/admin/sell-all-queue` que cria "posições fictícias"

```python
# Novo endpoint em main.py
@app.post("/admin/sell-all-queue", status_code=status.HTTP_201_CREATED)
async def add_ticker_to_sell_all(payload: dict = Body(...)):
    """Adiciona ticker manualmente à sell all list criando posição fictícia."""
    token = payload.get("token")
    ticker = payload.get("ticker")
    
    if token != FINVIZ_UPDATE_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")
    
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker required")
    
    try:
        # Criar posição fictícia para o ticker
        position_id = await db_manager.create_manual_position(
            ticker=ticker.upper(),
            source="manual_addition"
        )
        
        # Broadcast update via WebSocket
        await comm_engine.broadcast({
            "type": "sell_all_list_update",
            "data": await get_sell_all_list_data()
        })
        
        return {
            "message": f"Ticker {ticker} added to sell all list",
            "position_id": position_id,
            "ticker": ticker.upper()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error adding ticker: {str(e)}")
```

**Nova função no DBManager**:
```python
async def create_manual_position(self, ticker: str, source: str = "manual") -> int:
    """Cria posição fictícia para ticker adicionado manualmente."""
    async with self.get_session() as session:
        # Criar signal fictício primeiro
        manual_signal = Signal(
            signal_id=uuid.uuid4(),
            ticker=ticker,
            signal_type="manual_sell_entry",
            location=SignalLocationEnum.COMPLETED.value,
            status=SignalStatusEnum.APPROVED.value,
            timestamp=datetime.utcnow(),
            entry_price=0.0,  # Fictício
            source=source
        )
        session.add(manual_signal)
        await session.flush()
        
        # Criar posição fictícia
        manual_position = Position(
            ticker=ticker,
            status=PositionStatusEnum.OPEN.value,
            entry_signal_id=manual_signal.signal_id,
            opened_at=datetime.utcnow()
        )
        session.add(manual_position)
        await session.flush()
        
        return manual_position.id
```

**Impacto**: ✅ Permite adicionar tickers manualmente, criando posições rastreáveis

### **FASE 2: ACOMPANHAMENTO DE ORDENS EM TEMPO REAL (3-5 dias)**

#### **2.1 Criar Interface de Ordens Abertas**

**Ação**: Nova seção no admin.html para acompanhar ordens

```html
<!-- Nova seção no admin.html -->
<div class="card mt-4">
    <div class="card-header">
        <h5 class="card-title mb-0">
            <i class="bi bi-graph-up-arrow"></i> Ordens em Tempo Real
        </h5>
    </div>
    <div class="card-body">
        <!-- Filtros -->
        <div class="row mb-3">
            <div class="col-md-4">
                <select id="orderStatusFilter" class="form-select">
                    <option value="all">Todas as Ordens</option>
                    <option value="open">Abertas</option>
                    <option value="closing">Fechando</option>
                    <option value="closed">Fechadas</option>
                </select>
            </div>
            <div class="col-md-4">
                <input type="text" id="tickerFilter" class="form-control" placeholder="Filtrar por ticker...">
            </div>
            <div class="col-md-4">
                <button id="refreshOrdersBtn" class="btn btn-outline-primary">
                    <i class="bi bi-arrow-clockwise"></i> Atualizar
                </button>
            </div>
        </div>
        
        <!-- Contadores em Tempo Real -->
        <div class="row mb-3">
            <div class="col-md-3">
                <div class="alert alert-success mb-0">
                    <strong id="openOrdersCount">0</strong> Abertas
                </div>
            </div>
            <div class="col-md-3">
                <div class="alert alert-warning mb-0">
                    <strong id="closingOrdersCount">0</strong> Fechando
                </div>
            </div>
            <div class="col-md-3">
                <div class="alert alert-secondary mb-0">
                    <strong id="closedTodayCount">0</strong> Hoje
                </div>
            </div>
            <div class="col-md-3">
                <div class="alert alert-info mb-0">
                    <strong id="totalValueCount">$0</strong> Total
                </div>
            </div>
        </div>
        
        <!-- Lista de Ordens -->
        <div class="table-responsive">
            <table class="table table-hover" id="ordersTable">
                <thead>
                    <tr>
                        <th>Ticker</th>
                        <th>Status</th>
                        <th>Entrada</th>
                        <th>Saída</th>
                        <th>Duração</th>
                        <th>Valor</th>
                        <th>Ações</th>
                    </tr>
                </thead>
                <tbody id="ordersTableBody">
                    <tr>
                        <td colspan="7" class="text-center">
                            <div class="spinner-border spinner-border-sm" role="status">
                                <span class="visually-hidden">Carregando...</span>
                            </div>
                            Carregando ordens...
                        </td>
                    </tr>
                </tbody>
            </table>
        </div>
    </div>
</div>
```

#### **2.2 Implementar API para Ordens**

**Ação**: Novos endpoints para dados de ordens

```python
# Novos endpoints em main.py
@app.get("/admin/orders")
async def get_orders(status: Optional[str] = None, ticker: Optional[str] = None):
    """Retorna lista de ordens/posições com filtros."""
    try:
        orders = await db_manager.get_positions_with_details(
            status_filter=status,
            ticker_filter=ticker
        )
        
        return {
            "orders": orders,
            "count": len(orders),
            "timestamp": time.time()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting orders: {str(e)}")

@app.get("/admin/orders/stats")
async def get_orders_stats():
    """Retorna estatísticas de ordens em tempo real."""
    try:
        stats = await db_manager.get_positions_statistics()
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting stats: {str(e)}")

@app.post("/admin/orders/{position_id}/close")
async def close_order_manually(position_id: int, payload: dict = Body(...)):
    """Fecha ordem manualmente."""
    token = payload.get("token")
    if token != FINVIZ_UPDATE_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")
    
    try:
        result = await db_manager.close_position_manually(position_id)
        
        # Broadcast update
        await comm_engine.broadcast({
            "type": "order_status_change",
            "data": {
                "position_id": position_id,
                "new_status": "closed",
                "timestamp": time.time()
            }
        })
        
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error closing order: {str(e)}")
```

#### **2.3 Implementar Atualizações WebSocket Inteligentes**

**Ação**: Sistema de broadcast inteligente para mudanças de status

```python
# Nova função no DBManager
async def get_positions_with_details(self, status_filter=None, ticker_filter=None):
    """Retorna posições com detalhes completos para interface."""
    async with self.get_session() as session:
        query = select(
            Position.id,
            Position.ticker,
            Position.status,
            Position.opened_at,
            Position.closed_at,
            Signal.entry_price.label('entry_price'),
            func.extract('epoch', Position.closed_at - Position.opened_at).label('duration_seconds')
        ).join(
            Signal, Position.entry_signal_id == Signal.signal_id
        )
        
        if status_filter and status_filter != 'all':
            query = query.where(Position.status == status_filter)
        
        if ticker_filter:
            query = query.where(Position.ticker.ilike(f'%{ticker_filter}%'))
        
        result = await session.execute(query.order_by(Position.opened_at.desc()))
        
        orders = []
        for row in result:
            duration_str = "—"
            if row.duration_seconds:
                hours = int(row.duration_seconds // 3600)
                minutes = int((row.duration_seconds % 3600) // 60)
                duration_str = f"{hours}h {minutes}m"
            
            orders.append({
                "id": row.id,
                "ticker": row.ticker,
                "status": row.status,
                "opened_at": row.opened_at.isoformat() if row.opened_at else None,
                "closed_at": row.closed_at.isoformat() if row.closed_at else None,
                "entry_price": float(row.entry_price) if row.entry_price else 0.0,
                "duration": duration_str,
                "duration_seconds": row.duration_seconds or 0
            })
        
        return orders

async def get_positions_statistics(self):
    """Retorna estatísticas em tempo real das posições."""
    async with self.get_session() as session:
        # Contar por status
        status_counts = await session.execute(
            select(
                Position.status,
                func.count(Position.id).label('count')
            ).group_by(Position.status)
        )
        
        stats = {"open": 0, "closing": 0, "closed": 0}
        for row in status_counts:
            stats[row.status] = row.count
        
        # Posições fechadas hoje
        today = datetime.utcnow().date()
        closed_today = await session.execute(
            select(func.count(Position.id)).where(
                and_(
                    Position.status == PositionStatusEnum.CLOSED.value,
                    func.date(Position.closed_at) == today
                )
            )
        )
        stats["closed_today"] = closed_today.scalar() or 0
        
        return {
            "stats": stats,
            "timestamp": time.time()
        }
```

#### **2.4 JavaScript para Interface Dinâmica**

**Ação**: Sistema de atualização em tempo real

```javascript
// Nova funcionalidade no admin.html
let ordersData = [];
let ordersFilters = { status: 'all', ticker: '' };

// Carregar ordens inicialmente
async function loadOrders() {
    try {
        const params = new URLSearchParams();
        if (ordersFilters.status !== 'all') params.append('status', ordersFilters.status);
        if (ordersFilters.ticker) params.append('ticker', ordersFilters.ticker);
        
        const response = await fetch(`/admin/orders?${params}`);
        const data = await response.json();
        
        ordersData = data.orders;
        updateOrdersTable();
        updateOrdersStats();
    } catch (error) {
        console.error('Error loading orders:', error);
    }
}

// Atualizar tabela de ordens
function updateOrdersTable() {
    const tbody = document.getElementById('ordersTableBody');
    
    if (ordersData.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted">Nenhuma ordem encontrada</td></tr>';
        return;
    }
    
    tbody.innerHTML = ordersData.map(order => {
        const statusBadge = getStatusBadge(order.status);
        const actionButton = getActionButton(order);
        
        return `
            <tr id="order-${order.id}" class="order-row" data-status="${order.status}">
                <td><strong>${order.ticker}</strong></td>
                <td>${statusBadge}</td>
                <td>${formatDateTime(order.opened_at)}</td>
                <td>${order.closed_at ? formatDateTime(order.closed_at) : '—'}</td>
                <td>${order.duration}</td>
                <td>$${order.entry_price.toFixed(2)}</td>
                <td>${actionButton}</td>
            </tr>
        `;
    }).join('');
}

// Badge de status com cores
function getStatusBadge(status) {
    const badges = {
        'open': '<span class="badge bg-success">Aberta</span>',
        'closing': '<span class="badge bg-warning">Fechando</span>',
        'closed': '<span class="badge bg-secondary">Fechada</span>'
    };
    return badges[status] || `<span class="badge bg-info">${status}</span>`;
}

// Botão de ação baseado no status
function getActionButton(order) {
    if (order.status === 'open') {
        return `<button class="btn btn-sm btn-outline-danger" onclick="closeOrderManually(${order.id})">
                    <i class="bi bi-x-circle"></i> Fechar
                </button>`;
    }
    return '—';
}

// Fechar ordem manualmente
async function closeOrderManually(positionId) {
    if (!confirm('Tem certeza que deseja fechar esta ordem manualmente?')) return;
    
    try {
        const token = localStorage.getItem('admin_token') || prompt('Enter admin token:');
        
        const response = await fetch(`/admin/orders/${positionId}/close`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token })
        });
        
        if (response.ok) {
            showAlert('Ordem fechada com sucesso', 'success');
            loadOrders(); // Recarregar lista
        } else {
            throw new Error(`HTTP ${response.status}`);
        }
    } catch (error) {
        showAlert(`Erro ao fechar ordem: ${error.message}`, 'danger');
    }
}

// Atualizar estatísticas
async function updateOrdersStats() {
    try {
        const response = await fetch('/admin/orders/stats');
        const data = await response.json();
        
        document.getElementById('openOrdersCount').textContent = data.stats.open || 0;
        document.getElementById('closingOrdersCount').textContent = data.stats.closing || 0;
        document.getElementById('closedTodayCount').textContent = data.stats.closed_today || 0;
        
    } catch (error) {
        console.error('Error updating stats:', error);
    }
}

// Atualização via WebSocket
function handleWebSocketMessage(data) {
    // ... código existente ...
    
    // Nova funcionalidade para ordens
    if (data.type === 'order_status_change') {
        const { position_id, new_status } = data.data;
        
        // Atualizar linha específica na tabela
        const row = document.getElementById(`order-${position_id}`);
        if (row) {
            row.setAttribute('data-status', new_status);
            // Animar mudança
            row.classList.add('table-warning');
            setTimeout(() => {
                row.classList.remove('table-warning');
                loadOrders(); // Recarregar para dados completos
            }, 1000);
        }
        
        // Mostrar notificação
        showAlert(`Ordem ${position_id} alterada para: ${new_status}`, 'info');
    }
    
    if (data.type === 'sell_all_list_update') {
        loadSellAllList(); // Função existente
        loadOrders(); // Recarregar ordens também
    }
}

// Event listeners
document.getElementById('orderStatusFilter').addEventListener('change', (e) => {
    ordersFilters.status = e.target.value;
    loadOrders();
});

document.getElementById('tickerFilter').addEventListener('input', (e) => {
    ordersFilters.ticker = e.target.value;
    // Debounce para evitar muitas requests
    clearTimeout(window.tickerFilterTimeout);
    window.tickerFilterTimeout = setTimeout(loadOrders, 500);
});

document.getElementById('refreshOrdersBtn').addEventListener('click', loadOrders);

// Carregar ordens quando página carrega
window.addEventListener('load', () => {
    loadOrders();
    // Atualizar a cada 30 segundos
    setInterval(updateOrdersStats, 30000);
});
```

**Impacto**: ✅ Interface completa para acompanhar ordens em tempo real com excelente UX

### **FASE 3: MIGRAÇÃO PARA FONTE DE VERDADE ÚNICA (1-2 semanas)**

#### **3.1 Migrar Signal Metrics para PostgreSQL**

**Ação**: Criar tabela de métricas no banco

```sql
-- Nova tabela para métricas
CREATE TABLE IF NOT EXISTS signal_metrics (
    id BIGSERIAL PRIMARY KEY,
    metric_name VARCHAR(100) NOT NULL,
    metric_value BIGINT NOT NULL DEFAULT 0,
    last_updated TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    UNIQUE(metric_name)
);

-- Índice para performance
CREATE INDEX IF NOT EXISTS idx_signal_metrics_name ON signal_metrics(metric_name);

-- Inserir métricas iniciais
INSERT INTO signal_metrics (metric_name, metric_value) VALUES
    ('signals_approved', 0),
    ('signals_rejected', 0),
    ('signals_forwarded_success', 0),
    ('signals_forwarded_error', 0),
    ('processing_workers_active', 0),
    ('forwarding_workers_active', 0)
ON CONFLICT (metric_name) DO NOTHING;
```

**Nova classe no DBManager**:
```python
async def increment_metric(self, metric_name: str, increment: int = 1):
    """Incrementa métrica de forma thread-safe."""
    async with self.get_session() as session:
        await session.execute(
            text("""
                INSERT INTO signal_metrics (metric_name, metric_value, last_updated)
                VALUES (:name, :increment, NOW())
                ON CONFLICT (metric_name) 
                DO UPDATE SET 
                    metric_value = signal_metrics.metric_value + :increment,
                    last_updated = NOW()
            """),
            {"name": metric_name, "increment": increment}
        )

async def get_all_metrics(self) -> Dict[str, int]:
    """Retorna todas as métricas atuais."""
    async with self.get_session() as session:
        result = await session.execute(
            select(SignalMetric.metric_name, SignalMetric.metric_value)
        )
        return {row.metric_name: row.metric_value for row in result}
```

**Substituir chamadas em main.py**:
```python
# ANTES:
shared_state["signal_metrics"]["signals_approved"] += 1

# DEPOIS:
await db_manager.increment_metric("signals_approved", 1)
```

#### **3.2 Migrar Tickers do Finviz para PostgreSQL**

**Ação**: Criar tabela de tickers

```sql
CREATE TABLE IF NOT EXISTS finviz_tickers (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    added_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE(ticker)
);

CREATE INDEX IF NOT EXISTS idx_finviz_tickers_active ON finviz_tickers(is_active);
CREATE INDEX IF NOT EXISTS idx_finviz_tickers_ticker ON finviz_tickers(ticker);
```

**Modificar FinvizEngine**:
```python
# Em finviz_engine.py - substituir shared_state["tickers"]
async def update_tickers_in_database(self, new_tickers: Set[str]):
    """Atualiza tickers no banco em vez de memória."""
    from main import db_manager
    
    # Marcar todos como inativos
    await db_manager.deactivate_all_tickers()
    
    # Inserir/ativar novos tickers
    for ticker in new_tickers:
        await db_manager.upsert_ticker(ticker, active=True)

async def get_active_tickers(self) -> Set[str]:
    """Busca tickers ativos do banco."""
    from main import db_manager
    return await db_manager.get_active_tickers()
```

#### **3.3 Migrar Rate Limiter Metrics para PostgreSQL**

**Ação**: Usar mesma tabela signal_metrics

```python
# Em webhook_rate_limiter.py - substituir shared_state
async def update_rate_limiter_metrics(self):
    """Atualiza métricas no banco."""
    from main import db_manager
    
    await db_manager.set_metric("webhook_tokens_available", self.rate_limit_semaphore._value)
    await db_manager.set_metric("webhook_requests_this_minute", len(self.requests_last_minute))
    await db_manager.increment_metric("webhook_total_requests_limited", 0)  # Não incrementa, só garante existência
```

**Impacto**: ✅ Estado único e consistente no PostgreSQL, elimina race conditions

### **FASE 4: OTIMIZAÇÕES E MELHORIAS (1 semana)**

#### **4.1 Cache Inteligente para Performance**

**Ação**: Implementar cache Redis-like em PostgreSQL

```sql
CREATE TABLE IF NOT EXISTS cache_entries (
    cache_key VARCHAR(255) PRIMARY KEY,
    cache_value JSONB NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache_entries(expires_at);
```

#### **4.2 Limpeza Automática Inteligente**

**Ação**: Melhorar cleanup baseado em uso real

```python
async def intelligent_cleanup(self, retention_days: int = 30):
    """Limpeza inteligente preservando dados importantes."""
    async with self.get_session() as session:
        # Manter sempre as últimas 1000 posições
        # Manter sempre os últimos 7 dias
        # Aplicar retention_days apenas para dados mais antigos
        
        cleanup_date = datetime.utcnow() - timedelta(days=max(retention_days, 7))
        
        # Limpeza escalonada baseada em importância
        await self._cleanup_old_events(session, cleanup_date)
        await self._cleanup_completed_positions(session, cleanup_date)
        await self._cleanup_cache_entries(session)
```

#### **4.3 Monitoring e Alertas**

**Ação**: Sistema de alertas baseado em métricas do banco

```python
async def check_system_health(self):
    """Verifica saúde do sistema baseado em métricas."""
    metrics = await self.get_all_metrics()
    
    alerts = []
    
    # Alerta se muitos erros
    error_rate = metrics.get("signals_forwarded_error", 0) / max(metrics.get("signals_forwarded_success", 1), 1)
    if error_rate > 0.1:  # Mais de 10% de erro
        alerts.append({
            "type": "error_rate_high",
            "message": f"Taxa de erro alta: {error_rate:.2%}",
            "severity": "warning"
        })
    
    # Alerta se workers travados
    if metrics.get("processing_workers_active", 0) == 0 and time.time() - self.last_signal_time < 300:
        alerts.append({
            "type": "workers_stuck",
            "message": "Workers podem estar travados",
            "severity": "critical"
        })
    
    return alerts
```

---

## 📊 RESUMO DE BENEFITS

### **Benefícios Imediatos (Fase 1)**
- ✅ Botão "Limpar Banco" funcionando
- ✅ Possibilidade de adicionar tickers manualmente
- ✅ Zero perda de dados por constraints

### **Benefícios de Médio Prazo (Fase 2)**
- ✅ Acompanhamento completo de ordens em tempo real
- ✅ Interface moderna e intuitiva
- ✅ WebSocket para atualizações instantâneas
- ✅ Capacidade de fechar ordens manualmente

### **Benefícios de Longo Prazo (Fases 3-4)**
- ✅ Fonte de verdade única (PostgreSQL)
- ✅ Eliminação de race conditions
- ✅ Performance melhorada com cache inteligente
- ✅ Sistema de alertas proativo
- ✅ Robustez operacional máxima

---

## 🎯 PRIORIZAÇÃO RECOMENDADA

### **CRÍTICO (Implementar AGORA)**
1. **Corrigir botão limpar banco** - 2 horas
2. **Criar endpoint para adicionar tickers** - 4 horas

### **ALTO (Esta semana)**
3. **Interface de ordens em tempo real** - 2 dias
4. **WebSocket para atualizações** - 1 dia

### **MÉDIO (Próximas 2 semanas)**
5. **Migrar metrics para PostgreSQL** - 3 dias
6. **Migrar tickers para PostgreSQL** - 2 dias

### **BAIXO (Quando possível)**
7. **Cache inteligente** - 2 dias
8. **Sistema de alertas** - 3 dias

---

## 🔍 OUTROS PONTOS QUE USAM MEMÓRIA

### **Identificados para Migração Futura**

1. **Filas de Processamento**
   - `approved_signal_queue` → Usar PostgreSQL com status "queued_processing"
   - `forwarding_signal_queue` → Usar PostgreSQL com status "queued_forwarding"
   - **Benefit**: Persistência entre restarts, melhor debugging

2. **Signal Trackers**
   - `shared_state["signal_trackers"]` → Usar tabela de audit trail existente
   - **Benefit**: Histórico completo, search capabilities

3. **Configuration Cache**
   - Configurações do Finviz → Usar PostgreSQL config table
   - Rate limiter settings → Usar PostgreSQL config table
   - **Benefit**: Configurações persistentes, auditáveis

4. **Temporary State**
   - Worker status → Usar PostgreSQL com TTL
   - Health check results → Usar PostgreSQL cache table
   - **Benefit**: Visibilidade completa do sistema

5. **Metrics Agregadas**
   - Top performers → Computed views no PostgreSQL
   - Historical summaries → Materialized views
   - **Benefit**: Queries complexas, relatórios avançados

---

## 💡 CONSIDERAÇÕES FINAIS

Este plano foca exclusivamente em **robustez operacional** sem backup. O objetivo é criar um sistema que:

1. **Nunca perde dados durante operação normal**
2. **Permite operação manual quando necessário** 
3. **Fornece visibilidade completa do estado**
4. **Usa o banco como fonte de verdade única**
5. **Atualiza interface em tempo real**
6. **É fácil de operar e debugar**

A implementação deve ser **incremental** e **testável** em cada fase, garantindo que o sistema continue operacional durante as mudanças.

**Próximo passo recomendado**: Implementar Fase 1 (correção do botão limpar banco e endpoint para adicionar tickers) ainda hoje.

---

*Plano criado em: 8 de julho de 2025*  
*Foco: Robustez operacional e UX otimizada*  
*Escopo: Sistema completo sem dependência de backup*

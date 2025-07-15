# Plano Detalhado: Sistema Multi-URL Finviz + Admin Actions Log

## 1. Análise da Aplicação Atual

### Arquitetura Identificada

A aplicação Trading Signal Processor é um sistema robusto com as seguintes características principais:

1. **Backend FastAPI** (`main.py`): API principal com endpoints para administração e processamento de sinais
2. **FinvizEngine** (`finviz_engine.py`): Engine dedicado para buscar e processar dados do Finviz
3. **Interface Admin** (`templates/admin.html`): Dashboard administrativo completo com Bootstrap 5
4. **Sistema de Configuração**: Atualmente usa arquivo JSON (`finviz_config.json`) para configurações
5. **Banco PostgreSQL**: Sistema robusto de tracking de sinais com modelos SQLAlchemy
6. **Sistema de WebSocket** (`comm_engine.py`): Comunicação em tempo real para atualizações administrativas
7. **Sistema de Auditoria Existente**: Trail completo do lifecycle de signals via DBManager

### **NOVA FUNCIONALIDADE**: Admin Actions Log System
Baseado na investigação da arquitetura existente, implementaremos um sistema abrangente de log de ações administrativas que complementa o sistema de auditoria de signals existente:

#### Características do Admin Actions Log:
- **Dual Audit Trail**: Separação clara entre lifecycle de signals e ações administrativas
- **Autenticação Integrada**: Leveraging do sistema de token authentication existente
- **Real-time Updates**: Integração com o sistema WebSocket `comm_engine` existente
- **UI Consistency**: Seguindo padrões do admin dashboard Bootstrap 5 existente
- **Database Integration**: Utilizando padrões do DBManager para persistência

### Sistema de Configuração Atual

O sistema atual funciona da seguinte forma:

1. **Arquivo de Configuração**: `finviz_config.json` armazena:
   - `finviz_url`: URL única ativa
   - `top_n`: Número de tickers para buscar
   - `refresh_interval_sec`: Intervalo de atualização
   - Configurações de reprocessamento

2. **Engine**: `FinvizEngine` carrega e atualiza a configuração via:
   - `load_finviz_config()`: Carrega do arquivo JSON
   - `persist_finviz_config_from_dict()`: Salva no arquivo JSON
   - Endpoint `/finviz/config`: API para atualizar configuração

3. **Interface**: Modal de configuração no admin permite alterar a URL atual

## 2. Proposta de Implementação

### 2.1 Estrutura de Dados para Múltiplas URLs

#### Modelo de Banco de Dados

Criar nova tabela `finviz_urls` para armazenar múltiplas URLs como **estratégias completas**:

```sql
CREATE TABLE finviz_urls (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    url TEXT NOT NULL,
    description TEXT,
    
    -- Configurações de estratégia (completa preset configuration)
    top_n INTEGER NOT NULL DEFAULT 100,
    refresh_interval_sec INTEGER NOT NULL DEFAULT 10,
    reprocess_enabled BOOLEAN DEFAULT FALSE,
    reprocess_window_seconds INTEGER DEFAULT 300,
    respect_sell_chronology_enabled BOOLEAN DEFAULT TRUE,
    sell_chronology_window_seconds INTEGER DEFAULT 300,
    
    -- Controle de ativação e timestamping
    is_active BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_used_at TIMESTAMP WITH TIME ZONE
);

-- Garantir que apenas uma URL seja ativa por vez
CREATE UNIQUE INDEX idx_finviz_urls_active 
ON finviz_urls (is_active) 
WHERE is_active = TRUE;

-- Índices para performance
CREATE INDEX idx_finviz_urls_name ON finviz_urls (name);
CREATE INDEX idx_finviz_urls_created_at ON finviz_urls (created_at);
```

#### Modelo SQLAlchemy

```python
class FinvizUrl(Base):
    __tablename__ = "finviz_urls"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True, index=True)
    url = Column(Text, nullable=False)
    description = Column(Text)
    
    # Configurações completas de estratégia
    top_n = Column(Integer, nullable=False, default=100)
    refresh_interval_sec = Column(Integer, nullable=False, default=10)
    reprocess_enabled = Column(Boolean, default=False)
    reprocess_window_seconds = Column(Integer, default=300)
    respect_sell_chronology_enabled = Column(Boolean, default=True)
    sell_chronology_window_seconds = Column(Integer, default=300)
    
    # Controle de ativação
    is_active = Column(Boolean, default=False, index=True)
    created_at = Column(TIMESTAMP(timezone=True), default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), default=func.now(), onupdate=func.now())
    last_used_at = Column(TIMESTAMP(timezone=True))
```

### 2.2 Substituição Completa do Sistema Atual

#### Estratégia Única

1. **Banco de Dados como Única Fonte**: Remover completamente a dependência do arquivo JSON
2. **Strategy Presets**: Cada entrada na tabela `finviz_urls` é uma estratégia completa com todos os 7 parâmetros de configuração
3. **Inicialização Simples**: Se não houver URLs no banco, criar uma estratégia padrão básica
4. **Sistema Unificado**: Todas as configurações vêm apenas do banco de dados
5. **Eliminação de Arquivo**: `finviz_config.json` será completamente removido do sistema

#### Inicialização Robusta

```python
async def ensure_default_finviz_url():
    """Garante que sempre existe pelo menos uma URL ativa no sistema"""
    try:
        # Verifica se existe alguma URL no banco
        urls_count = await db_manager.count_finviz_urls()
        
        if urls_count == 0:
            # Cria estratégia padrão completa se não existir nenhuma
            default_strategy = {
                "name": "Default Strategy",
                "url": "https://finviz.com/screener.ashx?v=111&ft=4",
                "description": "Estratégia padrão do sistema com configurações básicas",
                "top_n": 100,
                "refresh_interval_sec": 10,
                "reprocess_enabled": False,
                "reprocess_window_seconds": 300,
                "respect_sell_chronology_enabled": True,
                "sell_chronology_window_seconds": 300,
                "is_active": True
            }
            await db_manager.create_finviz_url(**default_strategy)
            _logger.info("Created default Finviz strategy preset")
        else:
            # Garante que pelo menos uma URL está ativa
            active_url = await db_manager.get_active_finviz_url()
            if not active_url:
                # Se nenhuma está ativa, ativa a primeira
                first_url = await db_manager.get_first_finviz_url()
                if first_url:
                    await db_manager.set_active_finviz_url(first_url['id'])
                    _logger.info(f"Activated first URL: {first_url['name']}")
    except Exception as e:
        _logger.error(f"Error ensuring default URL: {e}")
        raise
```

### 2.3 Extensões no DBManager

#### Novos Métodos

```python
class DBManager:
    async def create_finviz_url(self, name: str, url: str, description: str = None, 
                              top_n: int = 100, refresh_interval_sec: int = 10,
                              reprocess_enabled: bool = False, reprocess_window_seconds: int = 300,
                              respect_sell_chronology_enabled: bool = True, 
                              sell_chronology_window_seconds: int = 300,
                              is_active: bool = False) -> int:
        """Cria uma nova estratégia Finviz completa"""
        
    async def get_finviz_urls(self) -> List[Dict[str, Any]]:
        """Retorna todas as estratégias cadastradas"""
        
    async def get_active_finviz_url(self) -> Optional[Dict[str, Any]]:
        """Retorna a estratégia ativa atual (com todos os parâmetros)"""
        
    async def get_first_finviz_url(self) -> Optional[Dict[str, Any]]:
        """Retorna a primeira estratégia cadastrada (para fallback)"""
        
    async def set_active_finviz_url(self, url_id: int) -> bool:
        """Define uma estratégia como ativa (desativa as outras) - TRANSAÇÃO ATÔMICA"""
        
    async def update_finviz_url(self, url_id: int, **kwargs) -> bool:
        """Atualiza uma estratégia existente (qualquer parâmetro)"""
        
    async def delete_finviz_url(self, url_id: int) -> bool:
        """Remove uma estratégia (não pode ser a ativa)"""
        
    async def count_finviz_urls(self) -> int:
        """Conta o número de estratégias cadastradas"""
        
    async def update_finviz_url_last_used(self, url_id: int) -> None:
        """Atualiza timestamp de último uso de uma estratégia"""
```

### 2.4 Modificações no FinvizEngine

#### Integração com Banco de Dados

```python
class FinvizEngine:
    def __init__(self, shared_state: Dict[str, Any], admin_ws_broadcaster: callable, db_manager):
        # ... código existente ...
        self.db_manager = db_manager  # Adicionar referência ao DBManager
        
    async def _load_config_from_db(self) -> None:
        """Carrega configuração ativa do banco de dados - ÚNICA FONTE"""
        active_strategy = await self.db_manager.get_active_finviz_url()
        if not active_strategy:
            raise RuntimeError("No active Finviz strategy found in database")
            
        # Constrói FinvizConfig com TODOS os 7 parâmetros do banco
        config_data = {
            "url": active_strategy["url"],
            "top_n": active_strategy["top_n"],
            "refresh": active_strategy["refresh_interval_sec"],
            "reprocess_enabled": active_strategy["reprocess_enabled"],
            "reprocess_window_seconds": active_strategy["reprocess_window_seconds"],
            "respect_sell_chronology_enabled": active_strategy["respect_sell_chronology_enabled"],
            "sell_chronology_window_seconds": active_strategy["sell_chronology_window_seconds"]
        }
        
        self._current_config = FinvizConfig(**config_data)
        await self.db_manager.update_finviz_url_last_used(active_strategy["id"])
        _logger.info(f"Loaded strategy from DB: {active_strategy['name']} - URL: {active_strategy['url'][:50]}...")
    
    async def switch_active_url(self, url_id: int) -> bool:
        """Troca a estratégia ativa em tempo real - OPERAÇÃO ATÔMICA"""
        try:
            # Operação atômica no banco
            success = await self.db_manager.set_active_finviz_url(url_id)
            if success:
                # Recarrega configuração do banco
                await self._load_config_from_db()
                # Força atualização do engine
                self.cfg_updated_event.set()
                
                # Broadcast para interface
                active_strategy = await self.db_manager.get_active_finviz_url()
                await self.admin_ws_broadcaster("finviz_strategy_changed", {
                    "active_strategy": active_strategy
                })
                
                return True
        except Exception as e:
            _logger.error(f"Error switching active strategy: {e}")
            
        return False
```

## 2.6 NOVO: Admin Actions Log System

### Modelo de Banco de Dados para Admin Actions

```sql
CREATE TABLE admin_actions (
    action_id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    action_type VARCHAR(50) NOT NULL,  -- 'config_update', 'engine_control', 'url_management', etc.
    action_name VARCHAR(100) NOT NULL,  -- 'pause_engine', 'update_finviz_config', 'switch_url', etc.
    admin_token TEXT,  -- Token admin para auditoria direta
    ip_address INET,
    user_agent TEXT,
    details JSONB,  -- Detalhes específicos da ação (old_value, new_value, etc.)
    target_resource VARCHAR(100),  -- Recurso afetado (URL ID, config key, etc.)
    success BOOLEAN NOT NULL DEFAULT TRUE,
    error_message TEXT,
    execution_time_ms INTEGER,
    
    -- Indexação para queries rápidas
    CONSTRAINT admin_actions_action_type_check CHECK (action_type IN (
        'config_update', 'engine_control', 'url_management', 'metrics_reset',
        'database_operation', 'rate_limiter_control', 'order_management',
        'file_import', 'manual_override', 'system_maintenance'
    ))
);

-- Índices para performance
CREATE INDEX idx_admin_actions_timestamp ON admin_actions(timestamp);
CREATE INDEX idx_admin_actions_type ON admin_actions(action_type);
CREATE INDEX idx_admin_actions_admin_token ON admin_actions(admin_token);
CREATE INDEX idx_admin_actions_success ON admin_actions(success);
```

### Modelo SQLAlchemy

```python
class AdminAction(Base):
    __tablename__ = "admin_actions"
    
    action_id = Column(BigInteger, primary_key=True, autoincrement=True)
    timestamp = Column(TIMESTAMP(timezone=True), default=func.now(), nullable=False)
    action_type = Column(String(50), nullable=False, index=True)
    action_name = Column(String(100), nullable=False)
    admin_token = Column(Text, index=True)  # Token admin direto para auditoria
    ip_address = Column(postgresql.INET)
    user_agent = Column(Text)
    details = Column(postgresql.JSONB)
    target_resource = Column(String(100))
    success = Column(Boolean, default=True, nullable=False, index=True)
    error_message = Column(Text)
    execution_time_ms = Column(Integer)
```

### Extensões no DBManager para Admin Actions

```python
class DBManager:
    async def log_admin_action(
        self,
        action_type: str,
        action_name: str,
        admin_token: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        target_resource: Optional[str] = None,
        success: bool = True,
        error_message: Optional[str] = None,
        execution_time_ms: Optional[int] = None
    ) -> int:
        """Log de ação administrativa com token direto para auditoria"""
        
    async def get_admin_actions_log(
        self,
        limit: int = 100,
        offset: int = 0,
        action_type_filter: Optional[str] = None,
        admin_token_filter: Optional[str] = None,
        hours: Optional[int] = None,
        success_filter: Optional[bool] = None
    ) -> List[Dict[str, Any]]:
        """Retorna log de ações administrativas com filtros"""
        
    async def get_admin_actions_count(
        self,
        action_type_filter: Optional[str] = None,
        admin_token_filter: Optional[str] = None,
        hours: Optional[int] = None,
        success_filter: Optional[bool] = None
    ) -> int:
        """Conta total de ações administrativas com filtros"""
        
    async def get_admin_actions_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Estatísticas resumidas das ações administrativas"""
```

### Decorator para Auto-Logging de Ações Admin

```python
import time
from functools import wraps
from fastapi import Request

def log_admin_action(action_type: str, action_name: str, target_resource: str = None):
    """Decorator para automaticamente logar ações administrativas"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            request = None
            admin_token = None
            
            # Extrair request e token dos argumentos
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
            
            # Extrair token do payload
            if 'payload' in kwargs and isinstance(kwargs['payload'], dict):
                admin_token = kwargs['payload'].get('token')
            
            # Dados da requisição
            ip_address = request.client.host if request else None
            user_agent = request.headers.get('user-agent') if request else None
            
            try:
                # Executar função original
                result = await func(*args, **kwargs)
                
                # Calcular tempo de execução
                execution_time_ms = int((time.time() - start_time) * 1000)
                
                # Log de sucesso
                if admin_token:
                    await db_manager.log_admin_action(
                        action_type=action_type,
                        action_name=action_name,
                        admin_token=admin_token,  # Token direto sem hash
                        ip_address=ip_address,
                        user_agent=user_agent,
                        target_resource=target_resource,
                        success=True,
                        execution_time_ms=execution_time_ms
                    )
                
                return result
                
            except Exception as e:
                # Calcular tempo de execução mesmo em erro
                execution_time_ms = int((time.time() - start_time) * 1000)
                
                # Log de erro
                if admin_token:
                    await db_manager.log_admin_action(
                        action_type=action_type,
                        action_name=action_name,
                        admin_token=admin_token,  # Token direto sem hash
                        ip_address=ip_address,
                        user_agent=user_agent,
                        target_resource=target_resource,
                        success=False,
                        error_message=str(e),
                        execution_time_ms=execution_time_ms
                    )
                
                raise
                
        return wrapper
    return decorator
```

### Integração com WebSocket (comm_engine)

```python
# Extensão do comm_engine.py
class CommunicationEngine:
    async def trigger_admin_action_update(self, action_data: Dict[str, Any]):
        """Trigger admin action log update broadcast"""
        await self.broadcast("admin_action_logged", action_data)
```

### 2.7 Novos Endpoints da API

#### Endpoints de Gerenciamento de Estratégias Finviz

```python
@app.get("/admin/finviz/strategies")
async def get_finviz_strategies():
    """Lista todas as estratégias Finviz com configurações completas"""

@app.post("/admin/finviz/strategies")
async def create_finviz_strategy(payload: dict = Body(...)):
    """Cria uma nova estratégia Finviz completa
    
    Payload esperado:
    {
        "token": "admin_token",
        "name": "Strategy Name",
        "url": "https://finviz.com/screener.ashx?...",
        "description": "Strategy description",
        "top_n": 100,
        "refresh_interval_sec": 10,
        "reprocess_enabled": false,
        "reprocess_window_seconds": 300,
        "respect_sell_chronology_enabled": true,
        "sell_chronology_window_seconds": 300
    }
    """

@app.put("/admin/finviz/strategies/{strategy_id}")
async def update_finviz_strategy(strategy_id: int, payload: dict = Body(...)):
    """Atualiza uma estratégia existente (qualquer campo)"""

@app.delete("/admin/finviz/strategies/{strategy_id}")
async def delete_finviz_strategy(strategy_id: int, payload: dict = Body(...)):
    """Remove uma estratégia (não pode ser a ativa)"""

@app.post("/admin/finviz/strategies/{strategy_id}/activate")
async def activate_finviz_strategy(strategy_id: int, payload: dict = Body(...)):
    """Ativa uma estratégia específica (troca em tempo real)"""

@app.get("/admin/finviz/strategies/active")
async def get_active_finviz_strategy():
    """Retorna a estratégia ativa atual com todas as configurações"""

@app.post("/admin/finviz/strategies/{strategy_id}/duplicate")
async def duplicate_finviz_strategy(strategy_id: int, payload: dict = Body(...)):
    """Duplica uma estratégia existente com novo nome"""

```

#### Endpoints para Admin Actions Log

```python
@app.get("/admin/actions-log")
async def get_admin_actions_log(
    limit: int = 50,
    offset: int = 0,
    action_type_filter: Optional[str] = None,
    hours: Optional[int] = None,
    success_filter: Optional[bool] = None
):
    """Retorna log de ações administrativas com filtros"""

@app.get("/admin/actions-log/summary")
async def get_admin_actions_summary(hours: int = 24):
    """Estatísticas resumidas das ações administrativas"""

@app.get("/admin/actions-log/export")
async def export_admin_actions_log(
    hours: Optional[int] = None,
    action_type_filter: Optional[str] = None,
    format: str = 'csv'  # csv ou json
):
    """Exporta log de ações administrativas"""
```

#### Aplicação do Decorator aos Endpoints Existentes

```python
# Exemplos de aplicação do decorator aos endpoints existentes

@app.post("/admin/engine/pause", status_code=status.HTTP_204_NO_CONTENT)
@log_admin_action("engine_control", "pause_engine")
async def pause_finviz_engine(request: Request, payload: dict = Body(...)):
    # ... código existente ...

@app.post("/finviz/config")
@log_admin_action("config_update", "finviz_config_update")
async def update_finviz_config(request: Request, payload: dict = Body(...)):
    # ... código existente ...

@app.post("/admin/finviz/urls/{url_id}/activate")
@log_admin_action("url_management", "activate_url", target_resource="{url_id}")
async def activate_finviz_url(url_id: int, request: Request, payload: dict = Body(...)):
    # ... código existente ...
```

### 2.8 Interface do Usuário

#### Extensão do Modal de Configuração

O modal atual será expandido para incluir:

1. **Seção "Gerenciar URLs"**:
   - Lista de URLs cadastradas
   - Indicador visual da URL ativa
   - Botões para ativar/editar/excluir

2. **Seção "Adicionar Nova URL"**:
   - Campo nome (obrigatório)
   - Campo URL (obrigatório)
   - Campo descrição (opcional)
   - Botão para salvar

3. **NOVA Seção "Admin Actions Log"**:
   - Visualização em tempo real das ações administrativas
   - Filtros por tipo de ação, sucesso/erro, período
   - Exportação para CSV/JSON
   - Estatísticas resumidas

4. **Indicadores em Tempo Real**:
   - Badge mostrando URL ativa no dashboard principal
   - Atualização automática via WebSocket
   - Notificações de ações administrativas

#### Nova Aba para Admin Actions Log

```html
<!-- Nova aba no admin dashboard -->
<div class="col-md-12 mt-4">
    <div class="card">
        <div class="card-header">
            <h5 class="card-title mb-0">
                <i class="bi bi-shield-check"></i> Admin Actions Log
                <span class="badge bg-info ms-2" id="adminActionsCount">-</span>
            </h5>
        </div>
        <div class="card-body">
            <!-- Filtros -->
            <div class="row mb-3">
                <div class="col-md-3">
                    <select class="form-select" id="actionTypeFilter">
                        <option value="">All Action Types</option>
                        <option value="config_update">Config Updates</option>
                        <option value="engine_control">Engine Control</option>
                        <option value="url_management">URL Management</option>
                        <option value="metrics_reset">Metrics Reset</option>
                        <option value="database_operation">Database Operations</option>
                    </select>
                </div>
                <div class="col-md-3">
                    <select class="form-select" id="actionSuccessFilter">
                        <option value="">All Results</option>
                        <option value="true">Successful Only</option>
                        <option value="false">Failed Only</option>
                    </select>
                </div>
                <div class="col-md-3">
                    <select class="form-select" id="actionHoursFilter">
                        <option value="">All Time</option>
                        <option value="1">Last Hour</option>
                        <option value="24">Last 24 Hours</option>
                        <option value="168">Last 7 Days</option>
                    </select>
                </div>
                <div class="col-md-3">
                    <button class="btn btn-outline-secondary" id="exportActionsBtn">
                        <i class="bi bi-download"></i> Export CSV
                    </button>
                </div>
            </div>
            
            <!-- Tabela de ações -->
            <div class="table-responsive">
                <table class="table table-striped table-hover">
                    <thead>
                        <tr>
                            <th>Timestamp</th>
                            <th>Action Type</th>
                            <th>Action Name</th>
                            <th>Target</th>
                            <th>Status</th>
                            <th>Duration</th>
                            <th>Details</th>
                        </tr>
                    </thead>
                    <tbody id="adminActionsTableBody">
                        <tr>
                            <td colspan="7" class="text-center">Loading...</td>
                        </tr>
                    </tbody>
                </table>
            </div>
            
            <!-- Paginação -->
            <div class="d-flex justify-content-between align-items-center mt-3">
                <div class="text-muted">
                    <span id="adminActionsInfo">-</span>
                </div>
                <div class="btn-group">
                    <button id="adminActionsPrevBtn" class="btn btn-outline-secondary btn-sm" disabled>
                        <i class="bi bi-chevron-left"></i> Previous
                    </button>
                    <button id="adminActionsNextBtn" class="btn btn-outline-secondary btn-sm" disabled>
                        Next <i class="bi bi-chevron-right"></i>
                    </button>
                </div>
            </div>
        </div>
    </div>
</div>
```

#### Estrutura HTML Proposta para Estratégias

```html
<!-- Dentro do modal configModal -->
<div class="mb-4">
    <h6 class="text-primary mb-3">
        <i class="bi bi-list-ul"></i> Gerenciar Estratégias Finviz
    </h6>
    
    <!-- Lista de estratégias existentes -->
    <div id="finvizStrategiesList" class="mb-3">
        <!-- Populated by JavaScript -->
    </div>
    
    <!-- Formulário para nova estratégia -->
    <div class="card border-light">
        <div class="card-header bg-light">
            <h6 class="mb-0">Adicionar Nova Estratégia</h6>
        </div>
        <div class="card-body">
            <div class="row mb-2">
                <div class="col-md-4">
                    <label for="newStrategyName" class="form-label">Nome</label>
                    <input type="text" class="form-control" id="newStrategyName" placeholder="Ex: High Volume Scanner">
                </div>
                <div class="col-md-8">
                    <label for="newStrategyUrl" class="form-label">URL do Finviz</label>
                    <input type="url" class="form-control" id="newStrategyUrl" placeholder="https://finviz.com/screener.ashx?...">
                </div>
            </div>
            <div class="mb-2">
                <label for="newStrategyDescription" class="form-label">Descrição (opcional)</label>
                <textarea class="form-control" id="newStrategyDescription" rows="2" placeholder="Descrição da estratégia..."></textarea>
            </div>
            
            <!-- Configurações da estratégia -->
            <div class="row mb-2">
                <div class="col-md-3">
                    <label for="newStrategyTopN" class="form-label">Top N</label>
                    <input type="number" class="form-control" id="newStrategyTopN" value="100" min="1" max="1000">
                </div>
                <div class="col-md-3">
                    <label for="newStrategyRefresh" class="form-label">Refresh (sec)</label>
                    <input type="number" class="form-control" id="newStrategyRefresh" value="10" min="5" max="600">
                </div>
                <div class="col-md-3">
                    <label for="newStrategyReprocessWindow" class="form-label">Reprocess Window</label>
                    <input type="number" class="form-control" id="newStrategyReprocessWindow" value="300" min="0">
                </div>
                <div class="col-md-3">
                    <label for="newStrategySellWindow" class="form-label">Sell Window</label>
                    <input type="number" class="form-control" id="newStrategySellWindow" value="300" min="0">
                </div>
            </div>
            
            <div class="row mb-3">
                <div class="col-md-6">
                    <div class="form-check">
                        <input class="form-check-input" type="checkbox" id="newStrategyReprocessEnabled">
                        <label class="form-check-label" for="newStrategyReprocessEnabled">
                            Enable Reprocessing
                        </label>
                    </div>
                </div>
                <div class="col-md-6">
                    <div class="form-check">
                        <input class="form-check-input" type="checkbox" id="newStrategySellChronologyEnabled" checked>
                        <label class="form-check-label" for="newStrategySellChronologyEnabled">
                            Respect Sell Chronology
                        </label>
                    </div>
                </div>
            </div>
            
            <button type="button" class="btn btn-success" id="addNewStrategyBtn">
                <i class="bi bi-plus-circle"></i> Adicionar Estratégia
            </button>
        </div>
    </div>
</div>
```

#### JavaScript para Gerenciamento

```javascript
// Carrega e exibe lista de estratégias
async function loadFinvizStrategies() {
    try {
        const response = await fetch('/admin/finviz/strategies');
        const strategies = await response.json();
        
        const container = document.getElementById('finvizStrategiesList');
        container.innerHTML = strategies.map(strategy => `
            <div class="card mb-2 ${strategy.is_active ? 'border-success' : ''}">
                <div class="card-body py-2">
                    <div class="row align-items-center">
                        <div class="col-md-3">
                            <strong>${strategy.name}</strong>
                            ${strategy.is_active ? '<span class="badge bg-success ms-2">ATIVA</span>' : ''}
                        </div>
                        <div class="col-md-4">
                            <small class="text-muted">${truncateUrl(strategy.url)}</small>
                            <br><small class="text-muted">Top: ${strategy.top_n} | Refresh: ${strategy.refresh_interval_sec}s</small>
                        </div>
                        <div class="col-md-2">
                            <small class="text-muted">
                                Reprocess: ${strategy.reprocess_enabled ? '✓' : '✗'}<br>
                                Sell Chronology: ${strategy.respect_sell_chronology_enabled ? '✓' : '✗'}
                            </small>
                        </div>
                        <div class="col-md-3 text-end">
                            ${!strategy.is_active ? `
                                <button class="btn btn-sm btn-outline-success" onclick="activateStrategy(${strategy.id})">
                                    <i class="bi bi-play-circle"></i> Ativar
                                </button>
                            ` : ''}
                            <button class="btn btn-sm btn-outline-primary" onclick="editStrategy(${strategy.id})">
                                <i class="bi bi-pencil"></i>
                            </button>
                            <button class="btn btn-sm btn-outline-secondary" onclick="duplicateStrategy(${strategy.id})">
                                <i class="bi bi-files"></i>
                            </button>
                            ${!strategy.is_active ? `
                                <button class="btn btn-sm btn-outline-danger" onclick="deleteStrategy(${strategy.id})">
                                    <i class="bi bi-trash"></i>
                                </button>
                            ` : ''}
                        </div>
                    </div>
                    ${strategy.description ? `<small class="text-muted">${strategy.description}</small>` : ''}
                </div>
            </div>
        `).join('');
        
        // Atualiza indicador na interface principal
        updateActiveStrategyIndicator(strategies.find(s => s.is_active));
        
    } catch (error) {
        console.error('Error loading Finviz strategies:', error);
        showAlert('Erro ao carregar estratégias do Finviz', 'danger');
    }
}

// Ativa uma estratégia específica
async function activateStrategy(strategyId) {
    if (!confirm('Deseja ativar esta estratégia? A estratégia atual será desativada e o sistema será reiniciado.')) return;
    
    try {
        const token = getAdminToken();
        if (!token) return;
        
        const response = await fetch(`/admin/finviz/strategies/${strategyId}/activate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token })
        });
        
        if (response.ok) {
            showAlert('Estratégia ativada com sucesso!', 'success');
            loadFinvizStrategies(); // Recarrega lista
            loadSystemStatus(); // Atualiza status do sistema
        } else {
            throw new Error(`HTTP ${response.status}`);
        }
    } catch (error) {
        showAlert(`Erro ao ativar URL: ${error.message}`, 'danger');
    }
}

// Adiciona nova URL
async function addNewUrl() {
    const name = document.getElementById('newUrlName').value.trim();
    const url = document.getElementById('newUrlUrl').value.trim();
    const description = document.getElementById('newUrlDescription').value.trim();
    
    if (!name || !url) {
        showAlert('Nome e URL são obrigatórios', 'warning');
        return;
    }
    
    try {
        const token = getAdminToken();
        if (!token) return;
        
        const response = await fetch('/admin/finviz/urls', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                token,
                name,
                url,
                description: description || null
            })
        });
        
        if (response.ok) {
            showAlert('URL adicionada com sucesso!', 'success');
            // Limpa formulário
            document.getElementById('newUrlName').value = '';
            document.getElementById('newUrlUrl').value = '';
            document.getElementById('newUrlDescription').value = '';
            // Recarrega lista
            loadFinvizUrls();
        } else {
            const errorData = await response.json();
            throw new Error(errorData.detail || `HTTP ${response.status}`);
        }
    } catch (error) {
        showAlert(`Erro ao adicionar URL: ${error.message}`, 'danger');
    }
}

// ============================================================================
// Admin Actions Log JavaScript
// ============================================================================

// Variáveis globais para paginação do log de ações
let adminActionsCurrentPage = 1;
let adminActionsLoading = false;

// Carrega log de ações administrativas
async function loadAdminActionsLog() {
    if (adminActionsLoading) return;
    
    adminActionsLoading = true;
    const tbody = document.getElementById('adminActionsTableBody');
    
    try {
        const params = new URLSearchParams();
        const limit = 25;
        
        // Filtros
        const actionType = document.getElementById('actionTypeFilter').value;
        const success = document.getElementById('actionSuccessFilter').value;
        const hours = document.getElementById('actionHoursFilter').value;
        
        params.append('limit', limit);
        params.append('offset', (adminActionsCurrentPage - 1) * limit);
        
        if (actionType) params.append('action_type_filter', actionType);
        if (success) params.append('success_filter', success === 'true');
        if (hours) params.append('hours', hours);
        
        const response = await fetch(`/admin/actions-log?${params}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        
        const data = await response.json();
        
        // Renderiza tabela
        tbody.innerHTML = data.actions.map(action => {
            const timestamp = new Date(action.timestamp).toLocaleString();
            const statusBadge = action.success 
                ? '<span class="badge bg-success">Success</span>'
                : '<span class="badge bg-danger">Failed</span>';
            const duration = action.execution_time_ms 
                ? `${action.execution_time_ms}ms` 
                : '-';
            
            return `
                <tr>
                    <td><small>${timestamp}</small></td>
                    <td><span class="badge bg-secondary">${action.action_type}</span></td>
                    <td><strong>${action.action_name}</strong></td>
                    <td><small>${action.target_resource || '-'}</small></td>
                    <td>${statusBadge}</td>
                    <td><small>${duration}</small></td>
                    <td>
                        ${action.error_message ? `<span class="text-danger">${action.error_message}</span>` : ''}
                        ${action.details ? `<small class="text-muted">${JSON.stringify(action.details).substring(0, 50)}...</small>` : ''}
                    </td>
                </tr>
            `;
        }).join('');
        
        // Atualiza informações de paginação
        const info = document.getElementById('adminActionsInfo');
        info.textContent = `Showing ${data.actions.length} of ${data.total} actions`;
        
        const prevBtn = document.getElementById('adminActionsPrevBtn');
        const nextBtn = document.getElementById('adminActionsNextBtn');
        
        prevBtn.disabled = adminActionsCurrentPage === 1;
        nextBtn.disabled = data.offset + data.actions.length >= data.total;
        
        // Atualiza contador
        const counter = document.getElementById('adminActionsCount');
        counter.textContent = data.total;
        
    } catch (error) {
        console.error('Error loading admin actions:', error);
        tbody.innerHTML = `<tr><td colspan="7" class="text-center text-danger">Error loading actions: ${error.message}</td></tr>`;
    } finally {
        adminActionsLoading = false;
    }
}

// Exporta log de ações para CSV
async function exportAdminActions() {
    try {
        const params = new URLSearchParams();
        
        // Aplicar mesmos filtros da visualização
        const actionType = document.getElementById('actionTypeFilter').value;
        const success = document.getElementById('actionSuccessFilter').value;
        const hours = document.getElementById('actionHoursFilter').value;
        
        if (actionType) params.append('action_type_filter', actionType);
        if (success) params.append('success_filter', success === 'true');
        if (hours) params.append('hours', hours);
        
        params.append('format', 'csv');
        
        const response = await fetch(`/admin/actions-log/export?${params}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        
        // Download do arquivo
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `admin_actions_${new Date().toISOString().split('T')[0]}.csv`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
        
        showAlert('Admin actions exported successfully!', 'success');
        
    } catch (error) {
        console.error('Error exporting admin actions:', error);
        showAlert(`Error exporting: ${error.message}`, 'danger');
    }
}

// Event handlers para filtros do log de ações
document.addEventListener('DOMContentLoaded', function() {
    // Filtros do log de ações
    const actionFilters = ['actionTypeFilter', 'actionSuccessFilter', 'actionHoursFilter'];
    actionFilters.forEach(filterId => {
        const element = document.getElementById(filterId);
        if (element) {
            element.addEventListener('change', () => {
                adminActionsCurrentPage = 1;
                loadAdminActionsLog();
            });
        }
    });
    
    // Botões de paginação do log de ações
    const prevBtn = document.getElementById('adminActionsPrevBtn');
    const nextBtn = document.getElementById('adminActionsNextBtn');
    
    if (prevBtn) {
        prevBtn.addEventListener('click', () => {
            if (adminActionsCurrentPage > 1) {
                adminActionsCurrentPage--;
                loadAdminActionsLog();
            }
        });
    }
    
    if (nextBtn) {
        nextBtn.addEventListener('click', () => {
            adminActionsCurrentPage++;
            loadAdminActionsLog();
        });
    }
    
    // Botão de exportação
    const exportBtn = document.getElementById('exportActionsBtn');
    if (exportBtn) {
        exportBtn.addEventListener('click', exportAdminActions);
    }
    
    // Carrega log inicial
    loadAdminActionsLog();
    
    // Auto-refresh a cada 30 segundos
    setInterval(loadAdminActionsLog, 30000);
});

// Atualização via WebSocket para ações administrativas
function handleAdminActionWebSocketMessage(data) {
    switch(data.type) {
        case 'admin_action_logged':
            // Recarrega log se estamos na primeira página
            if (adminActionsCurrentPage === 1) {
                loadAdminActionsLog();
            }
            
            // Mostra notificação se a ação falhou
            if (data.data && !data.data.success) {
                showAlert(`Admin action failed: ${data.data.action_name}`, 'warning');
            }
            break;
    }
}
```

### 2.7 Indicadores Visuais em Tempo Real

#### Dashboard Principal

```html
<!-- Novo card para mostrar URL ativa -->
<div class="col-md-3">
    <div class="card status-card bg-info text-white">
        <div class="card-body text-center">
            <i class="bi bi-bar-chart fs-1 mb-2"></i>
            <h6 class="card-title">URL Ativa</h6>
            <div id="activeUrlIndicator">
                <div class="metric-value" id="activeUrlName">-</div>
                <div class="metric-label" id="activeUrlDescription">Carregando...</div>
            </div>
        </div>
    </div>
</div>
```

#### WebSocket Updates

```javascript
// Adicionar ao handler de WebSocket existente
function handleWebSocketMessage(data) {
    // ... handlers existentes ...
    
    if (data.type === 'finviz_url_changed') {
        updateActiveUrlIndicator(data.data.active_url);
        if (typeof loadFinvizUrls === 'function') {
            loadFinvizUrls(); // Atualiza lista se modal estiver aberto
        }
        showAlert(`URL do Finviz alterada para: ${data.data.active_url.name}`, 'info');
    }
}

function updateActiveUrlIndicator(activeUrl) {
    const nameElement = document.getElementById('activeUrlName');
    const descElement = document.getElementById('activeUrlDescription');
    
    if (activeUrl) {
        nameElement.textContent = activeUrl.name;
        descElement.textContent = activeUrl.description || 'Sem descrição';
    } else {
        nameElement.textContent = 'Nenhuma';
        descElement.textContent = 'URL não configurada';
    }
}
```

## 3. Considerações de UI/UX

### 3.1 Experiência do Usuário

1. **Feedback Visual Imediato**: 
   - Indicadores claros da estratégia ativa
   - Visualização completa de parâmetros de configuração
   - Animações suaves para mudanças de estado
   - Badges coloridos para status e recursos

2. **Prevenção de Erros**:
   - Validação de URLs e parâmetros em tempo real
   - Confirmações para ações críticas (ativação, exclusão)
   - Não permitir exclusão da estratégia ativa
   - Validação de compatibilidade de parâmetros

3. **Informações Contextuais**:
   - Timestamps de última utilização
   - Descrições para lembrar a estratégia
   - Truncamento inteligente de URLs longas
   - Preview de configurações antes da ativação

### 3.2 Fluxo de Trabalho Otimizado

1. **Gestão de Estratégias**: Estratégias frequentes no topo da lista
2. **Presets Completos**: Configurações completas salvas como templates
3. **Duplicação Inteligente**: Clonar estratégias para ajustes rápidos
4. **Comparação Visual**: Ver diferenças entre estratégias lado a lado

## 4. Aspectos Técnicos e Robustez

### 4.1 Sistema Único e Robusto

1. **Única Fonte de Verdade**: Banco de dados PostgreSQL como única fonte de configuração completa
2. **Operações Atômicas**: Todas as mudanças de estratégia ativa em transações atômicas
3. **Validação Rigorosa**: URLs e todos os 7 parâmetros validados antes de serem salvos
4. **Auto-recuperação**: Sistema garante sempre ter uma estratégia ativa funcional

### 4.2 Performance e Confiabilidade

1. **Cache Local**: Estratégia ativa completa mantida em memória no FinvizEngine
2. **Constraint de Banco**: Garantia de apenas uma estratégia ativa por vez
3. **Transactions**: Mudanças atômicas com rollback automático em caso de erro
4. **Health Checks**: Validação periódica da estratégia ativa e seus parâmetros

### 4.3 Segurança e Validação

1. **Autenticação Consistente**: Mesmo token para todas as operações
2. **Validação Completa**: Verificação se são URLs válidas do Finviz + validação de parâmetros
3. **Prevenção de Estados Inválidos**: Impossível ficar sem estratégia ativa
4. **Audit Trail**: Log completo de todas as mudanças administrativas

## 5. Cronograma de Implementação

### Fase 1: Estrutura Base
1. ✅ Criar modelo de banco de dados FinvizUrl com 7 parâmetros completos
2. ✅ Implementar métodos no DBManager para estratégias completas
3. ✅ Criar função de inicialização robusta com estratégia padrão
4. ✅ Remover dependências do arquivo JSON finviz_config.json
5. ✅ Atualizar FinvizEngine para trabalhar com estratégias do banco
6. ✅ Testes básicos

### Fase 2: Backend API
1. ✅ Implementar endpoints REST completos para estratégias
2. ✅ Modificar FinvizEngine para carregar estratégias apenas do DB
3. ✅ Integrar operações atômicas de troca de estratégia
4. ✅ Adicionar WebSocket broadcasts para mudanças
5. ✅ Atualizar endpoint /finviz/config para estratégias
6. ✅ Testes de integração

### Fase 3: Interface do Usuário
1. ✅ Expandir modal de configuração para estratégias completas
2. ✅ Implementar JavaScript para gerenciamento de estratégias
3. ✅ Adicionar formulários para todos os 7 parâmetros
4. ✅ Integrar funcionalidades de duplicação e edição
5. ✅ Adicionar indicadores visuais em tempo real
6. ✅ Integrar WebSocket updates
7. ✅ Validações client-side

### Fase 4: Finalização
1. ✅ Testes end-to-end de estratégias
2. ✅ Migração de finviz_config.json para primeira estratégia
3. ✅ Otimizações de performance
4. ✅ Documentação completa
5. ✅ Deploy e verificação

## 6. Benefícios da Implementação Direta

### 6.1 Para o Usuário
- **Simplicidade**: Sistema direto sem complexidades de migração
- **Estratégias Completas**: Cada entrada é uma configuração completa de trading
- **Confiabilidade**: Operações atômicas garantem consistência total
- **Flexibilidade**: Múltiplas estratégias pré-configuradas prontas para uso
- **Eficiência**: Troca instantânea entre estratégias completas

### 6.2 Para o Sistema
- **Arquitetura Limpa**: Uma única fonte de verdade para todas as configurações
- **Eliminação de JSON**: Não há mais dependência de arquivos de configuração
- **Manutenibilidade**: Código mais simples e direto
- **Escalabilidade**: Base sólida para crescimento de estratégias
- **Robustez**: Sistema à prova de estados inconsistentes

## 7. Considerações Futuras

### 7.1 Funcionalidades Avançadas
- **Scheduling**: Estratégias ativas em horários específicos
- **A/B Testing**: Comparação de performance entre estratégias
- **Templates**: Estratégias pré-configuradas para cenários comuns
- **Grupos**: Organização de estratégias por categoria/tipo

### 7.2 Integrações
- **Alertas**: Notificações de mudanças importantes
- **Analytics**: Métricas de performance por estratégia
- **Backup**: Sincronização com sistemas externos
- **API Externa**: Exposição para ferramentas de terceiros

---

**Esta implementação direta criará um sistema único, robusto e confiável para gerenciamento de estratégias Finviz completas PLUS um sistema abrangente de auditoria administrativa, totalmente integrado com a aplicação existente, sem complexidades de migração ou fallbacks, garantindo máxima confiabilidade e simplicidade.**

**O Admin Actions Log System adicionará transparência e conformidade total ao sistema, permitindo auditoria completa de todas as ações administrativas com rastreabilidade detalhada.**

**Cada estratégia é um preset completo com todos os 7 parâmetros de configuração do FinvizEngine, eliminando a necessidade de arquivos JSON e centralizando tudo no banco de dados.**

-- PostgreSQL initialization script for Trading Signal Processor
-- ABORDAGEM HÍBRIDA: Enums no Python, Strings no Banco
-- Schema simples e eficiente para máxima performance

-- Ensure we're using the correct database
\c trading_signals;

-- Create extension for UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create extension for better JSON handling
CREATE EXTENSION IF NOT EXISTS "btree_gin";

-- =============================================================================
-- SCHEMA SIMPLES - TABELAS PRINCIPAIS
-- =============================================================================

-- Tabela principal de sinais
CREATE TABLE IF NOT EXISTS signals (
    -- Primary key
    signal_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Signal data
    ticker VARCHAR(20) NOT NULL,
    normalised_ticker VARCHAR(20) NOT NULL,
    side VARCHAR(10),
    price FLOAT,
    
    -- Status tracking (STRING no banco, ENUM no Python)
    status VARCHAR(30) NOT NULL DEFAULT 'received',
    
    -- Signal type (NEW: distinguish between buy, sell, manual_sell, sell_all)
    signal_type VARCHAR(20) NOT NULL DEFAULT 'buy',
    
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    
    -- Original signal as JSON
    original_signal JSONB NOT NULL,
    
    -- Performance tracking
    processing_time_ms INTEGER,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0
);

-- Tabela de eventos (auditoria detalhada)
CREATE TABLE IF NOT EXISTS signal_events (
    event_id BIGSERIAL PRIMARY KEY,
    signal_id UUID NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
    
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    status VARCHAR(30) NOT NULL,  -- STRING no banco
    details TEXT,
    worker_id VARCHAR(50)
);

-- =============================================================================
-- ÍNDICES PARA PERFORMANCE
-- =============================================================================

-- Índices na tabela signals
CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals(ticker);
CREATE INDEX IF NOT EXISTS idx_signals_normalised_ticker ON signals(normalised_ticker);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_signal_type ON signals(signal_type);
CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals(created_at);
CREATE INDEX IF NOT EXISTS idx_signals_updated_at ON signals(updated_at);

-- Índices na tabela signal_events
CREATE INDEX IF NOT EXISTS idx_signal_events_signal_id ON signal_events(signal_id);
CREATE INDEX IF NOT EXISTS idx_signal_events_timestamp ON signal_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_signal_events_status ON signal_events(status);

-- Índice composto para queries comuns
CREATE INDEX IF NOT EXISTS idx_signals_status_created ON signals(status, created_at);

-- =============================================================================
-- TRIGGER PARA AUTO-UPDATE DE updated_at
-- =============================================================================

-- Função para atualizar timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Trigger na tabela signals
DROP TRIGGER IF EXISTS update_signals_updated_at ON signals;
CREATE TRIGGER update_signals_updated_at
    BEFORE UPDATE ON signals
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- COMENTÁRIOS PARA DOCUMENTAÇÃO
-- =============================================================================

COMMENT ON DATABASE trading_signals IS 'Trading Signal Processor - Abordagem Híbrida: Enums no Python + Strings no Banco';

COMMENT ON TABLE signals IS 'Tabela principal de sinais - schema simples para máxima performance';
COMMENT ON COLUMN signals.status IS 'Status como string no banco (enum no Python para type safety)';
COMMENT ON COLUMN signals.original_signal IS 'Payload original do sinal em formato JSON';

COMMENT ON TABLE signal_events IS 'Eventos de auditoria - rastreamento detalhado do ciclo de vida';
COMMENT ON COLUMN signal_events.status IS 'Status do evento como string (enum no Python)';

-- =============================================================================
-- DADOS DE TESTE (OPCIONAL)
-- =============================================================================

-- Inserir sinal de teste se não existir nenhum dado
INSERT INTO signals (signal_id, ticker, normalised_ticker, side, price, status, original_signal)
SELECT 
    'test-init-' || uuid_generate_v4()::text,
    'AAPL',
    'AAPL', 
    'BUY',
    150.0,
    'received',
    '{"ticker": "AAPL", "side": "BUY", "price": 150.0, "test": true}'::jsonb
WHERE NOT EXISTS (SELECT 1 FROM signals LIMIT 1);

-- Log de inicialização
DO $$
BEGIN
    RAISE NOTICE '✅ Trading Signal Processor - Schema híbrido inicializado com sucesso!';
    RAISE NOTICE '   🗄️  Tabelas: signals, signal_events';
    RAISE NOTICE '   📊 Índices: otimizados para performance';
    RAISE NOTICE '   🔄 Abordagem: Enums no Python + Strings no banco';
END $$;

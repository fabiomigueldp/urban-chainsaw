-- PostgreSQL initialization script for Trading Signal Processor
-- HYBRID APPROACH: Enums in Python, Strings in Database
-- Simple and efficient schema for maximum performance

-- Ensure we're using the correct database
\c trading_signals;

-- Create extension for UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create extension for better JSON handling
CREATE EXTENSION IF NOT EXISTS "btree_gin";

-- =============================================================================
-- SIMPLE SCHEMA - MAIN TABLES
-- =============================================================================

-- Main signals table
CREATE TABLE IF NOT EXISTS signals (
    -- Primary key
    signal_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Signal data
    ticker VARCHAR(20) NOT NULL,
    normalised_ticker VARCHAR(20) NOT NULL,
    side VARCHAR(10),
    price FLOAT,
    
    -- Status tracking (STRING in database, ENUM in Python)
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

-- Events table (detailed audit)
CREATE TABLE IF NOT EXISTS signal_events (
    event_id BIGSERIAL PRIMARY KEY,
    signal_id UUID NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
    
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    status VARCHAR(30) NOT NULL,  -- STRING in database
    details TEXT,
    worker_id VARCHAR(50)
);

-- =============================================================================
-- INDEXES FOR PERFORMANCE
-- =============================================================================

-- Indexes on signals table
CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals(ticker);
CREATE INDEX IF NOT EXISTS idx_signals_normalised_ticker ON signals(normalised_ticker);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_signal_type ON signals(signal_type);
CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals(created_at);
CREATE INDEX IF NOT EXISTS idx_signals_updated_at ON signals(updated_at);

-- Indexes on signal_events table
CREATE INDEX IF NOT EXISTS idx_signal_events_signal_id ON signal_events(signal_id);
CREATE INDEX IF NOT EXISTS idx_signal_events_timestamp ON signal_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_signal_events_status ON signal_events(status);

-- Composite index for common queries
CREATE INDEX IF NOT EXISTS idx_signals_status_created ON signals(status, created_at);

-- =============================================================================
-- TRIGGER FOR AUTO-UPDATE OF updated_at
-- =============================================================================

-- Function to update timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Trigger on signals table
DROP TRIGGER IF EXISTS update_signals_updated_at ON signals;
CREATE TRIGGER update_signals_updated_at
    BEFORE UPDATE ON signals
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- COMMENTS FOR DOCUMENTATION
-- =============================================================================

COMMENT ON DATABASE trading_signals IS 'Trading Signal Processor - Hybrid Approach: Enums in Python + Strings in Database';

COMMENT ON TABLE signals IS 'Main signals table - simple schema for maximum performance';
COMMENT ON COLUMN signals.status IS 'Status as string in database (enum in Python for type safety)';
COMMENT ON COLUMN signals.original_signal IS 'Original signal payload in JSON format';

COMMENT ON TABLE signal_events IS 'Audit events - detailed lifecycle tracking';
COMMENT ON COLUMN signal_events.status IS 'Event status as string (enum in Python)';

-- =============================================================================
-- TEST DATA (OPTIONAL)
-- =============================================================================

-- Insert test signal if no data exists
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

-- Initialization log
DO $$
BEGIN
    RAISE NOTICE '✅ Trading Signal Processor - Hybrid schema initialized successfully!';
    RAISE NOTICE '   🗄️  Tables: signals, signal_events';
    RAISE NOTICE '   📊 Indexes: optimized for performance';
    RAISE NOTICE '   🔄 Approach: Enums in Python + Strings in database';
END $$;

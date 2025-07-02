-- Migration script to add the positions table
-- Run this to add position tracking functionality

-- =============================================================================
-- POSITIONS TABLE
-- =============================================================================
CREATE TABLE IF NOT EXISTS positions (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'open', -- 'open', 'closing', 'closed'
    entry_signal_id UUID NOT NULL REFERENCES signals(signal_id) ON DELETE RESTRICT,
    exit_signal_id UUID REFERENCES signals(signal_id) ON DELETE RESTRICT,
    opened_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMP WITH TIME ZONE,
    UNIQUE(ticker, entry_signal_id) -- Ensures an entry signal isn't duplicated
);

-- Indexes for positions table
CREATE INDEX IF NOT EXISTS idx_positions_ticker ON positions(ticker);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_ticker_status ON positions(ticker, status);

COMMENT ON TABLE positions IS 'Tracks the state of open and closed trading positions.';

-- Log the migration
DO $$
BEGIN
    RAISE NOTICE '✅ Positions table migration completed successfully!';
    RAISE NOTICE '   📊 Added table: positions';
    RAISE NOTICE '   📊 Added indexes for performance';
    RAISE NOTICE '   🔄 Position management now database-driven';
END $$;

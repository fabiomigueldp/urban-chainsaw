-- ============================================================================
-- MIGRATION: Add Finviz URLs and Admin Actions Tables
-- ============================================================================
-- This migration adds the new tables for Finviz strategy management and admin actions logging

-- =============================================================================
-- FINVIZ URLS TABLE - Strategy Management
-- =============================================================================

CREATE TABLE IF NOT EXISTS finviz_urls (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    url TEXT NOT NULL,
    description TEXT,
    
    -- Strategy configuration parameters
    top_n INTEGER NOT NULL DEFAULT 100,
    refresh_interval_sec INTEGER NOT NULL DEFAULT 10,
    reprocess_enabled BOOLEAN DEFAULT FALSE,
    reprocess_window_seconds INTEGER DEFAULT 300,
    respect_sell_chronology_enabled BOOLEAN DEFAULT TRUE,
    sell_chronology_window_seconds INTEGER DEFAULT 300,
    
    -- Activation control
    is_active BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_used_at TIMESTAMP WITH TIME ZONE
);

-- Indexes for finviz_urls table
CREATE INDEX IF NOT EXISTS idx_finviz_urls_name ON finviz_urls(name);
CREATE INDEX IF NOT EXISTS idx_finviz_urls_active ON finviz_urls(is_active);

-- Unique constraint to ensure only one active URL at a time
CREATE UNIQUE INDEX IF NOT EXISTS idx_finviz_urls_active_unique 
ON finviz_urls (is_active) 
WHERE is_active = TRUE;

-- =============================================================================
-- ADMIN ACTIONS TABLE - Audit Trail
-- =============================================================================

CREATE TABLE IF NOT EXISTS admin_actions (
    action_id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    action_type VARCHAR(50) NOT NULL,
    action_name VARCHAR(100) NOT NULL,
    admin_token TEXT,
    ip_address INET,
    user_agent TEXT,
    details JSONB,
    target_resource VARCHAR(100),
    success BOOLEAN DEFAULT TRUE NOT NULL,
    error_message TEXT,
    execution_time_ms INTEGER
);

-- Indexes for admin_actions table
CREATE INDEX IF NOT EXISTS idx_admin_actions_timestamp ON admin_actions(timestamp);
CREATE INDEX IF NOT EXISTS idx_admin_actions_type ON admin_actions(action_type);
CREATE INDEX IF NOT EXISTS idx_admin_actions_success ON admin_actions(success);
CREATE INDEX IF NOT EXISTS idx_admin_actions_token ON admin_actions(admin_token);

-- =============================================================================
-- UPDATE TRIGGER FOR finviz_urls
-- =============================================================================

-- Create trigger function for updated_at if it doesn't exist
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Add trigger to finviz_urls table
DROP TRIGGER IF EXISTS update_finviz_urls_updated_at ON finviz_urls;
CREATE TRIGGER update_finviz_urls_updated_at
    BEFORE UPDATE ON finviz_urls
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- DEFAULT DATA
-- =============================================================================

-- Insert default strategy if no strategies exist
INSERT INTO finviz_urls (
    name, 
    url, 
    description, 
    top_n, 
    refresh_interval_sec, 
    reprocess_enabled, 
    reprocess_window_seconds, 
    respect_sell_chronology_enabled, 
    sell_chronology_window_seconds, 
    is_active
) 
SELECT 
    'Default Strategy',
    'https://finviz.com/screener.ashx?v=111&ft=4',
    'Default system strategy with basic configuration',
    100,
    10,
    FALSE,
    300,
    TRUE,
    300,
    TRUE
WHERE NOT EXISTS (SELECT 1 FROM finviz_urls);

-- =============================================================================
-- COMMENTS
-- =============================================================================

COMMENT ON TABLE finviz_urls IS 'Finviz strategy presets with complete configuration parameters';
COMMENT ON TABLE admin_actions IS 'Administrative actions audit trail for complete transparency';

-- Log completion
DO $$
BEGIN
    RAISE NOTICE '✅ Finviz URLs and Admin Actions tables migration completed successfully!';
END $$;

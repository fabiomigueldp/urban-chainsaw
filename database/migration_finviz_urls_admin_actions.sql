-- Migration: Add Finviz URLs and Admin Actions tables
-- This file creates the required tables for Multi-URL Finviz strategies and Admin Actions Log

-- ==========================================================================================
--                              FINVIZ URLS TABLE
-- ==========================================================================================

CREATE TABLE IF NOT EXISTS finviz_urls (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    url TEXT NOT NULL,
    description TEXT,
    
    -- Complete strategy configuration (7 parameters)
    top_n INTEGER NOT NULL DEFAULT 100,
    refresh_interval_sec INTEGER NOT NULL DEFAULT 10,
    reprocess_enabled BOOLEAN DEFAULT FALSE,
    reprocess_window_seconds INTEGER DEFAULT 300,
    respect_sell_chronology_enabled BOOLEAN DEFAULT TRUE,
    sell_chronology_window_seconds INTEGER DEFAULT 300,
    
    -- Activation control and timestamping
    is_active BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_used_at TIMESTAMP WITH TIME ZONE
);

-- Ensure only one URL is active at a time
CREATE UNIQUE INDEX IF NOT EXISTS idx_finviz_urls_active 
ON finviz_urls (is_active) 
WHERE is_active = TRUE;

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_finviz_urls_name ON finviz_urls (name);
CREATE INDEX IF NOT EXISTS idx_finviz_urls_created_at ON finviz_urls (created_at);

-- ==========================================================================================
--                              ADMIN ACTIONS TABLE
-- ==========================================================================================

CREATE TABLE IF NOT EXISTS admin_actions (
    action_id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    action_type VARCHAR(50) NOT NULL,
    action_name VARCHAR(100) NOT NULL,
    admin_token TEXT,
    ip_address INET,
    user_agent TEXT,
    details JSONB,
    target_resource VARCHAR(100),
    success BOOLEAN NOT NULL DEFAULT TRUE,
    error_message TEXT,
    execution_time_ms INTEGER,
    
    -- Action type constraint
    CONSTRAINT admin_actions_action_type_check CHECK (action_type IN (
        'config_update', 'engine_control', 'url_management', 'metrics_reset',
        'database_operation', 'rate_limiter_control', 'order_management',
        'file_import', 'manual_override', 'system_maintenance'
    ))
);

-- Performance indexes for admin actions
CREATE INDEX IF NOT EXISTS idx_admin_actions_timestamp ON admin_actions(timestamp);
CREATE INDEX IF NOT EXISTS idx_admin_actions_type ON admin_actions(action_type);
CREATE INDEX IF NOT EXISTS idx_admin_actions_admin_token ON admin_actions(admin_token);
CREATE INDEX IF NOT EXISTS idx_admin_actions_success ON admin_actions(success);

-- ==========================================================================================
--                         MIGRATION COMPLETE MESSAGE
-- ==========================================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration completed successfully: finviz_urls and admin_actions tables created';
END $$;

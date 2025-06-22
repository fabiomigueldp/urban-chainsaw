-- PostgreSQL initialization script for Trading Signal Processor
-- This script creates the database structure automatically when the container starts

-- Ensure we're using the correct database
\c trading_signals;

-- Create extension for UUID generation if not exists
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Enable logging for debugging (optional)
-- SET log_statement = 'all';

-- Create a comment for documentation
COMMENT ON DATABASE trading_signals IS 'Trading Signal Processor - Signal Audit Trail Database';

-- The actual tables will be created by SQLAlchemy when the application starts
-- This file is here to ensure the database is properly initialized

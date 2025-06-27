-- Migration script to add signal_type column to existing signals table
-- Run this script on existing databases to add the new signal_type column

-- Add the signal_type column with default value 'buy'
ALTER TABLE signals ADD COLUMN IF NOT EXISTS signal_type VARCHAR(20) NOT NULL DEFAULT 'buy';

-- Create index for the new column
CREATE INDEX IF NOT EXISTS idx_signals_signal_type ON signals(signal_type);

-- Update existing records based on their characteristics
-- Set manual sell orders (those created by admin interface) to 'manual_sell'
UPDATE signals 
SET signal_type = 'manual_sell' 
WHERE side = 'sell' 
  AND original_signal->>'action' = 'sell'
  AND (
    original_signal->>'source' = 'admin' 
    OR original_signal ? 'admin_created'
  );

-- Set sell_all orders to 'sell_all' based on worker_id pattern
UPDATE signals 
SET signal_type = 'sell_all' 
WHERE side = 'sell' 
  AND EXISTS (
    SELECT 1 FROM signal_events se 
    WHERE se.signal_id = signals.signal_id 
      AND se.worker_id = 'admin_sell_all'
  );

-- Set regular sell orders to 'sell'
UPDATE signals 
SET signal_type = 'sell' 
WHERE side = 'sell' 
  AND signal_type = 'buy';  -- Only update those that haven't been classified yet

-- Log migration completion
DO $$
BEGIN
    RAISE NOTICE '✅ Signal type migration completed successfully!';
    RAISE NOTICE '   📊 Added signal_type column with index';
    RAISE NOTICE '   🔄 Updated existing records based on signal characteristics';
    RAISE NOTICE '   📋 Signal types: buy, sell, manual_sell, sell_all';
END $$;

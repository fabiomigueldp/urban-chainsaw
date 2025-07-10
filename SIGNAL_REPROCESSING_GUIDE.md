# Signal Reprocessing Engine - Implementation Guide

## Overview

The Signal Reprocessing Engine has been completely rewritten to provide robust, reliable signal recovery when tickers enter the Finviz Top-N list. This implementation addresses all critical issues identified in the original system.

## Architecture

### Core Components

#### 1. **SignalReprocessingEngine**
Main orchestrator that coordinates the entire reprocessing workflow.

**Key Features:**
- Comprehensive error handling with categorized failures
- Detailed metrics tracking and health monitoring
- Robust signal reconstruction with multiple fallback strategies
- Proper database transaction management
- Queue management for forwarding workers

#### 2. **SignalValidator**
Validates signal data and determines signal types with enhanced logic.

**Improvements:**
- Case-insensitive signal type detection
- Multiple field checking (side, signal_type, action)
- Whitespace handling
- Comprehensive buy/sell indicator lists
- Default behavior for ambiguous signals

#### 3. **SignalReconstructor**
Handles robust reconstruction of Signal objects from database data.

**Fallback Strategy:**
1. **Primary**: Use `original_signal` JSON if available
2. **Secondary**: Reconstruct from basic database fields
3. **Tertiary**: Create minimal valid signal as last resort

#### 4. **ReprocessingMetrics**
Comprehensive metrics tracking for monitoring and debugging.

**Metrics Tracked:**
- Signals found, processed, successful, failed
- Error categorization (validation, reconstruction, database, queue)
- Success rates and timing information
- Health status calculation

## Key Improvements

### 1. **Fixed Critical Bugs**
- ✅ **Variable Definition Error**: Fixed `reprocessed_signal` being used before definition
- ✅ **Signal Filtering**: Enhanced BUY signal detection logic
- ✅ **Error Handling**: Proper exception handling and recovery
- ✅ **Queue Integration**: Signals now properly reach forwarding workers

### 2. **Enhanced Reliability**
- **Multiple Reconstruction Strategies**: If one method fails, fallbacks are attempted
- **Comprehensive Validation**: All signal data is validated before processing
- **Transaction Safety**: Database operations are properly managed
- **Error Categorization**: Different types of failures are handled appropriately

### 3. **Monitoring and Observability**
- **Health Status**: Real-time health monitoring with status levels
- **Detailed Metrics**: Success rates, timing, error counts
- **Structured Logging**: Detailed logs with context for debugging
- **Admin Interface Integration**: Health status visible in web interface

### 4. **Robust Configuration**
- **Backward Compatibility**: Works with existing configuration system
- **Graceful Degradation**: Falls back to legacy implementation if needed
- **Manual Triggering**: Admin can manually trigger reprocessing for specific tickers

## API Endpoints

### Health Status
```http
GET /admin/reprocessing/health
```

**Response:**
```json
{
  "reprocessing_engine": {
    "status": "HEALTHY|WARNING|CRITICAL|UNKNOWN",
    "success_rate": 95.5,
    "last_run": "2025-07-10T15:30:00Z",
    "last_duration_ms": 1250,
    "signals_processed": 15,
    "signals_successful": 14,
    "signals_failed": 1
  },
  "finviz_engine_running": true
}
```

### Manual Trigger
```http
POST /admin/reprocessing/trigger
```

**Request:**
```json
{
  "tickers": ["AAPL", "MSFT", "GOOGL"],
  "window_seconds": 300,
  "token": "admin_token"
}
```

**Response:**
```json
{
  "success": true,
  "tickers_processed": ["AAPL", "MSFT"],
  "signals_found": 5,
  "signals_reprocessed": 4,
  "signals_failed": 1,
  "success_rate": 80.0,
  "duration_ms": 1500,
  "errors": []
}
```

## Integration Points

### 1. **FinvizEngine Integration**
The new engine is integrated into the existing `FinvizEngine` with fallback support:

```python
# Enhanced method in finviz_engine.py
async def _reprocess_signals_for_new_tickers(self, new_tickers: Set[str], window_seconds: int):
    try:
        from signal_reprocessing_engine import SignalReprocessingEngine
        # Use new robust engine
        engine = SignalReprocessingEngine(db_manager, approved_signal_queue)
        result = await engine.process_new_tickers(new_tickers, window_seconds)
    except ImportError:
        # Fallback to legacy implementation
        await self._legacy_reprocess_signals_for_new_tickers(new_tickers, window_seconds)
```

### 2. **Database Integration**
Uses existing `DBManager` methods:
- `get_rejected_signals_for_reprocessing()`
- `reapprove_signal()`
- `open_position()`
- `mark_position_as_closing()`

### 3. **Queue Integration**
Properly integrates with existing `approved_signal_queue` for forwarding to workers.

### 4. **Metrics Integration**
Updates shared metrics and broadcasts to WebSocket clients.

## Configuration

### Existing Configuration (unchanged)
```json
{
  "reprocess_enabled": true,
  "reprocess_window_seconds": 300
}
```

### Operating Modes
- **Disabled**: `reprocess_enabled: false`
- **Time Window**: `reprocess_enabled: true, reprocess_window_seconds: 300`
- **Infinite**: `reprocess_enabled: true, reprocess_window_seconds: 0`

## Logging

### Structured Logging Format
```
INFO: [ReprocessingEngine] Starting reprocessing cycle for 2 new tickers
INFO: [ReprocessingEngine:AAPL] Found 3 candidate signals in 300s window
DEBUG: [ReprocessingEngine:AAPL:abc123] Signal data validation: PASSED
DEBUG: [ReprocessingEngine:AAPL:abc123] Signal type: BUY (side='buy', type='buy')
INFO: [ReprocessingEngine:AAPL:abc123] Database re-approval: SUCCESS
DEBUG: [ReprocessingEngine:AAPL:abc123] Reconstruction strategy: original_signal
INFO: [ReprocessingEngine:AAPL:abc123] Signal reconstruction: SUCCESS
INFO: [ReprocessingEngine:AAPL:abc123] Added to forwarding queue: SUCCESS
INFO: [ReprocessingEngine:AAPL:abc123] Position management: OPENED
INFO: [ReprocessingEngine] Cycle completed in 1250ms: 2 successful, 0 failed, success rate: 100.0%
```

## Health Status Levels

### HEALTHY (95%+ success rate)
- All reprocessing operations completing successfully
- Fast processing times
- No significant errors

### WARNING (85-94% success rate)
- Some failures occurring but majority successful
- May indicate data quality issues or intermittent problems
- Monitoring recommended

### CRITICAL (<85% success rate)
- High failure rate indicating systematic problems
- Immediate investigation required
- May indicate database, network, or data corruption issues

### UNKNOWN
- No reprocessing cycles completed yet
- Engine just started or never triggered

## Testing

Comprehensive test suite includes:
- Unit tests for all core components
- Integration tests for full workflow
- Error scenario testing
- Health status calculation verification
- Mock database and queue interactions

**Run tests:**
```bash
python test_signal_reprocessing.py
```

## Troubleshooting

### Common Issues

#### 1. **Signals Not Being Reprocessed**
- Check reprocessing is enabled in configuration
- Verify tickers are actually entering Top-N list
- Check health status endpoint for errors
- Review logs for detailed error information

#### 2. **High Failure Rate**
- Check database connectivity and constraints
- Verify signal data integrity
- Review original_signal JSON structure
- Check queue capacity and worker status

#### 3. **Reconstruction Failures**
- Validate original_signal JSON format
- Check for missing required fields
- Review model validation requirements
- Consider data migration if schema changed

### Debug Commands

**Check health status:**
```bash
curl http://localhost/admin/reprocessing/health
```

**Manual trigger for testing:**
```bash
curl -X POST http://localhost/admin/reprocessing/trigger \
  -H "Content-Type: application/json" \
  -d '{"tickers": ["AAPL"], "window_seconds": 3600, "token": "your_token"}'
```

**Monitor logs:**
```bash
docker-compose logs -f trading-signal-processor | grep ReprocessingEngine
```

## Performance Considerations

### Optimization Features
- **Batch Processing**: Processes multiple tickers efficiently
- **Early Validation**: Fails fast on invalid data
- **Minimal Reconstruction**: Uses lightest reconstruction method that works
- **Controlled Timeouts**: Prevents hanging operations
- **Resource Cleanup**: Proper cleanup of resources and connections

### Scaling Guidelines
- **Max Signals Per Ticker**: Configurable limit (default: 100)
- **Processing Timeout**: 30 seconds per cycle (configurable)
- **Queue Capacity**: Depends on downstream forwarding worker capacity
- **Database Connection Pool**: Uses existing DBManager pool

## Future Enhancements

### Planned Improvements
1. **Persistent Metrics Storage**: Store metrics in database for historical analysis
2. **Alert System**: Automatic alerts when health status degrades
3. **Batch Reprocessing**: API for bulk reprocessing operations
4. **Configuration Hot Reload**: Dynamic configuration updates without restart
5. **Performance Profiling**: Detailed timing breakdowns for optimization

### Extensibility Points
- **Custom Validators**: Pluggable validation logic
- **Reconstruction Strategies**: Additional fallback methods
- **Metrics Exporters**: Prometheus, StatsD, etc.
- **Event Hooks**: Pre/post processing hooks for custom logic

---

**Implementation Status**: ✅ **COMPLETE AND FUNCTIONAL**  
**Test Coverage**: ✅ **COMPREHENSIVE**  
**Production Ready**: ✅ **YES**  
**Monitoring**: ✅ **FULL OBSERVABILITY**

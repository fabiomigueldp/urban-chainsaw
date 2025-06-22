# Database-Enhanced Signal Audit Trail - Migration Plan

## Overview
This document outlines the migration from in-memory signal tracking to a PostgreSQL-based solution with enhanced analytics capabilities.

## Benefits of Database Integration

### 1. Data Persistence
- Signals and events survive application restarts
- Historical data for trend analysis
- Backup and recovery capabilities

### 2. Performance & Scalability
- Indexed queries for fast lookups
- Efficient aggregations for metrics
- Handles millions of signals without memory constraints

### 3. Advanced Analytics
- Time-series analysis with PostgreSQL's built-in functions
- Complex queries with JOINs and window functions
- Real-time dashboards with materialized views

### 4. Data Integrity
- ACID transactions ensure consistency
- Foreign key constraints maintain referential integrity
- Automated cleanup with TTL policies

## Database Schema Design

### Tables Structure

#### 1. `signals` (Main signal records)
- `signal_id` (UUID, Primary Key)
- `ticker` (VARCHAR, Indexed)
- `normalised_ticker` (VARCHAR, Indexed)
- `side` (VARCHAR)
- `action` (VARCHAR)
- `price` (DECIMAL)
- `original_signal` (JSONB)
- `current_status` (ENUM)
- `current_location` (ENUM)
- `created_at` (TIMESTAMP, Indexed)
- `updated_at` (TIMESTAMP, Indexed)
- `total_processing_time` (INTERVAL)
- `error_count` (INTEGER, Default 0)
- `retry_count` (INTEGER, Default 0)
- `tags` (TEXT ARRAY)

#### 2. `signal_events` (Event tracking)
- `event_id` (BIGSERIAL, Primary Key)
- `signal_id` (UUID, Foreign Key to signals)
- `timestamp` (TIMESTAMP, Indexed)
- `event_type` (ENUM)
- `location` (ENUM)
- `worker_id` (VARCHAR)
- `details` (TEXT)
- `error_info` (JSONB)
- `http_status` (INTEGER)
- `response_data` (TEXT)

#### 3. `signal_metrics_summary` (Pre-computed metrics)
- `metric_id` (BIGSERIAL, Primary Key)
- `period_start` (TIMESTAMP)
- `period_end` (TIMESTAMP)
- `period_type` (ENUM: 'minute', 'hour', 'day')
- `total_signals` (INTEGER)
- `approved_signals` (INTEGER)
- `rejected_signals` (INTEGER)
- `forwarded_success` (INTEGER)
- `forwarded_errors` (INTEGER)
- `avg_processing_time` (INTERVAL)
- `created_at` (TIMESTAMP)

### Indexing Strategy
- Primary keys (automatic)
- `signals.ticker`, `signals.normalised_ticker`
- `signals.created_at`, `signals.updated_at`
- `signals.current_status`, `signals.current_location`
- `signal_events.signal_id`, `signal_events.timestamp`
- `signal_events.event_type`
- Composite indexes for common query patterns

## Implementation Plan

### Phase 1: Database Setup
1. Add PostgreSQL dependencies
2. Create database schema and migrations
3. Set up connection pooling
4. Create database service layer

### Phase 2: Dual-Write Implementation
1. Maintain current in-memory system
2. Add database writes alongside memory updates
3. Verify data consistency
4. Performance testing

### Phase 3: Read Migration
1. Update query endpoints to use database
2. Maintain backward compatibility
3. Add new database-specific features
4. Performance optimization

### Phase 4: Complete Migration
1. Remove in-memory tracking
2. Database-only operations
3. Add advanced analytics features
4. Cleanup legacy code

## Advanced Features with Database

### 1. Real-time Analytics
- Materialized views for instant metrics
- Triggers for automatic metric updates
- Streaming aggregations

### 2. Historical Analysis
- Time-series analysis of signal patterns
- Trend identification
- Performance regression detection

### 3. Advanced Querying
- Full-text search on signal details
- Complex filtering with multiple criteria
- Correlation analysis between signals

### 4. Data Retention Policies
- Automatic cleanup of old records
- Archival to cold storage
- Configurable retention periods

### 5. Monitoring & Alerting
- Database-level monitoring
- Performance alerts
- Data quality checks

## Performance Considerations

### Database Optimization
- Connection pooling (20-50 connections)
- Read replicas for analytics queries
- Partitioning for large tables by date
- Automatic vacuum and analyze

### Caching Strategy
- Redis for frequently accessed data
- Application-level caching for metrics
- Cache invalidation on data changes

### Async Operations
- Background processing for heavy analytics
- Async database writes where possible
- Queue-based processing for batch operations

## Migration Timeline

### Week 1: Setup & Schema
- Database installation and configuration
- Schema creation and testing
- Basic CRUD operations

### Week 2: Dual-Write Implementation
- Parallel data writing
- Consistency verification
- Performance baseline

### Week 3: Read Migration
- Update query endpoints
- Feature parity verification
- Performance optimization

### Week 4: Advanced Features
- New analytics capabilities
- Enhanced monitoring
- Documentation update

## Risk Mitigation

### Data Loss Prevention
- Backup strategies during migration
- Rollback procedures
- Data validation checkpoints

### Performance Monitoring
- Query performance tracking
- Resource utilization monitoring
- Alerting for degradation

### Compatibility
- API backward compatibility
- Feature flag for gradual rollout
- A/B testing capabilities

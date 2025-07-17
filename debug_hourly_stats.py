#!/usr/bin/env python3
"""
Debug script para verificar problema com get_hourly_signal_stats
"""

import asyncio
import sys
import os
from datetime import datetime, timedelta

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.DBManager import DBManager
from config import settings

async def debug_hourly_stats():
    """Debug da função get_hourly_signal_stats"""
    db_manager = DBManager()
    
    try:
        # Initialize database connection
        db_manager.initialize(settings.DATABASE_URL)
        print("Database connection initialized")
        
        async with db_manager.get_session() as session:
            # Test basic signal count
            from sqlalchemy import text, select, func
            from database.simple_models import Signal
            
            print("\n=== BASIC SIGNAL COUNT ===")
            total_count = await session.execute(select(func.count(Signal.signal_id)))
            total = total_count.scalar_one()
            print(f"Total signals in database: {total}")
            
            # Check recent signals
            print("\n=== RECENT SIGNALS ===")
            recent_query = select(Signal.created_at, Signal.status, Signal.ticker).order_by(Signal.created_at.desc()).limit(5)
            recent_result = await session.execute(recent_query)
            for row in recent_result:
                print(f"Signal: {row.ticker} | Status: {row.status} | Created: {row.created_at}")
            
            # Check time range for hourly stats
            print("\n=== TIME RANGE DEBUG ===")
            end_time = datetime.utcnow()
            start_time = end_time - timedelta(hours=24)
            print(f"Query range: {start_time} to {end_time}")
            
            # Test the exact hourly query
            print("\n=== HOURLY QUERY TEST ===")
            hourly_query = text("""
                SELECT 
                    DATE_TRUNC('hour', created_at) as hour,
                    COUNT(*) as total_signals,
                    COUNT(CASE WHEN status = 'approved' THEN 1 END) as approved_signals,
                    COUNT(CASE WHEN status = 'rejected' THEN 1 END) as rejected_signals,
                    COUNT(CASE WHEN status = 'forwarded_success' THEN 1 END) as forwarded_signals
                FROM signals s
                WHERE s.created_at >= :start_time AND s.created_at <= :end_time
                GROUP BY DATE_TRUNC('hour', created_at)
                ORDER BY hour DESC
                LIMIT 5
            """)
            
            hourly_result = await session.execute(hourly_query, {
                'start_time': start_time,
                'end_time': end_time
            })
            
            print("Recent hourly data:")
            for row in hourly_result:
                print(f"Hour: {row.hour} | Total: {row.total_signals} | Approved: {row.approved_signals} | Rejected: {row.rejected_signals} | Forwarded: {row.forwarded_signals}")
            
            # Check signals in the last hour
            print("\n=== LAST HOUR SIGNALS ===")
            last_hour = end_time - timedelta(hours=1)
            last_hour_query = select(func.count(Signal.signal_id)).where(Signal.created_at >= last_hour)
            last_hour_count = await session.execute(last_hour_query)
            print(f"Signals in last hour: {last_hour_count.scalar_one()}")
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await db_manager.close()

if __name__ == "__main__":
    asyncio.run(debug_hourly_stats())

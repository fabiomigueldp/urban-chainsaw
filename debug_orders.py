#!/usr/bin/env python3
"""
Debug script para testar get_positions_with_details isoladamente
"""

import asyncio
import sys
import os
from datetime import datetime

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.DBManager import DBManager
from config import settings

async def test_get_positions():
    """Testa a função get_positions_with_details"""
    db_manager = DBManager()
    
    try:
        # Initialize database connection
        db_manager.initialize(settings.DATABASE_URL)
        print("Database connection initialized")
        
        # Test the problematic function
        print("Calling get_positions_with_details...")
        orders = await db_manager.get_positions_with_details()
        print(f"Success! Got {len(orders)} orders")
        
        # Print first few orders for debugging
        for i, order in enumerate(orders[:3]):
            print(f"Order {i+1}: {order}")
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await db_manager.close()

if __name__ == "__main__":
    asyncio.run(test_get_positions())

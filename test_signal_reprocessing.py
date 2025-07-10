"""
Test suite for the Signal Reprocessing Engine.

This module contains comprehensive tests for the signal reprocessing functionality,
including unit tests for individual components and integration tests for the full workflow.
"""

import asyncio
import pytest
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, Any, List

# Import the modules to be tested
from signal_reprocessing_engine import (
    SignalReprocessingEngine,
    SignalValidator,
    SignalReconstructor,
    ReprocessingMetrics,
    ReprocessingStatus,
    SignalReprocessingOutcome
)


class TestSignalValidator:
    """Test cases for the SignalValidator class."""
    
    def test_is_buy_signal_explicit_buy_side(self):
        """Test detection of BUY signal from 'side' field."""
        signal_data = {"side": "buy", "signal_type": "", "original_signal": {}}
        assert SignalValidator.is_buy_signal(signal_data) is True
    
    def test_is_buy_signal_explicit_sell_side(self):
        """Test detection of SELL signal from 'side' field."""
        signal_data = {"side": "sell", "signal_type": "", "original_signal": {}}
        assert SignalValidator.is_buy_signal(signal_data) is False
    
    def test_is_buy_signal_signal_type(self):
        """Test detection from 'signal_type' field."""
        signal_data = {"side": "", "signal_type": "buy", "original_signal": {}}
        assert SignalValidator.is_buy_signal(signal_data) is True
    
    def test_is_buy_signal_action_in_original(self):
        """Test detection from 'action' in original_signal."""
        signal_data = {
            "side": "",
            "signal_type": "",
            "original_signal": {"action": "long"}
        }
        assert SignalValidator.is_buy_signal(signal_data) is True
    
    def test_is_buy_signal_case_insensitive(self):
        """Test case insensitive detection."""
        signal_data = {"side": "BUY", "signal_type": "", "original_signal": {}}
        assert SignalValidator.is_buy_signal(signal_data) is True
    
    def test_is_buy_signal_with_whitespace(self):
        """Test handling of whitespace in signal data."""
        signal_data = {"side": " buy ", "signal_type": "", "original_signal": {}}
        assert SignalValidator.is_buy_signal(signal_data) is True
    
    def test_is_buy_signal_default_empty(self):
        """Test default behavior with empty fields (should default to BUY)."""
        signal_data = {"side": "", "signal_type": "", "original_signal": {}}
        assert SignalValidator.is_buy_signal(signal_data) is True
    
    def test_is_buy_signal_sell_priority(self):
        """Test that explicit sell indicators have priority."""
        signal_data = {"side": "sell", "signal_type": "buy", "original_signal": {}}
        assert SignalValidator.is_buy_signal(signal_data) is False
    
    def test_validate_signal_data_valid(self):
        """Test validation of valid signal data."""
        signal_data = {
            "signal_id": "test-123",
            "ticker": "AAPL",
            "normalised_ticker": "AAPL",
            "original_signal": {"ticker": "AAPL"}
        }
        is_valid, error = SignalValidator.validate_signal_data(signal_data)
        assert is_valid is True
        assert error is None
    
    def test_validate_signal_data_missing_id(self):
        """Test validation with missing signal_id."""
        signal_data = {"ticker": "AAPL", "normalised_ticker": "AAPL"}
        is_valid, error = SignalValidator.validate_signal_data(signal_data)
        assert is_valid is False
        assert "signal_id" in error
    
    def test_validate_signal_data_missing_ticker(self):
        """Test validation with missing ticker."""
        signal_data = {"signal_id": "test-123", "normalised_ticker": "AAPL"}
        is_valid, error = SignalValidator.validate_signal_data(signal_data)
        assert is_valid is False
        assert "ticker" in error


class TestSignalReconstructor:
    """Test cases for the SignalReconstructor class."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.reconstructor = SignalReconstructor()
    
    @pytest.mark.asyncio
    async def test_reconstruct_from_original_signal(self):
        """Test reconstruction from original_signal data."""
        self.setUp()
        signal_data = {
            "signal_id": "test-123",
            "ticker": "AAPL",
            "original_signal": {
                "ticker": "AAPL",
                "side": "buy",
                "price": 150.25,
                "time": "2024-01-01T10:00:00Z"
            }
        }
        
        with patch('signal_reprocessing_engine.SignalPydanticModel') as mock_signal:
            mock_signal.return_value = MagicMock()
            result = await self.reconstructor.reconstruct_signal(signal_data)
            
            assert result is not None
            mock_signal.assert_called_once()
            # Verify signal_id was added to the original_signal data
            call_args = mock_signal.call_args[1]
            assert call_args["signal_id"] == "test-123"
    
    @pytest.mark.asyncio
    async def test_reconstruct_from_basic_fields(self):
        """Test reconstruction from basic database fields when original_signal is missing."""
        self.setUp()
        signal_data = {
            "signal_id": "test-123",
            "ticker": "AAPL",
            "side": "buy",
            "price": 150.25,
            "created_at": datetime.utcnow()
        }
        
        with patch('signal_reprocessing_engine.SignalPydanticModel') as mock_signal:
            # First call (from_original_signal) fails, second call (from_basic_fields) succeeds
            mock_signal.side_effect = [Exception("Original failed"), MagicMock()]
            result = await self.reconstructor.reconstruct_signal(signal_data)
            
            assert result is not None
            assert mock_signal.call_count == 2
    
    @pytest.mark.asyncio
    async def test_reconstruct_minimal_fallback(self):
        """Test minimal signal creation as last resort."""
        self.setUp()
        signal_data = {
            "signal_id": "test-123",
            "ticker": "AAPL"
        }
        
        with patch('signal_reprocessing_engine.SignalPydanticModel') as mock_signal:
            # First two calls fail, third call (minimal) succeeds
            mock_signal.side_effect = [
                Exception("Original failed"), 
                Exception("Basic failed"), 
                MagicMock()
            ]
            result = await self.reconstructor.reconstruct_signal(signal_data)
            
            assert result is not None
            assert mock_signal.call_count == 3


class TestReprocessingMetrics:
    """Test cases for the ReprocessingMetrics class."""
    
    def test_reset_metrics(self):
        """Test metrics reset functionality."""
        metrics = ReprocessingMetrics()
        metrics.signals_found = 10
        metrics.signals_successful = 8
        metrics.tickers_processed.add("AAPL")
        
        metrics.reset()
        
        assert metrics.signals_found == 0
        assert metrics.signals_successful == 0
        assert len(metrics.tickers_processed) == 0
    
    def test_success_rate_calculation(self):
        """Test success rate calculation."""
        metrics = ReprocessingMetrics()
        
        # Test with no signals processed
        assert metrics.get_success_rate() == 0.0
        
        # Test with some successful signals
        metrics.signals_processed = 10
        metrics.signals_successful = 8
        assert metrics.get_success_rate() == 80.0
        
        # Test with 100% success
        metrics.signals_successful = 10
        assert metrics.get_success_rate() == 100.0


class TestSignalReprocessingEngine:
    """Integration tests for the SignalReprocessingEngine."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.mock_db_manager = AsyncMock()
        self.mock_queue = AsyncMock()
        self.engine = SignalReprocessingEngine(self.mock_db_manager, self.mock_queue)
    
    @pytest.mark.asyncio
    async def test_process_new_tickers_success(self):
        """Test successful processing of new tickers."""
        self.setUp()
        
        # Mock database response
        self.mock_db_manager.get_rejected_signals_for_reprocessing.return_value = [
            {
                "signal_id": "test-123",
                "ticker": "AAPL",
                "normalised_ticker": "AAPL",
                "side": "buy",
                "signal_type": "buy",
                "original_signal": {"ticker": "AAPL", "side": "buy"}
            }
        ]
        
        # Mock re-approval success
        self.mock_db_manager.reapprove_signal.return_value = True
        
        # Mock position operations
        self.mock_db_manager.open_position = AsyncMock()
        
        # Mock signal reconstruction
        with patch.object(self.engine.reconstructor, 'reconstruct_signal') as mock_reconstruct:
            mock_signal = MagicMock()
            mock_signal.normalised_ticker.return_value = "AAPL"
            mock_reconstruct.return_value = mock_signal
            
            # Mock broadcasting
            with patch('signal_reprocessing_engine.comm_engine') as mock_comm:
                with patch('signal_reprocessing_engine.get_sell_all_list_data') as mock_sell_all:
                    mock_sell_all.return_value = {}
                    
                    result = await self.engine.process_new_tickers({"AAPL"}, 300)
        
        assert result.success is True
        assert result.tickers_processed == 1
        assert result.signals_found == 1
        assert result.signals_reprocessed == 1
        assert result.signals_failed == 0
        
        # Verify database calls
        self.mock_db_manager.get_rejected_signals_for_reprocessing.assert_called_once_with("AAPL", 300)
        self.mock_db_manager.reapprove_signal.assert_called_once()
        self.mock_queue.put.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_process_new_tickers_no_signals_found(self):
        """Test processing when no rejected signals are found."""
        self.setUp()
        
        # Mock empty database response
        self.mock_db_manager.get_rejected_signals_for_reprocessing.return_value = []
        
        result = await self.engine.process_new_tickers({"AAPL"}, 300)
        
        assert result.success is True
        assert result.tickers_processed == 1
        assert result.signals_found == 0
        assert result.signals_reprocessed == 0
        assert result.signals_failed == 0
    
    @pytest.mark.asyncio
    async def test_process_single_signal_validation_failure(self):
        """Test processing of a signal that fails validation."""
        self.setUp()
        
        signal_data = {"signal_id": "test-123"}  # Missing required fields
        
        outcome = await self.engine._process_single_signal(signal_data, "AAPL")
        
        assert outcome.success is False
        assert outcome.status == ReprocessingStatus.FAILED_VALIDATION
        assert "ticker" in outcome.error_message
    
    @pytest.mark.asyncio
    async def test_process_single_signal_non_buy_signal(self):
        """Test processing of a non-BUY signal."""
        self.setUp()
        
        signal_data = {
            "signal_id": "test-123",
            "ticker": "AAPL",
            "normalised_ticker": "AAPL",
            "side": "sell",  # This is a SELL signal
            "signal_type": "sell"
        }
        
        outcome = await self.engine._process_single_signal(signal_data, "AAPL")
        
        assert outcome.success is False
        assert outcome.status == ReprocessingStatus.SKIPPED_NON_BUY
    
    @pytest.mark.asyncio
    async def test_process_single_signal_database_error(self):
        """Test processing when database re-approval fails."""
        self.setUp()
        
        signal_data = {
            "signal_id": "test-123",
            "ticker": "AAPL",
            "normalised_ticker": "AAPL",
            "side": "buy",
            "signal_type": "buy"
        }
        
        # Mock database failure
        self.mock_db_manager.reapprove_signal.return_value = False
        
        outcome = await self.engine._process_single_signal(signal_data, "AAPL")
        
        assert outcome.success is False
        assert outcome.status == ReprocessingStatus.FAILED_DATABASE
    
    def test_get_health_status_no_runs(self):
        """Test health status when no reprocessing has been performed."""
        self.setUp()
        
        health = self.engine.get_health_status()
        
        assert health["status"] == "UNKNOWN"
        assert health["last_run"] is None
    
    def test_get_health_status_healthy(self):
        """Test health status calculation for healthy engine."""
        self.setUp()
        
        # Simulate metrics from a successful run
        self.engine.metrics.last_run_timestamp = datetime.utcnow()
        self.engine.metrics.signals_processed = 10
        self.engine.metrics.signals_successful = 10
        self.engine.metrics.last_run_duration_ms = 1500
        
        health = self.engine.get_health_status()
        
        assert health["status"] == "HEALTHY"
        assert health["success_rate"] == 100.0
        assert health["signals_processed"] == 10
        assert health["signals_successful"] == 10
    
    def test_get_health_status_warning(self):
        """Test health status calculation for warning state."""
        self.setUp()
        
        # Simulate metrics with moderate success rate
        self.engine.metrics.last_run_timestamp = datetime.utcnow()
        self.engine.metrics.signals_processed = 10
        self.engine.metrics.signals_successful = 9  # 90% success rate
        
        health = self.engine.get_health_status()
        
        assert health["status"] == "WARNING"
        assert health["success_rate"] == 90.0
    
    def test_get_health_status_critical(self):
        """Test health status calculation for critical state."""
        self.setUp()
        
        # Simulate metrics with low success rate
        self.engine.metrics.last_run_timestamp = datetime.utcnow()
        self.engine.metrics.signals_processed = 10
        self.engine.metrics.signals_successful = 5  # 50% success rate
        
        health = self.engine.get_health_status()
        
        assert health["status"] == "CRITICAL"
        assert health["success_rate"] == 50.0


class TestIntegrationScenarios:
    """Integration test scenarios for complete workflows."""
    
    @pytest.mark.asyncio
    async def test_complete_reprocessing_workflow(self):
        """Test a complete reprocessing workflow from start to finish."""
        # This would be a comprehensive integration test that:
        # 1. Sets up a test database with rejected signals
        # 2. Triggers reprocessing for new tickers
        # 3. Verifies signals are re-approved and queued
        # 4. Checks position management
        # 5. Validates metrics updates
        pass  # Implementation would require test database setup
    
    @pytest.mark.asyncio
    async def test_error_recovery_scenarios(self):
        """Test error recovery in various failure scenarios."""
        # This would test:
        # 1. Database connection failures
        # 2. Queue full scenarios
        # 3. Signal reconstruction failures
        # 4. Partial processing failures
        pass  # Implementation would require controlled error injection


if __name__ == "__main__":
    # Run tests with pytest
    import sys
    import subprocess
    
    print("Running Signal Reprocessing Engine tests...")
    result = subprocess.run([sys.executable, "-m", "pytest", __file__, "-v"], 
                          capture_output=True, text=True)
    
    print(result.stdout)
    if result.stderr:
        print("Errors:", result.stderr)
    
    sys.exit(result.returncode)

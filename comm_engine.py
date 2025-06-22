"""
Communication Engine for Trading Signal Processor
Centralized WebSocket communication system for real-time UI updates
Based on the proven metrics update architecture
Enhanced with robust audit trail management and consistency checks
"""

import asyncio
import logging
import time
import json
from typing import Any, Dict, List, Optional, Set, Union
from dataclasses import dataclass
from datetime import datetime

# Configuração básica de logging para podermos ver as mensagens
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
_logger = logging.getLogger(__name__)

@dataclass
class BroadcastMetrics:
    """Track broadcast metrics for monitoring."""
    total_broadcasts: int = 0
    failed_broadcasts: int = 0
    audit_entries_sent: int = 0
    last_broadcast_time: float = 0.0

class AuditEntryValidator:
    """Validate audit entries for consistency."""

    @staticmethod
    def validate_entry(entry: Dict[str, Any]) -> tuple[bool, List[str]]:
        """Validate audit entry structure and data."""
        errors = []

        # Required fields
        required_fields = ['signal_id', 'ticker', 'status', 'timestamp']
        for field in required_fields:
            if field not in entry or entry[field] is None:
                errors.append(f"Missing required field: {field}")

        # Validate signal_id format
        if 'signal_id' in entry and entry['signal_id']:
            if not isinstance(entry['signal_id'], str) or len(entry['signal_id']) < 10:
                errors.append("Invalid signal_id format")

        # Validate status
        valid_statuses = [
            'received', 'queued_processing', 'processing', 'approved',
            'rejected', 'queued_forwarding', 'forwarding', 'forwarded_success',
            'forwarded_http_error', 'forwarded_timeout', 'forwarded_generic_error', 'discarded'
        ]
        if 'status' in entry and entry['status'] not in valid_statuses:
            errors.append(f"Invalid status: {entry['status']}")

        # Validate timestamp
        if 'timestamp' in entry and entry['timestamp']:
            try:
                if isinstance(entry['timestamp'], str):
                    # Handle 'Z' for UTC timezone
                    datetime.fromisoformat(entry['timestamp'].replace('Z', '+00:00'))
                elif isinstance(entry['timestamp'], (int, float)):
                    datetime.fromtimestamp(entry['timestamp'])
            except (ValueError, OSError):
                errors.append("Invalid timestamp format")

        return len(errors) == 0, errors

class CommunicationEngine:
    """Centralized communication engine for real-time UI updates with enhanced reliability."""

    def __init__(self):
        self.active_connections: List[Any] = []  # WebSocket connections
        self.metrics = BroadcastMetrics()
        self.validator = AuditEntryValidator()
        self._audit_entry_cache: Dict[str, Dict[str, Any]] = {}  # signal_id -> latest_entry
        self._connection_states: Dict[Any, Dict[str, Any]] = {}  # connection -> state info

    async def add_connection(self, websocket: Any):
        """Add a new WebSocket connection with state tracking."""
        self.active_connections.append(websocket)
        client_repr = str(websocket.client) if hasattr(websocket, 'client') else 'unknown_client'
        self._connection_states[websocket] = {
            'connected_at': time.time(),
            'last_ping': time.time(),
            'audit_entries_sent': 0,
            'client_info': client_repr
        }
        _logger.info(f"Admin WebSocket connection established: {client_repr}")

        # Send current audit entry cache to new connection
        if self._audit_entry_cache:
            try:
                await self._send_audit_cache_to_connection(websocket)
            except Exception as e:
                _logger.warning(f"Failed to send audit cache to new connection {client_repr}: {e}")

    async def remove_connection(self, websocket: Any):
        """Remove a WebSocket connection and cleanup state."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            self._connection_states.pop(websocket, None)
            client_repr = str(websocket.client) if hasattr(websocket, 'client') else 'unknown_client'
            _logger.info(f"Admin WebSocket connection removed: {client_repr}")

    async def _send_audit_cache_to_connection(self, websocket: Any):
        """Send current audit entry cache to a specific connection."""
        if not self._audit_entry_cache:
            return

        # Send entries sorted by timestamp (newest first)
        entries = sorted(
            self._audit_entry_cache.values(),
            key=lambda x: x.get('updated_at', x.get('timestamp', 0)),
            reverse=True
        )

        message = {
            "event": "audit_log_data",
            "data": {
                "entries": entries[:100],  # Send last 100 entries
                "total_count": len(entries),
                "cache_sync": True
            }
        }

        await websocket.send_json(message)
        # Ensure websocket still in state before updating
        if websocket in self._connection_states:
            self._connection_states[websocket]['audit_entries_sent'] = len(entries[:100])

    # --- CORREÇÃO: A indentação deste método e dos seguintes foi corrigida para pertencer à classe ---
    async def broadcast(self, event_type: str, data: Any) -> tuple[int, int]:
        """
        Broadcast a message to all connected clients with enhanced error handling.
        Returns (successful_sends, failed_sends)
        """
        if not self.active_connections:
            _logger.debug(f"No active admin connections for broadcast: {event_type}")
            return 0, 0

        message = {"event": event_type, "data": data}
        self.metrics.total_broadcasts += 1
        self.metrics.last_broadcast_time = time.time()

        _logger.debug(f"Broadcasting to {len(self.active_connections)} admin clients: {event_type}")

        successful_sends = 0
        failed_sends = 0
        connections_to_remove = []

        # Iterate over a copy of the list in case of disconnections during broadcast
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
                successful_sends += 1

                # Update connection state
                if connection in self._connection_states:
                    self._connection_states[connection]['last_ping'] = time.time()
                    if event_type == 'new_audit_entry':
                        self._connection_states[connection]['audit_entries_sent'] += 1

            except Exception as e:
                client_repr = getattr(connection, 'client', 'unknown')
                _logger.warning(f"Failed to send to client {client_repr}: {e}. Marking for removal.")
                failed_sends += 1
                connections_to_remove.append(connection)

        # Remove failed connections after the loop
        if connections_to_remove:
            for connection in connections_to_remove:
                await self.remove_connection(connection)

        if failed_sends > 0:
            self.metrics.failed_broadcasts += failed_sends

        return successful_sends, failed_sends

    # === SPECIFIC BROADCAST METHODS (Following metrics pattern) ===

    async def broadcast_system_info_update(self, data: Dict[str, Any]):
        """Broadcast system information update - INSTANT like metrics."""
        await self.broadcast("system_info_update", data)

    async def broadcast_finviz_status_update(self, data: Dict[str, Any]):
        """Broadcast Finviz engine status update - INSTANT like metrics."""
        await self.broadcast("finviz_status_update", data)

    async def broadcast_overview_update(self, data: Dict[str, Any]):
        """Broadcast overview data update - INSTANT like metrics."""
        await self.broadcast("overview_update", data)

    async def broadcast_queue_status_update(self, data: Dict[str, Any]):
        """Broadcast queue status update - INSTANT like metrics."""
        await self.broadcast("queue_status_update", data)

    async def broadcast_webhook_config_update(self, data: Dict[str, Any]):
        """Broadcast webhook configuration update - INSTANT like metrics."""
        await self.broadcast("webhook_config_update", data)

    async def broadcast_webhook_rate_limiter_update(self, data: Dict[str, Any]):
        """Broadcast webhook rate limiter update - INSTANT like metrics."""
        await self.broadcast("webhook_rate_limiter_update", data)

    async def broadcast_ticker_list_update(self, data: Dict[str, Any]):
        """Broadcast ticker list update - INSTANT like metrics."""
        await self.broadcast("ticker_list_update", data)

    async def broadcast_auth_status_update(self, data: Dict[str, Any]):
        """Broadcast authentication status update - INSTANT like metrics."""
        await self.broadcast("auth_status_update", data)

    async def broadcast_sell_all_list_update(self, data: List[str]):
        """Broadcast sell all list update - INSTANT like metrics."""
        await self.broadcast("sell_all_list_update", data)

    async def broadcast_new_audit_entry(self, data: Dict[str, Any]):
        """Broadcast new audit entry - INSTANT like metrics."""
        # Here we also update the cache
        is_valid, errors = self.validator.validate_entry(data)
        if not is_valid:
            _logger.error(f"Invalid audit entry provided for broadcast. Errors: {errors}. Entry: {data}")
            return
            
        signal_id = data.get('signal_id')
        if signal_id:
            # Add/update the entry in our cache before broadcasting
            self._audit_entry_cache[signal_id] = data
            _logger.debug(f"Updated audit cache for signal_id: {signal_id}")
            
        await self.broadcast("new_audit_entry", data)
        self.metrics.audit_entries_sent += 1

    # === TRIGGER UPDATE METHODS (Refactored to accept data as parameters) ===

    async def trigger_system_info_update(self, data: Optional[Dict[str, Any]] = None):
        """Trigger system info update broadcast."""
        if data is None:
            _logger.warning("trigger_system_info_update called without data")
            return
        await self.broadcast_system_info_update(data)

    async def trigger_finviz_status_update(self, data: Optional[Dict[str, Any]] = None):
        """Trigger Finviz status update broadcast."""
        if data is None:
            _logger.warning("trigger_finviz_status_update called without data")
            return
        await self.broadcast_finviz_status_update(data)

    async def trigger_overview_update(self, data: Optional[Dict[str, Any]] = None):
        """Trigger overview update broadcast."""
        if data is None:
            _logger.warning("trigger_overview_update called without data")
            return
        await self.broadcast_overview_update(data)

    async def trigger_queue_status_update(self, data: Optional[Dict[str, Any]] = None):
        """Trigger queue status update broadcast."""
        if data is None:
            _logger.warning("trigger_queue_status_update called without data")
            return
        await self.broadcast_queue_status_update(data)

    async def trigger_webhook_config_update(self, data: Optional[Dict[str, Any]] = None):
        """Trigger webhook config update broadcast."""
        if data is None:
            _logger.warning("trigger_webhook_config_update called without data")
            return
        await self.broadcast_webhook_config_update(data)

    async def trigger_webhook_rate_limiter_update(self, data: Optional[Dict[str, Any]] = None):
        """Trigger webhook rate limiter update broadcast."""
        if data is None:
            _logger.warning("trigger_webhook_rate_limiter_update called without data")
            return
        await self.broadcast_webhook_rate_limiter_update(data)

    async def trigger_ticker_list_update(self, data: Optional[Dict[str, Any]] = None):
        """Trigger ticker list update broadcast."""
        if data is None:
            _logger.warning("trigger_ticker_list_update called without data")
            return
        await self.broadcast_ticker_list_update(data)

    async def trigger_auth_status_update(self, data: Optional[Dict[str, Any]] = None):
        """Trigger auth status update broadcast."""
        if data is None:
            _logger.warning("trigger_auth_status_update called without data")
            return
        await self.broadcast_auth_status_update(data)

    async def trigger_sell_all_list_update(self, data: Optional[List[str]] = None):
        """Trigger sell all list update broadcast."""
        if data is None:
            _logger.warning("trigger_sell_all_list_update called without data")
            return
        await self.broadcast_sell_all_list_update(data)

    async def trigger_new_audit_entry(self, audit_entry: Dict[str, Any]):
        """Trigger new audit entry broadcast."""
        await self.broadcast_new_audit_entry(audit_entry)


# Global instance - following the shared_state pattern
comm_engine = CommunicationEngine()

# Exemplo de como usar (apenas para demonstração)
async def main_example():
    # Simula um cliente WebSocket fake para teste
    class FakeWebSocket:
        def __init__(self, client_id):
            self.client = client_id
            self.sent_messages = []

        async def send_json(self, data):
            print(f"[{self.client}] Recebeu: {json.dumps(data, indent=2)}")
            self.sent_messages.append(data)
            await asyncio.sleep(0.01) # simula latência de rede

    print("--- Testando Communication Engine ---")

    # Criando clientes falsos
    ws1 = FakeWebSocket("Client-1")
    ws2 = FakeWebSocket("Client-2")

    # Adicionando conexões
    await comm_engine.add_connection(ws1)
    await comm_engine.add_connection(ws2)

    # Enviando uma atualização de sistema
    sys_info = {"cpu_load": 0.75, "memory_usage": "4.2GB"}
    print("\n[BROADCAST] Enviando atualização de System Info...")
    await comm_engine.trigger_system_info_update(data=sys_info)

    # Enviando uma nova entrada de auditoria
    audit_entry = {
        "signal_id": f"sig_{int(time.time())}",
        "ticker": "AAPL",
        "status": "received",
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }
    print("\n[BROADCAST] Enviando nova entrada de Auditoria...")
    await comm_engine.trigger_new_audit_entry(audit_entry=audit_entry)
    
    # Criando um terceiro cliente para testar o envio do cache
    print("\n[CONNECT] Novo cliente se conectando, deve receber o cache de auditoria...")
    ws3 = FakeWebSocket("Client-3")
    await comm_engine.add_connection(ws3) # O cache será enviado aqui

    # Removendo uma conexão
    print("\n[DISCONNECT] Removendo Client-1...")
    await comm_engine.remove_connection(ws1)

    # Enviando outra atualização, ws1 não deve receber
    print("\n[BROADCAST] Enviando atualização de Fila...")
    queue_status = {"processing_queue": 5, "forwarding_queue": 2}
    await comm_engine.trigger_queue_status_update(data=queue_status)

    print("\n--- Fim do Teste ---")
    print(f"Métricas Finais: {comm_engine.metrics}")


if __name__ == "__main__":
    # Para rodar o exemplo, você pode executar este arquivo diretamente
    asyncio.run(main_example())
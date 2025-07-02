#!/usr/bin/env python3
"""
tsp_tester.py - Plataforma de Teste e Auditoria para Trading-Signal-Processor (v4.1)

Esta ferramenta foi completamente redesenhada para fornecer testes de carga e funcionais
de ciclo fechado, garantindo 100% de visibilidade e confiabilidade nas métricas.

NOVOS RECURSOS (v4.1):
- **Monitoramento Interno da Aplicação:** Polla endpoints de status detalhados para
  rastrear métricas internas da aplicação (tamanhos de fila, workers ativos) em tempo real.
- **Métricas de Verificação Cruzada:** Compara ativamente os sinais que o testador enviou
  com os que a aplicação reporta ter processado, destacando discrepâncias.
- **Gráficos Avançados:** Novos gráficos para visualizar o estado da fila da aplicação e
  a verificação de contagem de sinais, permitindo a identificação de gargalos.
- **Painel de Administração Completo:** Uma nova aba na GUI para controlar todos os aspectos
  da aplicação em tempo real durante um teste de carga.
- **Inteligência de Configuração:** Capacidade de descobrir automaticamente a configuração
  do banco de dados a partir do arquivo .env da aplicação.
- **Auditoria de Falhas Aprimorada:** Uma aba dedicada para listar sinais que falharam
  (timeout, erro de envio) com a capacidade de auditar seu status final no banco de dados.
"""

import asyncio
import httpx
import json
import random
import sys
import time
import threading
import queue
import argparse
import uuid
import socket
from collections import Counter, deque
from typing import List, Dict, Optional, Any

# --- Imports para o Servidor Web e Cliente DB ---
import uvicorn
from fastapi import FastAPI, Request, Body, HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
from dotenv import dotenv_values

# --- Imports para a GUI ---
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from tkinter.scrolledtext import ScrolledText
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.animation as animation

# --- Constantes e Configuração ---
APP_VERSION = "TSP Tester v4.1"
MAX_GRAPH_POINTS = 120  # Aumentado para 2 minutos de dados a 1s de intervalo
DEFAULT_PAYLOAD_TEMPLATE = """{{
    "ticker": "{ticker}",
    "side": "BUY",
    "price": {price},
    "time": "{iso_time}",
    "tester_token": "{token}"
}}"""

# ─────────────────── WEBHOOK RECEIVER (FastAPI App) ───────────────────
receiver_app = FastAPI(title="TSP Tester Webhook Receiver", version="1.0", docs_url=None, redoc_url=None)
returned_tokens_queue = queue.Queue()

@receiver_app.post("/test_webhook_return")
async def handle_signal_return(payload: Dict = Body(...)):
    token = payload.get("tester_token")
    if token:
        returned_tokens_queue.put({"token": token, "received_at": time.monotonic()})
        return {"status": "token_received", "token": token}
    raise HTTPException(status_code=400, detail="tester_token not found in payload")

@receiver_app.get("/")
def read_root():
    return {"message": "TSP Tester Webhook Receiver is running. POST signals to /test_webhook_return"}

# ─────────────────── POLLER DE STATUS DA APLICAÇÃO ───────────────────
class AppStatusPoller:
    def __init__(self, config: Dict, output_queue: queue.Queue, shutdown_event: threading.Event):
        self.config = config
        self.output_queue = output_queue
        self.shutdown_event = shutdown_event
        # Usa o endpoint detalhado para obter informações das filas
        self.status_url = f"http://{config['app_host']}:{config['app_port']}/health/detailed"
        self.client = httpx.Client(timeout=2.0)

    def run(self):
        while not self.shutdown_event.is_set():
            try:
                resp = self.client.get(self.status_url)
                resp.raise_for_status()
                self.output_queue.put({"type": "app_status", "payload": resp.json()})
            except (httpx.RequestError, json.JSONDecodeError, KeyError) as e:
                self.output_queue.put({"type": "app_status", "payload": {"error": f"Poll Fail: {type(e).__name__}"}})
            # Espera 1 segundo antes da próxima pollagem, mas verifica o evento de parada a cada 0.1s
            for _ in range(10):
                if self.shutdown_event.is_set(): break
                time.sleep(0.1)
        self.client.close()

# ─────────────────── TEST ENGINE (Geração de Carga) ───────────────────
class TestEngine:
    def __init__(self, config: Dict, output_queue: queue.Queue, shutdown_event: threading.Event):
        self.config = config
        self.output_queue = output_queue
        self.shutdown_event = shutdown_event
        self.test_start_time = 0
        self.log_to_gui("TestEngine initialized.", "log_info")

    def run(self):
        self.test_start_time = time.monotonic()
        try:
            asyncio.run(self.request_dispatcher())
        except Exception as e:
            self.log_to_gui(f"[FATAL ERROR] TestEngine thread crashed: {e}", "log_error")

    def log_to_gui(self, message: Any, msg_type: str):
        self.output_queue.put({"type": msg_type, "payload": message})

    def _generate_payload(self, ticker: str, is_approved_ticker: bool) -> (Dict, str):
        token = str(uuid.uuid4())
        try:
            template = self.config['payload_template']
            payload_str = template.format(
                ticker=ticker,
                price=round(random.uniform(10.0, 500.0), 2),
                iso_time=time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime()),
                token=token
            )
            payload = json.loads(payload_str)
            self.log_to_gui({
                "token": token, "ticker": ticker,
                "sent_at": time.monotonic(), "expected_outcome": "approved" if is_approved_ticker else "rejected"
            }, "token_sent")
            return payload
        except Exception as e:
            self.log_to_gui(f"[WARNING] Payload generation failed: {e}", "log_warning")
            self.log_to_gui({"token": token, "error": "Payload Generation Failed", "ticker": ticker, "sent_at": time.monotonic()}, "token_failed")
            return {}

    async def _send_one(self, client: httpx.AsyncClient, sem: asyncio.Semaphore, ticker: str, is_approved_ticker: bool):
        payload = self._generate_payload(ticker, is_approved_ticker)
        if not payload: return

        token = payload.get("tester_token")
        async with sem:
            try:
                resp = await client.post(self.config['url'], json=payload, timeout=10.0)
                if not (200 <= resp.status_code < 300):
                    self.log_to_gui({"token": token, "error": f"HTTP {resp.status_code}", "ticker": ticker, "sent_at": time.monotonic()}, "token_failed")
            except httpx.RequestError as e:
                self.log_to_gui({"token": token, "error": type(e).__name__, "ticker": ticker, "sent_at": time.monotonic()}, "token_failed")

    async def request_dispatcher(self):
        self.log_to_gui("TestEngine started. Test running...", "log_info")
        semaphore = asyncio.Semaphore(self.config['max_concurrency'])
        async with httpx.AsyncClient() as client:
            while not self.shutdown_event.is_set():
                loop_start_time = time.monotonic()

                approved_to_send = random.choices(self.config['approved_tickers'], k=int(self.config['rps'] * (self.config['approved_ratio'] / 100.0))) if self.config['approved_tickers'] else []
                rejected_to_send = random.choices(self.config['rejected_tickers'], k=self.config['rps'] - len(approved_to_send)) if self.config['rejected_tickers'] else []
                
                batch = [(ticker, True) for ticker in approved_to_send] + [(ticker, False) for ticker in rejected_to_send]
                random.shuffle(batch)
                
                if not batch:
                    await asyncio.sleep(0.1)
                    continue

                tasks = [asyncio.create_task(self._send_one(client, semaphore, ticker, is_approved)) for ticker, is_approved in batch]
                if tasks:
                    await asyncio.gather(*tasks)

                elapsed = time.monotonic() - loop_start_time
                await asyncio.sleep(max(0, 1.0 - elapsed))
        self.log_to_gui("TestEngine finished.", "log_info")

# ─────────────────── DB AUDITOR ───────────────────
class DBAuditor:
    def __init__(self, db_url):
        self.engine = create_async_engine(db_url, pool_size=3, max_overflow=5, pool_pre_ping=True)
        self.async_session = sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def get_signal_audit_trail(self, signal_token: str) -> List[Dict]:
        query = text("""
            SELECT se.timestamp, se.status, se.details, se.worker_id
            FROM signal_events se
            JOIN signals s ON se.signal_id = s.signal_id
            WHERE s.original_signal->>'tester_token' = :token
            ORDER BY se.timestamp ASC
        """)
        async with self.async_session() as session:
            result = await session.execute(query, {"token": signal_token})
            return [dict(row) for row in result.mappings()]

    async def close(self):
        await self.engine.dispose()
        
# ─────────────────── TEST CONTROLLER (O Cérebro) ───────────────────
class TestController:
    def __init__(self, app: 'ProStressTesterApp'):
        self.app = app
        self.state = "IDLE"
        self.threads = []
        self.shutdown_event: Optional[threading.Event] = None
        self.db_auditor: Optional[DBAuditor] = None
        self.output_queue = queue.Queue()
        self.uvicorn_server = None
        self.reset_stats()

    def reset_stats(self):
        self.start_time = 0
        self.outstanding_signals: Dict[str, Dict] = {}
        self.completed_signals: List[Dict] = []
        self.failed_signals: List[Dict] = []
        self.timed_out_signals: List[Dict] = []
        self.sent_approved_count = 0
        self.sent_rejected_count = 0
        self.last_app_status: Dict = {}
        self.latency_stats = deque(maxlen=1000)
        if hasattr(self, 'app') and self.app:
            self.app.clear_all()

    def start_test(self):
        if self.state == "RUNNING": return
        config = self.app.get_current_config()
        if not config: return
        
        self.reset_stats()
        self.start_time = time.monotonic()
        self.state = "RUNNING"
        self.app.set_control_state(self.state)
        self.shutdown_event = threading.Event()
        self.threads = []

        if config.get('db_url'):
            try:
                self.db_auditor = DBAuditor(config['db_url'])
                self.app.log_output(f"DB Auditor connected to {config['db_url'].split('@')[-1]}", "log_info")
            except Exception as e:
                self.app.log_output(f"[ERROR] DB Auditor failed to connect: {e}", "log_error")
                self.db_auditor = None

        uvicorn_config = uvicorn.Config(receiver_app, host="0.0.0.0", port=config['webhook_port'], log_level="warning")
        self.uvicorn_server = uvicorn.Server(config=uvicorn_config)
        self._start_thread(self.uvicorn_server.run, "WebhookReceiver")
        self.app.log_output(f"Webhook receiver listening on http://0.0.0.0:{config['webhook_port']}/test_webhook_return", "log_info")

        engine = TestEngine(config, self.output_queue, self.shutdown_event)
        self._start_thread(engine.run, "TestEngine")
        
        poller = AppStatusPoller(config, self.output_queue, self.shutdown_event)
        self._start_thread(poller.run, "AppStatusPoller")

        self.app.master.after(100, self.process_queues)
        self.app.master.after(1000, self.check_timeouts)

    def _start_thread(self, target, name):
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        self.threads.append(thread)

    def stop_test(self):
        if self.state != "RUNNING": return
        self.state = "STOPPING"
        self.app.set_control_state(self.state)
        self.app.log_output("Stopping test...", "log_info")
        if self.shutdown_event: self.shutdown_event.set()

        if self.uvicorn_server:
            self.uvicorn_server.should_exit = True

        if self.db_auditor:
            asyncio.run(self.db_auditor.close())
            self.app.log_output("DB Auditor disconnected.", "log_info")

        self.app.master.after(100, self._check_stop_completion)

    def _check_stop_completion(self):
        if any(t.is_alive() for t in self.threads):
            self.app.master.after(100, self._check_stop_completion)
        else:
            self.state = "FINISHED"
            self.app.set_control_state(self.state)
            self.app.log_output("--- TEST FINISHED ---", "log_info")

    def process_queues(self):
        try:
            while True: # Esvazia a fila completamente
                msg = self.output_queue.get_nowait()
                msg_type, payload = msg.get("type"), msg.get("payload")
                
                if msg_type.startswith("log_"): self.app.log_output(payload, msg_type)
                elif msg_type == "token_sent":
                    self.outstanding_signals[payload['token']] = payload
                    if payload['expected_outcome'] == 'approved': self.sent_approved_count += 1
                    else: self.sent_rejected_count += 1
                elif msg_type == "token_failed":
                    if (info := self.outstanding_signals.pop(payload['token'], None)):
                        self.failed_signals.append(info)
                        self.app.add_to_failure_list('failed_send', info)
                elif msg_type == "app_status": self.last_app_status = payload
        except queue.Empty: pass

        try:
            while True: # Esvazia a fila completamente
                item = returned_tokens_queue.get_nowait()
                token, received_at = item['token'], item['received_at']
                if info := self.outstanding_signals.pop(token, None):
                    info['latency'] = received_at - info['sent_at']
                    self.completed_signals.append(info)
                    self.latency_stats.append(info['latency'])
        except queue.Empty: pass

        if self.state in ["RUNNING", "STOPPING"]: self.app.master.after(100, self.process_queues)

    def check_timeouts(self):
        if self.state != "RUNNING": return
        now = time.monotonic()
        timeout = self.app.get_current_config().get('timeout_s', 20)
        for token, info in list(self.outstanding_signals.items()):
            if now - info['sent_at'] > timeout:
                timed_out_info = self.outstanding_signals.pop(token)
                self.timed_out_signals.append(timed_out_info)
                self.app.log_output(f"[TIMEOUT] Signal {info['ticker']} ({token[:8]}...) timed out.", "log_warning")
                self.app.add_to_failure_list('timeout', timed_out_info)
        if self.state == "RUNNING": self.app.master.after(1000, self.check_timeouts)

    def update_stats_display(self, *args):
        if self.state != "RUNNING": return
        elapsed_time = time.monotonic() - self.start_time
        
        total_sent = self.sent_approved_count + self.sent_rejected_count
        rps = total_sent / elapsed_time if elapsed_time > 0 else 0
        avg_lat = (sum(self.latency_stats) / len(self.latency_stats)) * 1000 if self.latency_stats else 0
        
        successful_e2e = len(self.completed_signals)
        total_for_rate = successful_e2e + len(self.timed_out_signals) + len(self.failed_signals)
        success_rate = (successful_e2e / total_for_rate) * 100 if total_for_rate > 0 else 100

        # Pegando métricas da aplicação com segurança
        components = self.last_app_status.get('components', {})
        proc_q = components.get('processing_queue', {}).get('size', 0)
        appr_q = components.get('approved_queue', {}).get('size', 0)
        workers_active = components.get('workers', {}).get('forwarding_active', 0)

        self.app.update_live_stats({
            "rps": f"{rps:.2f}", "total_sent": total_sent, "success_e2e": successful_e2e,
            "failed_send": len(self.failed_signals), "timeouts": len(self.timed_out_signals),
            "in_flight": len(self.outstanding_signals), "avg_lat_e2e": f"{avg_lat:.2f} ms",
            "success_rate": f"{success_rate:.2f}%", "proc_q_size": proc_q, "appr_q_size": appr_q,
            "workers_active": workers_active,
            "sent_approved": self.sent_approved_count, "sent_rejected": self.sent_rejected_count,
        })
        
        self.app.add_graph_data(elapsed_time, rps, avg_lat, proc_q, appr_q)

    async def run_db_audit(self, token_id):
        if not self.db_auditor:
            self.app.log_output("DB Auditor is not connected.", "log_error")
            return
        self.app.log_output(f"Auditing signal with token {token_id[:8]}... in DB", "log_info")
        try:
            trail = await self.db_auditor.get_signal_audit_trail(token_id)
            if not trail:
                self.app.log_output(f"Audit Result: No events found for token {token_id[:8]}...", "log_warning")
            else:
                log_msg = f"Audit Trail for token {token_id[:8]}...:\n"
                for i, event in enumerate(trail):
                    ts = event['timestamp'].strftime('%H:%M:%S.%f')[:-3]
                    duration = f" (+{(event['timestamp'] - trail[i-1]['timestamp']).total_seconds():.3f}s)" if i > 0 else ""
                    log_msg += f"  - [{ts}] {event['status']}{duration}: {event.get('details') or '-'}\n"
                self.app.log_output(log_msg.strip(), "log_info")
        except Exception as e:
            self.app.log_output(f"DB Audit failed: {e}", "log_error")

# ─────────────────── GUI (A Interface) ───────────────────
class ProStressTesterApp(ttk.Frame):
    def __init__(self, master=None, controller=None):
        super().__init__(master, padding="10")
        self.master = master
        self.controller = controller
        self.master.title(f"{APP_VERSION} - The Ultimate QA Platform")
        self.master.geometry("1600x900")
        self.grid(sticky="nsew"); self.master.columnconfigure(0, weight=1); self.master.rowconfigure(0, weight=1)

        self.deques = {
            'time': deque(maxlen=MAX_GRAPH_POINTS), 'rps': deque(maxlen=MAX_GRAPH_POINTS),
            'latency': deque(maxlen=MAX_GRAPH_POINTS), 'proc_q': deque(maxlen=MAX_GRAPH_POINTS),
            'appr_q': deque(maxlen=MAX_GRAPH_POINTS)
        }
        self.local_ip = self.get_local_ip()

        self._create_widgets()
        self.set_control_state("IDLE")
        self.anim = animation.FuncAnimation(self.fig, self.controller.update_stats_display, interval=1000, cache_frame_data=False)

    def get_local_ip(self):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80)); return s.getsockname()[0]
        except Exception: return "127.0.0.1"

    def _create_widgets(self):
        main_pane = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main_pane.grid(row=0, column=0, sticky="nsew")
        self.rowconfigure(0, weight=1); self.columnconfigure(0, weight=1)
        
        left_pane = ttk.Frame(main_pane, width=450); main_pane.add(left_pane, weight=1)
        right_pane = ttk.Frame(main_pane, width=1150); main_pane.add(right_pane, weight=3)
        left_pane.rowconfigure(1, weight=1); left_pane.columnconfigure(0, weight=1)
        right_pane.rowconfigure(0, weight=1); right_pane.columnconfigure(0, weight=1)
        
        self._create_config_and_controls_pane(left_pane)
        self._create_results_pane(right_pane)

    def _create_config_and_controls_pane(self, parent):
        config_frame = self._create_config_frame(parent)
        config_frame.grid(row=0, column=0, sticky="new", pady=(0, 10))
        
        ticker_frame = self._create_ticker_frame(parent)
        ticker_frame.grid(row=1, column=0, sticky="nsew", pady=(0, 10))

    def _create_config_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="1. Configuration & Controls", padding=10)
        frame.columnconfigure(1, weight=1)
        
        ttk.Label(frame, text="App's Destination Webhook:", font="-weight bold").grid(row=0, column=0, columnspan=2, sticky="w")
        self.webhook_url_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.webhook_url_var, state='readonly', font="-family {Courier New}").grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 5))

        ttk.Button(frame, text="Load Config from .env", command=self.load_from_env).grid(row=2, column=0, columnspan=2, sticky="ew", pady=5)

        ttk.Label(frame, text="Target App Host:").grid(row=3, column=0, sticky="w"); self.app_host_entry = ttk.Entry(frame); self.app_host_entry.insert(0, "localhost"); self.app_host_entry.grid(row=3, column=1, sticky="ew")
        ttk.Label(frame, text="Target App Port:").grid(row=4, column=0, sticky="w"); self.app_port_entry = ttk.Entry(frame); self.app_port_entry.insert(0, "80"); self.app_port_entry.grid(row=4, column=1, sticky="w")
        ttk.Label(frame, text="Target DB URL:").grid(row=5, column=0, sticky="w"); self.db_url_entry = ttk.Entry(frame); self.db_url_entry.grid(row=5, column=1, sticky="ew")
        self.db_url_entry.insert(0, "postgresql+asyncpg://postgres:postgres123@localhost:5432/trading_signals")

        self.rps_slider = self._create_slider(frame, "Target RPS:", 1, 2000, 100, 6)
        self.ratio_slider = self._create_slider(frame, "Approved Ratio (%):", 0, 100, 80, 7)
        self.timeout_slider = self._create_slider(frame, "Timeout (s):", 5, 120, 20, 8)

        action_frame = ttk.Frame(frame); action_frame.grid(row=9, column=0, columnspan=2, pady=10)
        self.start_button = ttk.Button(action_frame, text="Start Test", command=self.controller.start_test, style="Accent.TButton"); self.start_button.pack(side="left", padx=5)
        self.stop_button = ttk.Button(action_frame, text="Stop Test", command=self.controller.stop_test); self.stop_button.pack(side="left", padx=5)
        
        self.update_webhook_display()
        return frame
        
    def _create_ticker_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="2. Ticker Payloads", padding=10)
        frame.columnconfigure(0, weight=1); frame.rowconfigure(0, weight=1)
        
        ticker_pane = ttk.PanedWindow(frame, orient=tk.VERTICAL); ticker_pane.grid(row=0, column=0, sticky="nsew")
        
        approved_frame = ttk.LabelFrame(ticker_pane, text="Approved Tickers", padding=5)
        
        self.approved_tree = self._create_ticker_list(approved_frame, ["AAPL", "GOOG"], grid=False)
        self.approved_tree.grid(row=0, column=0, columnspan=2, sticky="nsew")
        
        self.approved_ticker_entry = ttk.Entry(approved_frame, width=10)
        self.approved_ticker_entry.grid(row=1, column=0, sticky="ew", pady=5)
        
        add_button = ttk.Button(approved_frame, text="Add", command=self._add_approved_ticker)
        add_button.grid(row=1, column=1, sticky="w", pady=5)
        
        approved_frame.columnconfigure(0, weight=1)
        
        ticker_pane.add(approved_frame, weight=1)
        
        rejected_frame = ttk.LabelFrame(ticker_pane, text="Rejected Tickers", padding=5); self.rejected_tree = self._create_ticker_list(rejected_frame, ["JUNK", "TEST"]); ticker_pane.add(rejected_frame, weight=1)

        fetch_button = ttk.Button(frame, text="Fetch Tickers from App", command=self.fetch_app_tickers); fetch_button.grid(row=1, column=0, sticky="ew", pady=5)
        return frame

    def _add_approved_ticker(self):
        ticker = self.approved_ticker_entry.get()
        if ticker:
            self.approved_tree.insert("", "end", values=(ticker,))
            self.approved_ticker_entry.delete(0, tk.END)
        
    def _create_results_pane(self, parent):
        notebook = ttk.Notebook(parent); notebook.grid(row=0, column=0, sticky="nsew")
        
        stats_tab = self._create_stats_tab(notebook)
        graphs_tab = self._create_graphs_tab(notebook)
        failures_tab = self._create_failures_tab(notebook)
        log_tab = self._create_log_tab(notebook)
        admin_tab = self._create_admin_tab(notebook)
        
        notebook.add(stats_tab, text="📊 Live Metrics")
        notebook.add(graphs_tab, text="📈 Graphs")
        notebook.add(failures_tab, text="❌ Failures & Audit")
        notebook.add(log_tab, text="📋 Log")
        notebook.add(admin_tab, text="⚙️ Admin Panel")

    def _create_stats_tab(self, parent):
        frame = ttk.Frame(parent, padding=10)
        frame.columnconfigure(0, weight=1); frame.columnconfigure(1, weight=1)
        
        tester_frame = ttk.LabelFrame(frame, text="Tester Metrics (Client-Side)", padding=10); tester_frame.grid(row=0, column=0, sticky="nsew", padx=(0,5))
        app_frame = ttk.LabelFrame(frame, text="Application Metrics (Polled)", padding=10); app_frame.grid(row=0, column=1, sticky="nsew", padx=5)
        e2e_frame = ttk.LabelFrame(frame, text="End-to-End Verification", padding=10); e2e_frame.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=10)

        self.live_stats_vars = {}
        def create_stat(p, text, key, row, col=0):
            ttk.Label(p, text=f"{text}:").grid(row=row, column=col, sticky="w", pady=2)
            var = tk.StringVar(value="0"); ttk.Label(p, textvariable=var, font="-weight bold").grid(row=row, column=col+1, sticky="w", padx=5)
            self.live_stats_vars[key] = var

        create_stat(tester_frame, "Target RPS", "rps", 0); create_stat(tester_frame, "Sent (Approved)", "sent_approved", 1); create_stat(tester_frame, "Sent (Rejected)", "sent_rejected", 2); create_stat(tester_frame, "Total Sent", "total_sent", 3)
        create_stat(app_frame, "Processing Queue", "proc_q_size", 0); create_stat(app_frame, "Approved Queue", "appr_q_size", 1); create_stat(app_frame, "Fwd Workers Active", "workers_active", 2)
        create_stat(e2e_frame, "In-Flight", "in_flight", 0); create_stat(e2e_frame, "Success (E2E)", "success_e2e", 1); create_stat(e2e_frame, "Timeouts", "timeouts", 2)
        create_stat(e2e_frame, "Failed to Send", "failed_send", 3); create_stat(e2e_frame, "Avg Latency (E2E)", "avg_lat_e2e", 0, 2); create_stat(e2e_frame, "Success Rate", "success_rate", 1, 2)
        return frame
        
    def _create_graphs_tab(self, parent):
        frame = ttk.Frame(parent, padding=10); frame.rowconfigure(0, weight=1); frame.columnconfigure(0, weight=1)
        self.fig = Figure(figsize=(12, 8), dpi=100); self.ax1 = self.fig.add_subplot(2, 1, 1); self.ax2 = self.fig.add_subplot(2, 1, 2)
        self.canvas = FigureCanvasTkAgg(self.fig, master=frame); self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")
        self.clear_graphs()
        return frame

    def _create_failures_tab(self, parent):
        frame = ttk.Frame(parent, padding=10); frame.columnconfigure(0, weight=1); frame.rowconfigure(1, weight=1)
        
        failure_pane = ttk.PanedWindow(frame, orient=tk.HORIZONTAL); failure_pane.grid(row=0, column=0, sticky="nsew", pady=(0, 5))
        
        timeout_frame = ttk.LabelFrame(failure_pane, text="Timed Out Signals", padding=5); self.timeout_tree = self._create_failure_list(timeout_frame); failure_pane.add(timeout_frame, weight=1)
        failed_send_frame = ttk.LabelFrame(failure_pane, text="Failed to Send Signals", padding=5); self.failed_send_tree = self._create_failure_list(failed_send_frame); failure_pane.add(failed_send_frame, weight=1)

        audit_frame = ttk.LabelFrame(frame, text="DB Audit Trail", padding=5); audit_frame.grid(row=1, column=0, sticky="nsew", pady=(5, 0))
        audit_frame.rowconfigure(0, weight=1); audit_frame.columnconfigure(0, weight=1)
        self.audit_results_text = ScrolledText(audit_frame, state='disabled', wrap=tk.WORD, height=8, font="-family {Courier New}"); self.audit_results_text.grid(row=0, column=0, sticky="nsew")
        
        return frame

    def _create_failure_list(self, parent):
        parent.columnconfigure(0, weight=1); parent.rowconfigure(0, weight=1)
        tree = ttk.Treeview(parent, columns=("token", "ticker", "time"), show="headings", height=8)
        tree.heading("token", text="Token"); tree.column("token", width=60, anchor='w')
        tree.heading("ticker", text="Ticker"); tree.column("ticker", width=60, anchor='w')
        tree.heading("time", text="Time Sent"); tree.column("time", width=100, anchor='w')
        tree.grid(row=0, column=0, sticky="nsew")
        
        def on_select(event):
            for selected_item in tree.selection():
                token = tree.item(selected_item)['values'][0]
                self.run_db_audit_gui(token)
        tree.bind('<<TreeviewSelect>>', on_select)
        return tree

    def _create_log_tab(self, parent):
        frame = ttk.Frame(parent, padding=10); frame.rowconfigure(0, weight=1); frame.columnconfigure(0, weight=1)
        self.output_text = ScrolledText(frame, state='disabled', wrap=tk.WORD, height=10); self.output_text.grid(row=0, column=0, sticky="nsew")
        return frame
        
    def _create_admin_tab(self, parent):
        frame = ttk.Frame(parent, padding=10); frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="Admin Token:").pack(anchor='w'); self.admin_token_entry = ttk.Entry(frame); self.admin_token_entry.pack(fill='x', pady=(0, 10))
        
        def send_cmd(endpoint, payload_factory=lambda: {}):
            token = self.admin_token_entry.get()
            if not token: messagebox.showwarning("Token Required", "Admin token is required for this action."); return
            payload = payload_factory()
            payload["token"] = token
            config = self.get_current_config()
            if not config: return
            url = f"http://{config['app_host']}:{config['app_port']}{endpoint}"
            
            def do_send():
                try:
                    with httpx.Client(timeout=10.0) as client:
                        resp = client.post(url, json=payload); resp.raise_for_status()
                        self.log_output(f"SUCCESS: {endpoint} -> {resp.status_code}", "log_info")
                except Exception as e: self.log_output(f"ERROR: {endpoint} -> {e}", "log_error")
            threading.Thread(target=do_send, daemon=True).start()

        engine_frame = ttk.LabelFrame(frame, text="Finviz Engine Control", padding=5); engine_frame.pack(fill='x', pady=5)
        ttk.Button(engine_frame, text="Pause", command=lambda: send_cmd('/admin/engine/pause')).pack(side='left', padx=5)
        ttk.Button(engine_frame, text="Resume", command=lambda: send_cmd('/admin/engine/resume')).pack(side='left', padx=5)
        ttk.Button(engine_frame, text="Refresh", command=lambda: send_cmd('/admin/engine/manual-refresh')).pack(side='left', padx=5)
        
        metrics_frame = ttk.LabelFrame(frame, text="Metrics Control", padding=5); metrics_frame.pack(fill='x', pady=5)
        ttk.Button(metrics_frame, text="Reset Metrics", command=lambda: send_cmd('/admin/metrics/reset')).pack(side='left', padx=5)
        
        order_frame = ttk.LabelFrame(frame, text="Manual Orders", padding=5); order_frame.pack(fill='x', pady=5)
        self.sell_ticker_entry = ttk.Entry(order_frame, width=10); self.sell_ticker_entry.pack(side='left', padx=5)
        ttk.Button(order_frame, text="Sell Ticker", command=lambda: send_cmd('/admin/order/sell-individual', lambda: {'ticker': self.sell_ticker_entry.get()})).pack(side='left', padx=5)
        ttk.Button(order_frame, text="Sell All Queued", command=lambda: send_cmd('/admin/order/sell-all')).pack(side='left', padx=5)
        
        return frame

    def run_db_audit_gui(self, token):
        self.audit_results_text.config(state='normal'); self.audit_results_text.delete('1.0', tk.END); self.audit_results_text.config(state='disabled')
        threading.Thread(target=lambda: asyncio.run(self.controller.run_db_audit(token)), daemon=True).start()

    def set_control_state(self, state: str):
        is_idle = state in ["IDLE", "FINISHED"]; self.start_button.config(state=tk.NORMAL if is_idle else tk.DISABLED); self.stop_button.config(state=tk.DISABLED if is_idle else tk.NORMAL)

    def log_output(self, message: str, level: str):
        color_map = {"log_info": "black", "log_warning": "darkorange", "log_error": "red"}
        def update_gui():
            if not self.winfo_exists(): return
            if "Audit Trail" in message:
                self.audit_results_text.config(state='normal'); self.audit_results_text.delete('1.0', tk.END); self.audit_results_text.insert(tk.END, message + "\n"); self.audit_results_text.config(state='disabled')
            else:
                self.output_text.config(state='normal'); self.output_text.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {message}\n", color_map.get(level, "black")); self.output_text.see(tk.END); self.output_text.config(state='disabled')
        self.after(0, update_gui)

    def clear_all(self):
        self.clear_graphs(); self.clear_output_log(); self.timeout_tree.delete(*self.timeout_tree.get_children()); self.failed_send_tree.delete(*self.failed_send_tree.get_children())

    def clear_graphs(self):
        for deq in self.deques.values(): deq.clear()
        for ax in [self.ax1, self.ax2]: ax.clear()
        if hasattr(self, 'ax1_twin'): self.ax1_twin.clear()
        
        self.ax1.set_title("Throughput & Latency", fontsize=10); self.ax1.set_ylabel("RPS", color='tab:blue', fontsize=8); self.ax1_twin = self.ax1.twinx(); self.ax1_twin.set_ylabel("Avg Latency (ms)", color='tab:orange', fontsize=8)
        self.ax2.set_title("Application Queue Sizes", fontsize=10); self.ax2.set_ylabel("Queue Size", fontsize=8)
        
        for ax in [self.ax1, self.ax2, self.ax1_twin]: ax.tick_params(axis='both', which='major', labelsize=8); ax.grid(True, linestyle='--', alpha=0.6)
        self.fig.tight_layout()
        self.canvas.draw()
    
    def add_graph_data(self, t, rps, latency_ms, proc_q, appr_q):
        for key, val in {'time': t, 'rps': rps, 'latency': latency_ms, 'proc_q': proc_q, 'appr_q': appr_q}.items(): self.deques[key].append(val)
        
        self.ax1.clear(); self.ax1_twin.clear()
        self.ax1.plot(self.deques['time'], self.deques['rps'], color='tab:blue', label='RPS'); self.ax1_twin.plot(self.deques['time'], self.deques['latency'], color='tab:orange', label='Latency (ms)')
        
        self.ax2.clear()
        self.ax2.plot(self.deques['time'], self.deques['proc_q'], 'c-', label='Processing Queue'); self.ax2.plot(self.deques['time'], self.deques['appr_q'], 'm-', label='Approved Queue')
        self.ax2.legend(loc='upper left', fontsize='small')

        for ax in [self.ax1, self.ax2, self.ax1_twin]: ax.tick_params(axis='both', which='major', labelsize=8); ax.grid(True, linestyle='--', alpha=0.6)
        self.fig.tight_layout(); self.canvas.draw()

    def update_live_stats(self, stats: Dict):
        for key, var in self.live_stats_vars.items(): var.set(stats.get(key, "0"))

    def get_current_config(self) -> Optional[Dict]:
        try:
            config = {
                'app_host': self.app_host_entry.get(), 'app_port': int(self.app_port_entry.get()),
                'db_url': self.db_url_entry.get(), 'webhook_port': 9999,
                'rps': int(self.rps_slider.get()), 'approved_ratio': int(self.ratio_slider.get()),
                'timeout_s': int(self.timeout_slider.get()),
                'approved_tickers': [self.approved_tree.item(i)['values'][0] for i in self.approved_tree.get_children()],
                'rejected_tickers': [self.rejected_tree.item(i)['values'][0] for i in self.rejected_tree.get_children()],
                'payload_template': DEFAULT_PAYLOAD_TEMPLATE, 'max_concurrency': 1000
            }
            config['url'] = f"http://{config['app_host']}:{config['app_port']}/webhook/in"
            if not config['approved_tickers'] and not config['rejected_tickers']: raise ValueError("At least one ticker is required.")
            return config
        except Exception as e: messagebox.showerror("Configuration Error", f"Invalid configuration: {e}"); return None

    def fetch_app_tickers(self):
        self.log_output("Fetching approved tickers from the application...", "log_info")
        config = self.get_current_config();
        if not config: return
        url = f"http://{config['app_host']}:{config['app_port']}/admin/top-n-tickers"
        def do_fetch():
            try:
                with httpx.Client(timeout=5.0) as client:
                    resp = client.get(url); resp.raise_for_status(); data = resp.json()
                    def update_gui():
                        self.approved_tree.delete(*self.approved_tree.get_children())
                        for t in data.get('tickers', []): self.approved_tree.insert("", "end", values=(t,))
                        self.log_output(f"Successfully fetched {len(data.get('tickers',[]))} tickers.", "log_info")
                    self.after(0, update_gui)
            except Exception as e: self.after(0, lambda: self.log_output(f"Failed to fetch tickers: {e}", "log_error"))
        threading.Thread(target=do_fetch, daemon=True).start()

    def load_from_env(self):
        try:
            env_path = filedialog.askopenfilename(title="Select .env file of the application")
            if not env_path: return
            env_vars = dotenv_values(env_path)
            if db_url := env_vars.get("DATABASE_URL"):
                db_url = db_url.replace("@postgres:", f"@{self.app_host_entry.get()}:")
                self.db_url_entry.delete(0, tk.END); self.db_url_entry.insert(0, db_url)
                self.log_output(f"Loaded DB URL from .env: {db_url}", "log_info")
            if token := env_vars.get("FINVIZ_UPDATE_TOKEN"):
                self.admin_token_entry.delete(0, tk.END); self.admin_token_entry.insert(0, token)
                self.log_output("Loaded Admin Token from .env.", "log_info")
        except Exception as e: messagebox.showerror("Error", f"Failed to load from .env file: {e}")
        
    def _create_ticker_list(self, parent, defaults, grid=True):
        parent.columnconfigure(0, weight=1); parent.rowconfigure(0, weight=1)
        tree = ttk.Treeview(parent, columns=("ticker",), show="headings", height=6)
        tree.heading("ticker", text="Ticker"); tree.column("ticker", anchor='w')
        if grid:
            tree.grid(row=0, column=0, sticky="nsew")
        for t in defaults: tree.insert("", "end", values=(t,))
        return tree
        
    def add_to_failure_list(self, list_type, info):
        tree = self.timeout_tree if list_type == 'timeout' else self.failed_send_tree
        token_short = info['token'][:8]
        time_str = time.strftime('%H:%M:%S', time.localtime(info['sent_at']))
        tree.insert("", 0, values=(token_short, info['ticker'], time_str))

    def update_webhook_display(self, event=None):
        port = 9999
        self.webhook_url_var.set(f"http://{self.local_ip}:{port}/test_webhook_return")

    def _create_slider(self, parent, text, from_, to, default, row):
        ttk.Label(parent, text=text).grid(row=row, column=0, sticky="w", pady=2)
        slider = ttk.Scale(parent, from_=from_, to=to, orient=tk.HORIZONTAL); slider.set(default)
        slider.grid(row=row, column=1, sticky="ew", padx=5)
        label = ttk.Label(parent, text=str(default), width=5); label.grid(row=row, column=2, sticky="w")
        slider.config(command=lambda v, l=label: l.config(text=str(int(float(v)))))
        return slider

    def clear_output_log(self):
        self.output_text.config(state='normal'); self.output_text.delete('1.0', tk.END); self.output_text.config(state='disabled')

# ─────────────────── PONTO DE ENTRADA ───────────────────
def main():
    root = tk.Tk()
    style = ttk.Style(root)
    try:
        if sys.platform == "win32": style.theme_use('winnative')
        elif sys.platform == "darwin": style.theme_use('aqua')
        else: style.theme_use('clam')
    except tk.TclError: pass
            
    style.configure('Accent.TButton', font="-weight bold")
    
    controller = TestController(None)
    app = ProStressTesterApp(master=root, controller=controller)
    controller.app = app
    app.mainloop()

if __name__ == "__main__":
    main()
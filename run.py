#!/usr/bin/env python3
"""
Script para executar a aplicação Trading Signal Processor via Docker Compose.

Este script automatiza o processo de deploy da aplicação, incluindo:
- Verificação de dependências (Docker, Docker Compose)
- Limpeza de containers anteriores
- Build e execução da aplicação na porta 80
- Verificação de saúde da aplicação
- Logs em tempo real
"""

import os
import sys
import subprocess
import time
import json
import argparse
from pathlib import Path
from typing import List, Optional

# Configurações
CONTAINER_NAME = "trading-signal-processor"
COMPOSE_SERVICE = "trading-signal-processor"
HEALTH_CHECK_URL = "http://localhost:80/health"
MAX_HEALTH_CHECK_ATTEMPTS = 30
HEALTH_CHECK_INTERVAL = 2  # segundos

class Colors:
    """Cores ANSI para output colorido."""
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

def print_colored(message: str, color: str = Colors.ENDC) -> None:
    """Imprime mensagem colorida."""
    print(f"{color}{message}{Colors.ENDC}")

def print_step(step: str) -> None:
    """Imprime passo atual."""
    print_colored(f"\n🔄 {step}", Colors.OKBLUE)

def print_success(message: str) -> None:
    """Imprime mensagem de sucesso."""
    print_colored(f"✅ {message}", Colors.OKGREEN)

def print_warning(message: str) -> None:
    """Imprime mensagem de aviso."""
    print_colored(f"⚠️  {message}", Colors.WARNING)

def print_error(message: str) -> None:
    """Imprime mensagem de erro."""
    print_colored(f"❌ {message}", Colors.FAIL)

def run_command(command: List[str], capture_output: bool = True, check: bool = True, use_sudo: bool = False) -> subprocess.CompletedProcess:
    """Executa comando e retorna resultado, opcionalmente com sudo."""
    try:
        # Adicionar sudo se solicitado e não estiver no Windows
        if use_sudo and os.name != 'nt':
            command = ['sudo'] + command
        
        if capture_output:
            result = subprocess.run(command, capture_output=True, text=True, check=check)
        else:
            result = subprocess.run(command, check=check)
        return result
    except subprocess.CalledProcessError as e:
        if capture_output and e.stdout:
            print_error(f"STDOUT: {e.stdout}")
        if capture_output and e.stderr:
            print_error(f"STDERR: {e.stderr}")
        raise

def check_docker() -> bool:
    """Verifica se Docker está instalado e rodando."""
    print_step("Verificando Docker...")
    
    try:
        result = run_command(["docker", "--version"])
        print_success(f"Docker encontrado: {result.stdout.strip()}")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print_error("Docker não está instalado ou não está no PATH")
        return False
    
    try:
        run_command(["docker", "info"], capture_output=True)
        print_success("Docker daemon está rodando")
        return True
    except subprocess.CalledProcessError:
        print_error("Docker daemon não está rodando. Inicie o Docker Desktop ou systemctl start docker")
        return False

def check_docker_compose() -> bool:
    """Verifica se Docker Compose está instalado."""
    print_step("Verificando Docker Compose...")
    
    try:
        # Tenta docker compose (versão nova)
        result = run_command(["docker", "compose", "version"])
        print_success(f"Docker Compose encontrado: {result.stdout.strip()}")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        try:
            # Tenta docker-compose (versão legacy)
            result = run_command(["docker-compose", "--version"])
            print_success(f"Docker Compose (legacy) encontrado: {result.stdout.strip()}")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            print_error("Docker Compose não está instalado")
            return False

def check_required_files() -> bool:
    """Verifica se arquivos necessários existem."""
    print_step("Verificando arquivos necessários...")
    
    required_files = [
        "Dockerfile",
        "docker-compose.yml",
        "requirements.txt",
        "main.py"
    ]
    
    missing_files = []
    for file in required_files:
        if not Path(file).exists():
            missing_files.append(file)
    
    if missing_files:
        print_error(f"Arquivos necessários não encontrados: {', '.join(missing_files)}")
        return False
    
    print_success("Todos os arquivos necessários estão presentes")
    return True

def create_required_directories() -> None:
    """Cria diretórios necessários para volumes."""
    print_step("Criando diretórios necessários...")
    
    directories = ["data", "logs"]
    
    for directory in directories:
        dir_path = Path(directory)
        if not dir_path.exists():
            dir_path.mkdir(parents=True, exist_ok=True)
            print_success(f"Diretório '{directory}' criado")
        else:
            print_success(f"Diretório '{directory}' já existe")

def check_database_configuration() -> bool:
    """Verifica e configura o banco de dados PostgreSQL."""
    print_step("Verificando configuração do banco de dados...")
    
    # Verifica se as variáveis de ambiente estão configuradas
    env_file = Path(".env")
    if not env_file.exists():
        print_error("Arquivo .env não encontrado")
        return False
      # Lê variáveis do .env
    env_vars = {}
    with open(env_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                env_vars[key] = value
    
    # Verifica configurações do banco
    database_url = env_vars.get('DATABASE_URL', '')
    postgres_password = env_vars.get('POSTGRES_PASSWORD', 'postgres123')
    
    if not database_url:
        print_warning("DATABASE_URL não configurada, usando padrão")
        print_success("Configuração do banco de dados: PostgreSQL com valores padrão")
    else:
        print_success(f"Configuração do banco encontrada: {database_url.split('@')[-1] if '@' in database_url else '***'}")
    
    print_success(f"Password do PostgreSQL: {'***' if postgres_password else 'não configurada'}")
    return True

def wait_for_database() -> bool:
    """Aguarda o banco de dados ficar disponível."""
    print_step("Aguardando banco de dados PostgreSQL...")
    
    max_attempts = 30
    for attempt in range(max_attempts):
        try:
            # Verifica se o container do PostgreSQL está rodando
            result = run_command(["docker", "compose", "ps", "-q", "postgres"], capture_output=True)
            if result.stdout.strip():
                # Verifica se o banco está aceitando conexões
                result = run_command([
                    "docker", "compose", "exec", "-T", "postgres", 
                    "pg_isready", "-U", "postgres", "-d", "trading_signals"
                ], capture_output=True, check=False)
                
                if result.returncode == 0:
                    print_success("✅ PostgreSQL está disponível e aceitando conexões")
                    return True
            
            print(f"🔄 Tentativa {attempt + 1}/{max_attempts} - aguardando PostgreSQL...")
            time.sleep(2)
            
        except subprocess.CalledProcessError:
            print(f"🔄 Tentativa {attempt + 1}/{max_attempts} - PostgreSQL ainda não está pronto...")
            time.sleep(2)
    
    print_error("❌ PostgreSQL não ficou disponível no tempo esperado")
    return False

def initialize_database() -> bool:
    """Inicializa o banco de dados com as tabelas necessárias."""
    print_step("Inicializando esquema do banco de dados...")
    
    try:
        # O banco será inicializado automaticamente pela aplicação
        # quando ela se conectar pela primeira vez
        print_success("✅ Inicialização do banco delegada para a aplicação")
        return True
        
    except Exception as e:
        print_error(f"❌ Erro na inicialização do banco: {e}")
        return False

def create_env_file_if_missing() -> None:
    """Cria arquivo .env se não existir."""
    env_file = Path(".env")
    env_example = Path(".env.example")
    
    if not env_file.exists():
        print_step("Criando arquivo .env...")
        
        if env_example.exists():
            # Copia .env.example para .env
            with open(env_example, 'r', encoding='utf-8') as src, open(env_file, 'w', encoding='utf-8') as dst:
                content = src.read()
                dst.write(content)
            print_success("Arquivo .env criado baseado em .env.example")
            print_warning("⚠️  IMPORTANTE: Configure as variáveis no arquivo .env antes de usar em produção!")
        else:
            # Cria .env básico
            basic_env = """# =============================================================================
# TRADING SIGNAL PROCESSOR - CONFIGURAÇÃO COMPLETA
# =============================================================================

# =============================================================================
# WEBHOOK DE DESTINO (OBRIGATÓRIO)
# =============================================================================
# URL do webhook que receberá os sinais aprovados
DEST_WEBHOOK_URL=https://httpbin.org/post

# Timeout em segundos para requests ao webhook de destino
DEST_WEBHOOK_TIMEOUT=5

# Mapeia campo 'side' para 'action' (compatibilidade TradersPost)
MAP_SIDE_TO_ACTION_TRADERSPOST=true

# =============================================================================
# CONTROLE DE RATE LIMITING DO WEBHOOK
# =============================================================================
# Máximo de requests por minuto ao webhook de destino
DEST_WEBHOOK_MAX_REQ_PER_MIN=60

# Habilita rate limiting para o webhook de destino
DEST_WEBHOOK_RATE_LIMITING_ENABLED=true

# =============================================================================
# CONFIGURAÇÕES DO FINVIZ
# =============================================================================
# Quantidade de top tickers para filtrar (ex: Top-15)
TOP_N=15

# Intervalo em segundos para refresh da lista Finviz
FINVIZ_REFRESH_SEC=10

# Token para autenticar updates via API admin
FINVIZ_UPDATE_TOKEN=dev_token_change_me

# =============================================================================
# FINVIZ ELITE (OPCIONAL)
# =============================================================================
# Habilita recursos Finviz Elite com autenticação
FINVIZ_USE_ELITE=false

# URL de login do Finviz Elite
FINVIZ_LOGIN_URL=https://finviz.com/login_submit.ashx

# Credenciais Finviz Elite (só necessário se FINVIZ_USE_ELITE=true)
FINVIZ_EMAIL=
FINVIZ_PASSWORD=

# =============================================================================
# CONFIGURAÇÕES DE WORKERS
# =============================================================================
# Número de workers para processar fila principal
WORKER_CONCURRENCY=4

# Número de workers dedicados para forwarding (com rate limit)
FORWARDING_WORKERS=2

# =============================================================================
# CONFIGURAÇÕES DO SERVIDOR
# =============================================================================
# Porta do servidor FastAPI
SERVER_PORT=80

# Nível de log: DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_LEVEL=INFO

# =============================================================================
# CONFIGURAÇÕES AVANÇADAS (OPCIONAIS)
# =============================================================================
# Tamanho máximo da fila de sinais
QUEUE_MAX_SIZE=100000

# Tickers por página do Finviz (20 free, 100 elite)
FINVIZ_TICKERS_PER_PAGE=20

# Máximo requests por minuto ao Finviz (59 free, 120 elite)
MAX_REQ_PER_MIN=59

# Máximo requests concorrentes ao Finviz
MAX_CONCURRENCY=20

# Arquivo de configuração do Finviz
FINVIZ_CONFIG_FILE=finviz_config.json

# Intervalo padrão de refresh de tickers
DEFAULT_TICKER_REFRESH_SEC=10

# =============================================================================
# SIGNAL TRACKING
# =============================================================================
# Idade máxima em horas dos signal trackers antes da limpeza
SIGNAL_TRACKER_MAX_AGE_HOURS=24

# Intervalo em horas entre limpezas dos signal trackers
SIGNAL_TRACKER_CLEANUP_INTERVAL_HOURS=1

# =============================================================================
# PROMETHEUS (OPCIONAL)
# =============================================================================
# Porta para métricas Prometheus
PROMETHEUS_PORT=8008

# Habilita servidor Prometheus (não implementado ainda)
# ENABLE_PROMETHEUS=false
"""
            with open(env_file, 'w', encoding='utf-8') as f:
                f.write(basic_env)
            print_success("Arquivo .env completo criado")
            print_warning("⚠️  IMPORTANTE: Configure as variáveis obrigatórias no arquivo .env!")

def stop_existing_containers() -> None:
    """Para e remove containers existentes da aplicação."""
    print_step("Verificando containers existentes...")
    
    try:
        # Lista containers com o nome específico
        result = run_command([
            "docker", "ps", "-a", "--filter", f"name={CONTAINER_NAME}", "--format", "{{.Names}}"
        ])
        
        if result.stdout.strip():
            print_warning(f"Container '{CONTAINER_NAME}' encontrado. Removendo...")
            
            # Para container se estiver rodando
            try:
                run_command(["docker", "stop", CONTAINER_NAME])
                print_success("Container parado")
            except subprocess.CalledProcessError:
                pass  # Container já pode estar parado
            
            # Remove container
            try:
                run_command(["docker", "rm", CONTAINER_NAME])
                print_success("Container removido")
            except subprocess.CalledProcessError:
                pass  # Container pode não existir
        else:
            print_success("Nenhum container existente encontrado")
            
    except subprocess.CalledProcessError:
        print_warning("Erro ao verificar containers existentes, continuando...")

def cleanup_orphaned_containers() -> None:
    """Remove containers órfãos do docker-compose."""
    print_step("Limpando containers órfãos...")
    
    try:
        # Usa apenas docker compose (versão nova)
        run_command(["docker", "compose", "down", "--remove-orphans"])
        print_success("Containers órfãos removidos")
    except subprocess.CalledProcessError:
        print_warning("Erro ao limpar containers órfãos, continuando...")

def build_and_start_fast() -> bool:
    """Faz build rápido (com cache) e inicia a aplicação."""
    print_step("Fazendo build rápido e iniciando a aplicação...")
    
    try:
        print("📦 Fazendo build rápido da imagem (com cache)...")
        run_command(["docker", "compose", "build"], capture_output=False)
        
        print("🚀 Iniciando aplicação...")
        run_command(["docker", "compose", "up", "-d"], capture_output=False)
        
        print_success("Aplicação iniciada com sucesso")
        return True
        
    except subprocess.CalledProcessError as e:
        print_error(f"Erro ao iniciar aplicação: {e}")
        return False

def build_and_start() -> bool:
    """Faz build e inicia a aplicação."""
    print_step("Fazendo build e iniciando a aplicação...")
    
    try:
        print("📦 Fazendo build da imagem...")
        run_command(["docker", "compose", "build", "--no-cache"], capture_output=False)
        
        print("🚀 Iniciando aplicação...")
        run_command(["docker", "compose", "up", "-d"], capture_output=False)
        
        print_success("Aplicação iniciada com sucesso")
        return True
        
    except subprocess.CalledProcessError as e:
        print_error(f"Erro ao iniciar aplicação: {e}")
        return False

def wait_for_health_check() -> bool:
    """Aguarda aplicação ficar saudável."""
    print_step("Aguardando aplicação ficar online...")
    
    for attempt in range(1, MAX_HEALTH_CHECK_ATTEMPTS + 1):
        try:
            import urllib.request
            import urllib.error
            
            response = urllib.request.urlopen(HEALTH_CHECK_URL, timeout=5)
            if response.status == 200:
                print_success(f"Aplicação está online! (tentativa {attempt}/{MAX_HEALTH_CHECK_ATTEMPTS})")
                return True
                
        except (urllib.error.URLError, Exception):
            pass
        
        print(f"⏳ Tentativa {attempt}/{MAX_HEALTH_CHECK_ATTEMPTS} - Aguardando aplicação ficar online...")
        time.sleep(HEALTH_CHECK_INTERVAL)
    
    print_error("Aplicação não ficou online no tempo esperado")
    return False

def show_status() -> None:
    """Mostra status da aplicação."""
    print_step("Status da aplicação:")
    
    try:
        # Status dos containers
        result = run_command(["docker", "compose", "ps"])
        print(result.stdout)
        
        # URLs de acesso
        print_colored("\n🌐 URLs de acesso:", Colors.HEADER)
        print_colored("   • Interface Admin: http://localhost:80/admin", Colors.OKGREEN)
        print_colored("   • Health Check:    http://localhost:80/health", Colors.OKGREEN)
        print_colored("   • API Docs:        http://localhost:80/docs", Colors.OKGREEN)
        print_colored("   • WebSocket:       ws://localhost:80/ws/admin-updates", Colors.OKGREEN)
        
    except subprocess.CalledProcessError:
        print_error("Erro ao obter status")

def show_logs(follow: bool = False) -> None:
    """Mostra logs da aplicação."""
    print_step("Logs da aplicação:")
    
    try:
        cmd = ["docker", "compose", "logs"]
        if follow:
            cmd.append("-f")
        cmd.append(COMPOSE_SERVICE)
        
        run_command(cmd, capture_output=False)
            
    except subprocess.CalledProcessError:
        print_error("Erro ao obter logs")

def main():
    """Função principal."""
    parser = argparse.ArgumentParser(description="Script para executar Trading Signal Processor")
    parser.add_argument("--logs", action="store_true", help="Mostra logs após iniciar")
    parser.add_argument("--follow-logs", action="store_true", help="Acompanha logs em tempo real")
    parser.add_argument("--status-only", action="store_true", help="Apenas mostra status, sem reiniciar")
    parser.add_argument("--stop", action="store_true", help="Para a aplicação")
    parser.add_argument("--quick", action="store_true", help="Build rápido (usa cache Docker)")
    
    args = parser.parse_args()
    
    print_colored("""
╔══════════════════════════════════════════════════════════════╗
║                 Trading Signal Processor                     ║
║                      Deploy Script                           ║
╚══════════════════════════════════════════════════════════════╝
""", Colors.HEADER)
      # Verifica se deve apenas parar
    if args.stop:
        print_step("Parando aplicação...")
        try:
            run_command(["docker", "compose", "down"])
            print_success("Aplicação parada com sucesso")
        except subprocess.CalledProcessError:
            print_error("Erro ao parar aplicação")
        return
    
    # Verifica se deve apenas mostrar status
    if args.status_only:
        show_status()
        return
    
    # Verificações iniciais
    if not check_docker():
        sys.exit(1)
    
    if not check_docker_compose():
        sys.exit(1)
    
    if not check_required_files():
        sys.exit(1)
      # Cria .env se necessário
    create_env_file_if_missing()
      # Cria diretórios necessários
    create_required_directories()
    
    # Verifica configuração do banco de dados
    if not check_database_configuration():
        sys.exit(1)
    
    # Para containers existentes
    stop_existing_containers()
    cleanup_orphaned_containers()
    
    # Faz build e inicia
    if args.quick:
        if not build_and_start_fast():
            sys.exit(1)
    else:
        if not build_and_start():
            sys.exit(1)
    
    # Aguarda banco de dados ficar disponível
    if not wait_for_database():
        print_warning("⚠️  Banco de dados pode não estar funcionando. A aplicação tentará se conectar automaticamente.")
    
    # Inicializa banco de dados
    if not initialize_database():
        print_warning("⚠️  Inicialização do banco pode ter falhado. Verifique os logs da aplicação.")
    
    # Aguarda health check da aplicação
    if not wait_for_health_check():
        print_warning("Aplicação pode não estar funcionando corretamente")
        print_colored("Verifique os logs com: python run.py --logs", Colors.WARNING)
    
    # Mostra status
    show_status()
      # Mostra logs se solicitado
    if args.logs or args.follow_logs:
        show_logs(follow=args.follow_logs)
    
    print_colored(f"""
╔══════════════════════════════════════════════════════════════╗
║  🎉 Aplicação está rodando na porta 80!                      ║
║                                                              ║
║  Para atualização rápida: python run.py --quick             ║
║  Para ver logs:           python run.py --logs              ║
║  Para acompanhar logs:    python run.py --follow-logs       ║
║  Para ver status:         python run.py --status-only       ║
║  Para parar:              python run.py --stop              ║
╚══════════════════════════════════════════════════════════════╝
""", Colors.OKGREEN)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print_colored("\n\n🛑 Operação cancelada pelo usuário", Colors.WARNING)
        sys.exit(1)
    except Exception as e:
        print_error(f"Erro inesperado: {e}")
        sys.exit(1)

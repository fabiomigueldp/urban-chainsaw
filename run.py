#!/usr/bin/env python3
"""
Script to run the Trading Signal Processor application via Docker Compose.

This script automates the application deployment process, including:
- Dependency checks (Docker, Docker Compose)
- Cleanup of previous containers
- Building and running the application on port 80
- Application health verification
- Real-time logs
"""

import os
import sys
import subprocess
import time
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
    """ANSI colors for colored output."""
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
    """Prints colored message."""
    print(f"{color}{message}{Colors.ENDC}")

def print_step(step: str) -> None:
    """Prints current step."""
    print_colored(f"\n🔄 {step}", Colors.OKBLUE)

def print_success(message: str) -> None:
    """Prints success message."""
    print_colored(f"✅ {message}", Colors.OKGREEN)

def print_warning(message: str) -> None:
    """Prints warning message."""
    print_colored(f"⚠️  {message}", Colors.WARNING)

def print_error(message: str) -> None:
    """Prints error message."""
    print_colored(f"❌ {message}", Colors.FAIL)

def run_command(command: List[str], capture_output: bool = True, check: bool = True, use_sudo: bool = False) -> subprocess.CompletedProcess:
    """Executes command and returns result, optionally with sudo."""
    try:
        # Add sudo if requested and not on Windows
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
    """Checks if Docker is installed and running."""
    print_step("Checking Docker...")
    
    try:
        result = run_command(["docker", "--version"])
        print_success(f"Docker found: {result.stdout.strip()}")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print_error("Docker is not installed or not in PATH")
        return False
    
    try:
        run_command(["docker", "info"], capture_output=True)
        print_success("Docker daemon is running")
        return True
    except subprocess.CalledProcessError:
        print_error("Docker daemon is not running. Start Docker Desktop or systemctl start docker")
        return False

def check_docker_compose() -> bool:
    """Checks if Docker Compose is installed."""
    print_step("Checking Docker Compose...")
    
    try:
        # Try docker compose (new version)
        result = run_command(["docker", "compose", "version"])
        print_success(f"Docker Compose found: {result.stdout.strip()}")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        try:
            # Try docker-compose (legacy version)
            result = run_command(["docker-compose", "--version"])
            print_success(f"Docker Compose (legacy) found: {result.stdout.strip()}")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            print_error("Docker Compose is not installed")
            return False

def check_required_files() -> bool:
    """Checks if required files exist."""
    print_step("Checking required files...")
    
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
        print_error(f"Required files not found: {', '.join(missing_files)}")
        return False
    
    print_success("All required files are present")
    return True

def create_required_directories() -> None:
    """Creates required directories for volumes."""
    print_step("Creating required directories...")
    
    directories = ["data", "logs"]
    
    for directory in directories:
        dir_path = Path(directory)
        if not dir_path.exists():
            dir_path.mkdir(parents=True, exist_ok=True)
            print_success(f"Directory '{directory}' created")
        else:
            print_success(f"Directory '{directory}' already exists")

def check_database_configuration() -> bool:
    """Checks and configures PostgreSQL database."""
    print_step("Checking database configuration...")
    
    # Checks if environment variables are configured
    env_file = Path(".env")
    if not env_file.exists():
        print_error(".env file not found")
        return False
      # Read variables from .env
    env_vars = {}
    with open(env_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                env_vars[key] = value
    
    # Check database settings
    database_url = env_vars.get('DATABASE_URL', '')
    postgres_password = env_vars.get('POSTGRES_PASSWORD', 'postgres123')
    
    if not database_url:
        print_warning("DATABASE_URL not configured, using default")
        print_success("Database configuration: PostgreSQL with default values")
    else:
        print_success(f"Database configuration found: {database_url.split('@')[-1] if '@' in database_url else '***'}")
    
    print_success(f"PostgreSQL password: {'***' if postgres_password else 'not configured'}")
    return True

def wait_for_database() -> bool:
    """Waits for the database to become available."""
    print_step("Waiting for PostgreSQL database...")
    
    max_attempts = 30
    for attempt in range(max_attempts):
        try:
            # Check if PostgreSQL container is running
            result = run_command(["docker", "compose", "ps", "-q", "postgres"], capture_output=True)
            if result.stdout.strip():
                # Check if database is accepting connections
                result = run_command([
                    "docker", "compose", "exec", "-T", "postgres", 
                    "pg_isready", "-U", "postgres", "-d", "trading_signals"
                ], capture_output=True, check=False)
                
                if result.returncode == 0:
                    print_success("✅ PostgreSQL is available and accepting connections")
                    return True
            
            print(f"🔄 Attempt {attempt + 1}/{max_attempts} - waiting for PostgreSQL...")
            time.sleep(2)
            
        except subprocess.CalledProcessError:
            print(f"🔄 Tentativa {attempt + 1}/{max_attempts} - PostgreSQL ainda não está pronto...")
            time.sleep(2)
    
    print_error("❌ PostgreSQL did not become available within expected time")
    return False

def initialize_database() -> bool:
    """Initializes the database with required tables."""
    print_step("Initializing database schema...")
    
    try:
        # Database will be initialized automatically by the application
        # when it connects for the first time
        print_success("✅ Database initialization delegated to application")
        return True
        
    except Exception as e:
        print_error(f"❌ Error in database initialization: {e}")
        return False

def create_env_file_if_missing() -> None:
    """Cria arquivo .env se não existir."""
    env_file = Path(".env")
    env_example = Path(".env.example")
    
    if not env_file.exists():
        print_step("Creating .env file...")
        
        if env_example.exists():
            # Copia .env.example para .env
            with open(env_example, 'r', encoding='utf-8') as src, open(env_file, 'w', encoding='utf-8') as dst:
                content = src.read()
                dst.write(content)
            print_success("Arquivo .env criado baseado em .env.example")
            print_warning("⚠️  IMPORTANT: Configure the variables in the .env file before using in production!")
        else:
            # Create basic .env
            basic_env = """# =============================================================================
# TRADING SIGNAL PROCESSOR - COMPLETE CONFIGURATION
# =============================================================================

# =============================================================================
# DESTINATION WEBHOOK (REQUIRED)
# =============================================================================
# URL of the webhook that will receive approved signals
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
# Number of top tickers to filter (e.g., Top-15)
TOP_N=15

# Intervalo em segundos para refresh da lista Finviz
FINVIZ_REFRESH_SEC=10

# Token para autenticar updates via API admin
FINVIZ_UPDATE_TOKEN=dev_token_change_me

# =============================================================================
# FINVIZ ELITE (OPTIONAL)
# =============================================================================
# Habilita recursos Finviz Elite com autenticação
FINVIZ_USE_ELITE=false

# URL de login do Finviz Elite
FINVIZ_LOGIN_URL=https://finviz.com/login_submit.ashx

# Finviz Elite credentials (only needed if FINVIZ_USE_ELITE=true)
FINVIZ_EMAIL=
FINVIZ_PASSWORD=

# =============================================================================
# CONFIGURAÇÕES DE WORKERS
# =============================================================================
# Number of workers to process main queue
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

# Finviz configuration file
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
# PROMETHEUS (OPTIONAL)
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

def stop_existing_containers(use_sudo: bool = False) -> None:
    """Stops and removes existing application containers."""
    print_step("Verificando containers existentes...")
    
    try:
        # Lista containers com o nome específico
        result = run_command([
            "docker", "ps", "-a", "--filter", f"name={CONTAINER_NAME}", "--format", "{{.Names}}"
        ], use_sudo=use_sudo)
        
        if result.stdout.strip():
            print_warning(f"Container '{CONTAINER_NAME}' encontrado. Removendo...")
            
            # Para container se estiver rodando
            try:
                run_command(["docker", "stop", CONTAINER_NAME], use_sudo=use_sudo)
                print_success("Container parado")
            except subprocess.CalledProcessError:
                pass  # Container já pode estar parado
            
            # Remove container
            try:
                run_command(["docker", "rm", CONTAINER_NAME], use_sudo=use_sudo)
                print_success("Container removido")
            except subprocess.CalledProcessError:
                pass  # Container pode não existir
        else:
            print_success("Nenhum container existente encontrado")
            
    except subprocess.CalledProcessError:
        print_warning("Erro ao verificar containers existentes, continuando...")

def cleanup_orphaned_containers(use_sudo: bool = False) -> None:
    """Remove containers órfãos do docker-compose."""
    print_step("Limpando containers órfãos...")
    
    try:
        # Usa apenas docker compose (versão nova)
        run_command(["docker", "compose", "down", "--remove-orphans"], use_sudo=use_sudo)
        print_success("Containers órfãos removidos")
    except subprocess.CalledProcessError:
        print_warning("Erro ao limpar containers órfãos, continuando...")

def build_and_start_fast(use_sudo: bool = False) -> bool:
    """Does quick build (with cache) and starts the application."""
    print_step("Doing quick build and starting application...")
    
    try:
        print("📦 Doing quick image build (with cache)...")
        run_command(["docker", "compose", "build"], capture_output=False, use_sudo=use_sudo)
        
        print("🚀 Starting application with maximum privileges...")
        run_command(["docker", "compose", "up", "-d"], capture_output=False, use_sudo=use_sudo)
        
        print_success("Application started successfully")
        return True
        
    except subprocess.CalledProcessError as e:
        print_error(f"Error starting application: {e}")
        return False

def build_and_start(use_sudo: bool = False) -> bool:
    """Builds and starts the application."""
    print_step("Building and starting application...")
    
    try:
        print("📦 Building image...")
        run_command(["docker", "compose", "build", "--no-cache"], capture_output=False, use_sudo=use_sudo)
        
        print("🚀 Starting application with maximum privileges...")
        run_command(["docker", "compose", "up", "-d"], capture_output=False, use_sudo=use_sudo)
        
        print_success("Application started successfully")
        return True
        
    except subprocess.CalledProcessError as e:
        print_error(f"Erro ao iniciar aplicação: {e}")
        return False

def wait_for_health_check() -> bool:
    """Waits for application to become healthy."""
    print_step("Waiting for application to come online...")
    
    for attempt in range(1, MAX_HEALTH_CHECK_ATTEMPTS + 1):
        try:
            import urllib.request
            import urllib.error
            
            response = urllib.request.urlopen(HEALTH_CHECK_URL, timeout=5)
            if response.status == 200:
                print_success(f"Application is online! (attempt {attempt}/{MAX_HEALTH_CHECK_ATTEMPTS})")
                return True
                
        except (urllib.error.URLError, Exception):
            pass
        
        print(f"⏳ Attempt {attempt}/{MAX_HEALTH_CHECK_ATTEMPTS} - Waiting for application to come online...")
        time.sleep(HEALTH_CHECK_INTERVAL)
    
    print_error("Application did not come online within expected time")
    return False

def show_status() -> None:
    """Shows application status."""
    print_step("Application status:")
    
    try:
        # Container status
        result = run_command(["docker", "compose", "ps"])
        print(result.stdout)
        
        # Access URLs
        print_colored("\n🌐 Access URLs:", Colors.HEADER)
        print_colored("   • Admin Interface: http://localhost:80/admin", Colors.OKGREEN)
        print_colored("   • Health Check:    http://localhost:80/health", Colors.OKGREEN)
        print_colored("   • API Docs:        http://localhost:80/docs", Colors.OKGREEN)
        print_colored("   • WebSocket:       ws://localhost:80/ws/admin-updates", Colors.OKGREEN)
        
    except subprocess.CalledProcessError:
        print_error("Error getting status")

def show_logs(follow: bool = False, use_sudo: bool = False) -> None:
    """Shows application logs."""
    print_step("Application logs:")
    
    try:
        cmd = ["docker", "compose", "logs"]
        if follow:
            cmd.append("-f")
        cmd.append(COMPOSE_SERVICE)
        
        run_command(cmd, capture_output=False, use_sudo=use_sudo)
            
    except subprocess.CalledProcessError:
        print_error("Error getting logs")

def setup_maximum_permissions() -> bool:
    """Sets up maximum permissions for all necessary files."""
    print_step("Setting up maximum permissions...")
    
    try:
        # Set permissions for configuration files
        config_files = [
            'finviz_config.json',
            'webhook_config.json', 
            'system_config.json'
        ]
        
        for config_file in config_files:
            if not os.path.exists(config_file):
                # Criar arquivo se não existir
                with open(config_file, 'w') as f:
                    f.write('{}')
                print(f"📁 Criado arquivo: {config_file}")
            
            # No Linux/Mac, usar chmod para definir permissões máximas
            if os.name != 'nt':
                run_command(['chmod', '666', config_file], use_sudo=True, check=False)
            else:
                # No Windows, definir como não somente leitura
                os.chmod(config_file, 0o666)
        
        # Criar diretórios necessários com permissões máximas
        directories = ['logs', 'data', 'database/__pycache__']
        for directory in directories:
            os.makedirs(directory, exist_ok=True)
            if os.name != 'nt':
                run_command(['chmod', '777', directory], use_sudo=True, check=False)
            else:
                os.chmod(directory, 0o777)
            print(f"📁 Diretório criado/configurado: {directory}")
        
        # Definir permissões para arquivos Python
        python_files = [f for f in os.listdir('.') if f.endswith(('.py', '.json', '.txt'))]
        for py_file in python_files:
            if os.name != 'nt':
                run_command(['chmod', '666', py_file], use_sudo=True, check=False)
            else:
                os.chmod(py_file, 0o666)
        
        print_success("Permissões máximas configuradas com sucesso!")
        return True
        
    except Exception as e:
        print_warning(f"Aviso: Não foi possível definir algumas permissões: {e}")
        print_warning("Continuando com as permissões atuais...")
        return True  # Continuar mesmo com erro de permissões

def run_with_maximum_privileges() -> bool:
    """Executa aplicação com privilégios máximos se necessário."""
    print_step("Verificando necessidade de privilégios elevados...")
    
    # Verificar se precisa de sudo para Docker
    try:
        run_command(["docker", "info"], capture_output=True)
        print_success("Docker acessível sem sudo")
        use_sudo_docker = False
    except subprocess.CalledProcessError:
        print_warning("Docker requires sudo for execution")
        use_sudo_docker = True
    
    # Configurar permissões máximas antes de iniciar
    setup_maximum_permissions()
    
    return use_sudo_docker

def main():
    """Main function."""
    parser = argparse.ArgumentParser(description="Script to run Trading Signal Processor")
    parser.add_argument("--logs", action="store_true", help="Show logs after starting")
    parser.add_argument("--follow-logs", action="store_true", help="Follow logs in real time")
    parser.add_argument("--status-only", action="store_true", help="Only show status, without restarting")
    parser.add_argument("--stop", action="store_true", help="Stop the application")
    parser.add_argument("--quick", action="store_true", help="Quick build (uses Docker cache)")
    
    args = parser.parse_args()
    
    print_colored("""
╔══════════════════════════════════════════════════════════════╗
║                 Trading Signal Processor                     ║
║                      Deploy Script                           ║
╚══════════════════════════════════════════════════════════════╝
""", Colors.HEADER)
      # Check if should only stop
    if args.stop:
        print_step("Stopping application...")
        try:
            run_command(["docker", "compose", "down"])
            print_success("Application stopped successfully")
        except subprocess.CalledProcessError:
            print_error("Error stopping application")
        return
    
    # Check if should only show status
    if args.status_only:
        show_status()
        return
    
    # Initial checks
    print_colored("🔧 CONFIGURING MAXIMUM PRIVILEGES", Colors.BOLD)
    use_sudo = run_with_maximum_privileges()
    
    if not check_docker():
        sys.exit(1)
    
    if not check_docker_compose():
        sys.exit(1)
    
    if not check_required_files():
        sys.exit(1)
      # Create .env if necessary
    create_env_file_if_missing()
      # Cria diretórios necessários
    create_required_directories()
    
    # Check database configuration
    if not check_database_configuration():
        sys.exit(1)
    
    # Para containers existentes
    stop_existing_containers(use_sudo)
    cleanup_orphaned_containers(use_sudo)
    
    # Faz build e inicia
    if args.quick:
        if not build_and_start_fast(use_sudo):
            sys.exit(1)
    else:
        if not build_and_start(use_sudo):
            sys.exit(1)
    
    # Wait for database to become available
    if not wait_for_database():
        print_warning("⚠️  Database may not be working. Application will try to connect automatically.")
    
    # Initialize database
    if not initialize_database():
        print_warning("⚠️  Database initialization may have failed. Check application logs.")
    
    # Wait for application health check
    if not wait_for_health_check():
        print_warning("Application may not be working correctly")
        print_colored("Check logs with: python run.py --logs", Colors.WARNING)
    
    # Show status
    show_status()
      # Show logs if requested
    if args.logs or args.follow_logs:
        show_logs(follow=args.follow_logs, use_sudo=use_sudo)
    
    print_colored(f"""
╔══════════════════════════════════════════════════════════════╗
║  🎉 Application is running on port 80!                      ║
║  🔧 Running with MAXIMUM PRIVILEGES (ROOT)                  ║
║                                                              ║
║  For quick update:        python run.py --quick             ║
║  To view logs:            python run.py --logs              ║
║  To follow logs:          python run.py --follow-logs       ║
║  To view status:          python run.py --status-only       ║
║  To stop:                 python run.py --stop              ║
║                                                              ║
║  ⚠️  WARNING: Container running as ROOT to resolve          ║
║      file permission issues                                 ║
╚══════════════════════════════════════════════════════════════╝
""", Colors.OKGREEN)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print_colored("\n\n🛑 Operation cancelled by user", Colors.WARNING)
        sys.exit(1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        sys.exit(1)

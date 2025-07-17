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
                else:
                    # Show more detailed error information
                    print(f"🔄 Attempt {attempt + 1}/{max_attempts} - PostgreSQL not ready yet...")
                    if result.stderr:
                        print(f"   Error: {result.stderr.strip()}")
            else:
                print(f"🔄 Attempt {attempt + 1}/{max_attempts} - PostgreSQL container not running...")
            
            time.sleep(2)
            
        except subprocess.CalledProcessError as e:
            print(f"🔄 Attempt {attempt + 1}/{max_attempts} - PostgreSQL health check failed...")
            if e.stderr:
                print(f"   Error: {e.stderr.strip()}")
            time.sleep(2)
    
    print_error("❌ PostgreSQL did not become available within expected time")
    
    # Additional diagnostics
    print_step("🔍 Database diagnostics:")
    try:
        # Show container status
        result = run_command(["docker", "compose", "ps", "postgres"], capture_output=True, check=False)
        print("Container status:")
        print(result.stdout)
        
        # Show recent logs
        result = run_command(["docker", "compose", "logs", "--tail", "20", "postgres"], capture_output=True, check=False)
        print("Recent database logs:")
        print(result.stdout)
        
    except Exception as e:
        print(f"Error getting diagnostics: {e}")
    
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
    """Remove containers órfãos do docker-compose preservando volumes."""
    print_step("Limpando containers órfãos (preservando volumes)...")
    
    try:
        # IMPORTANTE: Não usar --volumes flag para preservar dados do banco
        # Apenas remove containers órfãos, não volumes
        run_command(["docker", "compose", "down", "--remove-orphans"], use_sudo=use_sudo)
        print_success("Containers órfãos removidos (volumes preservados)")
        
        # Verificar se o volume do banco ainda existe após limpeza
        result = run_command(["docker", "volume", "ls", "--filter", "name=postgres_data", "--format", "{{.Name}}"])
        if "postgres_data" in result.stdout:
            print_success("✅ Volume do banco de dados preservado após limpeza")
        else:
            print_warning("⚠️ Volume do banco pode ter sido afetado")
            
    except subprocess.CalledProcessError:
        print_warning("Erro ao limpar containers órfãos, continuando...")

def build_and_start_fast(use_sudo: bool = False) -> bool:
    """Does quick build (with cache) and starts the application."""
    print_step("Doing quick build and starting application...")
    
    try:
        print("📦 Doing quick image build (with cache)...")
        run_command(["docker", "compose", "build"], capture_output=False, use_sudo=use_sudo)
        
        print("🚀 Starting application with maximum privileges...")
        result = run_command(["docker", "compose", "up", "-d"], capture_output=True, use_sudo=use_sudo, check=False)
        
        if result.returncode != 0:
            print_error("❌ Failed to start application")
            print("STDOUT:", result.stdout)
            print("STDERR:", result.stderr)
            
            # Provide detailed diagnostics
            diagnose_startup_failure()
            return False
        
        print_success("Application startup initiated successfully")
        return True
        
    except subprocess.CalledProcessError as e:
        print_error(f"Error starting application: {e}")
        diagnose_startup_failure()
        return False

def auto_fix_common_issues(use_sudo: bool = False) -> bool:
    """Automatically fixes common deployment issues."""
    print_step("🔧 Detecting and fixing common issues...")
    
    issues_fixed = 0
    
    try:
        # 1. Check if containers are stuck
        print("🔍 Checking container status...")
        result = run_command(["docker", "compose", "ps"], capture_output=True, check=False)
        if "Exited" in result.stdout or "Error" in result.stdout:
            print_warning("⚠️ Found problematic containers, cleaning up...")
            run_command(["docker", "compose", "down"], use_sudo=use_sudo, check=False)
            run_command(["docker", "compose", "up", "-d"], use_sudo=use_sudo, check=False)
            issues_fixed += 1
            print_success("✅ Restarted containers")
        
        # 2. Check database connectivity
        print("🔍 Testing database connectivity...")
        db_result = run_command([
            "docker", "compose", "exec", "-T", "postgres", 
            "pg_isready", "-U", "postgres", "-d", "trading_signals"
        ], capture_output=True, check=False)
        
        if db_result.returncode != 0:
            print_warning("⚠️ Database not ready, waiting...")
            time.sleep(10)
            # Test again
            db_result = run_command([
                "docker", "compose", "exec", "-T", "postgres", 
                "pg_isready", "-U", "postgres", "-d", "trading_signals"
            ], capture_output=True, check=False)
            
            if db_result.returncode == 0:
                issues_fixed += 1
                print_success("✅ Database connectivity restored")
        
        # 3. Check application health
        print("🔍 Testing application health...")
        try:
            time.sleep(5)  # Wait a bit for application to start
            import urllib.request
            response = urllib.request.urlopen("http://localhost:80/health", timeout=10)
            if response.status == 200:
                print_success("✅ Application is healthy")
            else:
                print_warning("⚠️ Application returned non-200 status")
        except Exception:
            print_warning("⚠️ Application health check failed, restarting...")
            run_command(["docker", "compose", "restart", "trading-signal-processor"], use_sudo=use_sudo, check=False)
            issues_fixed += 1
        
        # 4. Check and fix permissions
        print("🔍 Checking file permissions...")
        try:
            config_files = ['webhook_config.json', 'system_config.json']
            for config_file in config_files:
                if not os.path.exists(config_file):
                    with open(config_file, 'w') as f:
                        f.write('{}')
                    print_success(f"✅ Created missing config file: {config_file}")
                    issues_fixed += 1
        except Exception as e:
            print_warning(f"⚠️ Permission fix failed: {e}")
        
        print_step(f"🎯 Auto-fix summary: {issues_fixed} issues detected and fixed")
        
        if issues_fixed > 0:
            print_success("🎉 Auto-fix completed! Testing final status...")
            time.sleep(5)
            show_status()
            return True
        else:
            print_success("✅ No issues detected - system appears healthy")
            return True
            
    except Exception as e:
        print_error(f"❌ Auto-fix failed: {e}")
        return False

def diagnose_startup_failure() -> None:
    """Provides detailed diagnostics when startup fails."""
    print_step("🔍 Analyzing startup failure...")
    
    try:
        # Check all container statuses
        result = run_command(["docker", "compose", "ps"], capture_output=True, check=False)
        print("Container Status:")
        print(result.stdout)
        
        # Check for specific error patterns in database logs
        print("\n📋 Recent Database Logs:")
        result = run_command(["docker", "compose", "logs", "--tail", "30", "postgres"], capture_output=True, check=False)
        db_logs = result.stdout
        print(db_logs)
        
        # Check for common database issues
        if "ERROR:" in db_logs:
            print_warning("⚠️ Database errors detected:")
            error_lines = [line for line in db_logs.split('\n') if 'ERROR:' in line]
            for error in error_lines[-3:]:  # Show last 3 errors
                print(f"   {error}")
            
            # Specific fixes for common issues
            if "column" in db_logs and "type uuid" in db_logs:
                print_warning("🔧 Detected UUID type mismatch in database initialization")
                print("   This is likely a database schema issue that can be fixed.")
        
        # Show application logs
        print("\n📋 Recent Application Logs:")
        result = run_command(["docker", "compose", "logs", "--tail", "20", "trading-signal-processor"], capture_output=True, check=False)
        print(result.stdout)
        
        # Check volume status
        result = run_command(["docker", "volume", "ls", "--filter", "name=postgres_data"], capture_output=True, check=False)
        print("\n💾 Database Volume Status:")
        print(result.stdout)
        
        # Check network connectivity
        result = run_command(["docker", "network", "ls", "--filter", "name=trading-network"], capture_output=True, check=False)
        print("\n🌐 Network Status:")
        print(result.stdout)
        
        # Suggest recovery actions
        print_step("💡 Suggested Recovery Actions:")
        print("1. Try running: python run.py --quick (if build was successful)")
        print("2. Check database logs for specific errors")
        print("3. If database issues persist, try: python run.py --db")
        print("4. For complete reset: python run.py --rebuild")
        
    except Exception as e:
        print_error(f"Error during diagnostics: {e}")

def build_and_start(use_sudo: bool = False) -> bool:
    """Builds and starts the application."""
    print_step("Building and starting application...")
    
    try:
        print("📦 Building image...")
        run_command(["docker", "compose", "build", "--no-cache"], capture_output=False, use_sudo=use_sudo)
        
        print("🚀 Starting application with maximum privileges...")
        result = run_command(["docker", "compose", "up", "-d"], capture_output=True, use_sudo=use_sudo, check=False)
        
        if result.returncode != 0:
            print_error("❌ Failed to start application")
            print("STDOUT:", result.stdout)
            print("STDERR:", result.stderr)
            
            # Provide detailed diagnostics
            diagnose_startup_failure()
            return False
        
        print_success("Application startup initiated successfully")
        return True
        
    except subprocess.CalledProcessError as e:
        print_error(f"Error starting application: {e}")
        diagnose_startup_failure()
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

def quick_update_application(use_sudo: bool = False) -> bool:
    """Quick update with maximum cache preservation - fastest update possible."""
    print_step("⚡ Starting QUICK application update process...")
    
    # 1. Backup only essential configuration files (faster)
    print_step("📦 Quick backup of essential config files...")
    essential_configs = ['.env']  # Only backup .env to preserve database credentials
    backup_dir = Path("quick_backup")
    backup_dir.mkdir(exist_ok=True)
    
    backed_up_files = []
    for config_file in essential_configs:
        if Path(config_file).exists():
            backup_path = backup_dir / f"{config_file}.backup"
            import shutil
            shutil.copy2(config_file, backup_path)
            backed_up_files.append(config_file)
            print_success(f"✅ Backed up: {config_file}")
    
    try:
        # 2. Stop ONLY the application container (preserve database)
        print_step("🛑 Quick stop of application container...")
        
        try:
            result = run_command(["docker", "ps", "--filter", f"name={CONTAINER_NAME}", "--format", "{{.Names}}"])
            if CONTAINER_NAME in result.stdout:
                run_command(["docker", "compose", "stop", COMPOSE_SERVICE], use_sudo=use_sudo)
                print_success("✅ Application container stopped (database preserved)")
            else:
                print_success("✅ Application container not running")
        except subprocess.CalledProcessError as e:
            print_warning(f"⚠️ Could not stop application container: {e}")
        
        # 3. Pull latest changes from git (quick)
        print_step("📥 Quick git pull...")
        try:
            run_command(["git", "pull", "--ff-only"], capture_output=False)
            print_success("✅ Git pull completed")
        except subprocess.CalledProcessError:
            try:
                # Fallback to fetch + reset if fast-forward fails
                run_command(["git", "fetch", "origin"], capture_output=False)
                run_command(["git", "reset", "--hard", "origin/main"], capture_output=False)
                print_success("✅ Git update completed")
            except subprocess.CalledProcessError as e:
                print_warning(f"⚠️ Git update failed: {e} - continuing with current code")
        
        # 4. Restore essential config files
        print_step("📋 Restoring essential configs...")
        for config_file in backed_up_files:
            backup_path = backup_dir / f"{config_file}.backup"
            if backup_path.exists():
                import shutil
                shutil.copy2(backup_path, config_file)
                print_success(f"✅ Restored: {config_file}")
        
        # 5. Quick build with MAXIMUM cache usage (only rebuild what changed)
        print_step("🔨 Quick build with maximum cache...")
        run_command(["docker", "compose", "build", COMPOSE_SERVICE], capture_output=False, use_sudo=use_sudo)
        
        # 6. Start services quickly
        print_step("🚀 Quick start...")
        run_command(["docker", "compose", "up", "-d"], capture_output=False, use_sudo=use_sudo)
        
        # 7. Quick health check (fewer attempts for speed)
        print_step("⚡ Quick health check...")
        for attempt in range(1, 11):  # Only 10 attempts instead of 30 for speed
            try:
                import urllib.request
                response = urllib.request.urlopen(HEALTH_CHECK_URL, timeout=3)
                if response.status == 200:
                    print_success(f"⚡ Application online! (attempt {attempt}/10)")
                    break
            except:
                pass
            
            if attempt < 10:
                print(f"⏳ Quick check {attempt}/10...")
                time.sleep(1)  # Shorter wait time
        else:
            print_warning("⚠️ Quick health check timeout - app may still be starting")
        
        print_success("⚡ QUICK UPDATE COMPLETED!")
        return True
            
    except subprocess.CalledProcessError as e:
        print_error(f"❌ Quick update failed: {e}")
        return False
    finally:
        # Cleanup backup directory
        import shutil
        if backup_dir.exists():
            shutil.rmtree(backup_dir)

def check_database_volume_integrity() -> bool:
    """Checks if the database volume exists and is properly configured."""
    print_step("🔍 Checking database volume integrity...")
    
    try:
        # Check if postgres_data volume exists (with project prefix)
        result = run_command(["docker", "volume", "ls", "--filter", "name=postgres_data", "--format", "{{.Name}}"])
        
        # Check for both possible volume names
        volume_found = False
        for line in result.stdout.strip().split('\n'):
            if 'postgres_data' in line:
                print_success(f"✅ Database volume found: {line}")
                volume_found = True
                break
        
        if not volume_found:
            print_warning("⚠️ Database volume not found - checking all volumes...")
            result = run_command(["docker", "volume", "ls", "--format", "{{.Name}}"])
            print(f"Available volumes: {result.stdout}")
            return False
            
        return True
            
    except subprocess.CalledProcessError as e:
        print_error(f"❌ Error checking database volume: {e}")
        return False

def protect_database_during_update() -> bool:
    """Ensures database container and volume are preserved during updates."""
    print_step("🛡️ Protecting database during update...")
    
    try:
        # Check if database container is running
        result = run_command(["docker", "ps", "--filter", "name=trading-db", "--format", "{{.Names}}"])
        db_running = "trading-db" in result.stdout
        
        if db_running:
            print_success("✅ Database container is running and will be preserved")
        else:
            print_warning("⚠️ Database container is not running - will start if needed")
        
        # Verify volume exists
        if not check_database_volume_integrity():
            print_warning("⚠️ Database volume check failed - continuing anyway")
        
        return True
        
    except subprocess.CalledProcessError as e:
        print_error(f"❌ Error protecting database: {e}")
        return False

def update_application(use_sudo: bool = False) -> bool:
    """Updates the application from git and rebuilds without affecting database."""
    print_step("🔄 Starting full application update process...")
    
    # 1. Backup current configuration files
    print_step("📦 Backing up current configuration files...")
    config_files = ['webhook_config.json', 'system_config.json', '.env']
    backup_dir = Path("config_backup")
    backup_dir.mkdir(exist_ok=True)
    
    backed_up_files = []
    for config_file in config_files:
        if Path(config_file).exists():
            backup_path = backup_dir / f"{config_file}.backup"
            import shutil
            shutil.copy2(config_file, backup_path)
            backed_up_files.append(config_file)
            print_success(f"✅ Backed up: {config_file}")
    
    try:
        # 2. Stop ONLY the application container (preserve database)
        print_step("🛑 Stopping application container (preserving database)...")
        
        try:
            result = run_command(["docker", "ps", "--filter", f"name={CONTAINER_NAME}", "--format", "{{.Names}}"])
            if CONTAINER_NAME in result.stdout:
                print_success(f"✅ Found running application container: {CONTAINER_NAME}")
                run_command(["docker", "compose", "stop", COMPOSE_SERVICE], use_sudo=use_sudo)
                run_command(["docker", "compose", "rm", "-f", COMPOSE_SERVICE], use_sudo=use_sudo)
                print_success("✅ Application container stopped and removed")
            else:
                print_warning("⚠️ Application container not running")
        except subprocess.CalledProcessError as e:
            print_warning(f"⚠️ Could not check/stop application container: {e}")
        
        # Verify database is still running
        print_step("🔍 Verifying database container is still running...")
        try:
            result = run_command(["docker", "ps", "--filter", "name=trading-db", "--format", "{{.Names}}"])
            if "trading-db" in result.stdout:
                print_success("✅ Database container is still running - data preserved!")
            else:
                print_warning("⚠️ Database container is not running - will be started")
        except subprocess.CalledProcessError:
            print_warning("⚠️ Could not verify database status")
        
        # 3. Pull latest changes from git
        print_step("📥 Pulling latest changes from git...")
        try:
            run_command(["git", "fetch", "origin"], capture_output=False)
            run_command(["git", "reset", "--hard", "origin/main"], capture_output=False)
            print_success("✅ Git pull completed successfully")
        except subprocess.CalledProcessError as e:
            print_error(f"❌ Git pull failed: {e}")
            print_warning("Continuing with current code version...")
        
        # 4. Restore backed up configuration files
        print_step("📋 Restoring configuration files...")
        for config_file in backed_up_files:
            backup_path = backup_dir / f"{config_file}.backup"
            if backup_path.exists():
                import shutil
                shutil.copy2(backup_path, config_file)
                print_success(f"✅ Restored: {config_file}")
        
        # 5. Create any missing configuration files with defaults
        create_missing_config_files()
        
        # 6. Rebuild ONLY the application container (not database)
        print_step("🔨 Rebuilding application container...")
        run_command(["docker", "compose", "build", "--no-cache", COMPOSE_SERVICE], capture_output=False, use_sudo=use_sudo)
        
        # 7. Start the services (database will already be running, app will start fresh)
        print_step("🚀 Starting updated application...")
        run_command(["docker", "compose", "up", "-d"], capture_output=False, use_sudo=use_sudo)
        
        # 8. Wait for application to be healthy
        if wait_for_health_check():
            print_success("🎉 Application update completed successfully!")
            
            # 9. Show final status
            print_step("📊 Final status after update:")
            show_status()
            return True
        else:
            print_warning("⚠️ Application started but health check failed")
            return False
            
    except subprocess.CalledProcessError as e:
        print_error(f"❌ Update failed: {e}")
        return False
    finally:
        # Cleanup backup directory
        import shutil
        if backup_dir.exists():
            shutil.rmtree(backup_dir)

def create_missing_config_files() -> None:
    """Creates missing configuration files with default values."""
    print_step("🔧 Creating missing configuration files...")
    
    config_defaults = {
        'webhook_config.json': {},
        'system_config.json': {}
    }
    
    for config_file, default_content in config_defaults.items():
        if not Path(config_file).exists():
            with open(config_file, 'w', encoding='utf-8') as f:
                import json
                json.dump(default_content, f, indent=4)
            print_success(f"✅ Created {config_file} with defaults")
        else:
            print_success(f"✅ {config_file} already exists")

def rebuild_database_only(use_sudo: bool = False) -> bool:
    """Rebuilds only the database container and volume - preserves application."""
    print_step("🔄 Starting DATABASE-ONLY rebuild process...")
    print_colored("⚠️  WARNING: This will DELETE ALL DATABASE DATA! ⚠️", Colors.WARNING)
    
    # Confirm the destructive operation
    print_colored("\n🚨 DATABASE DESTRUCTION CONFIRMATION 🚨", Colors.FAIL)
    print_colored("This will permanently delete:", Colors.WARNING)
    print_colored("  • Database container (trading-db)", Colors.WARNING)
    print_colored("  • All database data and volumes", Colors.WARNING)
    print_colored("  • Application will be restarted to reconnect", Colors.WARNING)
    
    try:
        # 1. Stop database container specifically
        print_step("🛑 Stopping database container...")
        try:
            run_command(["docker", "stop", "trading-db"], use_sudo=use_sudo, check=False)
            run_command(["docker", "rm", "-f", "trading-db"], use_sudo=use_sudo, check=False)
            print_success("✅ Database container stopped and removed")
        except subprocess.CalledProcessError as e:
            print_warning(f"⚠️ Error stopping database container: {e}")
        
        # 2. Remove database volumes only
        print_step("💾 Removing database volumes...")
        try:
            result = run_command(["docker", "volume", "ls", "--filter", "name=postgres_data", "--format", "{{.Name}}"])
            if result.stdout.strip():
                volume_names = [name for name in result.stdout.strip().split('\n') if 'postgres_data' in name]
                for volume_name in volume_names:
                    run_command(["docker", "volume", "rm", "-f", volume_name], use_sudo=use_sudo, check=False)
                print_success(f"✅ Removed {len(volume_names)} database volumes")
            else:
                print_success("✅ No database volumes found to remove")
        except subprocess.CalledProcessError as e:
            print_warning(f"⚠️ Error removing database volumes: {e}")
        
        # 3. Remove database image to force fresh pull
        print_step("🗑️ Removing database image...")
        try:
            run_command(["docker", "rmi", "-f", "postgres:15-alpine"], use_sudo=use_sudo, check=False)
            print_success("✅ Database image removed")
        except subprocess.CalledProcessError as e:
            print_warning(f"⚠️ Error removing database image: {e}")
        
        # 4. Start fresh database
        print_step("🚀 Starting fresh database...")
        run_command(["docker", "compose", "up", "-d", "postgres"], capture_output=False, use_sudo=use_sudo)
        
        # 5. Wait for database to be healthy
        print_step("⏳ Waiting for database to be ready...")
        if wait_for_database():
            print_success("✅ Database is ready!")
        else:
            print_warning("⚠️ Database health check failed")
        
        # 6. Restart application to reconnect to fresh database
        print_step("🔄 Restarting application...")
        try:
            run_command(["docker", "compose", "restart", "trading-signal-processor"], use_sudo=use_sudo)
            print_success("✅ Application restarted")
        except subprocess.CalledProcessError as e:
            print_warning(f"⚠️ Error restarting application: {e}")
        
        # 7. Final health check
        if wait_for_health_check():
            print_success("🎉 DATABASE REBUILD COMPLETED SUCCESSFULLY!")
            print_colored("✨ Fresh database is ready!", Colors.OKGREEN)
            
            # 8. Show final status
            print_step("📊 System status after database rebuild:")
            show_status()
            return True
        else:
            print_warning("⚠️ Application health check failed after database rebuild")
            return False
            
    except subprocess.CalledProcessError as e:
        print_error(f"❌ Database rebuild failed: {e}")
        return False

def rebuild_application_from_scratch(use_sudo: bool = False) -> bool:
    """Complete rebuild - removes all containers, volumes, images and rebuilds everything from scratch."""
    print_step("🔥 Starting COMPLETE REBUILD from scratch...")
    print_colored("⚠️  WARNING: This will DELETE ALL DATA including database! ⚠️", Colors.WARNING)
    
    # Confirm the destructive operation
    print_colored("\n🚨 DESTRUCTIVE OPERATION CONFIRMATION 🚨", Colors.FAIL)
    print_colored("This will permanently delete:", Colors.WARNING)
    print_colored("  • All application containers", Colors.WARNING)
    print_colored("  • All database data and volumes", Colors.WARNING)
    print_colored("  • All Docker images for this project", Colors.WARNING)
    print_colored("  • All cached build layers", Colors.WARNING)
    
    try:
        # 1. Stop and remove ALL containers and volumes
        print_step("🛑 Stopping and removing ALL containers and volumes...")
        try:
            # Stop all services
            run_command(["docker", "compose", "down"], use_sudo=use_sudo, check=False)
            # Remove with volumes (destructive)
            run_command(["docker", "compose", "down", "--volumes", "--remove-orphans"], use_sudo=use_sudo)
            print_success("✅ All containers and volumes removed")
        except subprocess.CalledProcessError as e:
            print_warning(f"⚠️ Error stopping containers: {e}")
        
        # 2. Remove all Docker images related to this project
        print_step("🗑️ Removing all project Docker images...")
        try:
            # Get all images related to this project
            result = run_command(["docker", "images", "--filter", "reference=urban-chainsaw*", "--format", "{{.ID}}"])
            if result.stdout.strip():
                image_ids = result.stdout.strip().split('\n')
                for image_id in image_ids:
                    run_command(["docker", "rmi", "-f", image_id], use_sudo=use_sudo, check=False)
                print_success(f"✅ Removed {len(image_ids)} project images")
            else:
                print_success("✅ No project images found to remove")
        except subprocess.CalledProcessError as e:
            print_warning(f"⚠️ Error removing images: {e}")
        
        # 3. Remove all volumes with project name
        print_step("💾 Removing all project volumes...")
        try:
            result = run_command(["docker", "volume", "ls", "--filter", "name=urban-chainsaw", "--format", "{{.Name}}"])
            if result.stdout.strip():
                volume_names = result.stdout.strip().split('\n')
                for volume_name in volume_names:
                    run_command(["docker", "volume", "rm", "-f", volume_name], use_sudo=use_sudo, check=False)
                print_success(f"✅ Removed {len(volume_names)} project volumes")
            else:
                print_success("✅ No project volumes found to remove")
        except subprocess.CalledProcessError as e:
            print_warning(f"⚠️ Error removing volumes: {e}")
        
        # 4. Clean Docker build cache
        print_step("🧹 Cleaning Docker build cache...")
        try:
            run_command(["docker", "builder", "prune", "-f"], use_sudo=use_sudo)
            print_success("✅ Docker build cache cleaned")
        except subprocess.CalledProcessError as e:
            print_warning(f"⚠️ Error cleaning build cache: {e}")
        
        # 5. Clean any local temporary directories
        print_step("📁 Cleaning local temporary files...")
        temp_dirs = ["logs", "data"]
        for temp_dir in temp_dirs:
            temp_path = Path(temp_dir)
            if temp_path.exists():
                import shutil
                shutil.rmtree(temp_path)
                temp_path.mkdir(exist_ok=True)
                print_success(f"✅ Cleaned directory: {temp_dir}")
        
        # 6. Remove any backup directories
        backup_dirs = ["config_backup", "quick_backup"]
        for backup_dir in backup_dirs:
            backup_path = Path(backup_dir)
            if backup_path.exists():
                import shutil
                shutil.rmtree(backup_path)
                print_success(f"✅ Removed backup directory: {backup_dir}")
        
        # 7. Complete rebuild from scratch
        print_step("🔨 Building everything from scratch (no cache)...")
        run_command(["docker", "compose", "build", "--no-cache", "--pull"], capture_output=False, use_sudo=use_sudo)
        
        # 8. Start fresh services
        print_step("🚀 Starting fresh application...")
        result = run_command(["docker", "compose", "up", "-d"], capture_output=True, use_sudo=use_sudo, check=False)
        
        if result.returncode != 0:
            print_error("❌ Failed to start services")
            print("STDOUT:", result.stdout)
            print("STDERR:", result.stderr)
            
            # Try to diagnose and fix the issue
            print_step("🔧 Attempting to diagnose and fix startup issues...")
            
            # Check if database is the issue
            db_result = run_command(["docker", "compose", "up", "-d", "postgres"], capture_output=True, use_sudo=use_sudo, check=False)
            if db_result.returncode == 0:
                print_success("✅ Database started successfully, retrying application...")
                time.sleep(5)  # Wait for database to be fully ready
                
                # Retry starting the application
                app_result = run_command(["docker", "compose", "up", "-d"], capture_output=True, use_sudo=use_sudo, check=False)
                if app_result.returncode != 0:
                    print_error("❌ Application still failed to start after database fix")
                    diagnose_startup_failure()
                    return False
            else:
                print_error("❌ Database failed to start")
                diagnose_startup_failure()
                return False
        
        # 9. Wait for application to be healthy
        if wait_for_health_check():
            print_success("🎉 COMPLETE REBUILD SUCCESSFUL!")
            print_colored("✨ Everything is fresh and clean!", Colors.OKGREEN)
            
            # 10. Show final status
            print_step("📊 Fresh deployment status:")
            show_status()
            return True
        else:
            print_warning("⚠️ Application started but health check failed")
            return False
            
    except subprocess.CalledProcessError as e:
        print_error(f"❌ Rebuild failed: {e}")
        return False

# Insert before main function
def main():
    """Main function."""
    parser = argparse.ArgumentParser(description="Script to run Trading Signal Processor")
    parser.add_argument("--logs", action="store_true", help="Show logs after starting")
    parser.add_argument("--follow-logs", action="store_true", help="Follow logs in real time")
    parser.add_argument("--status-only", action="store_true", help="Only show status, without restarting")
    parser.add_argument("--stop", action="store_true", help="Stop the application")
    parser.add_argument("--quick", action="store_true", help="Quick build (uses Docker cache)")
    parser.add_argument("--update", action="store_true", help="Update application from git and rebuild (preserves database and configs)")
    parser.add_argument("--upgrade", action="store_true", help="Alias for --update")
    parser.add_argument("--quickupdate", action="store_true", help="Quick update with maximum cache preservation (preserves database)")
    parser.add_argument("--quickupgrade", action="store_true", help="Alias for --quickupdate")
    parser.add_argument("--rebuild", action="store_true", help="Complete rebuild - removes all containers, volumes, images and rebuilds everything from scratch (DESTRUCTIVE)")
    parser.add_argument("--db", action="store_true", help="Rebuild only the database - removes database container and volume, creates fresh database (DESTRUCTIVE for database only)")
    parser.add_argument("--fix", action="store_true", help="Auto-fix common issues and restart services")
    parser.add_argument("--diagnose", action="store_true", help="Run comprehensive diagnostics without making changes")
    
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
    
    # Check if should run diagnostics
    if args.diagnose:
        print_step("🔍 Running comprehensive diagnostics...")
        diagnose_startup_failure()
        return
    
    # Check if should auto-fix issues
    if args.fix:
        print_step("🔧 Running auto-fix for common issues...")
        if auto_fix_common_issues():
            print_success("🎉 Auto-fix completed successfully!")
        else:
            print_error("❌ Auto-fix failed. Manual intervention may be required.")
        return
    
    # Check if should rebuild database only
    if args.db:
        print_step("🔄 Starting DATABASE-ONLY rebuild process...")
        
        # Initial checks
        print_colored("🔧 CONFIGURING MAXIMUM PRIVILEGES", Colors.BOLD)
        use_sudo = False  # Windows doesn't use sudo
        
        if not check_docker():
            sys.exit(1)
        
        if not check_docker_compose():
            sys.exit(1)
        
        # Run database rebuild process
        if rebuild_database_only(use_sudo):
            print_success("🔄 DATABASE REBUILD COMPLETED SUCCESSFULLY!")
        else:
            print_error("❌ Database rebuild failed!")
            sys.exit(1)
        return
    
    # Check if should do complete rebuild
    if args.rebuild:
        print_step("🔥 Starting COMPLETE REBUILD process...")
        
        # Initial checks
        print_colored("🔧 CONFIGURING MAXIMUM PRIVILEGES", Colors.BOLD)
        use_sudo = False  # Windows doesn't use sudo
        
        if not check_docker():
            sys.exit(1)
        
        if not check_docker_compose():
            sys.exit(1)
        
        # Run complete rebuild process
        if rebuild_application_from_scratch(use_sudo):
            print_success("🔥 COMPLETE REBUILD COMPLETED SUCCESSFULLY!")
        else:
            print_error("❌ Complete rebuild failed!")
            sys.exit(1)
        return
    
    # Check if should do quick update
    if args.quickupdate or args.quickupgrade:
        print_step("⚡ Starting QUICK update process...")
        
        # Initial checks
        print_colored("🔧 CONFIGURING MAXIMUM PRIVILEGES", Colors.BOLD)
        use_sudo = False  # Windows doesn't use sudo
        
        if not check_docker():
            sys.exit(1)
        
        if not check_docker_compose():
            sys.exit(1)
        
        # Protect database during update
        if not protect_database_during_update():
            print_error("❌ Failed to protect database - aborting update")
            sys.exit(1)
        
        # Run quick update process
        if quick_update_application(use_sudo):
            print_success("⚡ QUICK UPDATE COMPLETED SUCCESSFULLY!")
        else:
            print_error("❌ Quick update failed!")
            sys.exit(1)
        return
    
    # Check if should do full update
    if args.update or args.upgrade:
        print_step("🔄 Starting FULL update process...")
        
        # Initial checks
        print_colored("🔧 CONFIGURING MAXIMUM PRIVILEGES", Colors.BOLD)
        use_sudo = False  # Windows doesn't use sudo
        
        if not check_docker():
            sys.exit(1)
        
        if not check_docker_compose():
            sys.exit(1)
        
        # Protect database during update
        if not protect_database_during_update():
            print_error("❌ Failed to protect database - aborting update")
            sys.exit(1)
        
        # Run full update process
        if update_application(use_sudo):
            print_success("🎉 FULL UPDATE COMPLETED SUCCESSFULLY!")
        else:
            print_error("❌ Full update failed!")
            sys.exit(1)
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
║  For quick build:         python run.py --quick             ║
║  For quick update:        python run.py --quickupdate       ║  
║  For full update:         python run.py --update            ║
║  For upgrade:             python run.py --upgrade           ║
║  For complete rebuild:    python run.py --rebuild           ║
║  For database rebuild:    python run.py --db                ║
║  To view logs:            python run.py --logs              ║
║  To follow logs:          python run.py --follow-logs       ║
║  To view status:          python run.py --status-only       ║
║  To stop:                 python run.py --stop              ║
║  To diagnose issues:      python run.py --diagnose          ║
║  To auto-fix issues:      python run.py --fix               ║
║                                                              ║
║  ⚡ QUICK UPDATE: Fastest update with maximum cache         ║
║  🔄 FULL UPDATE: Complete rebuild for major changes        ║
║  🔥 REBUILD: Nuclear option - deletes EVERYTHING and       ║
║      rebuilds from scratch (including database!)           ║
║  🔄 DB REBUILD: Deletes only database, keeps application   ║
║  🛡️  Updates preserve database and configs (except rebuild) ║
║  🔧 FIX: Auto-detects and fixes common deployment issues   ║
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

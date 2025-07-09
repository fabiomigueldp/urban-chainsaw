# Trading Signal Processor - Documentação Completa do run.py

## Visão Geral

O arquivo `run.py` é o script principal de automação e gerenciamento da aplicação Trading Signal Processor. Ele automatiza todo o processo de deployment, atualização e gerenciamento da aplicação usando Docker Compose, incluindo verificações de dependências, configuração do banco de dados PostgreSQL, builds da aplicação e monitoramento de saúde.

## Arquitetura e Funcionamento

### Sistema de Containerização
A aplicação utiliza **Docker Compose** com dois serviços principais:
- **postgres** (container: trading-db): Banco de dados PostgreSQL 15-alpine
- **trading-signal-processor** (container: trading-signal-processor): Aplicação FastAPI

### Configuração de Privilégios Máximos
O script executa a aplicação com **privilégios máximos** (root) para resolver problemas de permissões de arquivo, especialmente em sistemas Windows e Linux:
- Container roda como `user: root`
- Flag `privileged: true` no docker-compose
- Permissões 777 para diretórios e 666 para arquivos

## Argumentos de Linha de Comando

### Argumentos Básicos

#### `--quick`
**Função**: Build rápido usando cache do Docker
- **Comportamento**: Executa `docker compose build` (com cache)
- **Uso**: Para deploys rápidos quando apenas código Python foi alterado
- **Impacto no banco**: Nenhum (preserva dados existentes)
- **Tempo**: ~30-60 segundos

#### `--logs`
**Função**: Mostra logs após iniciar a aplicação
- **Comportamento**: Executa `docker compose logs trading-signal-processor`
- **Uso**: Para debugging imediato após deploy
- **Impacto no banco**: Nenhum

#### `--follow-logs`
**Função**: Acompanha logs em tempo real
- **Comportamento**: Executa `docker compose logs -f trading-signal-processor`
- **Uso**: Para monitoramento contínuo da aplicação
- **Impacto no banco**: Nenhum

#### `--status-only`
**Função**: Apenas mostra status sem reiniciar
- **Comportamento**: Executa `docker compose ps` e mostra URLs de acesso
- **Uso**: Para verificar estado atual sem alterações
- **Impacto no banco**: Nenhum

#### `--stop`
**Função**: Para a aplicação
- **Comportamento**: Executa `docker compose down`
- **Uso**: Para parar todos os containers
- **Impacto no banco**: Para o container do banco, mas preserva dados no volume

### Argumentos de Atualização

#### `--update` / `--upgrade`
**Função**: Atualização completa preservando banco de dados
- **Processo**:
  1. Backup de arquivos de configuração (.env, finviz_config.json, etc.)
  2. Para APENAS o container da aplicação (preserva banco)
  3. Executa `git pull` para código mais recente
  4. Restaura configurações
  5. Rebuild da aplicação com `--no-cache`
  6. Reinicia aplicação
- **Impacto no banco**: **PRESERVA** todos os dados do banco
- **Uso**: Para atualizações de código mantendo dados históricos
- **Tempo**: ~2-5 minutos

#### `--quickupdate` / `--quickupgrade`
**Função**: Atualização rápida com máximo aproveitamento de cache
- **Processo**:
  1. Backup apenas do .env (mais rápido)
  2. Para container da aplicação (preserva banco)
  3. Git pull com `--ff-only` (fast-forward)
  4. Build com cache máximo
  5. Restart rápido
- **Impacto no banco**: **PRESERVA** todos os dados do banco
- **Uso**: Para pequenas mudanças de código com velocidade máxima
- **Tempo**: ~1-2 minutos

### Argumentos Destrutivos

#### `--rebuild`
**Função**: Rebuild completo from scratch
- **⚠️ DESTRUTIVO**: Remove TUDO (containers, volumes, imagens, cache)
- **Processo**:
  1. Para e remove todos os containers
  2. Remove todos os volumes (**APAGA BANCO DE DADOS**)
  3. Remove todas as imagens do projeto
  4. Limpa cache do Docker
  5. Limpa diretórios temporários
  6. Build completo sem cache
- **Impacto no banco**: **DESTROI** todos os dados do banco
- **Uso**: Apenas quando há problemas graves ou mudanças estruturais
- **Tempo**: ~5-10 minutos

#### `--db`
**Função**: Rebuild apenas do banco de dados
- **⚠️ DESTRUTIVO PARA BANCO**: Remove apenas dados do banco
- **Processo**:
  1. Para e remove container do banco
  2. Remove volumes do PostgreSQL
  3. Remove imagem do PostgreSQL
  4. Cria banco fresh
  5. Reinicia aplicação para reconectar
- **Impacto no banco**: **DESTROI** dados do banco, preserva aplicação
- **Uso**: Para resolver problemas de corrupção ou migração de schema
- **Tempo**: ~2-3 minutos

## Processo de Inicialização Padrão

Quando executado sem argumentos especiais, o script segue este fluxo:

### 1. Verificações de Dependências
```python
check_docker()          # Verifica Docker instalado e daemon rodando
check_docker_compose()  # Verifica Docker Compose disponível
check_required_files()  # Verifica Dockerfile, docker-compose.yml, etc.
```

### 2. Configuração de Ambiente
```python
create_env_file_if_missing()      # Cria .env se não existir
create_required_directories()     # Cria diretórios data/, logs/
check_database_configuration()    # Valida configurações do banco
```

### 3. Limpeza e Preparação
```python
stop_existing_containers()     # Para containers existentes
cleanup_orphaned_containers()  # Remove containers órfãos (preserva volumes)
setup_maximum_permissions()    # Configura permissões máximas
```

### 4. Build e Inicialização
```python
build_and_start()           # Build completo (--no-cache)
# ou
build_and_start_fast()     # Build rápido (com cache) se --quick
```

### 5. Verificação de Saúde
```python
wait_for_database()      # Aguarda PostgreSQL ficar disponível (30 tentativas)
initialize_database()    # Inicialização delegada à aplicação
wait_for_health_check()  # Aguarda endpoint /health retornar 200 (30 tentativas)
```

### 6. Status Final
```python
show_status()  # Mostra status dos containers e URLs de acesso
```

## Configuração de Banco de Dados

### Variáveis de Ambiente Críticas
- `DATABASE_URL`: postgresql+asyncpg://postgres:${POSTGRES_PASSWORD}@postgres:5432/trading_signals
- `POSTGRES_PASSWORD`: Senha do PostgreSQL (padrão: postgres123)

### Volume Persistente
- **Volume**: `postgres_data` (Docker named volume)
- **Mount Point**: `/var/lib/postgresql/data`
- **Preservação**: Mantido durante updates, removido apenas em rebuild

### Health Checks
```bash
pg_isready -U postgres -d trading_signals
```

## Sistema de Health Checks

### Database Health Check
- **Comando**: `pg_isready -U postgres -d trading_signals`
- **Intervalo**: 10 segundos
- **Timeout**: 5 segundos
- **Retries**: 5 tentativas

### Application Health Check
- **URL**: http://localhost:80/health
- **Intervalo**: 30 segundos
- **Timeout**: 10 segundos
- **Retries**: 3 tentativas

## Gerenciamento de Configurações

### Arquivos de Configuração Preservados
1. **`.env`**: Variáveis de ambiente principais
2. **`finviz_config.json`**: Configurações do scraper Finviz
3. **`webhook_config.json`**: Configurações de webhooks
4. **`system_config.json`**: Configurações do sistema

### Backup/Restore Durante Updates
- Backup antes de git pull
- Restore após git pull
- Proteção contra perda de configurações

## Sistema de Logs e Debugging

### Logs da Aplicação
```bash
docker compose logs trading-signal-processor        # Logs básicos
docker compose logs -f trading-signal-processor     # Follow logs
docker compose logs --tail 20 trading-signal-processor  # Últimas 20 linhas
```

### Logs do Banco
```bash
docker compose logs postgres
docker compose logs --tail 30 postgres  # Durante troubleshooting
```

### Diagnósticos Automáticos
Quando falhas ocorrem, o script automaticamente executa:
- Status de todos os containers
- Logs recentes do banco e aplicação
- Status dos volumes
- Status da rede Docker

## URLs de Acesso da Aplicação

Após inicialização bem-sucedida:
- **Interface Admin**: http://localhost:80/admin
- **Health Check**: http://localhost:80/health
- **API Docs**: http://localhost:80/docs
- **WebSocket**: ws://localhost:80/ws/admin-updates
- **Métricas Prometheus**: http://localhost:8008 (opcional)

## Resolução de Problemas Comuns

### 1. Docker não encontrado
```bash
# Linux/Mac
sudo systemctl start docker
# Windows
# Iniciar Docker Desktop
```

### 2. Permissões de arquivo
- Script executa com privilégios máximos automaticamente
- Containers rodam como root
- Permissões 777/666 são aplicadas automaticamente

### 3. Banco não conecta
- Health check aguarda até 60 segundos (30 tentativas × 2s)
- Diagnósticos automáticos mostram logs do PostgreSQL
- Volume preservado durante troubleshooting

### 4. Build falha
- Use `--quick` para build com cache
- Use `--rebuild` para limpeza completa (último recurso)

### 5. Aplicação não responde
- Health check tenta 30 vezes com intervalo de 2s
- Logs automáticos para debugging
- Verificação de portas e network

## Fluxos de Uso Recomendados

### Desenvolvimento Local
```bash
python run.py --quick              # Deploy rápido
python run.py --follow-logs        # Monitorar logs
python run.py --quickupdate        # Atualizações rápidas
```

### Produção
```bash
python run.py                      # Deploy completo
python run.py --update            # Atualizações preservando dados
python run.py --status-only       # Verificar status
```

### Resolução de Problemas
```bash
python run.py --logs              # Ver logs
python run.py --db                # Rebuild só banco (se corrupção)
python run.py --rebuild           # Último recurso (perde dados)
```

### Manutenção
```bash
python run.py --stop              # Parar aplicação
python run.py --status-only       # Verificar status sem restart
```

## Considerações de Segurança

### Privilégios Máximos
- Containers executam como root por necessidade de permissões
- Flag privileged=true para acesso completo ao sistema
- **Justificativa**: Resolver problemas de permissões de arquivo em diferentes sistemas operacionais

### Exposição de Portas
- Porta 80: Interface web e API
- Porta 5432: PostgreSQL (para debugging local)
- Porta 8008: Métricas Prometheus (opcional)

### Dados Sensíveis
- Variáveis de ambiente protegidas no .env
- Backup/restore preserva configurações durante updates
- Database protegido durante atualizações normais

## Performance e Otimizações

### Build Performance
- `--quick`: Usar cache para builds rápidos
- `--quickupdate`: Máximo aproveitamento de cache
- Build em multi-stage para otimizar imagem final

### Runtime Performance
- Worker concurrency configurável via `WORKER_CONCURRENCY`
- Rate limiting configurável
- Health checks otimizados para não sobrecarregar

### Resource Limits
```yaml
deploy:
  resources:
    limits:
      memory: 1G
      cpus: '2.0'
    reservations:
      memory: 512M
      cpus: '1.0'
```

## Monitoramento e Observabilidade

### Health Endpoints
- `/health`: Status básico da aplicação
- Métricas Prometheus (porta 8008, opcional)

### Logging
- Logs estruturados JSON
- Rotação automática (max-size: 10m, max-file: 3)
- Níveis configuráveis via LOG_LEVEL

### Container Status
- Docker Compose PS para status
- Health checks automáticos
- Restart automático em falhas

Este documento serve como referência completa para uso e manutenção do sistema Trading Signal Processor via o script run.py.

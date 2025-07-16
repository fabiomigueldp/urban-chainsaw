# Análise e Correção de Erros 500 no Painel de Administração

## Visão Geral

O painel de administração está apresentando erros 500 (Internal Server Error) em várias rotas, o que impede o carregamento de informações críticas do sistema. A análise inicial sugere que esses erros são causados por falhas no backend ao tentar acessar recursos ou dependências que podem estar ausentes ou mal configuradas.

Este documento descreve as tarefas necessárias para diagnosticar e corrigir esses erros de forma estruturada.

---

## Tarefas de Diagnóstico e Correção

### 1. Verificar a Existência e o Conteúdo dos Arquivos de Configuração

- [x] **Verificar `system_config.py`**:
  - [x] Confirmar se o arquivo `system_config.py` existe no diretório raiz do projeto.
  - [x] Revisar o conteúdo de `system_config.py` para garantir que não há erros de sintaxe.
  - [x] Certificar-se de que a função `get_sell_all_cleanup_config` está definida corretamente e retorna um dicionário com as chaves `enabled` e `lifetime_hours`.

- [x] **Verificar `signal_reprocessing_engine.py`**:
  - [x] Confirmar se o arquivo `signal_reprocessing_engine.py` existe no diretório raiz.
  - [x] Revisar o conteúdo de `signal_reprocessing_engine.py` para garantir que não há erros de sintaxe.
  - [x] Verificar se a classe `SignalReprocessingEngine` e seus métodos, especialmente `get_health_status`, estão implementados corretamente.

### 2. Garantir o Tratamento Robusto de Erros nas Rotas do Backend

- [x] **Refatorar a Rota `/admin/system-info`**:
  - [x] Envolver as chamadas a `get_current_metrics()`, `load_finviz_config()`, e `get_sell_all_cleanup_config()` em blocos `try...except`.
  - [x] Em caso de exceção, registrar o erro detalhado no log (`_logger.error`) para facilitar a depuração futura.
  - [x] Retornar um dicionário com valores padrão ou de fallback em caso de erro, em vez de deixar a exceção subir e causar um erro 500. Isso garantirá que o frontend sempre receba uma resposta JSON válida.

- [x] **Refatorar a Rota `/admin/reprocessing/health`**:
  - [x] Envolver a chamada ao `SignalReprocessingEngine` e ao método `get_health_status()` em um bloco `try...except`.
  - [x] Capturar exceções específicas, como `ImportError` (se o módulo não estiver disponível) e exceções genéricas.
  - [x] Retornar um objeto JSON com um status de erro claro (ex: `{"status": "MODULE_NOT_AVAILABLE"}`), em vez de um erro 500.

- [x] **Refatorar a Rota `/admin/sell-all/config`**:
  - [x] Envolver a chamada a `get_sell_all_cleanup_config()` em um bloco `try...except`.
  - [x] Em caso de erro, registrar a exceção e retornar uma resposta JSON com um status de erro e uma mensagem descritiva.

### 3. Implementar o Arquivo `system_config.py` (se ausente)

- [x] **Criar o arquivo `system_config.py`**:
  - [x] Se o arquivo não existir, criá-lo no diretório raiz.
  - [x] Adicionar o seguinte conteúdo para fornecer uma implementação padrão e segura para a configuração de limpeza da lista "Sell All":
    ```python
    import json
    import os

    # Caminho para o arquivo de configuração de limpeza
    CONFIG_FILE = 'sell_all_cleanup_config.json'

    def get_sell_all_cleanup_config():
        """Carrega a configuração de limpeza do arquivo JSON."""
        if not os.path.exists(CONFIG_FILE):
            # Se o arquivo não existir, retorna a configuração padrão
            return {'enabled': False, 'lifetime_hours': 24}
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except (IOError, json.JSONDecodeError):
            # Em caso de erro de leitura ou JSON inválido, retorna o padrão
            return {'enabled': False, 'lifetime_hours': 24}

    def update_sell_all_cleanup_config(enabled, lifetime_hours):
        """Atualiza e salva a configuração de limpeza no arquivo JSON."""
        config = {'enabled': enabled, 'lifetime_hours': lifetime_hours}
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=4)
        except IOError:
            # Trata erros de escrita, se necessário
            pass
    ```

### 4. Validar as Correções e Finalizar

- [x] **Testar o Painel de Administração**:
  - [x] Após aplicar as correções, iniciar o servidor e acessar o painel de administração.
  - [x] Verificar se todas as seções (System Status, Cleanup Config, Reprocessing Health) carregam sem erros.
  - [x] Confirmar no console do navegador que não há mais erros 500 ou erros de parsing de JSON.
- [x] **Revisar os Logs do Servidor**:
  - [x] Verificar os logs do servidor para garantir que não há mais exceções não tratadas sendo registradas.

---

## Resultado Esperado

Após a conclusão destas tarefas, o painel de administração deverá funcionar de forma estável, sem erros 500. As informações do sistema serão exibidas corretamente, e qualquer falha em subsistemas (como a ausência de um arquivo de configuração) será tratada de forma robusta, exibindo uma mensagem de erro controlada no frontend em vez de quebrar a aplicação.

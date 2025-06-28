# Análise do Sistema "Reprocess Rejected Signals"

## Visão Geral

O sistema "Reprocess Rejected Signals" foi projetado para reavaliar e potencialmente aprovar sinais de negociação que foram previamente rejeitados. A lógica central é que, se um ticker (símbolo de ação) que gerou um sinal rejeitado entrar posteriormente na lista de "Top-N" tickers aprovados, o sistema deve revisitar esse sinal rejeitado e aprová-lo para processamento.

Isso é útil em cenários onde um sinal chega um pouco antes do ticker ser oficialmente classificado como "Top-N", evitando a perda de oportunidades de negociação válidas.

## Componentes Principais

O sistema é composto por três componentes principais que trabalham em conjunto:

1.  **Interface do Admin (`templates/admin.html`)**: Permite que o usuário ative/desative o reprocessamento e configure a "janela de tempo" (quanto tempo no passado o sistema deve olhar para encontrar sinais rejeitados).
2.  **Motor Finviz (`finviz_engine.py`)**: O coração da lógica. Ele monitora a lista de tickers "Top-N" e, quando um novo ticker entra na lista, aciona o mecanismo de reprocessamento.
3.  **Gerenciador de Banco de Dados (`database/DBManager.py`)**: Fornece os métodos para consultar sinais rejeitados dentro da janela de tempo especificada e para atualizar o status de um sinal de "rejeitado" para "aprovado".

## Fluxo de Funcionamento

1.  **Configuração**:
    *   Através do painel de administração (`admin.html`), o usuário pode:
        *   Ativar ou desativar a funcionalidade de reprocessamento através de um *switch*.
        *   Definir a `reprocess_window_seconds`, que determina a janela de tempo (em segundos) para buscar sinais rejeitados.
    *   Essas configurações são salvas no arquivo `finviz_config.json` e carregadas pelo `finviz_engine.py`.

2.  **Detecção de Novos Tickers**:
    *   O `finviz_engine.py` atualiza periodicamente a lista de tickers "Top-N".
    *   A cada atualização, ele compara a nova lista com a lista anterior para identificar quais tickers acabaram de entrar no "Top-N" (`entered_top_n_tickers`).

3.  **Acionamento do Reprocessamento**:
    *   Se a funcionalidade estiver **ativada** e houver **novos tickers**, o `finviz_engine.py` chama a função `_reprocess_signals_for_new_tickers`.

4.  **Busca por Sinais Rejeitados**:
    *   Para cada novo ticker, a função `_reprocess_signals_for_new_tickers` chama `db_manager.get_rejected_signals_for_reprocessing`.
    *   Este método, no `DBManager.py`, executa uma consulta no banco de dados para encontrar todos os sinais com status `REJECTED` para aquele ticker específico, e cuja data de criação esteja dentro da `reprocess_window_seconds` configurada.

5.  **Reaprovação e Reenfileiramento**:
    *   Se sinais rejeitados são encontrados, o `finviz_engine.py` itera sobre eles:
        *   Chama `db_manager.reapprove_signal` para mudar o status do sinal no banco de dados para `APPROVED` e registrar um evento de reprocessamento.
        *   Reconstrói o objeto do sinal original.
        *   Coloca o sinal recém-aprovado na `approved_signal_queue` para ser encaminhado pelo *forwarding worker*, exatamente como um sinal aprovado normalmente.
        *   Atualiza as métricas do sistema (incrementa aprovados, decrementa rejeitados) e transmite essas atualizações para a interface do admin.

## Detalhes Técnicos

*   **Configuração (`config.py` e `finviz_config.json`)**:
    *   `FINVIZ_REPROCESS_ENABLED`: Flag booleana para ligar/desligar o sistema.
    *   `FINVIZ_REPROCESS_WINDOW_SECONDS`: Inteiro que define a janela de tempo em segundos.
    *   Essas configurações são carregadas e gerenciadas pelo `FinvizEngine`.

*   **Banco de Dados (`DBManager.py`)**:
    *   `get_rejected_signals_for_reprocessing(ticker, window_seconds)`: Seleciona sinais da tabela `signals` onde `status = 'rejected'`, `normalised_ticker` corresponde ao ticker, e `created_at` está dentro da janela de tempo.
    *   `reapprove_signal(signal_id, details)`: Atualiza o `status` do sinal para `'approved'` e adiciona um novo `SignalEvent` para registrar a ação.

*   **Lógica do Engine (`finviz_engine.py`)**:
    *   A lógica principal reside no método `_update_tickers_safely`, que detecta os novos tickers.
    *   O método `_reprocess_signals_for_new_tickers` orquestra a busca e a reaprovação dos sinais.

*   **Interface (`admin.html`)**:
    *   O *switch* "Enable Reprocessing" e o campo "Reprocess Window" na seção "Top-N Approved Tickers" controlam a funcionalidade.
    *   As funções JavaScript `updateReprocessSwitch()` e `updateReprocessConfig()` lidam com a comunicação com o backend para ler e salvar essas configurações através do endpoint `/finviz/config`.

## Análise Aprofundada da Detecção e Temporalidade

### Lógica de Detecção de Novos Tickers no Top-N

A detecção de novos tickers é o gatilho para todo o processo de reavaliação. A lógica reside no método `_update_tickers_safely` dentro de `finviz_engine.py` e funciona da seguinte maneira:

1.  **Estado Anterior vs. Estado Atual**: O `FinvizEngine` mantém duas listas (conjuntos, para ser exato, visando eficiência) de tickers:
    *   `self.last_known_good_tickers`: Armazena o conjunto de tickers da última atualização bem-sucedida.
    *   `new_tickers`: Contém o conjunto de tickers obtido na atualização que acabou de ocorrer.

2.  **Cálculo da Diferença**: A detecção dos tickers que "entraram" na lista é feita através de uma operação de diferença de conjuntos em Python. O código é o seguinte:

    ```python
    entered_top_n_tickers = new_tickers - previously_known_tickers
    ```

    *   `previously_known_tickers` é uma cópia de `self.last_known_good_tickers`.
    *   Esta operação retorna um novo conjunto contendo apenas os elementos que estão em `new_tickers` mas **não** estavam em `previously_known_tickers`.

3.  **Atualização do Estado**: Após o cálculo, se a atualização foi bem-sucedida, `self.last_known_good_tickers` é atualizado com os `new_tickers`, preparando o terreno para a próxima comparação.

#### Cenários de Ativação da Lógica:

*   **Cenário 1: Ticker Novo na Lista**
    *   **Antes**: `last_known_good_tickers = {'A', 'B', 'C'}`
    *   **Agora**: `new_tickers = {'A', 'B', 'D'}` (Ticker C saiu, D entrou)
    *   **Resultado**: `entered_top_n_tickers = {'D'}`. O sistema iniciará o reprocessamento para o ticker 'D'.

*   **Cenário 2: Aumento do Top-N**
    *   **Antes**: `last_known_good_tickers = {'A', 'B'}` (Top-2)
    *   **Agora**: `new_tickers = {'A', 'B', 'C'}` (Configuração mudou para Top-3)
    *   **Resultado**: `entered_top_n_tickers = {'C'}`. O sistema reprocessará para 'C'.

*   **Cenário 3: Primeira Execução**
    *   **Antes**: `last_known_good_tickers = {}` (Conjunto vazio)
    *   **Agora**: `new_tickers = {'A', 'B', 'C'}`
    *   **Resultado**: `entered_top_n_tickers = {'A', 'B', 'C'}`. Todos os tickers são considerados "novos", e o sistema buscará sinais rejeitados para todos eles (se houver).

#### Cenários em que a Lógica **NÃO** é Ativada:

*   **Lista Estável**: Se `new_tickers` for idêntico a `last_known_good_tickers`, a diferença de conjuntos resultará em um conjunto vazio. Nenhum reprocessamento é iniciado.
*   **Ticker Sai da Lista**: Se um ticker sai do Top-N, ele não está em `new_tickers`, portanto a lógica de *entrada* não é acionada para ele.
*   **Reprocessamento Desativado**: Mesmo que `entered_top_n_tickers` não esteja vazio, a verificação `if current_cfg.reprocess_enabled...` impedirá a execução.
*   **Falha na Atualização**: Se a busca por novos tickers falhar (`_fetch_all_tickers` retorna `None`), a lógica de comparação não é executada, e o sistema mantém a `last_known_good_tickers` como está, garantindo estabilidade.

### Cálculo Temporal para Recuperação de Sinais

A elegibilidade de um sinal rejeitado para reprocessamento é estritamente controlada por um critério temporal. A mecânica é implementada no `DBManager.py`.

1.  **A Janela de Reprocessamento (`reprocess_window_seconds`)**: Este é o parâmetro chave, definido pelo usuário no `admin.html`. Ele representa o quão "para trás" no tempo o sistema deve olhar a partir do momento da verificação.

2.  **Cálculo do Ponto de Corte (`cutoff_time`)**: Dentro do método `get_rejected_signals_for_reprocessing`, o ponto de corte no tempo é calculado da seguinte forma:

    ```python
    cutoff_time = datetime.utcnow() - timedelta(seconds=window_seconds)
    ```

    *   `datetime.utcnow()`: Pega o tempo universal coordenado (UTC) **no momento exato em que a verificação é feita**. Isso garante que a referência de tempo seja consistente, independentemente do fuso horário do servidor.
    *   `timedelta(seconds=window_seconds)`: Cria um objeto que representa a duração da janela de reprocessamento.
    *   A subtração resulta em um `datetime` exato no passado. Qualquer sinal criado **antes** deste momento é considerado antigo demais para ser reprocessado.

3.  **Consulta ao Banco de Dados**: O `cutoff_time` é usado diretamente na cláusula `WHERE` da consulta SQL (via SQLAlchemy), garantindo que apenas os registros relevantes sejam retornados pelo banco de dados, o que é altamente eficiente.

    ```python
    stmt = (
        select(Signal)
        .where(
            Signal.normalised_ticker == ticker.upper(),
            Signal.status == SignalStatusEnum.REJECTED.value,
            Signal.created_at >= cutoff_time  # A MÁGICA ACONTECE AQUI
        )
    )
    ```

    *   A condição `Signal.created_at >= cutoff_time` filtra todos os sinais rejeitados, mantendo apenas aqueles cuja data de criação (`created_at`) é **maior ou igual** ao ponto de corte. Isso significa que apenas os sinais dentro da janela de tempo configurada são selecionados.

#### Exemplo Prático:

*   **Configuração**: `reprocess_window_seconds` = `300` (5 minutos).
*   **Momento da Verificação**: O `FinvizEngine` detecta um novo ticker às `10:30:00 UTC`.
*   **Cálculo**: `cutoff_time` será `10:30:00 UTC` - `5 minutos` = `10:25:00 UTC`.
*   **Consulta**: O sistema buscará por sinais rejeitados para o novo ticker que foram criados entre `10:25:00 UTC` e `10:30:00 UTC`.
    *   Um sinal rejeitado às `10:26:15 UTC` **será** encontrado e reprocessado.
    *   Um sinal rejeitado às `10:24:59 UTC` **não será** encontrado, pois é anterior ao ponto de corte.

Esta abordagem baseada em `datetime` e `timedelta` é precisa e robusta, garantindo que a janela de reprocessamento seja aplicada de forma consistente e eficiente diretamente no nível do banco de dados.

## Conclusão

O sistema "Reprocess Rejected Signals" é um mecanismo robusto e bem implementado que adiciona uma camada de resiliência ao processo de negociação. Ele previne a perda de oportunidades válidas que poderiam ser descartadas por questões de *timing* entre a chegada de um sinal e a atualização da lista de tickers "Top-N". A integração entre a interface do admin, o motor de processamento e o banco de dados é clara e eficiente.

---

## Funcionalidade Adicional: Modo de Recuperação Infinita

Para aumentar a flexibilidade do sistema, foi implementado um **Modo de Recuperação Infinita**. Esta funcionalidade permite que, ao configurar a janela de reprocessamento para `0` segundos, o sistema recupere **todos** os sinais rejeitados para um ticker que entre no Top-N, independentemente de quão antigos eles sejam.

### Detalhes da Implementação

A seguir, as modificações realizadas para habilitar esta funcionalidade:

#### 1. Backend (`database/DBManager.py`)

*   **Lógica de Consulta Condicional**: O método `get_rejected_signals_for_reprocessing` foi modificado para tratar o valor `0` de `window_seconds` como um caso especial.
    *   Se `window_seconds` for maior que `0`, a consulta ao banco de dados continua a usar a cláusula `WHERE` para filtrar sinais com base no `cutoff_time`, como antes.
    *   Se `window_seconds` for `0`, a cláusula de filtro de tempo (`Signal.created_at >= cutoff_time`) é **completamente omitida** da consulta SQL.
    *   Isso faz com que o banco de dados retorne todos os sinais com status `REJECTED` para o ticker especificado, efetivamente criando uma janela de busca infinita.

#### 2. Backend (`main.py`)

*   **Comunicação com o Frontend**: Para que a interface do usuário pudesse reagir a este novo modo, a função `get_system_info_data` foi atualizada.
    *   Ela agora lê não apenas se o reprocessamento está ativo (`reprocess_enabled`), mas também o valor de `reprocess_window_seconds`.
    *   Foi adicionado um novo campo ao payload de resposta, chamado `reprocess_mode`.
    *   Este campo pode ter três valores:
        *   `"Disabled"`: Se `reprocess_enabled` for `False`.
        *   `"Infinite"`: Se `reprocess_enabled` for `True` e `reprocess_window_seconds` for `0`.
        *   `"<N>s Window"`: Se o modo estiver ativo com uma janela de tempo específica (ex: "300s Window").

#### 3. Frontend (`templates/admin.html`)

As seguintes alterações foram feitas na interface para refletir a nova funcionalidade:

*   **Permitir Janela Zero**: O campo de input `reprocessWindowInput` teve seu atributo `min` alterado de `30` para `0`, permitindo que o usuário insira o valor zero para ativar o modo infinito.

*   **Indicador Visual**: Um novo elemento `<span>` foi adicionado ao lado do status de reprocessamento:

    ```html
    <span id="reprocessInfiniteStatus" class="badge bg-info ms-1" style="display: none;">Infinite Recovery</span>
    ```

    *   Este indicador permanece oculto (`display: none;`) por padrão.

*   **Lógica Dinâmica no JavaScript**: A função `updateSystemStatus` foi aprimorada para ler o novo campo `reprocess_mode` vindo do backend.
    *   A função agora usa uma lógica `if/else if/else` para verificar o valor de `reprocess_mode`.
    *   Se o modo for `"Infinite"`, a função não apenas ativa o status para "Active", mas também altera o estilo do `reprocessInfiniteStatus` para `display: 'inline-block'`, tornando o indicador "Infinite Recovery" visível para o usuário.
    *   Nos outros modos ("Disabled" ou janela de tempo), o indicador é explicitamente escondido.

Com estas mudanças, o sistema agora oferece uma opção de recuperação mais poderosa e a interface do usuário fornece um feedback claro e imediato sobre o modo de operação atual.

---

## Análise e Sugestão de Melhoria para a Interface (UI/UX) do Sistema "Reprocess Rejected Signals"

### Análise da Interface Atual

A funcionalidade de reprocessamento de sinais rejeitados está atualmente integrada na interface de administração (`admin.html`) dentro do card "Top-N Approved Tickers". A implementação consiste em:

1.  **Um switch "Enable Reprocessing"**: Para ativar ou desativar a funcionalidade.
2.  **Um campo numérico "Reprocess Window (seconds)"**: Para definir a janela de tempo em segundos.
3.  **Um botão "Apply Reprocess Settings"**: Para salvar as configurações.

Embora funcional, essa abordagem apresenta algumas desvantagens do ponto de vista de UI/UX:

*   **Baixa Visibilidade e Contexto**: A funcionalidade está "escondida" dentro de um card que, a princípio, serve apenas para exibir a lista de tickers. Um usuário pode não perceber que uma ação de configuração tão importante está localizada ali. A relação entre a lista de tickers e o reprocessamento não é imediatamente óbvia.
*   **Feedback de Status Limitado**: A interface atual não fornece um feedback claro e persistente sobre o *status* do sistema de reprocessamento. O usuário ativa o switch, mas não há um indicador global que mostre se o sistema está "Ativo", "Inativo" ou em modo "Infinito". O status só é visível se o usuário abrir a seção "Top-N Approved Tickers".
*   **Fluxo de Ação Fragmentado**: O usuário precisa realizar três ações distintas (ligar o switch, definir o tempo, clicar em "Apply") para uma única configuração. Isso pode ser simplificado.
*   **Falta de Clareza sobre o Modo "Infinito"**: A interface permite inserir `0` para ativar a recuperação infinita, mas não explica o que isso significa. O único feedback é um pequeno badge "Infinite Recovery" que aparece, o que pode não ser claro para todos os usuários.

### Sugestão de Melhoria na Interface (UI/UX)

Proponho uma refatoração da interface para tornar o controle do sistema de reprocessamento mais intuitivo, visível e informativo. A ideia é criar um componente dedicado e centralizado para essa funcionalidade.

#### 1. Criar um Card Dedicado: "Signal Reprocessing Engine"

Em vez de colocar os controles dentro do card "Top-N", sugiro criar um novo card na interface principal, ao lado de "System Controls" ou "Queue Status". Este card centralizaria todas as informações e ações relacionadas ao reprocessamento.

**Estrutura do Novo Card:**

```html
<!-- NOVO CARD: Signal Reprocessing Engine -->
<div class="col-md-6">
    <div class="card">
        <div class="card-header">
            <h5 class="card-title mb-0">
                <i class="bi bi-arrow-repeat"></i> Signal Reprocessing Engine
            </h5>
        </div>
        <div class="card-body">
            <!-- 1. Indicador de Status Principal -->
            <div class="d-flex justify-content-between align-items-center mb-3">
                <h6 class="mb-0">Status:</h6>
                <span id="reprocessingEngineStatus" class="badge bg-secondary fs-6">DISABLED</span>
            </div>

            <!-- 2. Controles Simplificados -->
            <div class="mb-3">
                <label for="reprocessingModeSelect" class="form-label">Operating Mode:</label>
                <select class="form-select" id="reprocessingModeSelect">
                    <option value="disabled">Disabled</option>
                    <option value="window">Time Window</option>
                    <option value="infinite">Infinite Recovery</option>
                </select>
            </div>

            <!-- 3. Campo de Janela de Tempo (visível condicionalmente) -->
            <div id="reprocessingWindowContainer" class="mb-3" style="display: none;">
                <label for="reprocessingWindowInput" class="form-label">Lookback Window (seconds):</label>
                <input type="number" class="form-control" id="reprocessingWindowInput" value="300" min="1">
                <small class="form-text text-muted">
                    How far back to look for rejected signals (e.g., 300s = 5 minutes).
                </small>
            </div>

            <!-- 4. Botão de Ação Unificado -->
            <div class="d-grid">
                <button id="applyReprocessingConfigBtn" class="btn btn-primary">
                    <i class="bi bi-check-circle"></i> Apply Configuration
                </button>
            </div>
        </div>
    </div>
</div>
```

### 2. Justificativa das Melhorias de UI/UX

*   **Centralização e Visibilidade**:
    *   Um card dedicado dá à funcionalidade a importância que ela merece. O usuário pode ver o status e acessar os controles imediatamente, sem precisar procurar.
    *   O ícone `bi-arrow-repeat` e o título "Signal Reprocessing Engine" comunicam claramente o propósito do componente.

*   **Feedback de Status Claro e Persistente**:
    *   O `<span>` com `id="reprocessingEngineStatus"` serve como um indicador de status principal e sempre visível. Ele mudaria de cor e texto (e.g., "DISABLED" em cinza, "ACTIVE (300s Window)" em verde, "ACTIVE (Infinite)" em azul), fornecendo feedback imediato sobre o modo de operação.

*   **Fluxo de Ação Simplificado e Intuitivo**:
    *   A substituição do *switch* e do campo numérico por um único `<select>` ("Operating Mode") simplifica a decisão do usuário. Ele escolhe entre "Disabled", "Time Window" ou "Infinite Recovery".
    *   O campo para definir a janela de tempo (`reprocessingWindowInput`) só aparece se o modo "Time Window" for selecionado, guiando o usuário e evitando confusão (Design de *Progressive Disclosure*).
    *   Um único botão "Apply Configuration" consolida a ação, tornando o fluxo mais coeso.

*   **Clareza sobre os Modos de Operação**:
    *   O `<select>` torna a opção "Infinite Recovery" explícita, em vez de ser um "truque" ao digitar `0`.
    *   Pequenos textos de ajuda (`<small>`) podem ser adicionados para explicar o que cada modo faz, melhorando a usabilidade.

### 3. Lógica JavaScript Necessária

O `admin.html` precisaria de uma lógica JavaScript adicional para gerenciar este novo card:

1.  **Função `updateReprocessingCard(systemInfo)`**:
    *   Esta função seria chamada dentro do `handleWebSocketMessage` (no `case 'status_update'`).
    *   Ela leria o `reprocess_mode` e `reprocess_window_seconds` do backend.
    *   Atualizaria o texto e a cor do `reprocessingEngineStatus`.
    *   Selecionaria a opção correta no `<select>` (`reprocessingModeSelect`).
    *   Mostraria ou esconderia o `reprocessingWindowContainer` com base na seleção.
    *   Preencheria o valor do `reprocessingWindowInput`.

2.  **Event Listener para o `<select>`**:
    *   Um listener no `reprocessingModeSelect` que, ao mudar, mostra ou esconde o `reprocessingWindowContainer`.

3.  **Event Listener para o Botão "Apply"**:
    *   O `applyReprocessingConfigBtn` leria os valores do `<select>` e do `input`.
    *   Se "Disabled", enviaria `reprocess_enabled: false`.
    *   Se "Infinite", enviaria `reprocess_enabled: true` e `reprocess_window_seconds: 0`.
    *   Se "Time Window", enviaria `reprocess_enabled: true` e o valor do `reprocessingWindowInput`.
    *   Enviaria a configuração para o endpoint `/finviz/config` em um único request.

Esta abordagem resultaria em uma interface muito mais clara, organizada e fácil de usar, alinhada com as melhores práticas de UI/UX. Ela remove a ambiguidade, melhora o feedback e simplifica o fluxo de trabalho do usuário para configurar uma funcionalidade crítica do sistema.

---

## Relatório de Implementação da Melhoria de UI/UX

A seguir, o detalhamento das alterações realizadas para implementar a nova interface do "Signal Reprocessing Engine".

### 1. Modificações no `templates/admin.html`

*   **Remoção do Controle Antigo**: O `<div>` que continha o *switch* "Enable Reprocessing" e o campo de input para a janela de tempo foi completamente removido de dentro do card "Top-N Approved Tickers". Isso elimina a fonte de confusão e a localização pouco intuitiva.

*   **Adição do Novo Card "Signal Reprocessing Engine"**: Um novo card foi inserido na linha de "System Controls". A estrutura da linha foi ajustada para acomodar três cards (System Controls, Reprocessing Engine, Configuration), cada um com uma largura de `col-md-4`, criando um layout mais balanceado.

*   **Implementação dos Novos Controles**: O novo card contém:
    *   Um indicador de status (`reprocessingEngineStatus`) para feedback visual imediato.
    *   Um menu suspenso (`reprocessingModeSelect`) que permite ao usuário escolher explicitamente entre os modos "Disabled", "Time Window" e "Infinite Recovery".
    *   Um campo de input para a janela de tempo (`reprocessingWindowInput`) que é exibido condicionalmente, apenas quando o modo "Time Window" está selecionado.
    *   Um único botão "Apply Configuration" (`applyReprocessingConfigBtn`) para salvar as configurações.

### 2. Modificações no JavaScript (dentro de `admin.html`)

*   **Remoção de Funções Antigas**: As funções JavaScript `updateReprocessSwitch()` e `updateReprocessConfig()` foram removidas, pois se tornaram obsoletas.

*   **Criação da Função `updateReprocessingCard(systemInfo)`**: Esta nova função é responsável por atualizar a UI do novo card com base nos dados recebidos do backend via WebSocket. Ela define o status, seleciona a opção correta no menu e gerencia a visibilidade do campo de input da janela de tempo.

*   **Criação da Função `applyReprocessingConfig()`**: Esta função é chamada quando o botão "Apply Configuration" é clicado. Ela lê os valores dos novos controles, constrói o payload JSON correto e o envia para o endpoint `/finviz/config`. A lógica trata todos os três modos de operação (Disabled, Time Window, Infinite).

*   **Criação da Função `handleReprocessingModeChange()`**: Uma pequena função auxiliar que monitora o menu suspenso e alterna a visibilidade do campo de input da janela de tempo em tempo real, melhorando a interatividade.

*   **Atualização dos Event Handlers**: Os *event listeners* foram atualizados:
    *   O antigo listener para `updateReprocessConfigBtn` foi removido.
    *   Novos listeners foram adicionados para o `reprocessingModeSelect` e o `applyReprocessingConfigBtn`.

*   **Integração com o WebSocket**: A função `handleWebSocketMessage` foi modificada para chamar `updateReprocessingCard(data.data.system_info)` durante um evento `status_update`, garantindo que a interface de reprocessamento esteja sempre sincronizada com o estado do backend.

### 3. Remoção de Elementos Visuais Redundantes

*   Os `<span>`s `reprocessStatus` e `reprocessInfiniteStatus` foram removidos do card "System", pois o novo card dedicado agora exibe essas informações de forma muito mais clara.
*   A tag `<hr>` que separava as linhas no card "System" foi removida para um visual mais limpo.

Com estas alterações, a funcionalidade de reprocessamento de sinais agora é apresentada de forma centralizada, intuitiva e com feedback claro, conforme a análise e sugestão de UI/UX.
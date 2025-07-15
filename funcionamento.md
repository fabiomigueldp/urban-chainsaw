# Funcionamento do Sistema de Reprocessamento de Sinais

## Visão Geral do Sistema

O sistema processa sinais de trading em tempo real com validação baseada em Top-N tickers do Finviz e gerenciamento de posições em banco de dados.

## Fluxo Principal de Processamento de Sinais

### 1. Recebimento de Sinais (`main.py`)
- Sinais chegam via webhook `/webhook/in`
- São enfileirados em `queue` (asyncio.Queue)
- Cada sinal recebe um `signal_id` único e timestamp

### 2. Processamento por Workers (`_queue_worker`)
O worker identifica o tipo de ação:

#### Para Sinais BUY:
- Verifica se o ticker está no Top-N atual do Finviz
- **APROVADO** → Se ticker está no Top-N
  - Cria posição no banco (`open_position`)
  - Vai para `approved_signal_queue`
- **REJEITADO** → Se ticker não está no Top-N
  - Armazenado no banco com status `REJECTED`

#### Para Sinais SELL:
- Verifica se existe posição aberta para o ticker no banco
- **APROVADO** → Se posição existe
  - Marca posição como `CLOSING`
  - Vai para `approved_signal_queue`
- **REJEITADO** → Se não há posição
  - Armazenado no banco com status `REJECTED`

### 3. Forwarding (`_forwarding_worker`)
- Envia sinais aprovados para webhook de destino
- Para sinais SELL bem-sucedidos: fecha posição (`close_position`)

## Sistema de Reprocessamento Automático

### Detectar Novos Tickers no Top-N (`finviz_engine.py`)
- A cada atualização do Finviz, compara com lista anterior
- Identifica tickers que **entraram** no Top-N (`entered_top_n_tickers`)
- Se reprocessamento habilitado → chama `_reprocess_signals_for_new_tickers`

### Engine de Reprocessamento (`signal_reprocessing_engine.py`)
Para cada ticker que entrou no Top-N:

1. **Busca sinais rejeitados** (`get_rejected_signals_for_reprocessing`)
2. **Filtra apenas sinais BUY** (SELL rejeitados eram corretos)
3. **Re-aprova** no banco (`reapprove_signal`)
4. **Reconstrói objeto Signal** (`reconstruct_signal`)
5. **Abre posição** (`open_position`)
6. **Enfileira para forwarding** (`approved_signal_queue`)

## 🚨 PROBLEMA IDENTIFICADO

### Cenário Problemático:

1. **Ticker X não está no Top-N**
2. **Sinal BUY para X chega** → REJEITADO ✅ (correto)
3. **Sinal SELL para X chega** → REJEITADO ✅ (correto, sem posição)
4. **Ticker X entra no Top-N**
5. **Reprocessamento ativo**: 
   - Encontra sinal BUY rejeitado → REPROCESSA ✅
   - **NÃO encontra** sinal SELL rejeitado (filtrado como não-BUY)
6. **Resultado**: Ticker X tem posição ABERTA, mas robô de destino nunca recebeu sinal SELL

### Consequência:
- Sistema interno: posição ABERTA para ticker X
- Robô de destino: SEM conhecimento da necessidade de SELL
- **DESSINCRONIA** entre sistemas

## Análise da Causa Raiz

O problema está na lógica do reprocessamento que:
1. **Filtra apenas sinais BUY** para reprocessamento
2. **Assume** que sinais SELL rejeitados foram corretamente rejeitados
3. **Não considera** que um SELL pode ter sido rejeitado porque a posição não existia NO MOMENTO, mas deveria ser considerado quando a posição for criada posteriormente

## 💡 SOLUÇÃO ELEGANTE: Filtro Temporal de Reprocessamento

### Conceito:
**Não reprocessar sinais BUY se o ticker já recebeu um sinal SELL posterior.**

### Lógica:
Se um ticker recebeu um sinal SELL (mesmo rejeitado), significa que o robô de origem **já decidiu sair** dessa posição. Reprocessar um BUY anterior seria criar uma posição que o robô já quis fechar.

### Estratégia:
1. **Manter** toda funcionalidade existente
2. **Adicionar filtro** no reprocessamento: verificar se há SELL posterior
3. **Respeitar cronologia** dos sinais do robô
4. **Evitar posições indesejadas**

### Implementação:

#### 1. Nova Função: `has_subsequent_sell_signal`
- Verifica se existe sinal SELL para o ticker **posterior** ao BUY sendo analisado
- Considera qualquer SELL (aprovado ou rejeitado) como indicativo de intenção de saída
- Filtra por janela de tempo configurável

#### 2. Modificação no Reprocessamento:
Antes de reprocessar um sinal BUY rejeitado:
1. Verifica se há sinal SELL **posterior** para o ticker
2. **Se há SELL posterior** → PULA reprocessamento (BUY é "obsoleto")
3. **Se não há SELL posterior** → Reprocessa normalmente

#### 3. Lógica de Tempo:
- **Janela de verificação**: mesma do reprocessamento (300s padrão)
- **Critério**: `created_at_sell > created_at_buy`
- **Configurável** e **desabilitável**

### Fluxo da Solução:

```
1. Ticker X fora do Top-N
   ├─ 10:00 - Sinal BUY → REJEITADO
   └─ 10:05 - Sinal SELL → REJEITADO

2. Ticker X entra no Top-N (10:10)
   ├─ Reprocessamento encontra BUY rejeitado (10:00)
   ├─ Verifica: há SELL posterior? SIM (10:05)
   ├─ DECISÃO: NÃO reprocessar BUY (obsoleto)
   └─ Resultado: Nenhuma posição criada ✅ (respeitou intenção de saída)

3. Cenário alternativo - sem SELL posterior:
   ├─ 10:00 - Sinal BUY → REJEITADO
   ├─ 10:10 - Ticker entra no Top-N
   ├─ Verifica: há SELL posterior? NÃO
   └─ Reprocessa BUY normalmente ✅
```

### Vantagens:
- ✅ **Mais simples** - Apenas um filtro adicional
- ✅ **Mais lógico** - Respeita cronologia dos sinais
- ✅ **Preserva funcionalidade** existente
- ✅ **Evita posições indesejadas**
- ✅ **Performance excelente** - Uma query simples
- ✅ **Configível** e **desabilitável**

### Configuração:
- `respect_sell_chronology_enabled`: true/false
- `sell_chronology_window_seconds`: igual ao reprocessamento (300s)

## Estrutura de Dados Relevantes

### Banco de Dados:
- **signals**: Todos os sinais com status (approved/rejected)
- **positions**: Posições abertas/fechadas com entry_signal_id/exit_signal_id

### Filas:
- **queue**: Sinais para processamento inicial
- **approved_signal_queue**: Sinais aprovados aguardando forwarding

### Estados:
- **shared_state["tickers"]**: Top-N atual do Finviz
- **last_known_good_tickers**: Top-N anterior (para comparação)

## Resultado Final

Com a solução implementada:
- **Robô origem**: BUY → SELL (cronologia respeitada)
- **Sistema interno**: Não cria posições indesejadas ✅
- **Robô destino**: Recebe apenas sinais válidos temporalmente ✅ 
- **Lógica**: Simples e elegante ✅
- **Estabilidade**: Funcionalidade existente preservada ✅

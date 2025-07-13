# Investigação Profunda: Sinais de EXIT sendo interpretados como BUY

## CENÁRIO DO PROBLEMA
Recebemos um sinal com:
1. `ticker: "ATHE"`, `action: "buy"` → **REJEITADO** (ticker não está no Top-N)
2. Um minuto depois: `ticker: "ATHE"`, `action: "exit"` → **SENDO INTERPRETADO COMO BUY**

## FLUXO DE PROCESSAMENTO COMPLETO

### 1. INGRESSO DO SINAL (/webhook/in)
**Arquivo:** `main.py` linhas 1131-1170

**Lógica:**
```python
# Determine signal type based on side
signal_type = SignalTypeEnum.BUY  # Default
if signal.side and signal.side.lower() == 'sell':
    signal_type = SignalTypeEnum.SELL
```

**PROBLEMA IDENTIFICADO #1:** 
- A lógica só olha para `signal.side`, mas **NÃO** olha para `signal.action`
- Se um sinal chega com `action: "exit"` mas sem `side: "sell"`, será classificado como BUY
- Para sinais com `action: "exit"` e `side: null/undefined`, o sinal é marcado como BUY no banco

### 2. WORKER DE DECISÃO (_queue_worker)
**Arquivo:** `main.py` linhas 254-340

**Lógica de Detecção:**
```python
sell_triggers = {"sell", "exit", "close"}
buy_triggers = {"buy", "long", "enter"}

sig_action = (getattr(signal, 'action', '') or '').lower()
sig_side = (getattr(signal, 'side', '') or '').lower()

if sig_action in sell_triggers or sig_side in sell_triggers:
    action_type = "SELL"
elif sig_action in buy_triggers or sig_side in buy_triggers:
    action_type = "BUY"
```

**STATUS:** ✅ CORRETO - Esta lógica está funcionando corretamente e detectaria `action: "exit"` como SELL

### 3. FORWARDING WORKER
**Arquivo:** `main.py` linhas 420-470

**Lógica de Detecção:**
```python
# More precise SELL detection - prioritize 'side' over 'action'
is_sell_signal = False
if sig_side in {"sell"}:
    is_sell_signal = True
elif sig_side in {"buy", "long", "enter"}:
    is_sell_signal = False
elif sig_action in {"sell", "exit", "close"}:
    is_sell_signal = True
```

**STATUS:** ✅ CORRETO - Esta lógica também detectaria `action: "exit"` como SELL

### 4. REPROCESSAMENTO DE SINAIS (SUSPEITO PRINCIPAL)
**Arquivo:** `finviz_engine.py` linhas 522-670

#### 4.1 Filtro Inicial de BUY Signals
**Linhas 534-542:**
```python
# Filter to only BUY signals
buy_signals = []
for signal_data in rejected_signals_payloads:
    signal_side = (signal_data.get("side") or "").lower()
    signal_type = (signal_data.get("signal_type") or "").lower()
    
    # Check if it's a BUY signal
    if signal_side in {"buy", "long", "enter"} or signal_type in {"buy"}:
        buy_signals.append(signal_data)
```

**PROBLEMA IDENTIFICADO #2:** 
- Este filtro **NÃO** verifica o campo `action` do `original_signal`
- Só olha para `side` e `signal_type` da tabela `signals`
- Um sinal com `action: "exit"` passaria por este filtro se `side` fosse null e `signal_type` fosse "buy"

#### 4.2 Erro Crítico de Código
**Linha 562:**
```python
signal_action = reprocessed_signal.action.lower() if hasattr(reprocessed_signal, 'action') and reprocessed_signal.action else ""
```

**PROBLEMA IDENTIFICADO #3:** 
- A variável `reprocessed_signal` é usada **ANTES** de ser definida
- Isso deveria causar um `NameError`, mas se não está causando, significa que existe alguma lógica de fallback
- A variável `reprocessed_signal` só é definida na linha 624

#### 4.3 Lógica de Classificação BUY/SELL
**Linhas 565-575:**
```python
buy_triggers = {"buy", "long", "enter"}
sell_triggers = {"sell", "exit", "close"}

is_buy_signal = (signal_side in buy_triggers) or (signal_action in buy_triggers)
is_sell_signal = (signal_side in sell_triggers) or (signal_action in sell_triggers)
```

**PROBLEMA IDENTIFICADO #4:**
- Se `signal_action` não foi definido corretamente devido ao erro da linha 562, será uma string vazia
- Consequentemente, `is_sell_signal` será `False` mesmo para sinais com `action: "exit"`

## ANÁLISE DA CAUSA RAIZ - ATUALIZADA

### DESCOBERTA CRÍTICA: TRY/CATCH MASCARANDO ERRO

**Localização:** `finviz_engine.py` linha 530 e 653

```python
for ticker in new_tickers:
    try:  # LINHA 530
        # ... todo o processamento, incluindo:
        signal_action = reprocessed_signal.action.lower() if ...  # LINHA 568 (NameError)
        # ... resto do código
    except Exception as e:  # LINHA 653
        _logger.error(f"Error during reprocessing for ticker {ticker}: {e}", exc_info=True)
        # CONTINUA PARA PRÓXIMO TICKER - SINAL NUNCA É REPROCESSADO
```

**IMPLICAÇÃO:** O `NameError` na linha 568 está sendo capturado e ignorado, ou seja, **os sinais SELL rejeitados NUNCA são reprocessados** devido a este erro.

### CENÁRIO REVISADO:

1. **Sinal Original:** `{"ticker": "ATHE", "action": "exit"}`
   - ❌ Webhook: Classificado incorretamente como BUY (problema #1)
   - ✅ Worker: Detecta corretamente como SELL e rejeita (sem posição)
   - Status: REJECTED

2. **Tentativa de Reprocessamento:**
   - ❌ Filtro: Sinal passa como "BUY" devido ao signal_type="buy" no banco
   - ❌ Erro: `NameError` na linha 568 capturado pelo try/catch
   - ❌ Resultado: Reprocessamento falha completamente, sinal não é reprocessado

3. **MAS ENTÃO COMO O SINAL SELL ESTÁ CHEGANDO AO FORWARDING WORKER?**

### HIPÓTESES ADICIONAIS:

#### Hipótese A: Sinal Manual/Admin
- Alguém pode estar usando `/admin/order/sell-individual` ou `/admin/order/sell-all`
- Estes endpoints criam sinais SELL que vão direto para approved_signal_queue
- Estes sinais têm `action='exit'` mas podem estar sem `side='sell'`

#### Hipótese B: Novo Sinal Real (Não Reprocessamento)
- O sinal com `action: "exit"` pode ser um **novo sinal** recebido via webhook
- Não um sinal reprocessado, mas um sinal fresco que:
  1. Chega com `action: "exit"` mas sem `side: "sell"`
  2. É classificado incorretamente como BUY no webhook
  3. É detectado corretamente como SELL no queue_worker
  4. É **aprovado** porque existe uma posição aberta
  5. Vai para forwarding_worker onde é processado como SELL

#### Hipótese C: Bug de Concorrência
- Pode haver alguma condição de corrida onde:
  1. Sinal A cria posição para ATHE
  2. Sinal B (`action: "exit"`) é processado enquanto posição ainda existe
  3. Sinal B é aprovado e enviado para forwarding

### NOVA LINHA DE INVESTIGAÇÃO:

**PERGUNTA CHAVE:** O sinal `{"ticker": "ATHE", "action": "exit"}` que está sendo interpretado como BUY é:
1. Um sinal reprocessado (que deveria falhar devido ao NameError)?
2. Um novo sinal recebido via webhook?
3. Um sinal manual/admin?

**EVIDÊNCIA NECESSÁRIA:**
- Logs mostrando como o sinal chegou (webhook, admin, reprocessamento)
- Signal_id do sinal problemático
- Verificar se existe posição aberta para ATHE no momento do processamento

## PROBLEMAS ENCONTRADOS:

### 1. **WEBHOOK CLASSIFICATION INCORRETA**
- **Localização:** `main.py` linha 1136-1138
- **Problema:** Só verifica `side`, ignora `action`
- **Impacto:** Sinais com `action: "exit"` são marcados como BUY no banco

### 2. **REPROCESSING FILTER INCOMPLETO**
- **Localização:** `finviz_engine.py` linha 534-542
- **Problema:** Não verifica `action` no `original_signal`
- **Impacto:** Sinais SELL passam pelo filtro de BUY signals

### 3. **UNDEFINED VARIABLE ERROR**
- **Localização:** `finviz_engine.py` linha 562
- **Problema:** `reprocessed_signal` usado antes de ser definido
- **Impacto:** `signal_action` fica vazio, causando classificação incorreta

### 4. **FALLBACK LOGIC VERIFICADO**
- **Localização:** `finviz_engine.py` linha 607-609
- **STATUS:** ✅ CORRETO - Existe um `continue` que deveria pular sinais não classificados
- **Problema Real:** O `continue` não está sendo executado devido ao erro na linha 568

## RASTREAMENTO DETALHADO DO BUG:

### SINAL 1: `{"ticker": "ATHE", "action": "buy"}`
1. ✅ Webhook: Classificado como BUY (correto)
2. ✅ Worker: Detectado como BUY, rejeitado por não estar no Top-N
3. ✅ Status final: REJECTED

### SINAL 2: `{"ticker": "ATHE", "action": "exit"}`
1. ❌ **Webhook:** Classificado como BUY (ERRO - deveria ser SELL)
2. ✅ Worker: Detectado como SELL (correto, apesar do erro no webhook)
3. ✅ Worker: Rejeitado por não haver posição aberta (correto)
4. ✅ Status final: REJECTED

### REPROCESSAMENTO (quando ATHE entra no Top-N):
1. ❌ **Filtro:** Sinal SELL passa pelo filtro de BUY signals (devido a signal_type="buy")
2. ❌ **Classificação:** `reprocessed_signal` undefined → `signal_action = ""`
3. ❌ **Resultado:** `is_sell_signal = False`, tratado como BUY
4. ❌ **Posição:** Abre posição incorretamente
5. ❌ **Forwarding:** Sinal SELL é enviado como se fosse aprovado BUY

## CORREÇÕES NECESSÁRIAS:

### 1. **Corrigir Classificação no Webhook**
```python
# ANTES (main.py linha 1136-1138):
signal_type = SignalTypeEnum.BUY  # Default
if signal.side and signal.side.lower() == 'sell':
    signal_type = SignalTypeEnum.SELL

# DEPOIS:
signal_type = SignalTypeEnum.BUY  # Default
sig_side = (signal.side or "").lower()
sig_action = (signal.action or "").lower()
if (sig_side in {"sell"} or sig_action in {"sell", "exit", "close"}):
    signal_type = SignalTypeEnum.SELL
```

### 2. **Corrigir Filtro de Reprocessamento**
```python
# ANTES (finviz_engine.py linha 537-540):
if signal_side in {"buy", "long", "enter"} or signal_type in {"buy"}:
    buy_signals.append(signal_data)

# DEPOIS:
original_signal = signal_data.get("original_signal", {})
original_action = (original_signal.get("action") or "").lower()

# Check if it's truly a BUY signal
if (signal_side in {"buy", "long", "enter"} or 
    signal_type in {"buy"}) and \
   original_action not in {"sell", "exit", "close"}:
    buy_signals.append(signal_data)
```

### 3. **Corrigir Undefined Variable**
```python
# Mover a definição de reprocessed_signal para ANTES de usar signal_action
# OU usar original_signal diretamente para classificação
```

### 4. **Adicionar Logging Detalhado**
Para identificar quando este problema ocorre:
```python
_logger.info(f"[REPROCESSING] Signal {signal_id}: side='{signal_side}', "
           f"type='{signal_type}', original_action='{original_action}', "
           f"classification=BUY/SELL")
```

## IMPACTO DO PROBLEMA:

1. **Posições Incorretas:** Sinais SELL são processados como BUY, abrindo posições quando deveriam fechar
2. **Descasamento de Estado:** Banco mostra posições abertas que deveriam estar fechadas
3. **Sinais Duplicados:** Mesmo ticker pode ter múltiplas posições "abertas"
4. **Perda de Controle:** Sistema perde rastreamento correto de posições

## TESTE SUGERIDO:

1. Criar sinal de teste: `{"ticker": "TEST", "action": "exit"}`
2. Verificar classificação no webhook (deve ser SELL, provavelmente será BUY)
3. Verificar processamento (deve ser rejeitado corretamente)
4. Adicionar TEST ao Top-N artificialmente
5. Verificar reprocessamento (provavelmente abrirá posição incorretamente)

## CONCLUSÃO FINAL DA INVESTIGAÇÃO

### BUGS CONFIRMADOS:

1. **BUG #1: Classificação Incorreta no Webhook** ⭐ **CRÍTICO**
   - **Causa:** Só verifica `side`, ignora `action`
   - **Impacto:** Sinais `action: "exit"` sem `side: "sell"` são marcados como BUY
   - **Severidade:** ALTA - Causa classificação incorreta de sinais

2. **BUG #2: Filtro de Reprocessamento Incompleto** ⭐ **MÉDIO**
   - **Causa:** Não verifica `action` do `original_signal`
   - **Impacto:** Sinais SELL podem passar pelo filtro de BUY
   - **Severidade:** MÉDIA - Pode causar reprocessamento incorreto

3. **BUG #3: NameError no Reprocessamento** ⭐ **CRÍTICO**
   - **Causa:** `reprocessed_signal` usado antes de ser definido
   - **Impacto:** Reprocessamento falha completamente
   - **Severidade:** CRÍTICA - Quebra funcionalidade de reprocessamento

### CENÁRIO MAIS PROVÁVEL DO PROBLEMA RELATADO:

**O sinal `{"ticker": "ATHE", "action": "exit"}` NÃO é um sinal reprocessado, mas sim:**

1. **Um novo sinal recebido via webhook** após ATHE entrar no Top-N
2. **Bug #1:** Classificado incorretamente como BUY no webhook (signal_type="buy")
3. **Queue Worker:** Detecta corretamente como SELL due to `action: "exit"`
4. **Validação:** APROVADO porque agora existe uma posição aberta para ATHE
5. **Forwarding Worker:** Recebe sinal marcado como BUY no banco, mas com `action: "exit"`
6. **Resultado:** Confusão sobre se é BUY ou SELL

### EVIDÊNCIA SUPORTIVA:

- Reprocessamento está quebrado (Bug #3), então não pode ser origem do problema
- Webhook classification bug explicaria o sinal ser marcado como BUY no banco
- Queue worker está funcionando corretamente (detectaria SELL)
- Se há posição aberta, SELL seria aprovado

### TESTES PARA CONFIRMAR:

1. **Teste do Bug #1:**
   ```bash
   curl -X POST http://localhost/webhook/in \
     -H "Content-Type: application/json" \
     -d '{"ticker": "TEST", "action": "exit"}'
   ```
   - Verificar se signal_type no banco é "buy" (BUG CONFIRMADO)

2. **Teste do Bug #3:**
   - Adicionar ticker ao Top-N
   - Verificar logs para NameError durante reprocessamento

3. **Monitoramento em Produção:**
   - Adicionar logs detalhados mostrando origem do sinal (webhook/admin/reprocessamento)
   - Rastrear signal_id específicos problemáticos

## DESCOBERTA FINAL CRÍTICA ⚠️

### CONFIGURAÇÃO ATIVA CONFIRMADA:
**Arquivo:** `finviz_config.json`
```json
{
    "reprocess_enabled": true,
    "reprocess_window_seconds": 100
}
```

**ISSO SIGNIFICA:**
- ✅ Reprocessamento ESTÁ ATIVO no sistema
- ✅ Bug #3 (NameError) ESTÁ AFETANDO o sistema em produção
- ✅ Sinais rejeitados NÃO estão sendo reprocessados devido ao erro
- ❌ Funcionalidade principal quebrada silenciosamente

### IMPACTO REAL EM PRODUÇÃO:

1. **Reprocessamento Silenciosamente Quebrado:**
   - Sistema reporta "tentando reprocessar" mas falha no NameError
   - Logs mostram erro capturado, mas funcionalidade não funciona

2. **Sinais SELL Perdidos:**
   - Sinais com `action: "exit"` rejeitados não são reprocessados
   - Oportunidades de fechamento de posição perdidas

3. **Bug de Classificação Ativo:**
   - Novos sinais `action: "exit"` sendo classificados como BUY
   - Podem estar causando posições incorretas se há posições abertas

### AÇÃO IMEDIATA NECESSÁRIA:

**CRÍTICO - CORRIGIR BUG #3 IMEDIATAMENTE:**
```python
# LINHA 568 finviz_engine.py - CORRIGIR:
# ANTES:
signal_action = reprocessed_signal.action.lower() if hasattr(reprocessed_signal, 'action') and reprocessed_signal.action else ""

# DEPOIS (usando original_signal):
original_signal = signal_payload_dict.get("original_signal", {})
signal_action = (original_signal.get("action") or "").lower()
```

### PRIORIDADE ATUALIZADA: **EMERGÊNCIA** 🚨

- Sistema está em produção com funcionalidade crítica quebrada
- Reprocessamento não funciona há tempo desconhecido
- Classificação incorreta pode estar causando danos financeiros

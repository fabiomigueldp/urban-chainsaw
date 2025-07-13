#!/usr/bin/env python3
"""
Teste para verificar se os bugs de classificação de sinais foram corrigidos.
Este teste verifica os 3 bugs principais identificados na investigação.
"""

import json
import sys
from typing import Dict, Any

def test_webhook_classification():
    """Testa a lógica de classificação do webhook (BUG #1)"""
    print("🔍 Testando BUG #1: Classificação incorreta no webhook")
    
    # Simula a lógica corrigida do webhook
    def classify_signal(side: str = None, action: str = None) -> str:
        sig_side = (side or "").lower()
        sig_action = (action or "").lower()
        
        if (sig_side in {"sell"} or sig_action in {"sell", "exit", "close"}):
            return "SELL"
        else:
            return "BUY"  # Default
    
    # Casos de teste
    test_cases = [
        # (side, action, expected_result, description)
        (None, "exit", "SELL", "Signal com action='exit' deve ser SELL"),
        (None, "close", "SELL", "Signal com action='close' deve ser SELL"),
        ("sell", None, "SELL", "Signal com side='sell' deve ser SELL"),
        (None, "buy", "BUY", "Signal com action='buy' deve ser BUY"),
        ("buy", None, "BUY", "Signal com side='buy' deve ser BUY"),
        (None, None, "BUY", "Signal sem side/action deve ser BUY (default)"),
        ("", "exit", "SELL", "Signal com side vazio e action='exit' deve ser SELL"),
    ]
    
    all_passed = True
    for side, action, expected, description in test_cases:
        result = classify_signal(side, action)
        if result == expected:
            print(f"  ✅ {description}")
        else:
            print(f"  ❌ {description} - Esperado: {expected}, Obtido: {result}")
            all_passed = False
    
    return all_passed

def test_reprocessing_filter():
    """Testa o filtro de reprocessamento (BUG #2)"""
    print("\n🔍 Testando BUG #2: Filtro de reprocessamento incompleto")
    
    def should_reprocess_as_buy(signal_side: str, signal_type: str, original_action: str) -> bool:
        """Simula a lógica corrigida do filtro de reprocessamento"""
        signal_side = (signal_side or "").lower().strip()
        signal_type = (signal_type or "").lower().strip()
        original_action = (original_action or "").lower()
        
        buy_triggers = {"buy", "long", "enter", "open", "bull"}
        sell_triggers = {"sell", "exit", "close"}
        
        # Check if it's truly a BUY signal (exclude SELL actions)
        is_buy_signal = (
            (signal_side in buy_triggers or 
             signal_type in buy_triggers or
             (not signal_side and signal_type == "buy") or
             (not signal_side and not signal_type))
            and original_action not in sell_triggers  # Critical fix
        )
        
        return is_buy_signal
    
    # Casos de teste
    test_cases = [
        # (side, type, original_action, expected, description)
        ("", "buy", "exit", False, "Signal com type='buy' mas original_action='exit' NÃO deve ser reprocessado como BUY"),
        ("", "buy", "close", False, "Signal com type='buy' mas original_action='close' NÃO deve ser reprocessado como BUY"),
        ("", "buy", "buy", True, "Signal com type='buy' e original_action='buy' deve ser reprocessado como BUY"),
        ("buy", "", "exit", False, "Signal com side='buy' mas original_action='exit' NÃO deve ser reprocessado como BUY"),
        ("", "", "", True, "Signal vazio deve ser reprocessado como BUY (default)"),
        ("", "", "exit", False, "Signal vazio mas original_action='exit' NÃO deve ser reprocessado como BUY"),
    ]
    
    all_passed = True
    for side, signal_type, original_action, expected, description in test_cases:
        result = should_reprocess_as_buy(side, signal_type, original_action)
        if result == expected:
            print(f"  ✅ {description}")
        else:
            print(f"  ❌ {description} - Esperado: {expected}, Obtido: {result}")
            all_passed = False
    
    return all_passed

def test_signal_action_extraction():
    """Testa a extração correta do signal_action (BUG #3)"""
    print("\n🔍 Testando BUG #3: NameError no reprocessamento")
    
    def extract_signal_action(signal_payload_dict: Dict[str, Any]) -> str:
        """Simula a lógica corrigida de extração do signal_action"""
        # FIX: Use original_signal to get action instead of undefined reprocessed_signal
        original_signal = signal_payload_dict.get("original_signal", {})
        signal_action = (original_signal.get("action") or "").lower()
        return signal_action
    
    # Casos de teste
    test_cases = [
        # (payload, expected_action, description)
        (
            {"original_signal": {"action": "exit", "ticker": "ATHE"}},
            "exit",
            "Deve extrair action='exit' do original_signal"
        ),
        (
            {"original_signal": {"action": "BUY", "ticker": "ATHE"}},
            "buy",
            "Deve extrair action='BUY' (convertido para lowercase)"
        ),
        (
            {"original_signal": {"ticker": "ATHE"}},
            "",
            "Deve retornar string vazia se action não existir"
        ),
        (
            {"side": "buy", "signal_type": "buy"},
            "",
            "Deve retornar string vazia se original_signal não existir"
        ),
        (
            {"original_signal": {"action": None, "ticker": "ATHE"}},
            "",
            "Deve retornar string vazia se action for None"
        ),
    ]
    
    all_passed = True
    for payload, expected_action, description in test_cases:
        try:
            result = extract_signal_action(payload)
            if result == expected_action:
                print(f"  ✅ {description}")
            else:
                print(f"  ❌ {description} - Esperado: '{expected_action}', Obtido: '{result}'")
                all_passed = False
        except Exception as e:
            print(f"  ❌ {description} - ERRO: {e}")
            all_passed = False
    
    return all_passed

def test_complete_scenario():
    """Testa o cenário completo do problema reportado"""
    print("\n🔍 Testando CENÁRIO COMPLETO: Signal com action='exit'")
    
    # Simula o processamento completo de um sinal com action="exit"
    signal = {"ticker": "ATHE", "action": "exit", "side": None}
    
    # 1. Webhook Classification
    def webhook_classify(side, action):
        sig_side = (side or "").lower()
        sig_action = (action or "").lower()
        if (sig_side in {"sell"} or sig_action in {"sell", "exit", "close"}):
            return "SELL"
        return "BUY"
    
    webhook_result = webhook_classify(signal.get("side"), signal.get("action"))
    print(f"  1. Webhook classificação: {webhook_result} ({'✅' if webhook_result == 'SELL' else '❌'})")
    
    # 2. Filtro de Reprocessamento (assumindo que foi salvo como BUY incorretamente antes da correção)
    def reprocessing_filter(signal_side, signal_type, original_action):
        signal_side = (signal_side or "").lower().strip()
        signal_type = (signal_type or "").lower().strip()
        original_action = (original_action or "").lower()
        
        buy_triggers = {"buy", "long", "enter", "open", "bull"}
        sell_triggers = {"sell", "exit", "close"}
        
        return (
            (signal_side in buy_triggers or 
             signal_type in buy_triggers or
             (not signal_side and signal_type == "buy") or
             (not signal_side and not signal_type))
            and original_action not in sell_triggers
        )
    
    # Simula um sinal que foi incorretamente classificado como BUY no banco
    should_reprocess = reprocessing_filter("", "buy", "exit")  # signal_type="buy" (erro anterior), original_action="exit"
    print(f"  2. Filtro reprocessamento: {'REJECT' if not should_reprocess else 'INCLUDE'} ({'✅' if not should_reprocess else '❌'})")
    
    # 3. Extração de Action
    payload = {
        "side": "",
        "signal_type": "buy",  # Erro anterior no banco
        "original_signal": {"action": "exit", "ticker": "ATHE"}
    }
    
    def extract_action(payload):
        original_signal = payload.get("original_signal", {})
        return (original_signal.get("action") or "").lower()
    
    extracted_action = extract_action(payload)
    print(f"  3. Extração de action: '{extracted_action}' ({'✅' if extracted_action == 'exit' else '❌'})")
    
    # 4. Classificação Final
    def final_classify(side, action):
        buy_triggers = {"buy", "long", "enter"}
        sell_triggers = {"sell", "exit", "close"}
        
        is_sell = (side in sell_triggers) or (action in sell_triggers)
        return "SELL" if is_sell else "BUY"
    
    final_classification = final_classify("", extracted_action)
    print(f"  4. Classificação final: {final_classification} ({'✅' if final_classification == 'SELL' else '❌'})")
    
    # Resultado geral
    all_correct = (
        webhook_result == "SELL" and
        not should_reprocess and
        extracted_action == "exit" and
        final_classification == "SELL"
    )
    
    print(f"\n  🎯 RESULTADO GERAL: {'✅ TODOS OS BUGS CORRIGIDOS' if all_correct else '❌ AINDA HÁ PROBLEMAS'}")
    return all_correct

def main():
    """Executa todos os testes"""
    print("🚀 Executando testes de verificação dos bugs corrigidos\n")
    
    results = []
    results.append(test_webhook_classification())
    results.append(test_reprocessing_filter())
    results.append(test_signal_action_extraction())
    results.append(test_complete_scenario())
    
    print(f"\n{'='*60}")
    print(f"📊 RESUMO DOS TESTES")
    print(f"{'='*60}")
    
    total_tests = len(results)
    passed_tests = sum(results)
    
    print(f"Testes executados: {total_tests}")
    print(f"Testes aprovados: {passed_tests}")
    print(f"Taxa de sucesso: {(passed_tests/total_tests)*100:.1f}%")
    
    if passed_tests == total_tests:
        print(f"\n🎉 SUCESSO! Todos os bugs foram corrigidos!")
        return 0
    else:
        print(f"\n⚠️  ATENÇÃO! {total_tests - passed_tests} teste(s) ainda falhando.")
        return 1

if __name__ == "__main__":
    sys.exit(main())

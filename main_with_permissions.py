#!/usr/bin/env python3
"""
Wrapper para executar main.py com permissões máximas.
"""

import os
import sys
import stat
import subprocess

def setup_permissions():
    """Configura permissões máximas para todos os arquivos necessários."""
    try:
        # Definir permissões máximas para o diretório da aplicação
        os.chmod('/app', 0o777)
        
        # Criar arquivos de configuração com permissões máximas se não existirem
        config_files = [
            '/app/finviz_config.json',
            '/app/webhook_config.json',
            '/app/system_config.json'
        ]
        
        for config_file in config_files:
            if not os.path.exists(config_file):
                with open(config_file, 'w') as f:
                    f.write('{}')
            os.chmod(config_file, 0o666)
        
        # Definir permissões para diretórios de dados
        for directory in ['/app/logs', '/app/data', '/app/database']:
            if os.path.exists(directory):
                os.chmod(directory, 0o777)
                # Definir permissões para todos os arquivos no diretório
                for root, dirs, files in os.walk(directory):
                    for d in dirs:
                        os.chmod(os.path.join(root, d), 0o777)
                    for f in files:
                        os.chmod(os.path.join(root, f), 0o666)
        
        # Definir permissões para arquivos Python
        for file in os.listdir('/app'):
            if file.endswith(('.py', '.json', '.log', '.txt')):
                file_path = os.path.join('/app', file)
                if os.path.isfile(file_path):
                    os.chmod(file_path, 0o666)
        
        print("✅ Permissões máximas configuradas com sucesso!")
        
    except Exception as e:
        print(f"⚠️  Aviso: Não foi possível definir algumas permissões: {e}")
        # Continuar mesmo com erro de permissões

def main():
    """Função principal que configura permissões e executa a aplicação."""
    print("🔧 Configurando permissões máximas...")
    setup_permissions()
    
    print("🚀 Iniciando aplicação com permissões máximas...")
    
    # Executar a aplicação principal
    try:
        # Importar e executar o main normalmente
        import uvicorn
        from main import app
        
        # Executar uvicorn com as configurações necessárias
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=80,
            access_log=True,
            log_level="info"
        )
        
    except Exception as e:
        print(f"❌ Erro ao executar a aplicação: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

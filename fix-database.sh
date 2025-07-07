#!/bin/bash
# Solução rápida para o erro "database 'trading_signals' does not exist"

echo "🔧 Corrigindo problema do banco de dados trading_signals..."

# 1. Verificar se o container do PostgreSQL está rodando
if ! docker ps | grep -q "trading-db"; then
    echo "❌ Container trading-db não está rodando!"
    echo "Execute primeiro: docker-compose up -d postgres"
    exit 1
fi

echo "✅ Container PostgreSQL está rodando"

# 2. Conectar ao PostgreSQL e criar o banco se não existir
echo "🔄 Criando banco de dados trading_signals..."
docker exec -i trading-db psql -U postgres -c "CREATE DATABASE trading_signals;" 2>/dev/null || echo "📢 Banco trading_signals já existe (isso é normal)"

# 3. Verificar se o banco foi criado
echo "🔍 Verificando se o banco foi criado..."
if docker exec -i trading-db psql -U postgres -c "\l" | grep -q "trading_signals"; then
    echo "✅ Banco trading_signals existe"
else
    echo "❌ Falha ao criar banco trading_signals"
    exit 1
fi

# 4. Executar o script de inicialização das tabelas
echo "🔄 Executando script de inicialização das tabelas..."
if docker exec -i trading-db psql -U postgres -d trading_signals -f /docker-entrypoint-initdb.d/init.sql; then
    echo "✅ Tabelas criadas com sucesso"
else
    echo "⚠️ Alguns comandos falharam (normal se tabelas já existem)"
fi

# 5. Reiniciar a aplicação
echo "🔄 Reiniciando aplicação..."
docker restart trading-signal-processor

# 6. Aguardar alguns segundos e verificar status
echo "⏳ Aguardando aplicação reiniciar..."
sleep 5

# 7. Verificar se a aplicação está saudável
echo "🔍 Verificando health da aplicação..."
if curl -f http://localhost:80/health > /dev/null 2>&1; then
    echo "✅ Aplicação está funcionando!"
    echo ""
    echo "🎉 Problema resolvido com sucesso!"
    echo "📱 Acesse: http://localhost:80/admin"
else
    echo "⚠️ Aplicação ainda está iniciando ou há outros problemas"
    echo "📋 Para verificar logs: docker logs trading-signal-processor"
fi

echo ""
echo "📋 Para monitorar logs em tempo real:"
echo "   docker logs -f trading-signal-processor"

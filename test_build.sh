#!/bin/bash

echo "🔧 Building and testing metrics fix..."

echo "📦 Building new Docker image..."
docker-compose build

echo "🔄 Restarting services..."
docker-compose down
docker-compose up -d

echo "⏳ Waiting for services to start..."
sleep 10

echo "🩺 Checking service health..."
docker-compose ps

echo "📊 Testing metrics endpoint..."
curl -s http://localhost:8008/admin/system-info | jq '.signal_processing | {signals_approved, signals_received, data_source}'

echo "✅ Build and basic test completed!"
echo "💡 Run the Python test script for detailed testing:"
echo "   python test_metrics_fix.py"

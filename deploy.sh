#!/bin/bash
# ============================================
# AutoAI Builder - EC2 Deployment Script
# Run this on your EC2 instance after cloning
# ============================================

set -e

echo "=========================================="
echo "  AutoAI Builder - Deployment Script"
echo "=========================================="

# Check if .env exists
if [ ! -f .env ]; then
    echo ""
    echo "ERROR: .env file not found!"
    echo "Copy .env.production to .env and fill in your values:"
    echo "  cp .env.production .env"
    echo "  nano .env"
    echo ""
    exit 1
fi

# Source .env to get SERVER_IP
source .env

if [ "$SERVER_IP" = "YOUR_EC2_PUBLIC_IP" ] || [ -z "$SERVER_IP" ]; then
    echo ""
    echo "ERROR: Please set SERVER_IP in .env to your EC2 public IP"
    echo ""
    exit 1
fi

echo ""
echo "[1/5] Building sandbox base image..."
docker build -f sandbox/Dockerfile -t pyrun-sandbox-base ./sandbox

echo ""
echo "[2/5] Building and starting all services..."
docker-compose -f docker-compose.prod.yml --env-file .env up -d --build

echo ""
echo "[3/5] Waiting for services to start..."
sleep 10

echo ""
echo "[4/5] Checking service health..."
echo -n "  PostgreSQL: "
if docker exec autoai-postgres pg_isready -U pyrun -d pyrunai > /dev/null 2>&1; then
    echo "OK"
else
    echo "WAITING..."
    sleep 5
fi

echo -n "  Backend API: "
if curl -s http://localhost:8000/docs > /dev/null 2>&1; then
    echo "OK"
else
    echo "Starting up (may take 10-15 seconds)..."
fi

echo -n "  Frontend: "
if curl -s http://localhost:3000 > /dev/null 2>&1; then
    echo "OK"
else
    echo "Starting up (may take 10-15 seconds)..."
fi

echo ""
echo "[5/5] Deployment complete!"
echo ""
echo "=========================================="
echo "  Your app is running at:"
echo "  Frontend:  http://$SERVER_IP:3000"
echo "  Backend:   http://$SERVER_IP:8000"
echo "  API Docs:  http://$SERVER_IP:8000/docs"
echo "=========================================="
echo ""
echo "Useful commands:"
echo "  docker-compose -f docker-compose.prod.yml logs -f        # View logs"
echo "  docker-compose -f docker-compose.prod.yml restart        # Restart all"
echo "  docker-compose -f docker-compose.prod.yml down           # Stop all"
echo "  docker-compose -f docker-compose.prod.yml up -d --build  # Rebuild & start"
echo ""

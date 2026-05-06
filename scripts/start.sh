#!/usr/bin/env bash
# Start all memory engine services.
# Usage: ./scripts/start.sh          (start all)
#        ./scripts/start.sh api      (start API only)
#        ./scripts/start.sh daemon   (start daemon only)
#        ./scripts/start.sh status   (check running services)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Load .env if exists
if [ -f .env ]; then
    set -a
    source .env
    set +a
    echo "Loaded .env"
else
    echo "WARNING: .env not found. Copy .env.example to .env and fill in values."
    echo "  cp .env.example .env"
    exit 1
fi

# Defaults
: "${MEMORY_ENGINE_DB:=memory_engine.sqlite3}"
: "${PROJECT_REGISTRY_PATH:=config/project_registry.json}"
: "${MEMORY_API_HOST:=0.0.0.0}"
: "${MEMORY_API_PORT:=8000}"

start_api() {
    echo "Starting API on ${MEMORY_API_HOST}:${MEMORY_API_PORT}..."
    nohup python3 -m uvicorn openclaw_adapter.api:app \
        --host "$MEMORY_API_HOST" \
        --port "$MEMORY_API_PORT" \
        > api.log 2>&1 &
    echo "API PID: $!"
}

start_daemon() {
    echo "Starting lark_ws ingest daemon..."
    nohup python3 -m feishu_ingest.lark_ws_ingest_daemon \
        > ingest.log 2>&1 &
    echo "Daemon PID: $!"
}

stop_all() {
    pkill -f "uvicorn openclaw_adapter" 2>/dev/null && echo "Stopped API" || true
    pkill -f "lark_ws_ingest_daemon" 2>/dev/null && echo "Stopped daemon" || true
}

show_status() {
    if pgrep -f "uvicorn openclaw_adapter" > /dev/null; then
        echo "API: running (PID $(pgrep -f 'uvicorn openclaw_adapter'))"
    else
        echo "API: stopped"
    fi
    if pgrep -f "lark_ws_ingest_daemon" > /dev/null; then
        echo "Daemon: running (PID $(pgrep -f 'lark_ws_ingest_daemon'))"
    else
        echo "Daemon: stopped"
    fi
}

case "${1:-all}" in
    stop)
        stop_all
        ;;
    status)
        show_status
        ;;
    api)
        stop_all
        start_api
        ;;
    daemon)
        stop_all
        start_daemon
        ;;
    all)
        stop_all
        start_api
        sleep 1
        start_daemon
        ;;
    *)
        echo "Usage: $0 {all|api|daemon|stop|status}"
        exit 1
        ;;
esac

#!/usr/bin/env bash
# Production server management script.
# Usage: ./scripts/manage.sh [start|stop|restart|status]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
APP_NAME="uvicorn main_server:app"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/server.log"

# Ensure log directory exists
mkdir -p "$LOG_DIR"

cd "$PROJECT_DIR"

# Activate virtual environment if it exists
if [ -f ".venv/bin/activate" ]; then
  source .venv/bin/activate
fi

case "$1" in
  start)
    if pgrep -f "$APP_NAME" > /dev/null 2>&1; then
      echo "⚠️  Server is already running."
      exit 0
    fi
    echo "🚀 Starting server..."
    export PROD=true
    nohup uvicorn main_server:app --host 0.0.0.0 --port 8000 --workers 4 >> "$LOG_FILE" 2>&1 &
    sleep 1
    if pgrep -f "$APP_NAME" > /dev/null 2>&1; then
      echo "✅ Server started (PID: $(pgrep -f "$APP_NAME"))"
      echo "📄 Logs: tail -f $LOG_FILE"
    else
      echo "❌ Failed to start. Check $LOG_FILE"
      exit 1
    fi
    ;;

  stop)
    PIDS=$(pgrep -f "$APP_NAME" || true)
    if [ -z "$PIDS" ]; then
      echo "ℹ️  Server is not running."
      exit 0
    fi
    echo "🛑 Stopping server (PIDs: $PIDS)..."
    kill $PIDS 2>/dev/null || true
    # Wait for graceful shutdown
    for i in {1..10}; do
      if ! pgrep -f "$APP_NAME" > /dev/null 2>&1; then
        echo "✅ Server stopped."
        exit 0
      fi
      sleep 1
    done
    # Force kill if still running
    echo "⚠️  Force killing..."
    kill -9 $PIDS 2>/dev/null || true
    echo "✅ Server stopped."
    ;;

  restart)
    $0 stop
    sleep 1
    $0 start
    ;;

  status)
    if pgrep -f "$APP_NAME" > /dev/null 2>&1; then
      echo "🟢 Server is running."
      pgrep -af "$APP_NAME"
    else
      echo "🔴 Server is not running."
    fi
    ;;

  logs)
    tail -f "$LOG_FILE"
    ;;

  *)
    echo "Usage: $0 {start|stop|restart|status|logs}"
    exit 1
    ;;
esac

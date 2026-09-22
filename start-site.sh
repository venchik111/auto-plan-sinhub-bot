#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
PID_FILE="$ROOT_DIR/.auto-plan-web.pid"
LOG_FILE="$ROOT_DIR/.auto-plan-web.log"
URL="http://localhost:8000"

is_running() {
  [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

find_existing_pid() {
  pgrep -f "$ROOT_DIR/.venv/bin/python.*uvicorn app.web:app" | head -1 || true
}

status() {
  if is_running; then
    echo "Сайт запущен: $URL (PID $(cat "$PID_FILE"))"
    curl -fsS "$URL/api/health" 2>/dev/null || true
    echo
  else
    echo "Сайт не запущен."
    return 1
  fi
}

start() {
  if is_running; then
    echo "Сайт уже запущен: $URL"
    return 0
  fi
  existing_pid="$(find_existing_pid)"
  if [[ -n "$existing_pid" ]] && curl -fsS "$URL/api/health" >/dev/null 2>&1; then
    echo "$existing_pid" > "$PID_FILE"
    echo "Сайт уже запущен: $URL (PID $existing_pid)"
    return 0
  fi
  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    echo "Не найдено виртуальное окружение: $VENV_DIR" >&2
    echo "Создай его командой: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
  fi
  if [[ ! -f "$ROOT_DIR/.env" ]]; then
    echo "Не найден файл .env. Скопируй .env.example в .env и заполни настройки." >&2
    exit 1
  fi

  cd "$ROOT_DIR"
  nohup "$VENV_DIR/bin/python" -m uvicorn app.web:app \
    --host 0.0.0.0 --port 8000 >>"$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"

  for _ in {1..20}; do
    if curl -fsS "$URL/api/health" >/dev/null 2>&1; then
      echo "Готово: $URL"
      if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$URL" >/dev/null 2>&1 &
      fi
      return 0
    fi
    sleep 0.25
  done

  echo "Сайт не запустился. Последние логи:"
  tail -30 "$LOG_FILE" || true
  exit 1
}

stop() {
  if ! is_running; then
    rm -f "$PID_FILE"
    echo "Сайт уже остановлен."
    return 0
  fi
  kill "$(cat "$PID_FILE")"
  for _ in {1..20}; do
    kill -0 "$(cat "$PID_FILE")" 2>/dev/null || break
    sleep 0.1
  done
  rm -f "$PID_FILE"
  echo "Сайт остановлен."
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop || true; start ;;
  status) status ;;
  logs) tail -f "$LOG_FILE" ;;
  *)
    echo "Использование: $0 {start|stop|restart|status|logs}" >&2
    exit 2
    ;;
esac

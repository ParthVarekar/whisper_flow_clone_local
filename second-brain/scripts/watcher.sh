#!/bin/bash
# Auto-restart watcher for the Next.js dev server.
# Checks every 15s if the server responds; restarts if dead.
# This prevents "sandbox inactive" from OOM kills.

LOG=/home/z/my-project/dev.log
PIDFILE=/tmp/next-dev.pid

start_dev() {
  cd /home/z/my-project
  rm -rf .next 2>/dev/null
  nohup bun run dev > "$LOG" 2>&1 &
  echo $! > "$PIDFILE"
  echo "[watcher] dev server started (PID $!)"
}

while true; do
  PID=$(cat "$PIDFILE" 2>/dev/null)
  if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
    echo "[watcher] process dead, starting..."
    start_dev
    sleep 25
    continue
  fi

  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:3000/ 2>/dev/null)
  if [ "$HTTP_CODE" = "000" ]; then
    sleep 30
    HTTP_CODE2=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:3000/ 2>/dev/null)
    if [ "$HTTP_CODE2" = "000" ]; then
      echo "[watcher] server unresponsive, killing and restarting..."
      kill -9 "$PID" 2>/dev/null
      pkill -f "next" 2>/dev/null
      sleep 2
      start_dev
      sleep 25
    fi
  fi

  sleep 15
done

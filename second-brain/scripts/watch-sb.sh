#!/bin/bash
# Auto-restart watcher for the Second Brain dev server.
# The sandbox has limited memory; Next.js compiling heavy API routes (z-ai-sdk)
# can OOM-kill the process. This watcher restarts it automatically.
# Checks every 10s if the server responds; restarts if dead.

LOG=/tmp/sb-dev.log
PIDFILE=/tmp/sb-dev.pid
DIR=/home/z/whisper_flow_clone/second-brain

start_dev() {
  cd "$DIR"
  # Run next dev directly (not via bun run dev, which uses a tee pipe that
  # breaks when the parent shell exits). Explicitly set DATABASE_URL so the
  # server reads from the second-brain's own DB (the sandbox shell may have
  # a stale DATABASE_URL pointing at the parent project's DB).
  DATABASE_URL="file:/home/z/whisper_flow_clone/second-brain/db/second-brain.db" \
    nohup ./node_modules/.bin/next dev -p 3000 > "$LOG" 2>&1 &
  echo $! > "$PIDFILE"
  echo "[watcher] second-brain dev started (PID $!)"
}

while true; do
  PID=$(cat "$PIDFILE" 2>/dev/null)
  if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
    echo "[watcher] process dead, starting..."
    start_dev
    sleep 12
    continue
  fi

  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:3000/ 2>/dev/null)
  if [ "$HTTP_CODE" = "000" ]; then
    sleep 8
    HTTP_CODE2=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:3000/ 2>/dev/null)
    if [ "$HTTP_CODE2" = "000" ]; then
      echo "[watcher] server unresponsive, killing and restarting..."
      kill -9 "$PID" 2>/dev/null
      pkill -9 -f "next dev" 2>/dev/null
      sleep 2
      start_dev
      sleep 12
    fi
  fi

  sleep 10
done

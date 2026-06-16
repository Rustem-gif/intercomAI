#!/usr/bin/env bash
# Restart the local Ollama service (macOS).
set -euo pipefail

echo "==> Stopping Ollama..."
pkill -x ollama 2>/dev/null && echo "    killed running process" || echo "    (not running)"

# Give it a moment to release the port / model lock.
sleep 2

echo "==> Starting Ollama..."
nohup ollama serve > /tmp/ollama.log 2>&1 &
OLLAMA_PID=$!

echo -n "    Waiting for Ollama to be ready"
for i in $(seq 1 20); do
  if curl -sf http://localhost:11434/ > /dev/null 2>&1; then
    echo " — up (pid $OLLAMA_PID)"
    exit 0
  fi
  echo -n "."
  sleep 1
done

echo " — timed out. Check logs:"
echo "    tail -f /tmp/ollama.log"
exit 1

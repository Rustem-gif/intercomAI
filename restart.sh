#!/usr/bin/env bash
set -e

LABEL="com.intercom-qa-web"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

echo "Restarting $LABEL..."
launchctl stop  "$LABEL" 2>/dev/null || true
sleep 1
launchctl start "$LABEL"

echo -n "Waiting for port 8099"
for i in $(seq 1 15); do
  if lsof -iTCP:8099 -sTCP:LISTEN -nP &>/dev/null; then
    echo " — up"
    exit 0
  fi
  echo -n "."
  sleep 1
done
echo " — timed out, check logs:"
echo "  tail -f $HOME/Library/Logs/intercom-qa-web.err"
exit 1

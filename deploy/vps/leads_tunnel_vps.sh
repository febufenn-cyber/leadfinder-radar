#!/bin/bash
# VPS variant of scripts/leads_tunnel.sh — quick tunnel to the dashboard on
# :8100, publishes the current *.trycloudflare.com origin to the leads-proxy
# Worker's KV so leadfinder.robofox.online always proxies to the live tunnel.
# Run by systemd (leadfinder-radar-tunnel.service, Restart=always).
set -u
LOG="/opt/leadfinder-radar/logs/tunnel.log"
ANNOUNCE_URL="https://leadfinder.robofox.online/__announce"
# ANNOUNCE_TOKEN comes from the app .env (single secrets file on the box)
ANNOUNCE_TOKEN=$(grep '^ANNOUNCE_TOKEN=' /opt/leadfinder-radar/.env | cut -d= -f2-)
mkdir -p "$(dirname "$LOG")"

announce() {
  # Retry a few times — the Worker route may briefly race a fresh tunnel.
  for _ in 1 2 3; do
    if curl -fsS -m 15 -X POST "$ANNOUNCE_URL" \
        -H "x-announce-token: $ANNOUNCE_TOKEN" \
        -H "content-type: application/json" \
        -d "{\"origin\":\"$1\"}" >> "$LOG" 2>&1; then
      printf '\n== published origin %s\n' "$1" >> "$LOG"
      return 0
    fi
    sleep 5
  done
  printf '== PUBLISH FAILED for %s — leadfinder.robofox.online is serving a stale origin\n' "$1" >> "$LOG"
  return 1
}

published=""
/usr/bin/cloudflared tunnel --url http://localhost:8100 --no-autoupdate 2>&1 | \
while IFS= read -r line; do
  printf '%s\n' "$line" >> "$LOG"
  case "$line" in
    *trycloudflare.com*)
      url=$(printf '%s' "$line" | grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' | head -1)
      if [ -n "$url" ] && [ "$url" != "$published" ] && announce "$url"; then
        published="$url"
      fi
      ;;
  esac
done

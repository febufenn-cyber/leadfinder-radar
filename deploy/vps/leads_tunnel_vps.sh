#!/bin/bash
# VPS variant of scripts/leads_tunnel.sh — quick tunnel to the dashboard on
# :8100, publishes the current *.trycloudflare.com origin to the leads-proxy
# Worker's KV so leadfinder.robofox.online always proxies to the live tunnel.
# Run by systemd (leadfinder-radar-tunnel.service, Restart=always).
set -u
PROXY_DIR="/opt/leadfinder-radar/deploy/leads-proxy"
LOG="/opt/leadfinder-radar/logs/tunnel.log"
mkdir -p "$(dirname "$LOG")"

published=""
/usr/bin/cloudflared tunnel --url http://localhost:8100 --no-autoupdate 2>&1 | \
while IFS= read -r line; do
  printf '%s\n' "$line" >> "$LOG"
  case "$line" in
    *trycloudflare.com*)
      url=$(printf '%s' "$line" | grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' | head -1)
      if [ -n "$url" ] && [ "$url" != "$published" ]; then
        published="$url"
        (cd "$PROXY_DIR" && npx --yes wrangler kv key put origin "$url" --binding CONFIG --remote) \
          >> "$LOG" 2>&1
        printf '== published origin %s\n' "$url" >> "$LOG"
      fi
      ;;
  esac
done

#!/bin/zsh
# Quick tunnel for the LeadFinder dashboard (leadfinder.robofox.online).
#
# Runs a Cloudflare quick tunnel to localhost:8100 and publishes the current
# *.trycloudflare.com origin into the leads-proxy Worker's KV, so the Worker
# always proxies to the live tunnel. Run by launchd (KeepAlive) — if the
# tunnel dies, launchd restarts this script and the new URL is re-published.
#
# Proper upgrade later: `cloudflared tunnel login` once, then a named tunnel
# with a stable hostname — this script exists because quick tunnels need no
# interactive auth.
set -u
PROXY_DIR="$HOME/lead agent/deploy/leads-proxy"
LOG="$HOME/Library/Logs/leadfinder-tunnel.log"
export PATH="$HOME/.local/share/fnm/aliases/default/bin:/opt/homebrew/bin:/usr/bin:/bin"

published=""
/opt/homebrew/bin/cloudflared tunnel --url http://localhost:8100 --no-autoupdate 2>&1 | \
while IFS= read -r line; do
  print -r -- "$line" >> "$LOG"
  if [[ "$line" == *trycloudflare.com* ]]; then
    url=$(print -r -- "$line" | grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' | head -1)
    if [[ -n "${url}" && "${url}" != "${published}" ]]; then
      published="$url"
      (cd "$PROXY_DIR" && npx wrangler kv key put origin "$url" --binding CONFIG --remote) \
        >> "$LOG" 2>&1
      print -r -- "== published origin $url" >> "$LOG"
    fi
  fi
done

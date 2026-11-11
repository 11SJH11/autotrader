#!/usr/bin/env bash
# Read-only diagnostics. Intentionally does not read environment variables, passwords, .env, logs or credentials.
set -u
printf '=== Fedora / Kernel ===\n'
(cat /etc/fedora-release 2>/dev/null || true)
uname -sr
printf '\n=== Python / Git / Containers ===\n'
for tool in python3 git podman docker jq codex; do
  if command -v "$tool" >/dev/null 2>&1; then
    case "$tool" in
      python3) python3 --version;;
      git) git --version;;
      podman) podman --version;;
      docker) docker --version;;
      jq) jq --version;;
      codex) printf 'codex: installed\n';;
    esac
  else
    printf '%s: not installed\n' "$tool"
  fi
done
printf '\n=== System resources ===\n'
free -h | sed -n '1,3p'
df -h "$HOME" | tail -n 1
printf '\n=== Relevant user service states (read-only) ===\n'
for s in telegram-mt5-bot.service t02-mt5-demo.service ledger-backend.service ledger-frontend.service; do
  printf '%s: ' "$s"
  systemctl --user is-active "$s" 2>/dev/null || true
done
printf '\n=== Other potentially relevant user service names ===\n'
systemctl --user list-unit-files --type=service --no-legend --no-pager 2>/dev/null \
  | awk '{print $1}' | grep -Ei '(mt5|t02|telegram|ops|ledger|trading|ctrader)' | head -n 50 || true
printf '\n=== Relevant project directory names (names only; no contents) ===\n'
if [[ -d "$HOME/Projects" ]]; then
  find "$HOME/Projects" -mindepth 1 -maxdepth 2 -type d -printf '%P\n' 2>/dev/null \
    | grep -Ei '(ledger|investment|telegram|t02|synergy|trading|ops|demo|bot)' | head -n 60 || true
else
  printf '~/Projects not found\n'
fi
printf '\n=== Listening TCP ports (no addresses or process details) ===\n'
if command -v ss >/dev/null 2>&1; then
  ss -ltnH 2>/dev/null | awk '{print $4}' | sed -E 's/^.*:([0-9]+)$/port \1/' | sort -u | head -n 45
fi
printf '\n=== End read-only inventory ===\n'

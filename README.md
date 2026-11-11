# Trading Control Plane — Phase 0 Starter

A safe starting point for a **demo-only** multi-strategy trading platform.

**Current status:** planning + read-only laptop inventory. No broker connection, trade execution, API credentials, or order placement is implemented. Do not treat any status indicator as evidence that a trading system is running.

## Quick start (Fedora)

```bash
sudo dnf install -y git python3 python3-pip jq unzip
mkdir -p "$HOME/Projects/trading-control-plane"
# Extract this starter ZIP into the directory above (see ChatGPT instructions).
cd "$HOME/Projects/trading-control-plane"
bash scripts/inventory.sh | tee docs/local_inventory.txt
```

Next, give Codex `CODEX_PHASE_0_PROMPT.md`, plus `docs/BLUEPRINT.md` and `docs/SYNERGY_CONSTRAINTS.md`.

## Project constraints

- Do **not** touch, stop, replace, or modify the existing MT5/Hurricane/T02 services in Phase 0.
- Do **not** place orders or ask for broker trading credentials in Phase 0.
- No live accounts, live endpoints or live-trade toggle in V1.
- Synergy **must** use an authorized virtual display/screen-capture ingestion mechanism; direct chat/API access is not available for this group.
- Keep the execution runtime independent of the web dashboard.
- Reconcile broker truth and never blindly resend orders on timeouts.
- No credentials, auth codes, account secrets, Telegram sessions, or tokens in source code, screenshots, logs, terminal output or ChatGPT messages.

## Next gates

1. Safe inventory and architecture proposal (read-only).
2. Fake broker + durable queue + idempotency/reconciliation tests.
3. Demo cTrader read-only connection and account/symbol capability check.
4. Controlled broker-confirmed minimum-volume demo open/modify/close, only with explicit approval.
5. Hurricane adapter, then T02, then Synergy.

Read `docs/BLUEPRINT.md` for detailed acceptance checks.

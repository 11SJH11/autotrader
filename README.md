# Trading Control Plane — Phase 0 Starter

A safe starting point for a **demo-only** multi-strategy trading platform.

**Current status:** offline simulated engine and read-only legacy inventory implemented. No real broker connection, credentials or order placement is implemented. Simulated fills do not establish broker execution.

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

## Implemented offline foundation

The initial simulator now lives in `control_plane/`, with durable SQLite order
claims, risk reservations and reconciliation. It uses synthetic SIM-XAU units;
there is no external broker connection or real execution path.

Run tests with Python 3.11+ and pytest:

```bash
python -m pytest -q
```

See `docs/handover.md` for tested commands, results and remaining safety gates.

## Phase 1 offline lifecycle extension

Use the project-local Python 3.11 environment and pinned test dependencies:

```bash
uv venv --python /usr/bin/python3.11 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

See [Phase 1 handover](docs/phase1_handover.md) for pending orders, position
management, campaign invalidation, ownership safeguards, diagnostics and test
results. The [cTrader read-only plan](docs/ctrader_read_only_plan.md) is a
proposal only; this repository still has no real broker execution adapter.

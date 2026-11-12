# Trading Control Plane — Phase 0 Starter

A safe starting point for a **demo-only** multi-strategy trading platform.

**Current status:** offline simulated engine and a separate demo-only cTrader read observer implemented. No broker authentication or connection has been verified. No real order placement is implemented. Simulated fills do not establish broker execution.

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

## Phase 2 cTrader observer

The separate `ctrader_readonly` package implements demo-only, view-scope
observation with offline HTTP/WebSocket mocks. It cannot act as an execution
adapter. Install the pinned transport in the project environment and run all tests:

```bash
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Read [Phase 2 handover](docs/phase2_handover.md) before local OAuth setup. New
private configuration and tokens must live outside Git; application approval
and real broker behavior remain unverified. No authentication was attempted.

## Phase 2.5 strategy correctness audit

The Hurricane/T02 audit is offline and does not alter the old bots or services.
See [Phase 2.5 handover](docs/phase25_handover.md),
[Hurricane timing](docs/hurricane_timing_audit.md), and
[T02 signal comparison](docs/t02_signal_parity.md).

```bash
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m audit_phase25.replay
.venv/bin/python -m pytest -q
```

Matching entry signals on retained research candles does not establish broker
execution parity. Broker DST conventions remain unverified outside the documented
sample; no cTrader authentication is attempted while approval is pending.

## Phase 2.6 observation signal adapters

`signal_adapters` adds corrected London/UTC Hurricane interpretation and explicit
T02 Reference / Long-only policies. Every emitted intent is observation-only;
the central engine rejects it. No broker/Telegram connection is started.

```bash
.venv/bin/python -m audit_phase26.compare
.venv/bin/python -m pytest -q
```

See [Phase 2.6 handover](docs/phase26_handover.md) for fixes, the shared offline
performance comparison, remaining evidence gaps and integration boundaries.

## Phase 2.7 observation and explicit demo discovery

The continuous replay worker and local read-only dashboard are described in
[Phase 2.7 handover](docs/phase27_handover.md). Real execution remains disabled.
For private, explicit account discovery use the
[local demo selection guide](docs/ctrader_discovery.md). The approved application's
actual verification status and current blocker are recorded in
[cTrader connection verification](docs/ctrader_connection_verification.md).

## Phase 2.8 broker specifications and reliability

The [Phase 2.8 handover](docs/phase28_handover.md) records exact read-only XAUUSD
volume evidence, GBP/margin checks and the current **NO-GO** for demo order tests.
The 24-hour-target monitor records incremental sanitized telemetry and can resume
its SQLite database after a controlled restart; it does not install a service.
See the handover for start/resume/summary commands and
[token longevity](docs/ctrader_token_longevity.md) for the manual reauthorization
limit and optional future renewal design. No trading scope is enabled.

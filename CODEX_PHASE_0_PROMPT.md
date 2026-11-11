# Codex — first assignment (safe read-only inventory + project foundation)

You are working on a Fedora 44 KDE laptop. The repo is `~/Projects/trading-control-plane/`. Read `README.md`, `docs/BLUEPRINT.md`, and `docs/SYNERGY_CONSTRAINTS.md` first.

## Goal
Design a broker-agnostic, demo-only central trading execution system for Hurricane, T02, future Ledger strategies, and a later Synergy screenshot reader. Do **not** place trades, connect trading credentials, change startup services, stop the old bots, or modify old projects in this task. The existing MT5/Wine workers are historical references and may still be running; inspect read-only.

## Work now

1. Review `docs/local_inventory.txt` and inspect relevant code under `~/Projects/` using read-only commands; do not print or copy secrets, `.env`, sessions, tokens, login files, credentials, private keys, or account numbers into reports. Identify legacy worker processes, their connection ownership, cross-service dependencies and precise failure boundaries. Do not mutate those projects.
2. Produce:
   - `docs/current_system_inventory.md`
   - `docs/architecture.md`
   - `docs/safety_invariants.md`
   - `docs/broker_capability_checklist.md`
   - `docs/implementation_plan.md`
3. Build the initial Python project skeleton, with typed contract models for `SourceEvent`, `TradeIntent`, `RiskDecision`, `OrderCommand`, `BrokerObservation`; a fake broker and deterministic order-state transitions; persistent idempotency keys with durable state; policy gates for demo-only account, stale data, max risk/exposure, no protective stop; and a recovery/reconciliation design for UNKNOWN submission outcomes. Use migrations if you choose PostgreSQL, and state what services Phase 1 requires. Avoid premature extra services.
4. Implement focused automated tests for duplicate signals, old replay after restart, broker ack timeout, unknown outcome, stale quotes, database restart, account-environment mismatch, spread guards and conflicting simultaneous strategies. No real-broker dependency for these tests.
5. Optionally build a **read-only** FastAPI health endpoint to show component status, last signal, last rejection reason and startup reconciliation state, with fake data only. Do not create buttons that claim to trade without a backend action.
6. Run tests locally and report exact commands, results and failures. Write a concise `docs/handover.md` with next actions and any decisions needing the user.

## Non-negotiables

- No `sudo`, `systemctl stop`, `systemctl disable`, modifying legacy services, firewall changes, `rm -rf`, or broker trading API calls without explicit approval.
- No live-account execution code path in V1; not just a UI label. Add hard guardrails in the execution adapter.
- No direct Telegram ingestion for **Synergy**. The capture method is a later adapter using an authorized virtual display, history of zones, and conditional cancellation rules.
- The account display and broker *truth* must be distinct from model-simulated state. Never equate successful local tests to successful broker executions.
- A process crash, lost broker acknowledgement, retry, or reconnected worker must never blindly submit a duplicate order.
- Preserve legacy code, logs and trade data; do not copy secrets to the new repo.

## Stop condition
When architecture, read-only inventory, contracts, fake broker, tests and handover are completed, stop. Do not advance to real API credentials or real demo execution without the next instruction.

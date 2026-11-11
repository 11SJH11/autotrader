# Phase 0 handover — 2026-11-11

Implemented immutable typed contracts, an offline fake broker, durable SQLite
command identities/queue and audit, transactional portfolio reservations,
pre-dispatch policy revalidation and conservative UNKNOWN reconciliation.
Architecture, inventory, invariants, capability checklist and next gates are
in the adjacent documentation. All new implementation is in this repository.
No old service/project was modified, no credential file accessed, and no broker
connection or trade made.

Validation commands and results:

| Command | Result |
|---|---|
| `python -m pytest -q` | Failed before collection: system Python has no pytest |
| `/tmp/ledger-test-venv/bin/python -m pytest -q` | 24 passed in 0.06s; final repeat: 24 passed in 0.05s |
| `python -m compileall -q control_plane tests` | Passed |

The existing external test interpreter was used without installing dependencies
or changing that environment. Tests cover persistent duplicates/replay, engine
restart, crash after claim, lost acknowledgement both before/after fake fill,
unresolved outcomes, startup reconciliation, stale/future quotes, stale signals,
spread/slippage, finite quantities/steps, protective stops, account/environment
mismatch, total/per-strategy exposure/risk, opposite strategies, simultaneous
reservations, database lock/reopen and unowned fake observations.

Read-only service inspection initially failed due to sandbox bus restrictions;
the approved status-only retry succeeded. Inventory records current observations,
including active Ledger bridges and failed demo monitor. Their account identities
were deliberately not read. No service repair was attempted.

Limits: SQLite schema v1 rather than PostgreSQL; no real broker, market data,
margin/P&L model, close/modify lifecycle, source adapters or dashboard. Fake fills
and risk arithmetic are synthetic. Restart tests reopen database/engine objects,
not a machine reboot or real database server. The fake broker's evidence is
in-memory; absent evidence locks unknown commands rather than retrying them.

Next: broaden offline lifecycle and owner-fencing fault tests, then sanitized
Hurricane/T02 parity fixtures. Decisions for the user at a later gate: retain
SQLite for local prototype or move to PostgreSQL, approve a read-only demo
capability spike, and establish legacy writer ownership before any future demo
execution. This assignment stops before those actions.

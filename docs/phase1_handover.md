# Phase 1 handover — 2026-11-11

## Delivered scope

Extended the Phase 0 SQLite command queue rather than replacing its safety
model. Its implementation now lives in `control_plane/foundation.py`;
`control_plane/engine.py` preserves the existing public imports, and
`control_plane/lifecycle.py` adds order/position lifecycles. All 24 original
Phase 0 tests remain unchanged and pass.

The simulator supports market and pending limit entries, cancellation (including
queued local entries), fills, SL/TP changes on pending orders and positions,
partial/full closes, and synthetic stop/target execution. Entry, broker order,
position, fill and management-command identities survive restart. Management
requires a caller-provided stable idempotency key. A duplicate returns its
original result, even if the duplicate asks for different parameters; callers
must generate a new key for a distinct action. Risk-increasing stop amendments
are rejected; MODIFY requires an explicit stop and replaces TP (None clears TP).

`FakeBroker(path)` has an independent SQLite journal. Use different files for
engine and broker state. Omitting the broker path preserves Phase 0's in-memory
test convenience, but loses broker evidence on process restart: the engine then
blocks rather than inventing broker truth. Persisted engine schema v1 is
extended additively to v2. Old records without broker evidence remain blocked;
there is no automatic adoption of unowned orders or reconstruction of missing
broker history.

Entry commands retain Phase 0's terminal FILLED label, which means the
**entry command** was acknowledged. For a limit entry it does not mean the order
filled: `lifecycle_orders.status` is the authoritative PENDING/FILLED/CANCELLED
order state. `positions.status` is OPEN/CLOSED. Management states are
SUBMITTING, UNKNOWN, APPLIED or TOO_LATE; cancellation after a fill is TOO_LATE
and leaves the position protected and reserved. Broker fills are stored
separately with stable IDs. This distinction matters for API consumers.

## Safety and ownership

Risk reservations remain in the original command transaction, including queued,
pending, submitting and unknown entries. Confirmed partial closes reduce
reservations, and confirmed cancellation/full closure releases them. Reconcile
checks order attribution, initial quantity, position identity and fill arithmetic;
missing historical fills or mismatched evidence block entries without releasing
reservations. Unknown actions and entries never automatically resend. A crash
after claim but before dispatch deliberately sacrifices availability.

A nonblocking Linux `flock` on the canonical engine database path plus `.owner`
serializes broker dispatch, management and reconciliation across processes. The
lock is held through broker I/O and result persistence. Process death releases
the OS lock; durable SUBMITTING becomes UNKNOWN during reconciliation. A second
owner receives EXECUTION_OWNER_BUSY. This is a single-host/local-filesystem
safeguard, not distributed fencing. All writers for this simulated account must
share one canonical database path; do not copy databases, use hard-link aliases,
delete/replace the lock file while processes run, or use network filesystems.
SQLite transactions separately serialize concurrent entry reservations.

Policy adds quantity minimum/maximum/step, per-strategy quantity/risk overrides,
global and per-order risk, aggregate exposure and synthetic margin constraints.
Pending worst-case risk uses full requested quantity times entry-to-stop distance;
there is no liquidity discount. Conflicting opposite entries remain blocked.
Margin is reserved quantity times configured margin-per-unit, not broker margin.
Only risk-reducing stop changes are accepted. Policy configuration is Python
`Policy`; no external configuration loader or operator write API was added.

Campaigns are generic strategy/instrument records, with multiple setup/leg
entries linked by campaign_id. ABOVE/BELOW invalidation levels persist first;
queued entries are cancelled locally and pending entries use idempotent broker
cancels. Repeat `evaluate_campaigns` after startup to finish an interrupted
invalidation. Fills win cancellation races; invalidation never flattens positions.
Callers must supply fresh quotes and schedule tick/reconcile/campaign evaluation;
there is no background worker or market-data feed. No Synergy recognition exists.

## Read-only diagnostics

```bash
.venv/bin/python -m control_plane.diagnostics --database ./state.sqlite --port 8767
# GET http://127.0.0.1:8767/diagnostics
```

The API binds only loopback, opens SQLite with mode=ro/query_only, and reads one
consistent transaction. It shows last persisted reconciliation status, unresolved
commands, strategy entry counts, pending orders, open positions and recent
rejection codes. LAST_RECONCILED is historical evidence, not process liveness or
a current broker heartbeat. Counts include historical entries. It excludes
source payloads, setup/source/intent IDs, account IDs, audit detail, credentials
and private account information. Strategy identifiers should be public display
IDs. POST/other write methods have no handler; unknown routes return 404 and
unavailable/missing databases return 503. No remote exposure/authentication,
service registration or running API process was installed.

## Environment and validation

Checked available interpreters first: default Python is 3.14.10; project minimum
is 3.11 (StrEnum). Selected installed Python **3.11.17**, rather than assuming
all dependencies support 3.14. `.python-version` records 3.11. Runtime dependencies
are standard-library only. Test dependencies and all transitives are pinned in
`requirements-dev.txt`; pytest optional dependency is pinned in pyproject.toml.

From this repository:

```bash
uv venv --python /usr/bin/python3.11 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q control_plane tests
git diff --check
```

Initial uv execution could not write the shared uv cache under sandbox rules;
the approved retry created the repository-local .venv and installed pytest 8.3.5,
iniconfig 2.3.1, packaging 26.3 and pluggy 1.7.0. No external test environment is
used. Reinstalling from the full pinned requirements required the same approved
cache-access retry and completed successfully. uv-created environments omit pip by default; an optional pip-version probe
reported that absence. Use `uv pip`, as above. Pytest temporary databases are
configured inside ignored `.pytest-tmp/` in this repository.

Final validation: **49 passed in 11.41s**; compileall and diff whitespace checks
passed. Coverage includes all Phase 0 cases plus persistent broker/engine
reopens, linked campaigns, pending amendments, queued/pending cancellation,
duplicate management and cross-process dispatch, cancellation/fill races,
timeouts before/after side effects, reconnect failure/recovery, missing broker
state/fills, locks before dispatch and during result persistence, four injected
crash boundaries, two actual child-process deaths, and process ownership
contention. A queued Phase 0 payload without Phase 1 fields was also reopened and dispatched
successfully. HTTP handler tests exercise GET/404 and rejection of POST without
binding a port. These are offline tests, not a broker or network soak test.

## Known limitations and next steps

SQLite remains the prototype store; PostgreSQL migration, migrations with
rollback, distributed ownership, backup/restore and machine/power-loss testing
remain open. Actual process deaths were tested, not host reboot or disk failure.
The fake broker uses full fills at entry/limit price, synthetic protective exits,
and closes at entry price. It does not model partial entry fills, gaps, latency,
liquidity, commissions, currency conversion, lot/tick valuation, daily P&L stops,
dynamic equity/margin or rate/campaign-count limits. These remain blockers for
any execution adapter. Protection is a simulator rule, not verified broker-native
protection. A fill can precede campaign cancellation; position flattening is a
separate deliberately unsupported campaign policy.

Next: review schema/state naming, add a scheduled simulation worker and bounded
history retention, then consider PostgreSQL and sanitized strategy replay
fixtures. The separate `docs/ctrader_read_only_plan.md` uses official API
references and proposes a later read-only capability spike. No authentication,
secrets, real broker adapter or trade occurred. Ledger, Hurricane, T02 and their
services were untouched. No real broker reliability is claimed from these tests.

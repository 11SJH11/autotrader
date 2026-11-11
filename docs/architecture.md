# Offline foundation

The repository in this session is `autotrader`; the starter's
`trading-control-plane` path is descriptive, not an implementation destination.

`control_plane/contracts.py` contains immutable typed dataclasses.
`engine.py` owns risk evaluation, a durable SQLite command queue, audit records,
a simulator and reconciliation. Runtime dependencies: Python 3.11+ and its
SQLite library. Tests additionally need pytest. No daemon, database server,
network connection, browser, Wine or broker library is needed.

SQLite is a deliberate initial substitute for the proposed PostgreSQL outbox.
Schema version 1 is initialized locally, with WAL and FULL synchronous commits.
Command insertion, aggregate risk reservation and audit are one IMMEDIATE
transaction. Separate connections serialize competing reservations. A queued
command is claimed and committed as SUBMITTING before the synthetic broker call.
Duplicate setup/account/strategy/leg identities are protected by a UNIQUE key;
changed revision or random intent ID cannot bypass it. Rejected events are
audited but can be reevaluated later. Accepted payloads are immutable.

State flow: QUEUED -> SUBMITTING -> FILLED or UNKNOWN; policy rejection at
revalidation yields REJECTED. Startup converts abandoned SUBMITTING to UNKNOWN.
UNKNOWN -> FILLED requires matching positive simulator evidence. No observation
leaves UNKNOWN locked indefinitely; there is no automatic resend or operator
reset API. Startup and unknown/unowned observations block new dispatches.
Queued commands revalidate account, expiry, quote and portfolio limits at send.

Filled positions conservatively retain exposure forever in this foundation.
There are no closes, partial fills, amendments, real account snapshots or margin
calculations. Risk is price distance times synthetic quantity, not monetary XAU
risk. No actual broker stop is created. The simulator retains observations in
memory; tests restart database/engine connections while keeping that independent
fake broker alive. Losing fake broker memory with uncertain submissions blocks
safely; it cannot establish real broker truth.

Phase 1 needs one local execution worker and durable local disk. Production
requires PostgreSQL migrations, a properly fenced execution owner, complete
broker snapshots/deals, account/symbol metadata, transactional lifecycle coverage
and backups. A dashboard/read API is deferred; it must remain a separate client.
No external services were started by this task.

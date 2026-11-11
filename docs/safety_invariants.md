# Safety invariants

* Only the concrete offline FakeBroker is accepted; it has no external I/O.
  It hard checks the fixed SIM-DEMO identity and SIMULATION environment before
  every submission. There is no real or live adapter or endpoint configuration.
* Every accepted entry reserves account/strategy quantity and aggregate risk
  transactionally, including queued and uncertain commands.
* Stable setup/leg identity persists across restarts. Revision changes do not
  grant another entry. Source adapters must define stable identities later.
* A committed dispatch claim precedes the call. Only QUEUED can be dispatched.
  A crash in that gap sacrifices availability rather than risking duplicate send.
* UNKNOWN never returns to QUEUED. Missing broker evidence is not rejection.
* Startup reconciliation must succeed. Unowned fake observations block entries.
* Current quotes, signal expiry, spread, slippage, supported symbol/type, positive
  finite Decimal values, quantity step and protective-stop direction are checked.
* SQLite unavailability prevents dispatch; errors propagate rather than claiming
  success. Audit and command changes commit together.
* Simulation acceptance and fill labels are not evidence of broker execution.

Phase 1 extends these invariants:

* Broker mutations and reconciliation require the local OS execution-owner lock,
  held through side effects and durable results. Durable claims survive lock
  release/process death; unknown outcomes never resend.
* Pending entries reserve their full synthetic stop-distance risk and margin.
  Only confirmed cancellation/close evidence releases reservations. Missing fill
  history, unowned evidence or reconciliation failure blocks entries.
* Position and pending-order stop changes must reduce or preserve risk. Partial
  closes cannot exceed remaining quantity; management keys persist across restart.
* Campaign invalidation persists before cancellation. Fills win races and do not
  cause automatic flattening. Invalid campaigns cannot dispatch queued entries.
* Diagnostic HTTP access is loopback/read-only with explicit safe response fields.

Still deferred: real margin, currency/tick/lot conversion, native protection
verification, daily P&L stops, rate/campaign-count limits, dynamic market-risk
repricing, authenticated operator controls and real reconciliation proof. These
remain blockers for a real demo adapter. Synthetic fills, protective exits and
local process-death tests do not prove broker reliability. See
`phase1_handover.md` for exact ownership assumptions and remaining limitations.
Synergy remains screenshot-only; no ingestion or recognition is implemented.

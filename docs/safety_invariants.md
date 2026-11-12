# Safety invariants

* Only the concrete offline FakeBroker is accepted by the execution engine; it has no external I/O.
  It hard checks the fixed SIM-DEMO identity and SIMULATION environment before
  every submission. There is no real broker execution adapter or live endpoint
  configuration. The separate Phase 2 observer cannot submit execution commands.
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

Phase 2 observer boundaries:

* The observer never imports or plugs into the execution engine. Its fixed demo
  WebSocket endpoint has verified TLS, disabled redirects/proxy inheritance and
  an outbound read-only schema allowlist checked at the transport boundary.
* OAuth requests accounts scope only. An explicit remote SCOPE_VIEW plus the
  pinned demo account/environment/broker identity is required before account
  authorization or token persistence. Trading/unknown scope is rejected.
* Account-owner accessRights are separate from OAuth token permissions; an
  account's FULL_ACCESS does not grant this observer trading permission.
* Private configuration and verified access tokens stay outside Git in owner-only
  files/directories. Callback/status/error output cannot expose them. No existing
  credentials are searched or reused. No raw broker response is persisted.
* Authentication requires a confirmed approved new application and local config.
  Callback state must match; unknown approval or missing state never permits a
  relaxed flow. Expired/revoked tokens require deliberate reauthorization.
* Missing/incomplete history, unexpected responses, stale data and disconnects
  prevent healthy status. Reconnect always reauthorizes and resnapshots. The
  non-authoritative margin estimate cannot authorize trading.

See `phase2_handover.md` for exact offline results and unverified broker behavior.

Phase 2.6 adapter boundaries:

* Signal adapters consume explicit source-time conventions and normalized UTC
  quotes/candles. They never apply a broker offset to Telegram/normalized data.
  Ambiguous/nonexistent London wall times reject unless explicitly resolved.
* A broker raw epoch requires separately bounded offset evidence. One historical
  UK+2 sample does not authorize a permanent offset or historical candle mapping.
* Named T02 Reference and Long-only policies share NY session and weekday rules;
  the first inversion consumes the session even if direction/ratio rejects.
  Signal detection does not allocate position size or account risk.
* All adapter proposals have observation_only=True. The central engine rejects
  that flag at enqueue and dispatch revalidation. No adapter broker client or
  execution-enable option exists. Existing simulator defaults remain unchanged.
* Durable adapter duplicate/edit records are observation decisions, not an order
  outbox. Edit tombstones never submit cancellation or position-management calls.
* Performance numbers are offline zero-cost OHLC research results. They do not
  verify original trader intent, real costs, margin, fills or broker protection.

Phase 2.7 discovery and observation boundaries:

* An unpinned account is permitted only by the explicit discovery command.
  Discovery reads accounts-scope grant metadata from the demo endpoint; it never
  account-authorizes or saves a token before explicit local confirmation.
* Demo candidates require isLive=false and exact broker identity. No candidate is
  preselected. Typed displayed login, browser capability, CSRF, Origin/Host and
  expiry checks precede revalidation and private pinning of the API account ID.
* Offline worker paper state is isolated from central execution and broker
  clients. Every adapter intent remains observation-only. Worker market time is
  replay time, never presented as a current broker quote.

Phase 2.8 broker verification boundaries:

* Raw volume fields are never replaced with plausible defaults or generic broker
  marketing. Matching-account independent evidence is required; the read-only
  reader/monitor explicitly report execution_enabled=False and NO_GO.
* Hypothetical expected-margin requests and indicative GBP quote conversions do
  not establish order admissibility, actual fill/protection behavior or free
  margin. Detailed account financial values remain excluded from reports/status.
* Reliability duration is actual persisted active time, with restart/suspend gaps
  uncredited. A target window is not a completed observation; sampled health is
  distinct from continuous availability and execution suitability.
* Refresh tokens remain discarded. Expiry/revocation requires deliberate
  accounts-only reauthorization; neither monitor nor reconnect broadens scope,
  changes account pins or opens OAuth automatically.

Phase 3.1 attended DEMO test boundaries:

* The view-only observer, its scope/allowlist and simulator production factory
  remain unchanged. A separate manual transport never broadens their authority.
* Trading OAuth needs separate explicit owner consent and a separate private
  token store. No live/unknown-environment grant is accepted. Exact pinned Razor
  demo identity, broker, USD and hedged mode are revalidated remotely.
* The sole first-test entry needs confirmed actual/intended capital, matching
  API/UI native specifications, reviewed stop/cost/risk/margin bounds, fresh data,
  an empty book and controlling-terminal approval. All strategy controls stay
  stopped and global entry suspension remains enabled. Only this separately
  consented manual capability operation can cross that gate.
* A canonical private journal has one durable entry slot and an exclusive local
  owner. Send consumption commits before network bytes. Crashes/unknown outcomes
  never authorize blind resubmission, a new entry slot or inference from absence.
* Native position protection and positive entry/exit history must be verified.
  Missing protection is a failed test and invokes only the expressly approved
  owned-position emergency response; unresolved exposure requires critical
  operator attention. The first test normally ends flat and enables no strategy.
* Expiry, outages and incomplete evidence can prevent automated recovery. This
  is an attended single-host test harness, not unattended broker reliability.
  See `phase31_handover.md` and `phase3_first_trade.md`; no trading consent was
  obtained and no mutation was performed during the Phase 3.1 assignment.

# Trading Control Platform — V1 Blueprint

**Prepared:** 11 November 2026  
**Purpose:** Build one dependable, demo-only trading control plane for Hurricane, T02, Synergy and future algorithms.  
**Status:** Design and build brief; no existing service has been modified, and no broker connection or execution has been verified by this document.

## 1. Decisions and constraints

1. Build **fresh infrastructure** rather than patch separate, conflicting MT5/Wine workers. Inventory and safely disable the legacy execution services before activating any new execution path. Preserve old projects/read-only data as references.
2. Treat the proposed **Pepperstone + cTrader Open API DEMO** route as the leading option, **not a confirmed integration**. First verify account eligibility, account type/hedging, XAUUSD symbol, lot/volume mapping, partial closes, order types, stop limits, margin, commissions, demo lifespan, API registration and fills. Choose another broker API only if the proof-of-capability fails.
3. No live-account authorization, configuration or endpoints in V1. Broker account IDs must be checked against a demo-only allowlist server-side. No UI toggle should enable live trading.
4. The control centre is a **client**, not the runtime. Strategies, signal ingestion, risk checks and reconciliation must keep running with the browser closed, display asleep, and after a controlled service restart.
5. Each strategy emits normalized **trade intents**; only one broker execution service may place orders. Strategy processes never hold broker trading credentials.
6. Order lifecycle changes require durable journaling, idempotency, reconciliation and deterministic policy for uncertain outcomes: **never automatically resubmit an order whose broker outcome is unknown**.
7. Prefer keeping positions protected with broker-native stop-loss orders when supported and verified; loss of the local process must not silently remove protection.
8. Trading account exposure must be coordinated across strategies, with per-bot and whole-account caps. Demonstrate—not merely unit-test—broker-confirmed opens, modifies, partial closes, closes, restart recovery and duplicate suppression.

## 2. Architecture

```text
Telegram user/API events ─┐
Hurricane adapter         │
Other group adapters      ├──► Event Store ─► Intent Normalizer ─► Risk & Policy ─► Order Queue
Synergy screenshot adapter│                                                │
Virtual display capture   ┤                                                ▼
T02 market-data engine ──┤                                        SINGLE EXECUTION OWNER
VWAP/future strategies ──┘                                                │
                                                                          ▼
                                                                    Broker Open API
                                                                          │
                                                              Broker fills / positions
                                                                          │
                                                                          ▼
                                                                 Reconciliation loop
                                                                          │
                                                                          ▼
                                                            Postgres audit/event store
                                                                          │
                                                                          ▼
                                                            Control Centre dashboard
```

**Suggested stack:** Python 3.11+; FastAPI for dashboard/control APIs; Pydantic for contracts; PostgreSQL for durable records and transactional outbox; explicit worker processes with bounded retries; React + TypeScript dashboard (or reuse visual components from the current Ops Centre); Docker Compose or systemd services with health probes; HTTPS and authenticated remote access. Avoid distributed infrastructure such as Kubernetes or Redis queues until actual scale requires it.

**Code directories (suggested):**

```text
trading-control-plane/
  apps/api/                  # read/control endpoints, auth, SSE/WebSocket
  apps/dashboard/            # React/TS monitoring interface
  workers/telegram_ingest/
  workers/strategy_engine/
  workers/vision_ingest/     # optional, later
  workers/execution/
  workers/reconciliation/
  core/contracts/
  core/risk/
  core/broker/
  core/eventstore/
  strategies/hurricane/
  strategies/t02/
  strategies/synergy/
  tests/unit/
  tests/integration/
  tests/fault_injection/
  tests/e2e_demo/
  deploy/
  docs/
```

## 3. Canonical entities and messages

**SourceEvent**: `source`, `source_account/channel`, `message_id`, `revision`, `event_type` (new/edit/delete/media/price-update), `event_timestamp_utc`, `received_timestamp_utc`, `raw_payload_hash`, `raw_payload_ref`, `ingest_latency_ms`.

**Setup**: `setup_id`, `source`, `instrument`, `strategy`, `created_at`, `origin_zone_id`, `parent_setup_id`, `entry_zone`, `side`, `invalidation`, `take_profit_targets`, `status` (UNCONFIRMED, WATCHING, ACTIVE, FILLED, PARTIAL, CLOSED_TP2, STOPPED, INVALIDATED, EXPIRED), `supersedes`, `evidence_refs`.

**TradeIntent**: `intent_id`, `source_event_id`, `strategy_id`, `setup_id`, `campaign_id`, `account_scope`, `instrument`, `side`, `order_type` (MARKET/LIMIT/STOP), `entry_price`, `quantity_or_sizing_policy`, `stop_loss`, `take_profit_plan`, `valid_until`, `created_at_utc`, `strategy_context`, `confidence_or_parser_status`.

**RiskDecision**: `intent_id`, `accepted`, `reason_code`, `reason_detail`, `quote_snapshot`, `equity_snapshot`, `open_exposure_snapshot`, `policy_version`, `evaluated_at_utc`.

**OrderCommand**: immutable `command_id`, `idempotency_key`, `intent_id`, `broker_account_id`, `instrument_id`, `normalized_broker_volume`, `broker_request`, `command_state`, `last_reconciliation_at`.

**BrokerObservation**: `broker_order_id`, `broker_position_id`, `deal_id`, `broker_status`, `executed_price`, `executed_volume`, `timestamp`, `broker_raw_ref`.

Use UTC internally; record broker timezone and local strategy timezone explicitly (e.g. America/New_York for T02), including daylight-saving transitions. Record strategy and policy versions, as well as the market-bar timestamp used to decide an entry.

## 4. Non-negotiable risk controls

- **Demo hard lock:** validate the actual broker environment and account ID before *every* order. Reject any unknown account or endpoint.
- **One trade writer:** all order creation, amendment and close commands pass through the broker execution owner.
- **Per-order idempotency:** stable source/setup/leg identity, unique database constraints and transactional handoff; never rely on memory-only deduplication.
- **Uncertain-order lock:** after timeout/disconnect, mark UNKNOWN, reconcile broker orders/deals/positions first, and do not resend until conclusively safe.
- **Risk limits:** configurable overall exposure, per-strategy volume/risk, max concurrent campaigns, daily realized/unrealized loss stops, order-rate limits, available margin, abnormal spread/slippage and stale-market-data gates. At V1 defaults, prefer restrictive values and require review of symbol-specific limits.
- **Protective orders:** validate side, stop distance, tick size, order type, broker-native protective SL, volume step, conversion from lots to units, margin and execution constraints. Reject rather than silently rounding up risk.
- **Circuit breakers:** block new entries when broker data is stale, reconciliation fails, signal ingestion goes stale, credential/auth fails, or observed broker position deviates from internal state. Existing-position management follows a separate, explicitly defined policy.
- **Controls:** RUNNING, PAUSED_NEW_ENTRIES, DRAINING, FAULTED; separate 'pause new entries' from 'flatten demo positions now'. Emergency close requires deliberate confirmation and is audited.
- **No overlapping legacy writers:** old MT5/Hurricane/T02 services cannot execute simultaneously against the same account while the new service is armed.
- **Protection against restarts:** startup always loads broker truth before accepting new signals and applies expiry checks to backlogged events.
- **Security:** encrypted credentials/Telegram sessions; authenticated dashboard + second factor for sensitive controls, restricted network access, secret redaction in logs, no credentials in source control.
- **Auditability:** append-only event log for every input, classification, rejection, retry, order action, fill, cost, failure, operator change and restart.

## 5. Source-specific behaviour

### Hurricane

Consume Telegram text/media via a permitted client account with channel access. Map original message ID and revisions to a single setup. Handle explicit entry instructions, edits, cancels, target hit announcements, and deduplication. Preserve most recent discussed demo policy as an **initial configuration to verify**: total 0.03 lots across three 0.01-lot TP legs, broker-validated, with breakeven movement after TP1. Do not hard-code this before confirming contract/volume compatibility.

### T02

Pure strategy engine running from validated market-data bars, completely separate from broker execution. Capture complete candle/15-minute boundary, Asia session based on New York timezone and daylight savings, long-only constraints, fixed 0.05 lots as a configurable **demo baseline**, and the historical one-trade/aggregate exposure policy. Record exactly why a signal was or was not generated; use strict parity tests against frozen backtest fixtures. Never use future candle information.

### Synergy

**Mandatory source:** an authorized virtual-display screen/desktop watcher; direct Telegram/API ingestion is not available for this group. Detect changed regions rather than interpreting every identical screenshot as a new signal. Retain timestamped source evidence and OCR/vision confidence; detect frozen windows and login failures. A capture-compatible graphical session can run without a lit physical display. See `docs/SYNERGY_CONSTRAINTS.md`.

Maintain a persistent virtual order registry (not automatic broker-pending orders for every historical zone). Validate zone history, eligibility, account risk, market prices and cancellation races before promoting any entry layer to a broker order.

**Interpretation:** separate NEW_ZONE, WATCH_EXISTING_ZONE, REPOST, ACTIVATE_ENTRY, LAYER_ENTRY, MODIFY_SL, TP1, TP2, STOPPED, INVALIDATE, COMMENTARY. Maintain source zone creation and first-posted dates, untouched/touched flags, and per-campaign reentry state. A repost or historical-zone summary must never be treated as a new executable market order. A setup loses reentry eligibility on TP2 or SL. Low-confidence media and ambiguous actions are 'REVIEW_REQUIRED', not auto-executed.

Observe and compare many real messages with user-labeled interpretation before demo trading. Capture permission and provider/channel terms; do not work around technical access restrictions.

### Future strategies

Add adapters for approved backtested logic (e.g., VWAP/EMA) with versioned entry/exit contracts, explicit price/timeframe feed, slippage/commission assumptions, data lineage and unseen/out-of-sample validation. Prefer shadow mode before live execution even on a demo account.

## 6. Dashboard V1

**Overview:** account DEMO badge, connected broker environment/account, broker heartbeat latency, Telegram ingestion status, strategy engine liveness, market-data freshness, current margin/equity/balance, aggregate exposure and daily P&L. Dashboard outage must not stop workers.

**Strategy cards:** Hurricane, T02, Synergy, future bots; enabled/paused/faulted; state explanation; last signal seen; last signal accepted; last execution; filled open trades; rejected signals with reason; cumulative P&L, win rate, commission/slippage, and drawdown by strategy.

**Trades:** current broker positions and pending orders, linked to originating strategy/setup, live SL/TP, R-risk at entry, execution costs, fill IDs and history. Clearly distinguish intended orders from broker-confirmed orders.

**Timeline:** ingestion -> classification -> risk decision -> order submission -> broker acknowledgement -> fill -> partial/exit. Search by source message/setup ID; show raw source/media alongside interpretation.

**Control operations:** pause/resume new entries by strategy and globally; optionally drain; deliberate emergency close with confirmation; retry ingestion only (not unknown broker orders); audited account/symbol risk policy editor; incident history.

**Notifications:** severity tiers (INFO, WARNING, ACTION_REQUIRED, CRITICAL); compact summary or latest-three deduplicated incidents, without message spam. CRITICAL alerts for broker disconnection, active-position protection failure, execution/reconciliation uncertainty and exceeding risk limits.

**Advanced later:** backtest vs forward-test overlay, replay of events, strategy parameter experiment tracker, Synergy label-correction UI, performance attribution, portfolio-wide simultaneous trade stress testing.

## 7. Delivery plan and measurable acceptance gates

### Phase 0 — Safe inventory and broker spike

- Inventory legacy Hurricane, T02, shared MT5, Ops Centre and all startup services. Document ownership, live services, ports, secrets, data and log retention.
- Disable any legacy **order writers** before enabling new execution; do not delete code, account data or evidence. Do not change an account or stop a service until its identity and purpose are verified.
- Validate broker API on a DEMO account: connect, heartbeat, discover account/symbol, normalize XAUUSD, list orders/positions/deals, check sizing/margins and partial-close/stop support. Record proof and anomalies.
- Gate: no broker execution until account-demo verification is machine-enforced.

### Phase 1 — Reliable shared infrastructure, no actual orders

- PostgreSQL schema/migrations, structured event log, canonical contract, idempotent commands, execution state machine, broker simulator/fake, reconciler, policy controls and read-only API.
- Basic dashboard: services, signals, rejection reasons, account snapshot and incident timeline.
- Fault injection: duplicate inputs, edits, dropped connection, stale quotes, daemon restart, database interruption, unknown broker acknowledgement, timezone rollover.
- Gate: tests prove at-most-once order dispatch policy and **no ambiguous resends**; broker truth wins on reconcile; no silent failures.

### Phase 2 — Broker-confirmed end-to-end DEMO tests

- Authorize a single controlled minimum-size open/modify/close test in demo **only after** broker identity, protective SL, margin and sizing checks pass.
- Reconcile every broker order/deal/position; restart worker with an open position and prove proper adoption and protection; verify split partial closes.
- Gate: artifact with command IDs, broker order/position IDs, fills, timestamps, screenshots/exports where available, and passing scenario tests.

### Phase 3 — Hurricane adapter

- Replay known historical Telegram fixture messages, edits, TP notices and duplicates.
- Observe real Telegram events with execution suppressed; verify source-to-intent transformations.
- Enable demo execution at verified configured sizes only when Phase 2 and fixture parity pass.

### Phase 4 — T02 adapter

- Freeze backtest signal fixtures; verify session/timezone/candle-boundary parity; demonstrate why each live candle is accepted or skipped.
- Forward-test in shadow mode, then enable demo entries when validated. Review broker fill and cost differences with replay/backtest.

### Phase 5 — Synergy and additional adapters

- Prototype Synergy virtual-display screenshot reader (direct Telegram/API ingestion is unavailable for this group).
- Label many real campaigns, including old untouched zones, reposts, TP2/SL cancellations and reentry layers. Evaluate misses and false-positive proposed trades.
- Use manual approval or shadow mode until high-confidence interpretation is demonstrated; only then consider narrow demo automation.

### Phase 6 — Deployment hardening

- Move core services to an appropriate always-on host/VPS after local integration proves correct. Ensure monitoring, storage backups, clock sync, restarts, credential rotation, update rollback, retained database/audit history and notification delivery.
- Gate: restart/reboot tests, temporary broker outage tests, all-apps-closed dashboard test, pending/open-position recovery, and continuous observation with no unexplained order or missing incident.

## 8. Crucial negative-path acceptance tests

| Situation | Required result |
|---|---|
| Telegram emits same message twice | No duplicate trade |
| Old message replayed after restart | Ignored or classified as stale, not executed |
| Source edits or cancels setup | Correct revision/state update and policy-defined pending order management |
| Broker accepts order but acknowledgement is lost | UNKNOWN -> reconcile; never blind resend |
| Broker connected but price quote stale | New entries blocked with explicit explanation |
| API reconnects with existing open positions | Positions adopted and mapped before accepting new entries |
| T02 15m candle not closed | No look-ahead execution |
| Synergy reposts month-old untouched zone | Update watch-state; no unconditional new market order |
| Synergy TP2 or SL reached | Campaign reentry disabled |
| Two strategies fire on same symbol | Aggregate position/exposure limit enforced |
| Dashboard browser, laptop display or UI crashes | Headless workers continue if host/network healthy |
| Server/VPS reboots | Restores policy and broker truth, blocks until reconcile |
| Requested account is live or unknown | Hard reject, no configuration-based override in V1 |
| Live volume below symbol minimum | Reject; never silently increase quantity |
| Risk engine or source parser unavailable | No new entries; protective exit-management policy remains defined |

## 9. First Codex assignment (copy/paste)

> You are building V1 of an **always-on, demo-only trading automation control plane**. Follow `trading_control_platform_v1_blueprint.md` in this repository. Start with an inventory of the existing Hurricane, T02, shared MT5/Wine and Ops Control Centre code, and identify the root reliability/ownership failures. Do not place orders, touch a live account, change broker credentials, disable services or alter existing bots as part of the first task. Produce `docs/architecture.md`, `docs/current_system_inventory.md`, `docs/broker_capability_checklist.md`, `docs/safety_invariants.md`, an initial repo structure, database migrations, typed SourceEvent/TradeIntent/RiskDecision/OrderCommand/BrokerObservation models, fake broker, idempotent durable intent queue, risk/policy gates, deterministic reconciliation state machine, and pytest tests for restarts/duplicates/timeouts/stale quotes. Build an initial read-only dashboard with health, last-event, reason-for-no-trade and incident timeline. Make the design broker-agnostic with a proposed cTrader Open API adapter only after capability validation. Define concrete pass/fail acceptance criteria. Every stage must work with synthetic data before real connections. Return changed files, test commands/results, gaps and steps requiring explicit user authorization. Do not claim broker/demo execution until verified by broker-side deals/positions.

## 10. Documentation links checked November 2026

- cTrader Open API introduction: https://help.ctrader.com/open-api/
- cTrader API app registration and OAuth: https://help.ctrader.com/open-api/api-application/ and https://help.ctrader.com/open-api/account-authentication/
- cTrader API connection/heartbeat: https://help.ctrader.com/open-api/connection/
- cTrader Open API FAQ: https://help.ctrader.com/open-api/faq/
- Pepperstone demo availability: https://pepperstone.com/en-eu/ways-to-trade/demo-accounts/
- Telegram API user-client registration: https://core.telegram.org/api/obtaining_api_id
- Telethon event handling: https://docs.telethon.dev/en/stable/quick-references/events-reference.html

**Reminder:** A passing unit-test suite does not establish real order routing, risk enforcement, or always-on reliability. All broker-dependent statements must be proven with the selected DEMO account before automation is enabled.

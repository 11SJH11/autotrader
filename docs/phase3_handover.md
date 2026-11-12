# Phase 3 handover — Razor CFD preparation

12 November 2026. **NO-GO for the first real Razor demo order test.**

Razor read-only integration succeeded. Execution preparation is implemented and
tested offline, but the production mutation transport, trading OAuth integration
and bounded first-trade/emergency-remediation harness remain unfinished. Passing
fake-broker tests does not establish broker execution reliability. No trading
scope was requested, no trading token was read or stored, and no real broker
order was placed, amended, cancelled or closed.

Feature/test development stopped at the owner's request. The final requested
full regression run passed **334 tests in 44.26 seconds** using the project-local
CPython 3.11 environment. No additional dependencies were needed.

## Read-only Razor results

A separate owner-only configuration and token store were created outside Git:
`/home/SJH/.config/autotrader/ctrader/razor/config.json` and its configured token
file. They reuse the approved application credentials privately, not the old
account's token or pin. Accounts-only OAuth discovery required explicit local
selection/typed-login confirmation. No account was preselected. The returned
Open API ID is pinned privately; it and the displayed login are omitted here.

Verified against the actual demo API:

- DEMO, remote view-only authorization, exact configured broker title
  `Pepperstone - Europe`, detailed broker name `pepperstoneuk`.
- Selected account ID matches its private pin; currency USD and mode HEDGED.
  “Razor CFD” is the owner's selected account classification, supported by its
  commission/specification data; the API mode field itself says HEDGED.
- Separate old/new token paths and account pins; both saved scopes are accounts.
- XAUUSD symbol identity and full symbol response, USD quote asset, native volume
  fields, price precision, hypothetical margin estimates and a quote stream.
- Four samples from 18:56:11 to 18:56:41 UTC were HEALTHY, with fresh quotes,
  heartbeats and snapshots. Quote ages were approximately 0.266–0.742 seconds.
  Reconciliation showed zero pending orders and positions; the requested history
  window returned zero orders/deals and passed the reader's completeness checks.
  Nonempty broker execution/history handling remains unproven.

**Capital discrepancy:** `phase3_account_checks.json` records
`starting_balance_matches_owner_statement: false`. The API balance did not match
the stated $1,000 starting amount at that check. Its actual value was not printed
or exported. An earlier progress update incorrectly described this as a match;
this handover corrects it. Confirm the selected account's current capital/funding
privately before setting risk limits or authorizing any trade. No explanation
for the discrepancy is assumed and no balance-changing action was attempted.

Evidence: `phase3_account_checks.json`, `phase3_razor_probe.json` and
`phase3_razor_observer.json`. These contain sanitized facts, not credentials,
account identifiers or complete broker responses.

### XAUUSD volume and risk facts

| Field | Raw protocol hundredth-units | Units | Lots |
|---|---:|---:|---:|
| Minimum | 100 | 1 | 0.01 |
| Maximum | 500000 | 5000 | 50 |
| Step | 100 | 1 | 0.01 |
| Lot size | 10000 | 100 | 1 |

The minimum/maximum and lot-size arithmetic agree with the owner-provided Razor
UI figures. The API step is 0.01 lot; independent confirmation of that UI step is
still pending. The owner reports 100 oz per lot. The probe did not recognize the
API's measurement-unit string, so ounces are not independently inferred from
that field. The old GBP profile's huge min/max anomaly is absent on this account;
its historical evidence was not overwritten.

Price digits=2, pip position=1. Stop/target distances were present as 0/0 POINTS.
`isLimitedRisk=false` and `guaranteedStopLoss=false` were returned. Zero metadata
distances do not prove that a proposed protective price will be accepted, and a
standard stop does not guarantee a maximum realized loss.

At the 19:12:35 UTC probe, XAUUSD was 3879.55/3879.64, approximately 0.222 seconds
old when read. Hypothetical expected margins in the USD account were:

| Lots | Units | Buy estimate | Sell estimate |
|---|---:|---:|---:|
| 0.01 | 1 | $193.98 | $193.98 |
| 0.1 | 10 | $1,939.83 | $1,939.78 |
| 1 | 100 | $19,398.30 | $19,397.80 |

These are historical estimates, not order admissibility, available margin or
current pricing. They support approximately 1:20 gold leverage at the sampled
price. The dynamic profile returned raw leverage 2000; its raw scale is preserved
rather than interpreted as literal 1:2000. USD account valuation requires no GBP
conversion. API quantity conversion is units=volume/100, lots=volume/lotSize;
no MT5 or synthetic margin-per-unit assumption is used in the native coordinator.

Commission metadata: `preciseTradingCommissionRate=350000000`,
`commissionType=USD_PER_LOT`, which normalizes to $3.50 per lot under the
[official model scaling](https://help.ctrader.com/open-api/model-messages/#protooasymbol).
[Pepperstone's cTrader pricing](https://pepperstone.com/en-gb/trading/costs-and-fees)
states $7 round trip for XAUUSD, consistent with $3.50 per side. Actual charged
deals, minimum-volume rounding and funding costs were not observed. At 0.01 lot,
nominal per-side commission is $0.035; do not silently assume a $0.07 final total
without verifying rounding. Offline risk preparation conservatively rounds USD
cents per side and requires an explicit extra fee buffer; no real risk policy
was installed from those test assumptions.

## Implemented versus simulated versus unfinished

| Area | Implemented now | Real-broker status |
|---|---|---|
| Razor discovery/identity/quotes | Separate private view-only profile, explicit selection, currency/mode pins, read-only observer | Verified on selected demo account, bounded observations above |
| Market/limit entry | Narrow official request codecs, native units, price/volume checks, relative market SL/TP and absolute limit SL/TP | Offline only; no order submitted |
| Amend/cancel/close | Pending amendment/cancellation, protection amendment, partial/full closure codecs and durable actions | Offline only; risk-increasing amendments and partially filled pending amendments reject |
| Lifecycle/reconciliation | Native orders, positions, deals, rejected-deal records, partial fills, balanced historical closure, immutable fill checks | Offline projections; real nonempty snapshots/deal mapping unproven |
| Unknown outcomes | Durable claim before side effect; restart SUBMITTING→UNKNOWN; no automatic resend; entry recovery requires positive matching evidence | Offline and process-death tests; uncertain management actions remain blocked without a correlated result |
| Risk/ownership/audit | Transactional native-unit/USD reservations, per-strategy/global caps, pending worst-case exposure, fresh account/symbol-bound margin evidence, local account-scoped ownership lock, domain audit | Offline; real free-margin authority, fee/fill behavior and production ownership binding unfinished |
| Linked campaigns | Linked IDs, configured-price invalidation, idempotent pending cancellation, no automatic flattening of filled legs | Offline only |
| Strategy integration | Native sizing previews retain IDs/times/version and observation-only flag; both engines reject observation proposals for execution | No automatic execution promotion or production routing |
| Hurricane management | Structured CANCEL/edit tombstones; pure registered-leg TP1→BE proposals require complete, profitable, TP-classified exit evidence | Observation only; cTrader exit-reason mapping and management worker unfinished |
| Operations | Persistent per-bot observation start/stop/pause/resume, lots-per-leg preview settings, global submission suspension, audited local controls/dashboard | Logical replay controls, not OS/service supervision or live-trade controls |
| Production execution | Hard gate in production factory; network transports cannot be supplied to OfflineAdapter | **Not implemented/enabled**; cannot be switched on with a config flag |
| Trading OAuth | Separate preparation manifest/store rules and unconditional approval gate | **Inert preparation/design only**, not a working authorized trading flow |

The new central extension is `control_plane/native.py`, using the existing
TradeIntent/RiskDecision contracts and the shared durable SQLite primitive in
`control_plane/storage.py`. The Phase 0/1 FakeBroker engine and its type/environment
fences remain. Its only storage refactor uses the same WAL/FULL connection setup.
The new coordinator does not pass native instruments through synthetic Policy
quantity/margin calculations.

`ctrader_execution/schema.py` implements the documented mutation subset;
`ctrader_execution/offline.py` accepts only its exact in-memory OfflinePeer.
The production factory always raises. The existing read-only WebSocket transport
still rejects every mutation type. `ctrader_execution/authorization.py` does not
generate a trading authorization request or load trading credentials. Consequently
this is reviewable execution **preparation**, not a deployed broker adapter.

Market protection uses documented fill-relative distances in 1/100000 price
units: absolute SL/TP are unsupported on NewOrder MARKET. That can differ from a
strategy's original structural price after slippage. Native partial-fill/VWAP
protection semantics and source-exact structural-stop handling need real broker
verification before automated strategy execution.

Risk is gross across hedged directions, not netted away. Margin evidence must
match account, symbol, units, USD currency and freshness, with explicit authority;
the reader's non-authoritative free-margin estimate cannot authorize entries.
Margin is conservatively retained until exposure is closed. Source/cost/risk
limits in offline fixtures are invented and are not the owner's approved policy.

## Strategies and worker preparation

- Hurricane keeps aware UTC Telegram times and Europe/London conversion for
  explicit local times. Existing age, quote and market/limit eligibility checks
  remain. EDIT/CANCEL never create entries and retain durable tombstones.
- Reference and Long-only T02 policies and New York weekday/session rules are
  unchanged. The full suite retains the run-214 comparison: Reference matches
  all 64 saved development signals with zero missing/extra; Long-only matches
  the 30 BUY signals, not all 64. This is not unseen validation or broker parity.
- The legacy `staged3_be.py` TP1 confirmation/BE functions were inspected read-only.
  The new management proposals do not infer TP1 from price touches or Telegram
  announcements, do not loosen stops and preserve targets. The cTrader-specific
  proof of an actual TP exit is still missing.
- `signal_adapters/native_preview.py` converts configured lots using reviewed
  native specifications but preserves observation_only=True. It cannot enable
  execution. The native spec factory rejects stale/mismatched API/UI evidence.
- The replay worker can use an explicit controls database. Missing controls fail
  closed; restart cannot silently omit a previously bound control store. Existing
  fictional paper sizing remains separate from the native sizing preview.
- Native and original engine observation fences remain. A production intent/
  management router, authorized promotion boundary and live M1/M15/source feed
  are unfinished. No private Telegram session was accessed.

All three real observation controls remain STOPPED and global entry suspension
is ON (`phase3_control_state.json`). No strategy worker was activated.

## Operational state and commands

The Razor read-only observer was launched separately on loopback port 18771:

```bash
.venv/bin/python -u -m ctrader_readonly observe \
  --config /home/SJH/.config/autotrader/ctrader/razor/config.json \
  --status-port 18771
```

The existing GBP configuration, token, observer and `.phase28/reliability.sqlite`
history were preserved. At the final local database read through
19:39:28 UTC, the old monitor had recorded about 11335 seconds (3.15 hours),
10 connection attempts, 8 disconnects and 1 recovered snapshot, with zero counted
API/rate-limit/history errors. Earlier samples recorded SNAPSHOT_CHANGED_RETRY.
This is not a completed 24-hour test or a Razor reliability result. The old
monitor remains responsible for its own incremental history; none was reset.

The new dashboard was checked with local GETs, isolated Firefox rendering and
HTTP security tests. Its task-owned preview was stopped after QA; no service was
installed. To launch the finalized observation-control screen:

```bash
.venv/bin/python -u -m control_plane.operations \
  --database .phase3/ops.sqlite serve --broker-port 18771 \
  --worker-database .phase3/replay.sqlite --port 18772
```

It displays broker health, worker freshness, states, logs and any native offline
preparation records. CSRF cookie/token and exact Host/Origin protect controls;
responses are no-store and use origin-only referrers. The boundary is trusted
local loopback users, not authenticated remote administration. No broker or
systemd control is exposed. Stop/pause prevents new decisions/submissions; it
does not cancel existing pending orders, prevent their fills or flatten positions.

A separately launched historical replay can consume these logical controls:

```bash
.venv/bin/python -m observation --database .phase3/replay.sqlite \
  --controls-database .phase3/ops.sqlite --start 2026-03-03 \
  --end 2026-03-04 --delay 1
```

That command is provided, not started for live operation. New stores default to
stopped/suspended. Buttons govern the connected replay worker; they do not launch
OS processes. The CLI restart smoke used an isolated fixture store: two runs,
10 distinct persisted events, no paper orders, STOPPED at cursor 9.

Unattended prerequisites remain: a reviewed production runtime/supervisor, durable
private execution storage, crash/ownership recovery, operator alerts and clock
monitoring, real strategy data/source authentication, calendar-aware stale-data
handling, reviewed risk/cost limits and token renewal. Refresh tokens are still
discarded; indefinite operation requires deliberate reauthorization. The old
services were not modified or restarted to supply any of these prerequisites.

## Final validation

Requested final command:

```bash
.venv/bin/python -m pytest -q
```

**334 passed in 44.26s** (277 pre-Phase-3 tests plus 57 new cases). Coverage
includes native volume/precision, protection schemas, margin identity/freshness,
gross portfolio reservations, partial fills/rejections, cancellation-vs-fill,
unknown outcomes, actual child-process ownership/death, database locks, history
conflicts, campaign invalidation, controls/CSRF, replay restart, observation fences,
profile checks before token storage and Hurricane management evidence.

Additional completed checks: compileall, JavaScript syntax and git diff --check;
no dependency changes. Offline protocol smoke:

```bash
.venv/bin/python -m tools.phase3_protocol_smoke
```

Its report records seven fictional peer calls, two entry commands, five
management actions, released reservations and **zero network calls**. See
`phase3_protocol_smoke.json` and `phase3_worker_smoke.json`. These must never be
presented as actual broker order/fill/protection verification.

## Acceptance gate and next steps

We are **not ready to submit the first controlled Razor demo order now**.

1. Confirm the current balance/funding discrepancy privately and approve a
   real USD risk/margin budget. Confirm the matching UI volume step and the
   commission charge basis/rounding. No cash amount or credential needs to be
   pasted into Git or logs.
2. Complete and review the gated production mutation transport, separate trading
   OAuth/token lifecycle and bounded single-entry capability harness. Preserve
   remote demo/account/login/broker/currency/scope checks and a single execution
   owner. Implement a verified emergency protection/exit path: the current
   coordinator blocks on missing protection and cannot safely remediate that
   condition automatically. Do not connect a live transport to OfflinePeer.
3. Review `phase3_first_trade_plan.json`. It is explicitly DRAFT_NO_GO and
   unapproved: proposed one 0.01-lot BUY market entry, fill-relative $2 stop/$4
   target, $5 planned-risk ceiling, $250 margin ceiling and five-minute window.
   These are review parameters, not an order or guaranteed maximum loss. Owner
   approval must specify any bounded normal/emergency close as well as entry.
4. Obtain explicit trading-scope authorization approval **separately** from the
   one-order test approval. Keep the existing view tokens unchanged; use a new
   owner-only trading store. No such approval or trading token exists here.
5. Immediately before a future approved test: prove the intended Razor demo pin,
   USD/mode/specs, empty orders/positions, fresh quotes and native margin,
   available funds, all strategies stopped and exclusive execution ownership.
   Reject stale/ambiguous evidence. No automatic retries after unknown outcomes.
6. During that future test, require broker-confirmed fill/volume and initial
   native protection, then full order/deal/position reconciliation and the
   specifically approved exit. Missing/mismatched protection, partial/unknown
   outcomes or disconnects suspend new entries and require the bounded recovery
   plan. No second entry, scaling or automatic strategy activation is permitted.

Only that separately authorized real test can establish the first native order/
protection facts. Partial-close/amendment reliability and autonomous strategy
execution would require additional controlled capability testing and approval.

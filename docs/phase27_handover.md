# Phase 2.7 handover — 12 November 2026

## Strategy validation

VERIFIED on retained development data only: 2026-03-01 through 2026-10-17. Run 214 explicitly identifies test_role=development; all 44 inspected saved summaries have that role. No genuinely unseen validation is claimed. Frozen Reference and Long-only policies share source candles, costs and fill assumptions. No parameters were optimized.

Exact float outputs and all BUY/SELL, UTC-month, New York confirmation-hour/session-date and prior-hour-condition breakdowns are retained in phase27_validation.json. Monetary values below are in the research account model currency. Average loser is signed; win rate includes breakevens in its denominator. Drawdown is M1-marked equity, not merely closed trades.

| Cost scenario | Policy | Trades | Win % | Average winner | Average loser | PF | Net P&L | Max DD | DD % |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| zero_cost | t02_reference | 64 | 57.8125 | 110.74527476626358 | -112.72949478006146 | 2.1381612131330225 | 2181.1737550907055 | 438.6872256308325 | 3.80270808595055 |
| zero_cost | t02_long_only | 30 | 50.0 | 102.17545893775961 | -103.30367552831603 | 1.4836179605693627 | 499.5951287832322 | 371.56116152015056 | 3.563474031269002 |
| mild | t02_reference | 64 | 59.375 | 104.86489173794929 | -77.61399965603346 | 1.9746962303103714 | 1966.9018949852052 | 461.1787923826305 | 4.04319670588149 |
| mild | t02_long_only | 30 | 50.0 | 96.52026939802498 | -73.34862871073167 | 1.31591102784861 | 347.57461030939885 | 410.2444619289399 | 3.9579415454770346 |
| moderate | t02_reference | 64 | 56.25 | 94.60361635327618 | -81.50735011162945 | 1.4922975763419672 | 1123.52438559232 | 380.259486346682 | 3.4607636232896 |
| moderate | t02_long_only | 30 | 50.0 | 88.17975744110502 | -80.20086059760713 | 1.0994864242608382 | 119.68345265246717 | 468.7961351735048 | 4.56398363654137 |
| severe | t02_reference | 64 | 51.5625 | 77.64210675970575 | -90.00631740955049 | 0.9182830418632507 | -228.0063166257769 | 782.7521866780935 | 7.574499740207294 |
| severe | t02_long_only | 30 | 40.0 | 72.18983943648344 | -92.51736549718632 | 0.5201894732485216 | -799.0345057115519 | 898.8195898312606 | 8.988195898312608 |

Costs: zero; mild full spread 1 bps, slippage 0.25 bps/side, commission 1/side; moderate 3 bps, 0.5 bps/side, 2.5/side; severe 6 bps, 1 bps/side, 5/side. Starting balance 10000, risk 1%, leverage 20 for both. These are hypothetical OHLC stress assumptions, not observed broker costs. Spread/slippage shift modeled prices and recompute risk/targets, so individual outcomes need not deteriorate monotonically. This does not model a bid/ask tick path, liquidity, latency or broker rejection.

Zero-cost Reference exceeds Long-only on this development sample; both lose under the severe assumptions. That does not establish superiority out of sample. Side contributions within Reference differ from standalone Long-only due to compounding and portfolio path.

VERIFIED: 109 prefix/future-perturbation cases had zero signal causality mismatches. Completed candles, next-open entries and first-session consumption remain covered by Phase 2.6 regressions. NOT YET VERIFIED: independent implementation and genuinely unseen feed validation. The adapter and research replay share canonical detection code; agreement is susceptible to common bugs. OHLC stop-first/gap/breakeven assumptions and source timestamps remain limitations. Prior-hour condition labels use only 60 contiguous prior completed M1 candles and a preset 20bps range threshold, not a claim of predictive regimes.

Needed for unseen validation: an immutable, documented untouched date/feed split or prospective data collected after policy freeze; source timezone and candle availability evidence; fixed symbol specs, bid/ask costs and execution assumptions; independently reviewed signal implementation. Retained data after the saved end date is not automatically unseen. Do not tune on that split.

## Continuous offline observation

VERIFIED: observation/worker.py persists input identities/cursor, candle history, adapter decisions, intents, paper orders/positions and health in SQLite. A transaction includes adapter claims and paper projections; crash rollback permits safe retry. Canonical file ownership lock prevents simultaneous workers. Dataset/configuration hashes pin restart semantics. Duplicates are no-ops; conflicting/out-of-order events reject. Stale receipt envelopes, clock regression and missing sequences stop new decisions. Heartbeat uses wall time; replay market time is explicitly separate.

The worker uses synthetic 0.01 units per leg and its own local paper book. It never calls broker execution or clears observation_only. It is not a portfolio-risk benchmark of the performance research. Market proposals fill at the next replay M1 open; stops take precedence on ambiguous bars. Hurricane consumes structured replay messages only. No Telegram session or live feed is connected. Input exhaustion idles with heartbeat unless --once is set.

```bash
.venv/bin/python -m audit_phase27.validate
.venv/bin/python -m observation --database .phase27-demo.sqlite --start 2026-03-03 --end 2026-03-04 --delay 0 --max-events 180 --once
.venv/bin/python -m control_plane.diagnostics --help
.venv/bin/python -m pytest -q
```

Worker CLI smoke completed with cursor 179 and CONSISTENT_LOCAL_PAPER_BOOK. The local diagnostics accepts --worker-database and optional --ctrader-config. GET-only dashboard labels OFFLINE_REPLAY separately from DEMO READ-ONLY and explicitly disables real order execution. It displays worker heartbeat/configurations/signals/rejections, paper book, local reconciliation and errors. The broker connector uses only an already verified private token; it does not automatically launch OAuth. No service deployment or browser rendering claim is made.

## cTrader discovery and limits

Application approval was reported by the owner and configured locally. Implemented explicit accounts-scope demo discovery; see ctrader_discovery.md and ctrader_connection_verification.md. No account is automatically selected. Credentials and tokens stay outside Git and diagnostics. Existing legacy bots/services were not modified or restarted.

Validation: the full suite passed 238 tests in 30.12s before the final sanitized-failure regression; final run is recorded below. New coverage includes restart/duplicate/multi-strategy decisions, transaction rollback, stale input, clock changes, locks, dataset mismatch, local dashboard and account-selection security. Mock results do not establish real broker reliability.

Next: complete explicit local demo confirmation, verify actual view scope, account/symbol/history/freshness capabilities without execution, collect prospective observation data and review risk/fill differences before considering a separately authorized controlled demo-execution phase.

Final pre-filter-diagnostics suite: 241 passed in 29.86s. The empty cTrader application acknowledgement compatibility fix is covered by success and correlation-rejection regressions. Subsequent filter-diagnostic tests are recorded in the final test result below.

Final full-suite result: **244 passed in 29.89s** with the project-local Python
3.11 environment. compileall completed and git diff --check passed. No pinned
dependency change was needed for this increment.

Read-only demo integration update: owner-confirmed detailed broker configuration now matches; GBP demo identity verified; observer active on loopback port 18769. Full read-only snapshot and four post-subscription-fix health samples succeeded. Returned min/max volume anomaly remains unresolved; execution disabled. Tests: 257 passed in 29.45s. See ctrader_connection_verification.md for bounded observations and remaining gates.

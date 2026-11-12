# Phase 2.6 handover — 12 November 2026

## Delivered and verified

Implemented reusable, broker-free signal adapters inside AutoTrader. **212 tests
passed in 20.64s**: all 175 existing tests plus 37 new regressions. No old bot,
Ledger, MT5 or existing service was modified, imported, restarted or used for
execution. No credentials were read, no cTrader authentication occurred, and no
broker order was submitted. cTrader approval remains pending.

The adapter-to-intent replay matches **all 64 saved Ledger run 214 entry signals,
with zero missing or extra reference signals**. The separately named long-only
policy emits the 30 long signals with the same NY session/weekday filters. All
seven formerly extra Sunday long candidates are explicitly rejected.

These conclusions apply to the retained dates, feed and rules described below.
They do not establish general broker parity, historical broker DST conventions,
future performance or the original source trader's underspecified intentions.

## Code and interfaces

| File | Responsibility |
|---|---|
| `signal_adapters/timestamps.py` | Explicit Telegram/UTC versus UK-local conversion, overlap/gap handling, typed raw broker epochs with bounded caller-supplied offset evidence. No calendar inference or I/O. |
| `signal_adapters/hurricane.py` | Structured Hurricane message/level adapter, publication/receipt/quote freshness, edits, market-versus-limit eligibility and three linked observation legs. |
| `signal_adapters/t02.py` | Typed UTC M1 candles, contiguous M15 formation, pure first-inversion detection, named direction/NY-weekday policies, no-catch-up observation adapter. Standard-library only. |
| `signal_adapters/common.py` | Caller-provided synthetic quantity, stable hashed identities, durable observation decisions/edit tombstones, and normalized central TradeIntent creation. |
| `control_plane/contracts.py` | Backward-compatible boolean `observation_only` field; old simulator intents/payloads retain their previous default behavior. |
| `control_plane/foundation.py` | Rejects observation-only intents as OBSERVATION_ONLY at enqueue and dispatch revalidation. Existing queue/lifecycle implementation is preserved. |
| `audit_phase26/compare.py` | Independent offline OHLC comparison of the two policies, with shared costs/sizing/fill/management assumptions. No broker/engine adapter imports. |
| `tests/test_phase26.py` | 37 regression cases, including all saved signal adapter paths, Sunday exclusions, durable duplicate/edit behavior and the central execution fence. |
| `docs/phase26_comparison.json` | Detailed assumptions, input hash, matching counts, metrics and independently replayed trade records. |

The Hurricane adapter consumes **structured parsed fields**, not raw Telegram
messages. Its `published` value has an explicit source convention, and
`received`, evaluation time and Quote.at must be aware UTC. Raw-text grammar,
source authentication and live Telegram collection are outside this phase.
The nine earlier reconstructed fixtures still have no original publication
instant; their receipt times are not substituted to fabricate live eligibility.

Example interface (inputs must come from an authorized observation/replay source):

```python
from decimal import Decimal
from signal_adapters.common import ObservationJournal, ObservationSizing
from signal_adapters.t02 import T02Adapter, T02Policy

journal = ObservationJournal("observation.sqlite")
try:
    adapter = T02Adapter(journal, T02Policy.reference())
    decision = adapter.evaluate(
        utc_completed_candles, normalized_utc_quote, evaluated_at_utc,
        ObservationSizing(Decimal("0.01")),
        connection_epoch=observer_started_at_utc,
    )
    proposals = decision.intents  # Inspect only; every proposal is observation_only.
finally:
    journal.close()
```

Quantity is explicitly supplied in synthetic units. `SIM-DEMO` / `SIM-XAU` are
simulation contract identifiers, not cTrader/MT5 mappings or broker lot sizes.
The adapters have no enable-trading option, broker client, submission loop or
engine.enqueue call. Manually handing their intents to the engine is tested to
produce no commands and zero simulated broker calls. A queued observation flag
is also rejected during dispatch revalidation.

ObservationJournal is separate from the central order queue. Hash-based keys
suppress duplicates across restarts; SQLite transactions serialize competing
observations. Invalid edits become tombstones, including when an edit precedes
the original delivery. They do not cancel orders or modify positions. Rejected
source decisions are consumed rather than automatically retried as later fresh
entries. A crash after the observation record commits but before the caller sees
the proposal can lose an observation; there is no delivery outbox/resend promise.
This is deliberate observation behavior, not a replacement execution lifecycle.

## Hurricane timing fixes

**VERIFIED in the new adapter:**

- Explicit UK wall time uses Europe/London. For example, 09:00 on 1 July becomes
  08:00 UTC; 09:00 after the October transition remains 09:00 UTC.
- Aware Telegram API timestamps preserve their instant. No London reinterpretation
  or broker offset is applied. A naive API timestamp is rejected rather than
  silently labelled UTC. The adapter does not guess a missing source convention.
- Nonexistent London spring times reject. Ambiguous autumn times reject unless
  an explicit fold or consistent supplied UTC offset establishes their meaning.
  An offset inconsistent with London for that date rejects.
- Age is measured from publication to evaluation, including processing delay,
  with a default 90s maximum and 3s future tolerance. Receipt cannot freshen an old
  publication. A connection epoch can explicitly prevent reconnect backlog.
- Quote freshness uses normalized UTC, defaults to five seconds, and rejects
  future/invalid/crossed quotes. Eligible BUY/SELL prices follow the audited
  current-quote market/limit rules, with at/past TP1 rejected.
- Edits emit no entry intent; duplicates and edit-before-original deliveries do
  not generate new proposals. Concurrent duplicate delivery emits once, and a
  database lock fails without emitting a proposal.

The old bot is intentionally unchanged: its naive-summer behavior remains in the
Phase 2.5 audit regression as historical evidence. Only the new adapter is fixed.

**Historical clock evidence, still sample-scoped:** the stored 9 November raw
broker epoch label 09:08:13.840 and UTC reference 07:08:14.073750 differ by 7200s
minus the 0.233750s tick age. UK is UTC on that date, supporting UK+2 for that
observation. The new helper accepts only a RawBrokerEpoch plus an explicitly
bounded OffsetEvidence interval. It returns a UTC datetime, rejects already
normalized datetime input, rejects a rewrapped UTC epoch outside the evidence
interval, and refuses another date outside that interval. There is no global
+2/+3 default and no inferred UK/EU/US broker calendar. Normalized quotes and
Telegram inputs never pass through this raw-epoch helper.

**NOT YET VERIFIED:** broker chart/candle conventions, cross-DST historical
mapping, independently archived clock/reference evidence before 25 October,
and publication-to-evaluation bid/ask paths. OffsetEvidence is a caller-attested
input contract, not an automatic verifier of an arbitrary claimed offset. Raw
broker ingestion must verify provenance/interval authority before using it.
A single retained observation cannot establish a permanent broker timezone.

## T02 policies and corrected Sunday behavior

`T02Policy.reference()` explicitly permits BUY and SELL.
`T02Policy.long_only()` explicitly permits BUY only. Both default to NY weekdays
Monday–Friday, NY 20:00 inclusive to midnight exclusive, and the audited .20
signal-close FVG/stop filter. Alternative weekday sets require an explicit policy
configuration; policy version hashes include sides and weekdays. The first
inversion consumes its session even when its ratio or direction rejects; neither
variant searches for a more profitable later replacement.

The old seven extras were Monday UTC timestamps falling on **Sunday in New
York**. The new policies filter on confirmation time converted to NY, not UTC
weekday. Production-adapter replay verifies that each is rejected:

- 2 March 03:45 UTC; 9 March 01:15 UTC;
- 4 May 02:00, 11 May 01:15, 18 May 02:00 UTC;
- 3 August 02:00 and 10 August 00:15 UTC.

M1 inputs must be aware UTC with minute OPEN timestamps and valid positive OHLC.
Duplicate minutes reject. Out-of-order input is sorted. Runtime observation
requires every minute from NY 19:15 through the latest completed minute; missing
history fails closed. Forming candles are excluded. M15/FVG paths cannot bridge
missing candles. Detection returns the first setup independently of sizing;
the runtime adapter then applies policy, a 15s candle freshness bound, quote
freshness and no historical catch-up. Session consumption persists before an
observation is returned. There is no real-time feed or background scheduler.

| Tested path | Matches saved 64 | Missing saved | Extra | Sunday emitted |
|---|---:|---:|---:|---:|
| New Reference detector and adapter-to-intent path | 64 | 0 | 0 | 0 |
| New Long-only detector and adapter-to-intent path | 30 | 34 shorts | 0 | 0 |

The 34 omitted shorts are the intended separately named long-only variant,
not a claim that it reproduces the both-direction run. Quantity and risk
allocation are absent from T02Policy/first_setup. Entry intents receive quantity
from ObservationSizing; central risk/ownership/demo controls are unchanged apart
from the additional observation rejection.

## Strategy comparison

Sufficient retained research M1 data exists for a **shared offline comparison**.
Both variants were replayed independently from their generated signals rather
than computing long-only profit by filtering the original run's P&L rows.

Common assumptions, also recorded in phase26_comparison.json:

- Dates 1 March–17 October 2026; retained OANDA mid-price M1 OHLC, with the same
  .20 first-IFVG/session/NY-weekday rules and 19:15 warmup as the audited sample.
- Starting simulated account 10,000 USD; each trade risks 1% of current cash
  balance to initial stop, capped at 20x cash notional. Quantity is unrounded
  research units, not MT5/cTrader lots. This sizing belongs to the comparison,
  not the signal adapter or the existing fixed-lot bots.
- Both policies use zero commission, spread and slippage, matching the available
  research run's assumptions. These costs are configurable for synthetic tests
  but no real broker cost estimate is substituted or claimed.
- Entry is next M1 open; structural stop; target is 1R from actual modeled fill.
  Protective stop/target checks precede completed-bar management. Open gaps fill
  at the open; same-bar ambiguity is stop-first. A completed M1 .68R touch makes
  breakeven active at the next M1 open, including protection on the entry bar.
- One concurrent position for this instrument, overnight allowed; signals while
  occupied would be skipped. Neither policy had occupancy skips or leverage
  caps bind in this sample. Any remaining position would close at final data
  close. This does not model the old worker's multi-ticket execution behavior.
- Drawdown is M1-close marked-to-market equity, not an intrabar/tick maximum.
  Win rate uses all closed trades, including breakeven trades in its denominator.
  Profit factor is summed positive net trade P&L / absolute summed negative P&L.

| Metric | Reference, both directions | Long-only |
|---|---:|---:|
| Trades | 64 | 30 |
| Wins / losses / breakeven | 37 / 17 / 10 | 15 / 10 / 5 |
| Win rate | 57.8125% | 50.0000% |
| Profit factor | 2.1382 | 1.4836 |
| Net P&L, simulated USD | +2,181.17 | +499.60 |
| Ending balance, simulated USD | 12,181.17 | 10,499.60 |
| Maximum M1-close equity drawdown, USD | 438.69 | 371.56 |
| Maximum M1-close equity drawdown, percent | 3.8027% | 3.5635% |

For this retained sample, the independent Reference model matches all 64 saved
entry/exit times, entry/exit prices, initial stops and R multiples, and also
quantities/net trade P&L, within 1e-7. The selected saved CSV exit facts were copied
through a narrow field allowlist into `tests/fixtures/phase26/ledger_exit_facts.json`,
with original file hash and no credentials/account/source identifiers. No Ledger
runtime or database was opened. Zero discrepancies are explicitly recorded in
the generated comparison, rather than inferred from equal aggregate counts.

Reference earned more under this sample/model; Long-only had slightly smaller
measured drawdown. This is one in-sample historical period with zero costs and
reconstructed rules, not evidence that either policy is generally superior or
appropriate to trade. It does not prove equality to all original engine/config
behavior, out-of-sample returns, actual margin, currency/tick/lot economics,
liquidity, broker fills or native protection. The original complete run input
fingerprint/effective environment remains unverified as documented in Phase 2.5.

## Verification commands and results

Used existing project-local CPython **3.11.17** and already pinned Phase 2.5 test/
audit dependencies. No new dependency or external test environment was required.

```bash
.venv/bin/python -m audit_phase26.compare
.venv/bin/python -m pytest -q tests/test_phase26.py
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q control_plane signal_adapters audit_phase26 tests
git diff --check
```

| Check | Observed result |
|---|---|
| Independent comparison | Reference 64/0/0; long-only 30/34/0; no Sundays; zero reference saved exit/quantity/P&L fact mismatches. |
| New regressions | 37 passed in 7.01s. |
| Entire existing + new suite | **212 passed in 20.64s**. |
| Compile/whitespace | Passed. |

Coverage includes UK spring/fall DST, explicit overlap resolution and mismatched
offsets, preserved API UTC, bounded historical raw-epoch normalization/double
application rejection, delayed/future publications, edits/duplicates/restart,
concurrent delivery and database lock failure, stale/future quotes, NY boundaries,
Sunday exclusions across both policies, full saved adapter signal identities,
missing/duplicate candles, stale/no-catch-up session consumption, the central
observation fence at enqueue and dispatch, independent performance metrics,
stop-first/open-gap behavior, next-open BE timing and consistent common costs.
All brokers used in existing engine tests are offline fakes; new production
adapters do not make broker calls. Passing these tests is not broker reliability.

## Unresolved discrepancies and broker integration evidence

1. The historical 27 October and 9 November candidates still lack underlying
   broker candles; the available cache ends on 21 October. Their recorded facts
   were not relabelled as independently detected trades by these fixes.
2. Broker tick and historical candle encoding can differ. Retain dated raw epoch/
   UTC-reference/candle evidence through UK and US DST transitions before
   populating any broader offset interval. Reject unverified intervals.
3. The old bots retain their existing BUY-only, fixed-lot and naive-time behavior.
   This task corrects AutoTrader adapters only. Original source-trader ambiguities
   and the separately selected reference/policy must remain explicit.
4. The new detector deliberately fails closed on missing data. The canonical
   Ledger FVG concept can span dataframe gaps; parity is proved only on tested
   complete sessions, not arbitrary sparse feeds.
5. Adapters normalize entry proposals only. The benchmark's .68R BE model is not
   wired to position management, and edit tombstones are not broker cancellation
   commands. A future management-intent contract/worker needs separate validation.
6. Before integration, require approved read-only cTrader evidence for demo
   identity, symbol IDs, contract units/minimum/step, account mode/currency,
   protective constraints, broker history completeness, margin and clock/data
   freshness. Do not reuse existing services' credentials or infer cTrader
   specifications from SIM-XAU/MT5/OANDA.
7. Any future change from observation-only to execution is a separate reviewed
   action: approved ownership/risk policy, complete reconciliation and broker-
   confirmed demo capabilities must precede it. There is no activation path in
   these adapters. No trading or authentication permission is implied here.

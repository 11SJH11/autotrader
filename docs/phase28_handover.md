# Phase 2.8 — Broker specifications and reliability

Date: 12 November 2026. **NO-GO for beginning demo order testing.**

Read-only observation may continue. The raw volume anomaly is reproduced and
scaling is verified, but actual admissible min/max/step requirements are not
independently established. No broker orders were placed, modified or cancelled;
no trading-scope credential was requested or accessed. Existing Ledger,
Hurricane, T02, MT5 and systemd services were not changed. The existing observer
on port 18769 was left running; the new monitor is a separate task-owned process.

## Requirement assessment

| Requirement | Status | Evidence / remaining gap |
|---|---|---|
| Official full symbol schema and scaling | VERIFIED | Official ProtoOASymbol + request 2116/response 2117; exact selected fields below. |
| Correct account/symbol/full-details response | VERIFIED | Revalidated accounts/view grant, pinned demo/broker, trader GBP currency, symbol name/base/quote assets, response account/correlation/symbol ID and one full symbol entry. IDs are not exported. |
| Min/max anomaly caused by client scaling | FAILED hypothesis | Raw integers already contain the anomalous magnitude; no float/default substitution. |
| Actual admissible volume limits from matching UI | NOT YET VERIFIED | Owner requested to read only volume/specification fields in matching cTrader demo UI; no reply recorded yet. No guessed defaults adopted. |
| Unit/lot arithmetic | VERIFIED for returned API lotSize | Protocol hundredths -> units -> lots, using Decimal; physical contract and admissible range still need matching UI confirmation. |
| Account currency / indicative GBP conversion | VERIFIED, bounded | GBP deposit identity; fresh account-provided GBPUSD quote and explicit bid/ask inversion. Not a realized P&L guarantee. |
| Hypothetical expected margin | VERIFIED retrieval; partial independent validation | Three read-only estimates and indicative 1:20 crosscheck; no position was created. |
| Commission / stop acceptance | NOT YET VERIFIED in execution | Selected metadata retrieved; public account-type-dependent pricing cannot establish this account's actual deal charges. Zero stop distances do not prove acceptance. |
| Hedged mode | VERIFIED metadata only | HEDGED; totalMarginCalculationType MAX. Nonempty hedge allocation/close behavior untested. |
| 24-hour reliability observation | IN PROGRESS, NOT COMPLETED | Restartable monitor targets 86400 active seconds; current snapshot reports actual duration only. |
| Authentication longevity | VERIFIED code review; design supplied | Refresh token discarded, manual reauthorization required; no unattended refresh enabled. |
| Controlled demo execution readiness | NO-GO | Volume, cost/protection and longer reliability gates remain. |

## Volume anomaly: raw protocol, units and lots

[Official symbol definitions](https://help.ctrader.com/open-api/model-messages/#protooasymbol)
express volumes and lotSize in hundredths of a unit. The
[full-symbol request/response schema](https://help.ctrader.com/open-api/messages/#protooasymbolbyidres)
distinguishes the full entity from the light symbol list. Probe code is
`ctrader_readonly/specifications.py`; exact selected numeric evidence is in
`phase28_broker_probe.json`. Complete broker messages, symbol/account IDs,
account login, balances and credentials are not exported.

| Field | Presence / JSON type | Exact raw integer | Normalized units | Lots using returned lotSize |
|---|---|---:|---:|---:|
| minVolume | present / int | 99999999999900 | 999999999999 | 9999999999.99 |
| maxVolume | present / int | 99999999999900 | 999999999999 | 9999999999.99 |
| stepVolume | present / int | 1 | 0.01 | 0.0001 |
| lotSize | present / int | 10000 | 100 | 1 |

The probe uses the same authorized demo pin, validates XAU/USD asset identities,
requests full details by the resolved symbol ID, validates the correlated 2117
response and matching ID, and inspects exact field presence/type before integer
conversion. JSON decimal strings are accepted for int64 but floats/bools/missing
values are not defaults. Invalid fields reject; no fallback volume is installed.

The anomaly is confirmed in the raw broker metadata. Its server-side cause is
NOT established. Possibilities such as scope-specific masking or broker symbol
configuration require vendor confirmation; they are hypotheses, not findings.
Do not broaden OAuth scope to investigate. A read-only margin estimate accepting
a hypothetical volume does not establish that order validation would accept it.

Needed independent evidence: in the same Pepperstone - Europe demo login, open
XAUUSD symbol information and record min/max/step, displayed unit (lots versus
units), lot/contract size, commission/account type and effective leverage.
Confirm the source is this cTrader account, not MT5 or a generic contract table.
If UI and API differ, obtain Pepperstone/cTrader clarification of view-scope
field behavior before any execution adapter. No credentials/screenshots/private
messages are needed in Git; retain only sanitized specification facts/source
and date. Generic minimum-lot marketing is not an account-specific override.

## Read-only risk investigation

Account identity revalidated: demo, GBP, detailed broker `pepperstoneuk`, matching
configured account-list broker `Pepperstone - Europe`. Returned account mode is
HEDGED, leverageInCents=3000 (account-level 1:30) and margin computation type=0
(MAX). Gold-specific risk cannot inherit the account-level leverage blindly.

The dynamic leverage read request (2177/2178) was added to the same closed
outbound allowlist using the official
[Spotware proto definitions](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto).
The profile returned one tier with USD-notional threshold 1000000 and raw
leverage=2000. Its raw value is retained; the model reference's short description
does not explicitly state its scale. Do not treat it as literal 1:2000 or
silently normalize it based only on expectation. Margin estimates/public retail
gold pricing support approximately 1:20 on this sample, pending matching UI
confirmation. The read-only leverage request cannot change leverage.

Hypothetical quantities based on returned lotSize (not approved order sizes):

| Lots | Units | Protocol volume | Buy margin estimate | Sell margin estimate |
|---|---:|---:|---:|---:|
| 0.01 | 1 | 100 | 146.35 | 146.33 |
| 0.1 | 10 | 1000 | 1463.50 | 1463.30 |
| 1 | 100 | 10000 | 14634.95 | 14632.95 |

Amounts are interpreted in the verified GBP deposit currency after applying the
response moneyDigits. The response does not echo a currency code; this is not an
independent bank/deal statement. Every requested volume must have exactly one
matching estimate; missing/duplicate volumes, absent precision and negative
margin reject. The
[expected-margin schema](https://help.ctrader.com/open-api/messages/#protooaexpectedmarginreq)
describes an estimate based on leverage profiles, not an order-validation test;
it excludes certain GSL margin behavior. No free-margin or affordability claim
is made and private account finances remain excluded.

Probe samples: XAUUSD 3873.08/3873.16 USD at 16:30:17.895 UTC, GBPUSD
1.32336/1.32339 at 16:30:18.314 UTC. Quotes were about 0.139 and 0.545 seconds old
when read. Bid/ask inversion gives about 0.755635–0.755652 GBP per USD. Spread
was 0.08 USD per API unit, about 0.060451–0.060452 GBP per unit. For a $1 price
move, hypothetical gross P&L is units × $1 before costs; at 100 units this is
$100, approximately £75.56 at those separate sampled rates. This is indicative
contract arithmetic, not realized valuation/FX conversion verification. No MT5
server offset or contract assumption was used.

The reproducible report includes indicative notional and notional/20 compared
with returned buy margin. Source for the **comparison assumption only** is
[Pepperstone UK pricing](https://pepperstone.com/en-gb/trading/costs-and-fees),
which lists 1:20 retail gold leverage and distinguishes Standard/Razor commission.
It is not the selected account's symbol-info screen. Prices/margin/FX are read at
separate instants, so exact equality is not expected. Larger tiers, hedged margin
allocation, losses/gaps and actual protection remain unverified.

Commission fields were present: commission=0, preciseTradingCommissionRate=0,
minCommission=0 and preciseMinCommission=0; commissionType=2 (USD_PER_LOT).
pnlConversionFeeRate=0. These are observed metadata, not proof of zero total
transaction costs. The public pricing page varies charges by account/platform;
matching UI and, later under separate authorization, actual deal economics would
be needed. Swaps/overnight funding were not validated. Current spread is dynamic
and the single quote is not a distribution or execution-cost guarantee.

Stop/target distances were explicitly present as 0/0, distanceSetIn=POINTS;
price digits=2, pip position=1, tradingMode=ENABLED. A zero metadata distance is
not evidence that any chosen stop price, freeze distance, side, GSL policy or
change will be accepted. No protection request was submitted. Hedged mode and
MAX margin calculation are metadata observations; separate long/short positions,
partial closures and actual aggregate margin were not exercised.

## Restartable incremental reliability observation

`ctrader_readonly/reliability.py` owns a new read-only Reader; it never invokes
execution, OAuth consent or refresh. Private config/token are loaded only by
that Reader. The prior observer and its status listener are unchanged.

Start/resume the same campaign from the repository:

```bash
.venv/bin/python -u -m ctrader_readonly.reliability \
  --config /home/SJH/.config/autotrader/ctrader/config.json \
  --database .phase28/reliability.sqlite --target-hours 24 --interval 5
```

Get a coherent read-only summary at any time:

```bash
.venv/bin/python -m ctrader_readonly.reliability \
  --database .phase28/reliability.sqlite --summary
```

The currently launched monitor remains running as a task-owned process, not a
service. Ctrl-C in its foreground terminal or SIGTERM to that specific monitor
PID requests clean shutdown. To identify the task process, inspect
`pgrep -af 'ctrader_readonly.reliability'` and match the exact database before
sending a signal; do not signal any other observer/service. Rerun the same start
command after stopping; an exclusive canonical file lock rejects a concurrent
writer. SQLite WAL + synchronous FULL commits each sanitized sample with duration
atomically. Target changes reject. Committed data survive unclean exit; between
run gaps and large sampling/suspend gaps are uncredited and reported. Monotonic
time measures active duration; wall time supplies UTC labels. Host/session
termination can stop this task process; no login/systemd autostart was installed.

Runtime data are in the ignored `.phase28/reliability.sqlite` and sidecars;
`phase28_monitor_current.json` is a sanitized point-in-time report, not a claim
that the requested window completed. Target completion means accumulated
recorded active monitoring reached 86400 seconds, **not** uninterrupted healthy
uptime. Samples are five-second right-endpoint estimates: a short stale or
incomplete period between samples can be missed. In-process counters supplement
sampling for connection attempts, successful auth, disconnect/recovery, requests,
history failures, heartbeats, quote events, stale rejects, API/rate-limit errors
and completed snapshots. Counters restart per process and summary accumulates
per-run deltas. A final stop after the last sample may not appear in counters;
planned stops are separately counted from run records.

Snapshot/history flags are deliberately false during a refresh; those sampled
incomplete periods are not automatically broker/history failures. Likewise the
first disconnected sample of each process is startup, not a discovered network
outage. Every read reconnect repeats view/demo/broker/account checks and
resnapshots. A recovery counter means a new complete snapshot after reauth,
not proof of sustained fresh quotes. Reconnection behavior still needs longer
real observation, including closed markets and nonempty books.

Heartbeat continuity records sends and incoming-message liveness. No inbound
heartbeat events were seen in the current sample, so no heartbeat-ACK guarantee
is claimed. Fresh quotes provide incoming liveness. Market closures can cause
stale quotes/reconnect loops under the deliberately strict freshness policy;
calendar-aware monitoring remains future work. No API rate limit was deliberately
exceeded; local ceilings stay 10 requests/s and 2 historical requests/s, and local
waits/peaks and recognized server rate-limit errors are counted.

Resource samples are this new monitor's RSS, getrusage high-water RSS, cumulative
CPU seconds and CPU percent of one logical core. They exclude old services and
the separate existing observer. High-water RSS and instantaneous RSS differ;
no leak conclusion can be drawn from a few minutes. Summary reports maxima per
sample/run, not total whole-platform resource use. Raw error text, account IDs,
login, account finance values, credentials and raw broker responses are absent
from monitoring storage. Unknown error text becomes OTHER_ERROR.

## Authentication and execution gates

See [token longevity design](ctrader_token_longevity.md). Existing access tokens
are rejected 30 seconds before expiry; refresh tokens remain discarded. A
bounded reconnect loop cannot renew an expired token. Deliberate accounts-only
OAuth is currently required. Status exposes remaining seconds; no automatic
browser/notification/refresh is enabled. Future optional refresh design requires
private opt-in storage, one renewal owner, remote SCOPE_VIEW + pinned demo/broker
verification before atomic bundle replacement, rotation/generation fencing and
lost-ack/crash/concurrency/redaction tests. No refresh request was enabled here.

Before a controlled demo execution adapter may be considered:

1. Resolve matching-account volume/lot/physical contract specifications from
   independent evidence and explain or safely handle the anomalous view response.
2. Confirm GBP conversions, actual account/platform costs, effective leverage,
   hedged aggregation and protective-order constraints; no guessed stop semantics.
3. Finish and review the monitoring window, then exercise longer real reconnect,
   incomplete-history and nonempty-book cases without conflating mocks with broker
   reliability. Quantify stale periods and expected market closures.
4. Retain existing central durable ownership, idempotency, transactional risk
   reservations, worst-case pending exposure, restart/reconciliation and unknown
   outcome fail-closed behavior. Observation intents must remain rejected by
   execution. A future broker mutation adapter must be separate and reviewed.
5. Obtain separate explicit approval for controlled demo execution and its
   tightly bounded risk limits/credentials. This phase neither requests nor
   enables trading scope; no readiness flag can grant it automatically.

## Regression and commands

Python 3.11 project-local `.venv` and existing pinned dependencies were used;
this phase adds only standard-library functionality and no dependency update.

```bash
.venv/bin/python -m ctrader_readonly.specifications \
  --config /home/SJH/.config/autotrader/ctrader/config.json \
  --output docs/phase28_broker_probe.json
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q ctrader_readonly tests
git diff --check
```

New offline regressions cover exact raw integer scaling, missing/invalid numeric
fields without defaults, margin-volume/precision validation, redacted probes,
NO_GO status, durable monitor restart/crash/gaps/locks/ownership, safe telemetry,
rate-limiter windows, API rate rejection/recovery metrics, expired-token handling,
refresh-token discard and indicative margin arithmetic. These are mock/offline
claims only; the real bounded probes and monitoring are reported separately.

## Recorded real monitoring cutoff and final results

The persisted summary snapshot was taken through **2026-11-12T16:41:30.193133+00:00**. The process continues beyond this frozen report.

| Measurement | Observed value |
|---|---:|
| Target duration (seconds) | 86400.0 |
| Recorded active monitoring (seconds) | 656.6664432489779 |
| Samples | 133 |
| Runs / planned stops | 2 / 1 |
| Unobserved gaps (seconds) | 14.713166336528957 |
| Connection attempts / successful connections | 2 / 2 |
| Unexpected disconnect counter | 0 |
| Recovery counter within process | 0 |
| API / rate-limit / history errors | 0 / 0 / 0 |
| Heartbeat sends / received heartbeat events | 64 / 0 |
| Completed snapshots | 41 |
| RSS maximum sampled (MiB) | 40.19140625 |
| Kernel getrusage RSS high-water (MiB) | 284.34765625 |
| Peak sampled CPU, percent of one logical core | 6.416399140180223 |
| Local peak total / history requests per second | 10 / 2 |
| Local limiter wait count / seconds | 1 / 0.19842130108736455 |
| 24-hour target completed | False |

There were no unexpected disconnects or true in-process recoveries during this
Phase 2.8 cutoff. Two successful initial connections across a planned restart
prove restart/reload/resnapshot only, not real network fault recovery. The
previous phase's recorded subscription-rejection/reconnect remains separate
historical evidence. Small snapshot/history-incomplete sample intervals occurred
during normal refresh, with no history request error counter. Startup false
periods and the planned gap must not be counted as unexplained outages. Kernel
high-water RSS is reported separately from sampled RSS; its substantially larger
value is not evidence of current resident use or proof of a leak. Continue to
inspect the incremental resource trend over the full window.

Final full suite: **277 passed in 30.08s** on project-local CPython **3.11.17**
(257 previous tests plus 20 Phase 2.8 regressions). compileall and git diff
--check passed. A local scan against the configured private client ID/secret,
account ID/display login and access token found none in Phase 2.8 artifacts or
new source/tests; values were never printed by that check. No dependency change
or /tmp test environment was used. Pinned dependencies remain unchanged.

NO-GO remains in force. Independent matching-account UI volume evidence,
account-specific costs and stop/hedge behavior, longer reliability evidence and
separate execution authorization remain outstanding. The monitor is running;
the 24-hour observation has **not** completed.

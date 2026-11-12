# Phase 3.1 handover — gated Razor DEMO transport

12 November 2026. **NO-GO for submitting an order during this assignment.**
The previously missing network mutation transport, separate trading OAuth flow
and bounded first-order harness are implemented and tested offline. They remain
unapproved and unused against broker execution. No trading authorization was
requested, no trading-scope credential was accessed, and no broker order was
placed, modified, cancelled or closed. No strategy was enabled. No dashboard
features, dependencies or service changes were added.

**384 tests passed in 42.25 seconds**, using the project-local Python 3.11
virtual environment. This includes the existing 334 tests and 50 new Phase 3.1
cases. Fake socket/peer results establish the tested local safeguards, not real
cTrader reliability or actual broker protection/fill semantics.

## Baseline and real read-only evidence

The selected private Razor profile was verified with its existing view-only
credentials through the demo endpoint. Pinned account/display-login and broker
identity checks passed; broker title is Pepperstone - Europe, detailed broker name
pepperstoneuk, currency USD, mode HEDGED. The actual API balance **matched the
owner's intended $10,000** during this check. The earlier $1,000 expectation is
not used by the new harness. Account identifiers and exact private records are
not reproduced here.

Executed read-only command:

```bash
.venv/bin/python -m ctrader_execution.harness baseline \
  --config /home/SJH/.config/autotrader/ctrader/razor/config.json \
  --intended-balance 10000
```

Sanitized result: pinned_demo_verified=true, currency=USD,
balance_matches_intended=true, actual_balance_saved_privately=true,
local_confirmation_pending=true, execution_enabled=false.

This saved only `baseline-pending.json` beside the private Razor configuration.
It did not change any account pin, balance, configured expectation, view token or
old observer. **Explicit local confirmation of actual account/balance and intended
capital is still pending.** The harness reads actual capital, allows a knowingly
confirmed difference from intended capital, and uses the smaller value for its
funds ceiling. A subsequent actual-balance change blocks entry until reconfirmed.
This is a point-in-time baseline check, not continuing funding verification.

The existing full Razor specifications/evidence in `phase3_razor_probe.json`
remain the real read-only evidence: raw volume hundredths min=100, max=500000,
step=100, lotSize=10000; one lot=100 units, min/step=0.01 lots, max=50 lots.
No synthetic quantity or margin-per-unit assumptions are used by the new network
transport. Fresh full-symbol data and hypothetical native margin are read again
for a future preflight. Independent matching-account UI confirmation of step,
commission basis and reviewed stop constraints remains a gate. Zero API stop
minimums do not establish acceptance. Fee rounding, actual fills and native stop
behaviour remain unobserved.

## Implementation and boundaries

| Component | Implemented | Evidence / limit |
|---|---|---|
| `ctrader_execution/transport.py` | Separate fixed-demo TLS mutation wire and `TradeClient`; reuses existing reader correlation, identity, heartbeat, history, rate limiting and quote validation | Actual mutation send path runs against a fake socket in tests; never connected for trading |
| Entry requests | Official 2106 MARKET/LIMIT, native volume/price precision, stable clientOrderId; market relative SL/TP and limit absolute SL/TP | Mock serialization, fills and positive reconciliation tested; broker behaviour unproven |
| Management requests | 2108 cancel, 2109 pending amendment, 2110 position SL/TP amendment, 2111 close with native remaining volume | Owned-target and non-increasing-risk checks; amendments restricted to unfilled limits; first-test grant excludes amendments |
| Partial/full closure | Close request encoder, explicit partial-operation consent, native step and minimum-residual checks; full remaining volume can close partial-fill dust without rounding upward | The first 0.01-lot test permits full closure only. A standard 0.01-lot XAUUSD position cannot be partially closed on a 0.01-lot grid. Real partial-close capability and larger-position grants remain future work |
| `ctrader_execution/ledger.py` | Owner-only SQLite FULL durability, unique lifetime entry slot, immutable payload digest, transactional pre-send consumption, local process ownership lock, private audit | Crash before/after write, restart UNKNOWN, duplicate/idempotency conflict, DB-lock and competing-owner tests |
| `ctrader_execution/approvals.py` | Exact Razor demo/USD/HEDGED profile binding; separate time-bounded OAuth/test roles; canonical private journal; controlling-terminal consent | Config flags or a view token cannot supply test authority; no approved production grant created |
| `ctrader_execution/oauth.py` | Separate explicitly gated trading OAuth, state/Host/path/TTL/replay validation, remote TRADE/demo/account/broker/currency/mode validation, separate private trading store | Mocked success/failure only. Existing accounts-only OAuth and view transport remain unchanged |
| `ctrader_execution/harness.py` | Read-only baseline/preflight, private confirmations, one minimum-volume entry maximum, bounded fill observation, native protection verification, cancel/close recovery, sanitized status | Mocked complete round trip, partial entry, race, missing protection and outage paths; future attended use only |
| Existing engine | Shared TradeIntent/Quote/native codecs and risk math retained; offline coordinator still accepts only its exact OfflineAdapter | No real adapter installed into an automated execution engine; no strategy promotion or dashboard execution route |

The simulator's `production_execution()` still raises unconditionally. The real
transport is a separate manually gated harness, not a flag that switches an
existing replay worker to broker trading. The original Phase 0/1 simulator and
strategy observation fences continue to pass their tests. Automated production
routing, a broader execution grant and unattended recovery are intentionally not
enabled by this assignment.

### Protocol handling and a corrected enum

Requests were checked against the [official cTrader message definitions](https://help.ctrader.com/open-api/messages/)
and [model enums](https://help.ctrader.com/open-api/model-messages/). MARKET entries
use fill-relative protective distances scaled by 100000, avoiding unsupported
absolute SL/TP fields on a new MARKET order. Client order IDs provide correlation;
they are not assumed to deduplicate broker submissions.

The review found an earlier offline mismatch: execution type 6 denotes
ORDER_EXPIRED. Protection replacement now expects ORDER_REPLACED (4) in the native
coordinator, peer and new transport. Request-specific acknowledgement types and
matching order/position/account identity are enforced. Unsupported responses
fail closed; actual broker acknowledgement variants still need authorized testing.

An accepted execution event is not proof of a fill or of protection. The harness
requires consistent account-scoped order/deal/position snapshots, complete bounded
history, unique clientOrderId mapping, positive opening/closing deals and matching
volumes/sides/symbols. Absence alone does not prove a lost request failed or a
position closed. Account and margin monetary scaling uses returned moneyDigits;
volume uses raw hundredth-units and returned lotSize.

## Safety behaviour

- Exact pinned demo profile only, fixed demo TLS endpoint, no live endpoint
  option, no redirects/proxy inheritance or wire trace. Remote trading grant is
  required separately from the immutable view-only allowlist. Any live or
  unknown-environment account in that grant causes rejection.
- No trading-token fallback to the view store. Tokens/credentials/callback values
  are excluded from logs, audit projections and status. New private files stay
  outside Git with owner-only permissions; journal data includes selected private
  IDs/trading facts and must not be published.
- Every strategy must remain STOPPED, global entry suspension ON. The manual test
  is a specifically consented exception for one capability test, not a mechanism
  for releasing strategy entries. Controls are rechecked before and at submission.
- One canonical journal is bound to the profile and approval. A lifetime process
  lock excludes competing owners; one unique entry slot survives restart. The
  send fence commits before bytes; SUBMITTING becomes UNKNOWN after reopening.
  No sent/unknown entry or management command is blindly resent. Even a rejected
  entry spends this test's slot. New approval cannot reset a bound journal.
- Before entry: fresh quote/full symbol snapshot, UI/API volume agreement, explicit
  stop floor, spread/price/quantity validation, empty pending/open book, current
  broker margin for the actual raw volume/side, current confirmed balance and
  planned account-risk/funds ceilings. Stop/target distances, explicit costs and
  slippage buffers are checked independently of strategy sizing.
- The minimal test has one manual strategy, one unit and an empty starting book,
  so its per-order/strategy/global ceilings coincide. The existing offline native
  coordinator retains multi-strategy/gross exposure/pending risk tests; that
  portfolio coordinator is not authorized to route real trades.
- A filled position's actual SL and TP are checked against its fill/approved plan.
  Missing/mismatched native protection triggers the explicitly approved full-close
  response and a failed-test result even if flat is later confirmed. Partial entry
  cancels its remainder and closes the positively owned position, also flagged as
  a failed/partial test. Normal successful capability testing also ends flat.
- UNKNOWN outcomes reconnect for reads only and recover by positive ownership
  evidence. Cancellation-vs-fill is reconciled; an existing position can be closed
  after an entry ACK is lost. No evidence means UNKNOWN, not permission to retry.
  Failure to positively confirm flat emits a critical attention code/private audit.

This is an attended, local, single-host boundary. It is not a distributed lease or
an assurance against an operator deleting/copying approvals/journals. Do not remove
or relocate that state, run conflicting broker clients or edit permits to evade
limits. Other manually operated clients cannot be locked out by this application.

## Validation results

Environment: existing project-local CPython 3.11.17 `.venv`, pinned dependencies;
no `/tmp/ledger-test-venv` dependency and no new package installation.

```bash
.venv/bin/python -m pytest -q tests/test_phase31.py
# 50 passed in 0.37s (final focused Phase 3.1 pass before enum tightening)
.venv/bin/python -m pytest -q tests/test_phase31.py tests/test_phase3_native.py
# 95 passed in 7.80s after the final transport/enum changes
.venv/bin/python -m pytest -q
# 384 passed in 42.25s
.venv/bin/python -m compileall -q ctrader_execution control_plane/native.py tests/test_phase31.py
.venv/bin/python -m ctrader_execution.harness --help
# both passed; help performs no connection/authentication
```

`git diff --check` passed. New tests use only invented identities/tokens and an
in-memory socket/peer; none loads a real broker credential or connects externally.
Coverage includes valid native MARKET/LIMIT requests; cancel/amend/protect/close;
wrong scope/environment/login/broker/currency/account mode; expired tokens and
approvals; separate token stores; OAuth state/replay/expiry and cross-attempt
rejection; response correlation/account/target and unknown errors; unclaimed or
repeated wire sends; process ownership/DB locks; crash after bytes before ACK;
restart recovery without a second entry; lost ACK before/after acceptance;
partial fill/residual cancellation; cancellation-vs-fill; incomplete/conflicting
history; missing protection and failed emergency close; foreign-target rejection;
stale data, balance/margin/risk/spread/UI/stop constraints; controls changing before
send; missing controlling terminal; preservation of the view-only mutation block.

This does not claim real partial fills, rejection mapping, reconnect closure,
commission rounding, server stop acceptance or nonempty broker history have been
verified. Real read-only evidence here is the baseline check and preserved earlier
observer/specification records, not a new prolonged reliability run.

## Remaining blockers and exact next steps

The executable attended procedure is [phase3_first_trade.md](phase3_first_trade.md).
That file did not exist at the start of Phase 3.1; the existing Phase 3 JSON draft
was used as the proposal, and this new document specifies the implemented gates.

1. **Owner decision required:** confirm the pinned account/actual balance/intended
   capital locally and independently review matching UI volume step, commission
   basis/rounding, stop floor and planned risk/margin/cost buffers. The baseline is
   pending, not an approval. The current proposal is one 0.01-lot market entry,
   $2/$4 relative protection, $5 planned risk and $250 margin ceiling; all values
   require the owner's explicit review, not silent adoption.
2. **Separate permission required:** only after the owner approves trading OAuth,
   invoke `approve-oauth` and `authorize-trading`. No such request or permission
   was made during this task. Verify only the pinned demo account is selected and
   the separate saved token is remotely TRADE/demo/pin validated.
3. **Separate one-order permission required:** invoke `approve-test`, review
   preflight and obtain explicit consent covering the single entry, residual
   cancellation and normal/emergency full close. Entry permit lasts 10 minutes,
   management permit one hour; the baseline lasts 24 hours. The owner must be
   present with the correct demo UI available.
4. Only then, following separate explicit execution approval, `run --execute`
   requires another local confirmation and can consume the sole entry slot.
   Preserve the private journal. `resume --execute` permits recovery only, with
   local confirmation and unexpired authority; it cannot authorize another entry.
5. Review actual broker fill, native protection, fees, margin and positive full
   reconciliation before any further capability test. No second entry or strategy
   activation follows automatically from success.

**Operational limits:** standard stops do not guarantee a maximum realized loss;
market-relative stops can differ from original structural prices after slippage.
Actual partial-fill protection semantics remain unknown. Network/process failure,
expired trading tokens/management permits, incomplete history or unconfirmed
management results can block automated remediation. A partially filled close is
not retried automatically. The operator must use the demo UI when critical
exposure cannot be resolved; there is no unattended supervisor/remote alerting or
automatic token renewal. A deliberately fresh authorization is required on token
expiry. General portfolio execution, larger-volume/partial-close capability
grants, automatic strategies and unattended running remain outside this gate.

**Assessment:** GO for reviewing the completed offline-tested, attended harness
and preparing the private confirmations. **NO-GO for real submission now:** the
owner's baseline/UI/risk confirmation, separate trading OAuth approval and
separate first-test execution approval remain absent. Real broker reliability
cannot be established until the separately authorized bounded test is performed.

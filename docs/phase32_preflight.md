# Phase 3.2 — read-only first-trade preflight

12 November 2026, sampled around 21:12 UTC. **NO-GO pending local baseline and
matching-account UI confirmation.** The read-only technical checks passed. No
trading OAuth, test approval, order or service change was made. No production
code or new tests were added. Evidence: [phase32_preflight.json](phase32_preflight.json).

| Check | Result |
|---|---|
| Pinned identity | VERIFIED remotely: selected DEMO pin/display-login, Pepperstone - Europe / pepperstoneuk, USD, HEDGED; remote view-only grant |
| Capital | VERIFIED privately: current balance matches intended $10,000. **NOT YET VERIFIED locally:** `baseline-confirmed.json` does not exist; no confirmation was manufactured |
| Account book | VERIFIED: zero pending orders/positions, complete requested history and fresh snapshot |
| Health/quote | HEALTHY; bid/ask 3873.38/3873.55; quote timestamp 21:12:03.885 UTC, sampled age about 0.038s |
| Spread | 0.17 price distance; below proposed 0.50 limit |
| Volume | Current full-symbol response matches Phase 3 evidence: minimum 0.01, maximum 50, step 0.01 lots; lotSize 10000 raw hundredth-units = 100 units/lot; test raw volume 100 = 1 unit = 0.01 lot |
| Contract unit | Owner previously reported 100 oz/lot; API numeric lot conversion agrees. Independent current matching-account UI confirmation, including ounces, remains pending |
| Commission | API USD_PER_LOT, precise rate 350000000 / 10^8 = $3.50/lot; precise minimum commission zero. Charge basis/rounding still require matching-account confirmation |
| Stops | API SL/TP distances 0/0 POINTS, price digits 2; standard-stop account, no guaranteed stops. Zero does not prove $2/$4 protective prices will be accepted |
| Instrument leverage | Native hypothetical margin and sampled price imply approximately 1:20. Dynamic tier raw leverage=2000 retained without treating it as literal 1:2000 or assuming a documented scale; account-specific UI leverage confirmation remains pending |
| Hypothetical margin | 0.01 lot BUY $193.68 / SELL $193.67, both below $250; checked private funds cover margin plus $5 planned risk |
| Strategy controls | Hurricane observation, T02 Reference, T02 Long-only all STOPPED; global entry suspension ON |
| Approval/journal | No first-test approval or journal exists. Nothing was initialized to grant permission |

These are point-in-time observations. Recheck identity, balance, book, quote and
margin immediately before any separately approved test; they are not reservations
or promises of order acceptance. No financial account values, account IDs, tokens
or raw broker responses are exported; the capital result is a boolean match.

## Conditional test proposal, not an approval

Exactly one 0.01-lot BUY MARKET entry; $2 fill-relative SL, $4 fill-relative TP;
$5 planned-risk and $250 margin ceilings. The existing codec uses
relativeStopLoss=200000 and relativeTakeProfit=400000 rather than unsupported
absolute protection on a new MARKET order. Actual fill/protection must be read
back from broker position state; it has not been tested against real execution.

At a confirmed $7/lot round-trip commission, nominal 0.01-lot commission is
$0.07. Existing conservative risk math rounds $0.035 per side upward to cents
($0.08 total), adds the explicit $0.50 fee buffer and $0.25/unit slippage allowance:
$2 + $0.08 + $0.50 + $0.25 = **$2.83 planned risk**, below $5. This remains
conditional on account-specific costs; stop slippage/gaps can exceed the budget.
The 0.17 sampled spread is checked separately; it is not added again to the
fill-to-stop distance. No overnight holding is intended.

Pepperstone's [published cTrader pricing](https://pepperstone.com/en-gb/trading/costs-and-fees)
supports $7 round trip for XAUUSD with proportional charges below one lot.
Its [gold pricing page](https://pepperstone.com/en/go/ways-to-trade-gold) describes
$3.50/lot/side on Razor Spot Gold. These support the proposed basis but do not
replace this account's UI, minimum-fee or rounding confirmation. Native scaling
was checked against [official cTrader model definitions](https://help.ctrader.com/open-api/model-messages/).

## Recovery and second-entry review

Reviewed existing `Journal.claim`, `consume_send`, profile/approval binding and
exclusive owner lock. The lifetime unique entry slot survives rejection/restart;
SUBMITTING recovers as UNKNOWN, and a consumed send cannot be resent. A new grant
cannot reset a bound journal. These are local single-host safeguards; do not copy,
remove or edit private state to evade them. No actual journal was created here.

The attended harness checks actual native SL/TP and, on missing/mismatched
protection, reports failure and invokes only the specifically approved owned-
position full-close response. It cancels any pending remainder, reconciles
positive entry/exit deals and normally ends flat. `resume --execute` is recovery
only, requires local confirmation/unexpired authority and cannot create another
entry. Unknown management outcomes are not blindly retried. If it cannot confirm
flat, the owner must immediately use the correct Razor demo UI. Network/process
failure or token/permit expiry can prevent automated recovery. Availability of
that UI and owner understanding of this fallback remain to be confirmed.

Ran existing mocked safety tests, without broker mutations:

```bash
.venv/bin/python -m pytest -q tests/test_phase31.py
# 50 passed in 0.29s
```

These cover single-entry durability, ownership, restart/lost ACK, cancellation-vs-
fill, missing protection/emergency-close failure and scope/environment guards.
The full suite's last recorded result remains Phase 3.1's 384 passing tests;
a full rerun was not needed for this review-only assignment.

## Exact remaining gates

1. Privately confirm the pinned account, actual capital and intended $10,000
   using the existing `confirm-baseline` flow. There is currently no valid local
   confirmation record. This is distinct from trading/order consent.
2. Confirm the matching Razor account's XAUUSD UI: min/max/step, 100 oz/lot,
   commission per-side/round-trip basis, minimum charge/rounding, instrument
   leverage and reviewed stop constraints. No UI access or confirmation was
   assumed; a specification-only question was presented to the owner.
3. Confirm the attended recovery plan and access to the correct demo UI; review
   the $2/$4/$5/$250 proposal and cost buffers before any separate order approval.
4. Only after those confirmations, consider separate explicit trading OAuth
   authorization. No trading OAuth was requested in this review. Test approval,
   fresh immediate preflight and final local execution consent remain later gates.

**Decision:** technical read-only checks PASS; **NO-GO to advance to trading
authorization until gates 1–3 are resolved.** No code fix was identified as
necessary for the current read-only preflight. Real order acceptance, native
protection and full broker execution reconciliation remain deliberately unproven
until a separately authorized attended test. Stop here; do not approve/execute.

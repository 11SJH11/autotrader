# Final Razor preflight and margin reconciliation

12 November 2026, read-only sample approximately 21:34 UTC.
**GO to the next, separately approved DEMO trading-authorization step only.**
**No order authorization or submission.** Evidence: [razor_final_preflight.json](razor_final_preflight.json).

| Check | Current result |
|---|---|
| Baseline | Private confirmed record exists, permissions/profile/time validation passes; actual and intended $10,000 match; fresh broker balance still matches |
| Identity | Correct pinned DEMO account and displayed-login checks passed; Pepperstone - Europe / pepperstoneuk; USD, HEDGED; existing remote VIEW scope |
| Book | Zero orders/positions, complete requested history, fresh reconciliation |
| Quote | 3873.57 bid / 3873.74 ask; 21:34:06.678 UTC; sampled age 0.544s; HEALTHY |
| Spread | $0.17 per oz; below proposed $0.50 limit |
| Quantity | 0.01 lot = 1 oz = protocol volume 100; API min/max/step 0.01/50/0.01 lots; 100 units/lot, matching owner's 100 oz contract confirmation |
| Native expected margin | BUY $193.69 / SELL $193.68; both below $250; private funds check covers margin plus $5 risk |
| Protection evidence | UI 0.0-pip SL/TP minimum and API 0/0 POINTS; digits=2, pip position=1. Standard stop account, guaranteed stops false |
| Controls | Hurricane observation and both T02 configurations STOPPED; global entry suspension ON |
| Entry slot | First-test journal absent, therefore unconsumed; no order approval exists. Neither was initialized by this review |

No account identifiers, credentials, tokens or detailed account financial records
were exported. Actual capital is recorded only as boolean agreement with the
owner-approved intended amount. No existing configuration or confirmation was
changed. No bots/services were started or modified.

## Margin discrepancy

At 0.01 lot, the confirmed 100 oz/lot contract gives exactly 1 oz exposure.
Using the quote sampled near the native margin request:

| Direction | Notional USD | Broker margin USD | Notional / returned margin |
|---|---:|---:|---:|
| BUY | 3873.74 | 193.69 | 19.99969 |
| SELL | 3873.57 | 193.68 | 19.99985 |

Thus **effective leverage implied by the broker estimate is about 1:20 (5% margin)**.
At a hypothetical 1:30, comparison-only margin would be about $129.12; that
calculation is not used for validation. The approximately 50% larger returned
margin is far beyond a cent-rounding or sampled-spread difference. Quotes and
margin estimates are separate requests, so tiny leverage deviations from 20 are
expected and exact synchronization is not claimed.

The API trader account field leverageInCents=3000 explicitly describes account
leverage 1:30. Its linked symbol dynamic profile returns raw leverage=2000 for
the returned tier. That value is preserved: the short official tier schema says
"Applied leverage" without explaining its scale, so this report does not invent
a /100 conversion or treat it as proof of literal 1:2000.

[cTrader documents](https://help.ctrader.com/trading-with-ctrader/dynamic-leverage/)
that account leverage is a ceiling and a lower symbol leverage applies.
[Pepperstone's published UK pricing/leverage table](https://pepperstone.com/en-gb/trading/costs-and-fees)
lists gold at 1:20. Together with the account's actual margin estimates, these
support a lower gold-specific margin profile as the likely explanation. The
owner specifically reports a **gold UI** label of 1:30, so it must not simply be
relabelled as an account-level observation. The exact reason that screen displays
1:30 remains unconfirmed; a broker clarification or same-account UI estimated
margin preview can settle that discrepancy without submitting anything.

This uncertainty does not require assuming less margin or changing leverage.
Use the native estimate (~$193.69), re-request it immediately before entry, retain
the $250 cap and fail if it is exceeded. Margin remains an estimate, not proof of
actual execution margin or acceptance. No leverage setting was modified.

## Proposed bounded test assessment

The unchanged review proposal is BUY MARKET, 0.01 lot; $2 fill-relative stop and
$4 fill-relative target; $5 planned-risk ceiling; $250 native-margin ceiling.
The codec uses relativeStopLoss=200000 / relativeTakeProfit=400000. Actual broker
position protection must still be verified during a separately approved test.

The owner-confirmed UI $3.50/lot label matches API USD_PER_LOT precise rate
350000000 / 10^8. API precise minimum commission is zero.
[Pepperstone's cTrader pricing](https://pepperstone.com/en-gb/trading/costs-and-fees)
supports the working assumption $3.50/side, $7 round trip per lot. The label alone
does not prove per-side charging or final rounding; record that assumption in
future explicit test consent and inspect actual charged deals during the test.
At 0.01 lot nominal round-trip fees are $0.07; the existing risk math budgets
$0.08 after conservative per-side cent rounding, plus a $0.50 fee buffer and
$0.25 slippage allowance: **$2.83 planned risk**, below $5. These are planned
buffers, not a guaranteed loss cap. The spread is separately checked and not
added twice to the fill-to-stop distance. No overnight holding is proposed.

Owner-provided volume/contract observations and zero minimum-stop display are
retained; no re-entry is requested for this review. Zero does not guarantee
acceptance or stop execution without slippage. Native protection and actual
fees/fill/margin are capabilities the attended test is intended to verify, not
facts falsely claimed before it.

## Decision and remaining gates

There is **no current read-only technical blocker to proceeding to an explicit
request for separate demo-only trading OAuth authorization**. The baseline,
identity, quantity, book, freshness, proposed risk and returned margin checks
pass. The leverage-label discrepancy is documented; the safer native margin
estimate is used and fits the cap. Its exact UI cause is unresolved, rather than
silently assumed fixed. It is not necessary to lower the margin estimate to
proceed to authorization.

Order submission remains **NO-GO until** separate trading OAuth consent and its
remote pinned-DEMO/TRADE verification, followed by explicit one-entry approval
(including cost assumptions, stop uncertainty and normal/emergency full close),
final local execution confirmation and a fresh immediate preflight. Owner access
to the correct demo UI for failed automated recovery remains part of that attended
approval. The durable single-entry slot and no-blind-resend rules remain intact;
no strategy may activate automatically.

No trading OAuth request, trade approval, broker mutation or strategy startup was
performed. No application features or tests were added; this was read-only
verification using existing components. Stop at the separate authorization gate.

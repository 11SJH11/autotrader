# Razor XAUUSD commission verification

12 November 2026, 21:53 UTC. Single read-only full-symbol check against the pinned
DEMO/USD account; account/broker validation passed. Existing view-only token only.
No trading approval, OAuth request, order, bot or service action.
Evidence: [razor_commission_check.json](razor_commission_check.json).

| Field | Present | Raw | Interpretation |
|---|---|---:|---|
| preciseTradingCommissionRate | yes | 350000000 | $3.50 per lot, divided by 10^8 |
| commissionType | yes | 2 | USD_PER_LOT |
| preciseMinCommission | yes | 0 | Explicit zero configured minimum; not an absent-field default |
| minCommissionType | yes | 1 | CURRENCY |
| minCommissionAsset | yes | USD | Minimum denominated in USD |

Legacy minCommission=0 also present. No rounding-named fields were returned.
The official full-symbol schema provides no commission-rounding policy field.
See [cTrader model definitions](https://help.ctrader.com/open-api/model-messages/)
for scaling/types. A zero configured minimum does not itself define rounding.

[Pepperstone UK cTrader pricing](https://pepperstone.com/en-gb/trading/costs-and-fees)
states $7 round trip for XAUUSD, proportionately adjusted below one lot. Together
with the selected account's $3.50 USD_PER_LOT rate and the owner's matching UI
label, this supports the per-side $3.50 / round-trip $7 basis.

For 0.01 lots, nominal opening commission=max($3.50*0.01,$0)=$0.035;
nominal closing commission=$0.035; total=$0.070. No configured minimum increases
these values. These are nominal calculations, not observed billings. Actual
fractional-cent rounding, aggregation across fills, and posting timing remain
unverified without broker clarification or future authorized deal evidence.
Do not assert $0.035 is necessarily the actual currency debit on each side.
Existing planned risk budgets $0.04 per side / $0.08 total conservatively, plus
its separate $0.50 fee allowance. That is a budget, not a returned rounding rule.

The same full symbol response reconfirmed min/max/step=0.01/50/0.01 lots,
100 units per lot (owner confirms ounces), SL/TP distances explicitly zero POINTS.
The six specification values, in approve-test input order, supported by this
metadata, the owner observations and published commission basis are:

```text
0.01,50,0.01,100,7,0
```

Order: min lots, max lots, step lots, units/lot, round-trip commission USD/lot,
reviewed minimum stop **price** distance. The last zero reflects reviewed zero
metadata/UI minimum, not guaranteed acceptance of a particular stop. The fifth
value is 7, not the bare per-side 3.50 label. Do not attest that actual rounding
has been independently verified: the API does not expose it, and this check did
not observe charged deals. If the approval evidence prompt requires that stronger
assertion, obtain broker clarification before confirming it. No approval was
created or confirmation supplied during this check.

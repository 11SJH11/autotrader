# First real Razor DEMO fill — reconciliation diagnosis

12 November 2026. **Real entry and subsequent closure confirmed. Current read-only
check: no open positions and no pending orders. The first-test journal is
permanently consumed; no retry or renewal is allowed.** No broker mutation,
execution/recovery command, OAuth flow or strategy startup was performed during
this investigation.

## Real evidence

| Item | Verified result |
|---|---|
| Identity | Existing view-only grant, pinned Razor DEMO account and broker/USD identity checks passed |
| Journal | One 2106 entry mutation, ACCEPTED acknowledgement, consumed send fence; remains unchanged |
| Entry | Positive opening deal and matching entry order/position: BUY raw 100 = 1 oz = 0.01 lot |
| Fill/protection | 3876.83 entry, 3874.83 native SL, 3880.83 native TP; preserved actual position state matches $2/$4 distances |
| Opening commission | Deal commission -4 with moneyDigits=2: $0.04 charge |
| Closure | Positive closing deal for the same position, raw 100 closed against raw 100 opened; closing MARKET order consistent with the reported manual closure |
| Closing commission | $0.04 charge; recorded round-trip total **$0.08** |
| Final exposure | At approximately 23:46 UTC: zero open positions, zero pending orders, complete bounded history, consistent before/after snapshots |

Ownership is established from the original durable client ID to the entry order,
its position link and positive opening deal. Closure matches that position and
balances opening/closing/current volume; it is not inferred merely from absence.
The harness journal has no closing mutation: this was not successful automatic
harness closure. The broker's cancelled protection order and zero remaining
pending book confirm no protective child was left active at the check.

The observed $0.04/$0.04 charges support the conservative $0.08 allowance for
this one round trip. They do not establish a universal broker rounding rule,
partial-fill aggregation, or posting policy for other trades.

Evidence before correction: [razor_first_fill_evidence.json](razor_first_fill_evidence.json).
Final corrected read-only check: [razor_first_fill_after_fix.json](razor_first_fill_after_fix.json).
Selected private API facts were also saved in owner-only files outside Git;
arbitrary broker responses, comments, balances, private IDs and tokens were not
exported. Hash comparisons confirmed the original journal, approval and trading
token were unchanged by each collection. No Journal constructor/recovery update
was used to inspect the real journal.

## Exact failure and targeted fix

The preserved first snapshot contains two orders with the **same clientOrderId**:

- MARKET (type 1), BUY, FILLED: the entry.
- STOP_LOSS_TAKE_PROFIT (type 4), SELL, ACCEPTED, linked to the same position:
  broker-native protection, not another entry.

`owned_state` previously collected every matching clientOrderId and rejected
`len(orders)>1` with **AMBIGUOUS_CLIENT_ORDER_ID**. That exact exception was
reproduced directly against the immutable preserved snapshot. The execution
error whitelist omitted it, producing EXECUTION_VALIDATION_FAILED. The harness
aborted before its normal protection-verification/closure loop; the open
position was not automatically closed. This was an incorrect protocol role/
identity assumption, not a snapshot timing race. The same old predicate failed
on stable post-closure history too.

The [official cTrader model](https://help.ctrader.com/open-api/model-messages/)
defines order type 4 as native SL/TP and optional closingOrder/position links.
The matcher now selects entry candidates by client ID **and role**, preserving
strict rejection of multiple real entry candidates. It separately validates
linked protective/closing orders against the same position, symbol, opposite side
and bounded volume. Unsupported roles and mismatched links still fail closed.
A closing deal or explicit closingOrder role can disambiguate a closing order
that inherits the client ID. The private audit projection now retains the
optional closingOrder field.

Positive deal/position conservation, complete history, unique deals, entry
volume/side/symbol, close-detail volume and native protection requirements remain.
A still-active linked protection order after closure blocks a flat conclusion.
No duplicate entry is silently selected and no native child is auto-cancelled by
this investigation.

The corrected strict matcher passes both actual private snapshots: preserved
open=(100 opened, 0 closed, 1 position); final=(100 opened, 100 closed, 0 positions).

## Exposure reporting on incomplete reconciliation

A separate **observation-only** projection extracts positive order/position links
before full history validation. It can report a positively identified open
position even if deal history is incomplete. If identity cannot be established,
it still reports account-wide open/pending counts, without claiming ownership.
A failed snapshot/history request makes a best-effort view-only reconcile read;
no partial evidence authorizes management or a flat/closed conclusion.

Execution diagnostics now preserve AMBIGUOUS_CLIENT_ORDER_ID and related precise
reconciliation codes. Known open/pending exposure returns
CRITICAL_OBSERVED_EXPOSURE with immediate demo-UI attention, while preserving
stored journal_state separately. A stored accepted acknowledgement with incomplete
reconciliation reports UNKNOWN_RECONCILIATION rather than implying a completed
trade. Failure to read evidence remains UNKNOWN. No automatic compensating order
or blind retry is added.

## Focused tests

```bash
.venv/bin/python -m pytest -q tests/test_first_fill_reconciliation.py tests/test_approval_renewal.py tests/test_execution_diagnostics.py tests/test_approval_input.py tests/test_execution_status.py tests/test_baseline_confirmation.py tests/test_phase31.py
# 154 passed in 1.27s
```

Seventeen new cases use [sanitized broker-shaped fixtures](../tests/fixtures/razor_first_fill.json):
all account/order/deal/position IDs, client strings and times are invented;
closing price is synthetic; P&L/balances/private metadata are omitted. The public
entry/protection prices and observed commission are retained. Tests cover native
protection sharing a client ID, completed manual-close history, genuine duplicate
entries, invalid child links/sides/symbols/volume/types, active protection after
closure, incomplete histories with visible exposure, closing-role variants,
close-volume mismatch and role preservation. Existing 137 focused tests pass.
Compileall and diff whitespace checks passed. No full-suite or broad real-broker
reliability claim is made.

## Journal and future-test gate

The journal remains one claimed/consumed entry with its original ACCEPTED state.
Local `status` therefore still reports historical SUBMITTED; it is not a fresh
broker-position view. The investigation did not relabel the journal CLOSED or
alter its audit. Independent read-only evidence above proves this round trip
closed. No emergency action is indicated by the final flat broker snapshot.
If the demo UI shows discrepant exposure, stop and re-reconcile; do not resend.

**The journal is permanently spent for new entries.** Neither ordinary approval
nor renew-test may reuse it; closure never frees its lifetime entry slot. Do not
reset/delete/replace it or change the private configuration to evade that guard.
No recovery command is needed to close a position already proven flat.

**NO-GO for initiating another test in this task.** A future separately authorized
capability test needs a reviewed allocation of a distinct bounded test identity
that preserves the spent first-test evidence and account-wide exclusive execution
ownership, rather than bypassing the existing canonical-journal guard. This task
does not implement or authorize that allocation. The future plan must explicitly
cover one entry, native protection, controlled native close, complete history,
outage/emergency handling and operator presence. Recheck current balance/baseline,
identity, empty book, specs/costs, quote/margin, risk caps and stopped/suspended
strategies. Actual automatic broker closure/recovery, partial-fill behaviour and
reconnect reliability remain unverified despite this successful real entry,
attached protection and manually completed closure. No automatic strategy follows.

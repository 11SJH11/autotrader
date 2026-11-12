# First Razor DEMO test — approval gate and attended procedure

Phase 3.1 preparation, 12 November 2026. **Not authorized; do not execute this
procedure until the owner separately approves trading OAuth and the one-order
plan.** No command below has been used to obtain trading scope or submit orders
in this assignment. The older `phase3_first_trade_plan.json` remains an unapproved
historical proposal; this document describes the implemented harness.

Use the repository's Python 3.11 `.venv`. Leave the existing view-only observers,
old GBP profile, monitoring evidence and other projects/services alone. All
private state belongs beside the Razor private configuration, outside Git, in a
700 directory with 600 files. Do not paste IDs, tokens, browser/callback URLs or
broker responses into terminal logs, reports or Git. The displayed login and
actual balance are shown only through the controlling local terminal during
confirmation. Public status exposes fixed codes and sanitized counters.

## Before any authorization

The owner must approve the pinned Razor DEMO account, its actual USD balance,
intended testing capital, independent specifications and proposed loss/margin
budget. The read-only Phase 3.1 baseline matched the intended $10,000; this does
not replace local confirmation and does not change the account's balance.

In a private local terminal, establish the configuration variable (it contains a
path, not a credential), then refresh and confirm the baseline:

```bash
RAZOR_CONFIG=/home/SJH/.config/autotrader/ctrader/razor/config.json
.venv/bin/python -m ctrader_execution.harness baseline \
  --config "$RAZOR_CONFIG" --intended-balance 10000
.venv/bin/python -m ctrader_execution.harness confirm-baseline \
  --config "$RAZOR_CONFIG"
```

The second command privately displays the pinned displayed login, actual balance
and intended budget and requires an exact hidden confirmation. It authenticates
with the existing view-only token, not a trading token. An intentional difference
between actual and intended capital is allowed; capacity uses the smaller value.
A balance change after confirmation blocks entry and requires reconfirmation.
Baseline confirmation expires after 24 hours. No balance or account pin is edited.

Verify the same account's cTrader UI contract size, minimum/maximum/step lots,
commission charge basis and reviewed minimum stop distance. API zero minimum
stops does not prove stop acceptance. Standard stops can slip or gap; planned
risk and slippage buffers are not guaranteed maximum losses. Be prepared to use
the broker's own demo UI to cancel/close exposure if this process, network,
authorization or reconciliation fails. No unattended operation is authorized.

## Only after separate approval for trading OAuth

```bash
.venv/bin/python -m ctrader_execution.harness approve-oauth --config "$RAZOR_CONFIG"
.venv/bin/python -m ctrader_execution.harness authorize-trading --config "$RAZOR_CONFIG"
```

The first command requires typed local consent and creates a 15-minute OAuth-only
permit. The second opens the local browser without printing URLs. Select only
the pinned Razor demo account; do not select any live account. Callback state,
exact loopback Host/path, expiry and one-time use remain enforced. Remote scope
must be TRADE; every returned account must explicitly be demo; the pinned ID,
login, broker title/name, USD and HEDGED identity must pass before saving the
separate `trading-token.json`. No order permission comes from OAuth approval.
Existing configuration scope and view-only token remain unchanged. Refresh tokens
are discarded; expiry requires deliberate reauthorization, never a hidden retry.

## Only after separate approval for the bounded order plan

Leave every strategy STOPPED and global entry suspension ON. The manual test is
a separate, specifically approved capability operation; it cannot activate a
strategy or unlock the simulator's production factory.

```bash
.venv/bin/python -m ctrader_execution.harness approve-test \
  --config "$RAZOR_CONFIG" --controls-database .phase3/ops.sqlite \
  --side BUY --order-type MARKET
.venv/bin/python -m ctrader_execution.harness preflight --config "$RAZOR_CONFIG"
```

`approve-test` asks privately for six matching UI/review facts, comma-separated:
minimum lots, maximum lots, step lots, units per lot, USD round-trip commission
per lot, reviewed minimum stop price distance. Do not infer a commission basis
from a bare "$3.50" label. Enter zero stop floor only after reviewing its meaning
and acknowledging uncertain acceptance. The API min/max/step/lot must agree with
the confirmed UI values. No metadata is substituted to force agreement.

The current review proposal is exactly 0.01 lot / 1 unit / raw volume 100;
fill-relative $2 SL, $4 TP; $5 planned-risk ceiling; $250 margin ceiling;
$0.25/unit slippage allowance; conservative per-side cent-rounded commission plus
$0.50 fee buffer; maximum $0.50 spread. These values require explicit approval;
if unsuitable, stop and review the proposal rather than approving it. Proposed
entry/stop/target, risk and expected margin are displayed before consent. Entry
approval lasts ten minutes; management authority lasts one hour. It explicitly
includes cancellation of the entry remainder and full normal/emergency closure.
A first test does not authorize pending amendments, protection changes, partial
closure, additional entries, strategies or scaling.

Immediately before execution, preflight requires fresh quotes and native margin,
matching specs and account/balance, an empty order/position book, sufficient USD
funds, stopped/suspended controls and exclusive ownership of the private journal.
A plain `run` without `--execute` performs view-only preflight.

**The following command is the execution gate. Do not run it without the owner's
separate explicit approval:**

```bash
.venv/bin/python -m ctrader_execution.harness run \
  --config "$RAZOR_CONFIG" --execute
```

It requires another exact local confirmation, refreshes preflight afterward and
claims the sole entry durably before any bytes are sent. There can be at most one
entry attempt in the bound journal, including rejection, crash and UNKNOWN cases.
It waits a bounded 15 seconds for evidence, verifies native position SL/TP against
the fill, cancels any remainder and closes the test position, then requires
complete positive entry/exit deal reconciliation. The test is intended to end
flat; it does not leave a strategy or managed position running. Partial entry and
missing/mismatched protection return a failure indication even after successful
remediation. Missing protection triggers an immediate specifically approved full
close; failure to confirm closure is CRITICAL, not a successful test.

## Recovery and evidence

```bash
.venv/bin/python -m ctrader_execution.harness status --config "$RAZOR_CONFIG"
.venv/bin/python -m ctrader_execution.harness resume \
  --config "$RAZOR_CONFIG" --execute
```

`resume` requires local confirmation and existing unexpired management authority;
it never submits an entry. It reauthenticates, reads matching order/deal/position
history and can cancel/close only positively owned exposure. Previously sent or
UNKNOWN management actions are not blindly resent either. A partial closing fill,
missing history, unknown identity, failed cancellation or still-open position
requires operator attention. A lost entry acknowledgement with no positive order
evidence remains UNKNOWN, even if snapshots are empty. Do not rerun `run`, delete
or relocate the journal, mint a new grant to evade it, or reset its entry slot.

If recovery cannot positively confirm flat, inspect the pinned Razor demo UI
immediately and carry out the approved emergency response there. Expired tokens,
expired management permits, missing history and an unavailable process/network
can prevent automated closure. This harness cannot guarantee an exit during an
outage and supplies no remote alerting or unattended supervisor.

Private files: `baseline-pending.json`, `baseline-confirmed.json`,
`trading-oauth-approval.json`, `trading-token.json`, `first-test-approval.json`,
`first-test.sqlite` and its owner lock. The SQLite journal stores fixed audit
codes, commands, selected private trading facts and durable outcomes; no tokens,
credentials, arbitrary broker descriptions, labels or raw responses. Preserve it
for review. Do not publish it. `status` reports sanitized types/states/codes only.

Acceptance requires one entry at the approved minimum volume, correct broker
native protection, known fees/fill/margin, no residual orders/positions, complete
positive history and a preserved audit. A passed mock suite, accepted request or
an empty snapshot alone does not establish these facts. Further capability tests,
strategy execution and unattended operation require separate review/approval.

# Phase 3.3 — automatic-close capability preparation

13 November 2026. **GO for owner review and separately consented allocation;
NO-GO for an order during this development task.** The next attended capability
test is prepared, not performed. No broker order, cancellation, protection change,
closure, OAuth flow, bot activation or service change occurred in this task.

**512 tests passed in 44.24 seconds** in the existing project-local Python 3.11
`.venv`, including 24 new Phase 3.3 cases. No dependencies or dashboard features
were added. Automatic closure is tested offline, not yet verified on the real
Razor broker account.

## Real evidence retained

The first trade's preserved evidence establishes BUY 0.01 lot at 3876.83, native
SL 3874.83 / TP 3880.83, subsequent manual full closure and $0.04 commission on
each side. Its native SL/TP child inherited the entry clientOrderId; role-aware
reconciliation handles that observed behaviour. See
[the first-fill handover](razor_first_fill_handover.md), its private evidence and
[the sanitized fixture](../tests/fixtures/razor_first_fill.json).

The original journal was inspected **read-only** during this task: one 2106 entry,
ACCEPTED acknowledgement, send consumed. It remains permanently spent. Its
historical SUBMITTED status was not rewritten to simulate successful automatic
closure. Existing configuration, approval, tokens, observers and monitoring
history were preserved. No real `capability-tests.sqlite` exists and no second
test has been allocated. The actual CLI status returned NO_ALLOCATIONS.

## Implemented boundaries

`ctrader_execution/allocations.py` supplies a private durable registry;
`ctrader_execution/capabilities.py` supplies its minimal attended CLI. Both reuse
the existing execution codecs, TradeClient, OneTest, risk checks and view reader.
The simulated engine/observation adapters remain fenced; no strategy routing or
production factory was enabled.

- Every new allocation receives a unique immutable UUID, a digest-bound immutable
  approval and its own durable `capability-<UUID>.sqlite` journal. Its unique entry
  slot is never released by rejection, closure, expiry or recovery.
- The existing first journal is neither renamed nor reset. Initial registry
  enrollment requires fresh, complete view-only history with positive matching
  opening and closing deals, no remaining account exposure and a known consumed
  legacy entry. UNKNOWN/SUBMITTING/rejected legacy mutations block enrollment.
  Its manual-close classification and selected evidence are recorded separately
  in the new registry after explicit owner consent; the old journal stays intact.
- One unresolved allocation is allowed. A database uniqueness constraint and
  owner lock block overlapping allocations/sessions. PREPARING, PREPARED, ACTIVE,
  FAILED and FAILED_FLAT all block another allocation. Only a positively verified
  automatic-close success permits another separately consented test.
- Closed historical journals/approvals are fingerprinted and must remain present
  and unchanged. Orphan journals/approvals, missing registry history or modified
  evidence block operation; deleting the registry does not make old test files
  disappear or authorize replacement tests.
- Journal, renewal and registry paths share an OS execution fence. A per-user
  owner-only `/tmp/autotrader-owner-<uid>/<profile-hash>.owner` lifetime lock also
  excludes concurrent copies of the same approved pinned profile across private
  directories. The canonical private-root owner and per-journal locks remain.
  The registry also locks the original legacy owner file for compatibility with
  an older process that has not adopted the new account fence.
- All maintained paths use the same canonical private profile directory and
  approved application/account binding. This is a **single-host** boundary, not a
  distributed broker lease or protection against an owner intentionally deleting
  or forging private state. Do not copy/relocate state or run another account
  controller. Changing the approved profile/client or moving to another host
  requires separate review, not a second independent registry.
- Consent commits a PREPARING allocation before approval/journal publication.
  A publication crash leaves it blocking, not silently reallocated. No command
  automatically discards it or allocates a replacement.

New approvals retain the first tested fixed plan: BUY MARKET, raw volume 100 /
0.01 lot, $2 relative SL / $4 relative TP, $5 planned-risk cap, $250 native-margin
cap, $0.50 max spread, $0.25 slippage allowance and $0.50 fee buffer. Costs use the
verified $7/lot round-trip basis and conservative $0.08 rounded commission for
0.01 lot. Native symbol/volume/stop/cost metadata, actual/intended capital and
fresh expected margin are revalidated. No synthetic margin assumptions or higher
limits were installed. All bots must remain STOPPED, entry suspension ON.

Allocation obtains a fresh view-only identity/snapshot/cost check before and
after exact local consent. It closes the connection during input, preventing the
previous stale-socket defect. New tokens/OAuth are not requested. Actual execution
uses only the existing separate demo trading store, revalidating remote scope,
pin, broker, USD/HEDGED mode and token expiry. Expired tokens fail closed; they
are never refreshed or broadened automatically.

## Automatic close, positive proof and private costs

The existing attended runner recognizes the positive entry fill, verifies actual
native SL/TP, then requests one full remaining-volume close and reconciles flat.
Native SL/TP children sharing a client ID remain distinct from entry candidates.
Pending children cannot be ignored when concluding the account is flat.

Closing ORDER_ACCEPTED (2), FILLED (3) and PARTIAL_FILL (11) responses are handled
as acknowledgements, not assumed closures. Selected correlated acknowledgement
IDs are persisted before the command outcome. A bounded read-only settlement
loop waits for positive closing history, balanced volumes and disappearance of
positions/pending orders; it does **not** resend the close. Snapshot/deal lag can
be polled without relaxing final reconciliation predicates. An uncertain or
partially completed close still requires operator attention.

A registry CLOSED_SUCCESS requires all of:

1. Complete matching entry/exit history and an empty whole-account book.
2. Recorded verification of native SL/TP on the actual position.
3. One consumed, accepted 2111 close command and its correlated order/deal IDs.
4. The full 100 raw units attributed to that acknowledged close order, not just
   one partial deal followed by somebody else's manual closure.
5. No UNKNOWN/rejected mutation, uncertain-outcome/protection-failure audit or
   partial/emergency failure disguised as normal success.
6. Complete returned monetary precision/commission/gross-profit/swap evidence;
   actual net realized loss within $5 and commission within the reviewed
   $0.08+$0.50 allowance.

Realized gross P&L, signed commissions, swap and net P&L are stored privately.
Commission comes from the actual deal fields with explicit moneyDigits; missing
cost/precision data never defaults to zero. Entry price is compared with the
recorded last pre-submit bid/ask where available. This is a reference-price
comparison, **not** a guaranteed measure of pure execution slippage. Market P&L
is not mislabeled as execution cost. Balances and tokens are excluded from public
status. The `report` command displays the private result in the controlling TTY. It and
status use read-only registry snapshots without acquiring the execution owner, so
historical reports remain available during another managed test. Legacy reports
omit the private raw-ID evidence and retain its manual-close classification.

Historical result details remain in append-only registry events even when a
session enters recovery. Current result publication and final journal fingerprint
commit atomically. Broker acknowledgement correlation is not assumed to be a
broker idempotency guarantee; the consumed send fence prevents resending.

## Restart and failure policy

A restart always uses the same allocation ID/journal. After an entry claim,
`run` cannot create another entry; `resume` is management/reconciliation only,
with explicit local consent and unexpired management authority. SUBMITTING
recovers as UNKNOWN. A sent/UNKNOWN action is never blindly retried.

`resolve` uses **view-only** reads to record positive flat evidence after a failed
or manually resolved test. It does not change that test into automatic-close
success or permit a new allocation: FAILED_FLAT stays blocking. Native-stop exits
and manual closure cannot silently count as successful harness full-close proof.
Any unresolved exposure triggers the existing critical demo-UI procedure. No
compensating order is generated by a diagnostic or view-only command.

Conservative limits: allocation approval is immutable and expires after ten
minutes; management authority lasts one hour. An unused expired allocation,
PREPARING publication crash, FAILED or FAILED_FLAT test requires separate audited
operator review. There is no automatic abandonment, expiry renewal, clearing or
release command for new allocations. Do not delete state or edit IDs to make a
new allocation pass. The old `renew-test` remains limited to the original unused
first journal and cannot renew this already-consumed first test or a new
allocation. Allocate only when ready for the attended test.

## Verification performed

```bash
.venv/bin/python -m pytest -q tests/test_phase33.py
# 24 passed in 0.45s
.venv/bin/python -m pytest -q
# 512 passed in 44.24s
.venv/bin/python -m compileall -q ctrader_execution tests/test_phase33.py
.venv/bin/python -m ctrader_execution.capabilities --help
```

Compileall/help and `git diff --check` passed. The new tests cover consent,
legacy positive closure, unresolved/UNKNOWN/rejected outcomes, overlap rejection,
immutable approvals, missing history/state, orphan files, expiry, pre-dispatch
failure, restart after claim, process death/global owner release, cross-directory
ownership, legacy lock exclusion, allocation publication crash, native protective
children, close accepted before fill, settlement lag, lost close acknowledgements,
no resend, positive close attribution, missing costs and historical reporting.
The earlier simulated-engine, strategy, OAuth, safety and observation regressions
remain passing. Fake sockets use invented identities and never connect externally.

Real automatic close/ACK timing, delayed native protection cancellation, partial
fills, disconnect recovery and fee policy beyond the first observed round trip
remain unverified against cTrader. The 15-second fill observation and 10-second
close-settlement budgets also incur bounded RPC time; they are not wall-clock
availability guarantees. No unattended supervisor or remote alerting was added.

## Exact owner commands for the second supervised test

These commands are provided, **not executed during development** (except the
local status/help checks). Keep the correct Razor DEMO UI available throughout.
The first trade changed actual capital, so refresh/confirm the private baseline
while retaining the $10,000 intended testing budget:

```bash
RAZOR_CONFIG="$HOME/.config/autotrader/ctrader/razor/config.json"
.venv/bin/python -m ctrader_execution.harness baseline \
  --config "$RAZOR_CONFIG" --intended-balance 10000
.venv/bin/python -m ctrader_execution.harness confirm-baseline --config "$RAZOR_CONFIG"
.venv/bin/python -m ctrader_execution.capabilities status --config "$RAZOR_CONFIG"
.venv/bin/python -m ctrader_execution.capabilities allocate --config "$RAZOR_CONFIG"
```

Review the local proposal and give allocation consent only when ready. Copy the
printed **test UUID**, not a broker account identifier, into this variable:

```bash
CAPABILITY_TEST_ID='<UUID printed by allocate>'
.venv/bin/python -m ctrader_execution.capabilities preflight \
  --config "$RAZOR_CONFIG" --test-id "$CAPABILITY_TEST_ID"
```

Allocation/preflight submit no order. If they fail, report the sanitized stage/code
and stop. Initial legacy enrollment/history is bounded to seven days; if evidence
falls outside the supported window, obtain separately reviewed evidence handling
rather than overriding history completeness.

**Only after separate explicit execution approval**, the future owner command is:

```bash
.venv/bin/python -m ctrader_execution.capabilities run \
  --config "$RAZOR_CONFIG" --test-id "$CAPABILITY_TEST_ID" --execute
```

It requires another exact local confirmation, reauthorizes, rechecks freshness,
risk/book/controls, and can submit at most one entry. It should automatically
close and finish flat. Review results afterward:

```bash
.venv/bin/python -m ctrader_execution.capabilities status --config "$RAZOR_CONFIG"
.venv/bin/python -m ctrader_execution.capabilities report \
  --config "$RAZOR_CONFIG" --test-id "$CAPABILITY_TEST_ID"
```

After any uncertainty, inspect the demo UI immediately. To collect flat evidence
without trading, use `resolve --config ... --test-id ...`; this remains a failed
blocking result. If existing exposure needs the previously approved bounded
recovery, the **separately confirmed** future command is `resume --config ...
--test-id ... --execute`, never another `run` or allocation. Expired management
authority/network/history failure requires the operator's demo UI response;
no automatic new authorization is requested.

## Assessment

**Prepared for review and separately consented allocation. No actual test is
allocated or authorized by this handover.** The original first-test journal is
permanently spent and preserved. Real submission remains **NO-GO** until the
owner confirms updated baseline/plan, current native market/margin/book checks,
existing demo trading authorization and explicit second-test execution consent.
Actual automatic closure can be claimed only after that test produces the
correlated full close and complete broker evidence described above.

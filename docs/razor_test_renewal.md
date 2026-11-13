# Safe renewal of the unused Razor first test

12 November 2026. **Renewal is implemented and tested; the real permit was not
renewed during this task. No order, OAuth, broker management or bot action.**

## Existing state and why ordinary approval cannot renew it

Read-only inspection still finds the original bound journal: zero entry claims,
zero mutations, two pre-dispatch attention audit rows. It matches the existing
private approval ID/profile; that entry approval is expired. No renewal receipt
table exists in the real journal, confirming this task has not applied a renewal.

`approve-test` generates a new approval ID. Reusing it would replace the file but
leave the journal bound to the old ID; `Journal` correctly rejects the mismatch.
It now explicitly reports RENEWAL_REQUIRED_FOR_EXISTING_JOURNAL instead of
allowing that accidental overwrite. There is no automatic binding migration or
journal reset. An existing journal with any entry remains permanently spent for
new entries, regardless of rejection, closure, timeout or absence in a snapshot.

## New narrowly scoped renew-test command

The owner invokes it; the agent did not supply consent or run it on the real
profile. It preserves the **same approval ID, profile, canonical journal, policy,
quantity, side/type, risk/margin/spread/cost buffers, UI evidence and one-entry
lifetime limit**. Only issue/expiry/management times and a renewal receipt change.
It renews only an expired entry permit, for ten minutes; management lasts one
hour. A still-current permit reports RENEWAL_NOT_REQUIRED. No new entry slot,
mutation row or execution recovery is created.

Requirements before renewed consent:

- Existing private approval passes schema/profile/native-policy validation at its
  original issue time; it must now be expired, not future-dated or malformed.
- Baseline remains valid and agrees with the old confirmed/intended capital;
  routine renewal cannot change the funding policy.
- Existing initialized journal and existing owner-lock file, safe private
  permissions, exact original profile/approval-ID binding.
- Zero **all mutation records**, zero execution observations, and no audit evidence
  beyond recognized pre-dispatch attention/interruption and prior renewal events.
  A SUBMITTING/UNKNOWN/rejected/closed claim, management-only attempt, send-fence
  audit, conflicting binding or unexplained evidence blocks renewal. Broker
  absence is never used to override durable evidence.
- The same execution-owner lock plus a write reservation excludes concurrent
  execution/renewal during checking, user consent and publication. No Journal
  constructor/schema initializer is used and no missing journal is created.
- Strategies remain stopped/suspended. Existing view-only credentials revalidate
  the demo identity and obtain fresh native specs, quote/margin, full snapshot
  and complete recent history. Nonempty current exposure or recent order/deal
  history requires review, not renewal. Fresh API commission fields must still
  match the original verified USD_PER_LOT basis and explicit zero USD minimum;
  missing or changed fee metadata blocks, with no zero defaults.
- The owner sees the unchanged proposal and gives a new exact hidden local
  confirmation. Actual rounding/posting remain unobserved; consent concerns the
  existing reviewed conservative allowance and normal/emergency management plan.
- The read connection closes during input. A new view-only session rechecks
  identity, market, book, baseline, costs, controls, native margin/risk and all
  freshness limits after consent. No bound is increased to make renewal pass.

If successful, a private `approval_renewals` receipt and ENTRY_APPROVAL_RENEWED
append-only audit event commit to the **existing** journal before atomic permit
replacement. The receipt stores old/new permit snapshots and digests privately,
not tokens or credentials. Existing binding, audit rows, observations, mutations
and unique entry constraint are untouched. The journal inode is retained.
`validate_test` requires any renewed permit's exact digest to match its durable
receipt, so file-only renewal/tampering cannot silently authorize use.

Crash ordering is fail closed: before receipt commit, the expired permit remains;
after commit but before publication, it still remains expired. After atomic
publication, the new permit already has a durable consent receipt. A write failure
may leave a receipt/audit without a published permit; it does not create an entry
or reset any state. Inspect status/preflight rather than assuming a failed command
saved nothing. Reattempting renewal still requires fresh consent and zero-attempt
proof. There is no automatic retry or recovery execution.

This is the existing single-host, owner-controlled-files boundary. Do not delete,
copy, edit or relocate approval/journal files to evade it. Ordinary expiry and
claim guards remain in force when a future execution opens the original journal.

## Focused verification

```bash
.venv/bin/python -m pytest -q tests/test_approval_renewal.py tests/test_execution_diagnostics.py tests/test_approval_input.py tests/test_execution_status.py tests/test_baseline_confirmation.py tests/test_phase31.py
# 137 passed in 1.24s
```

25 renewal cases plus the existing 112 focused cases. Coverage includes expired
versus current permit, fresh consent/decline, pre-dispatch audit preservation,
unchanged ID/binding/inode, owner/database locks, any claimed/unknown/consumed or
completed attempt, management-only evidence, binding conflicts, unexplained audit,
changed baseline/costs, missing minimum metadata, spread/margin gates, crash before
or after atomic publication, receipt tampering, read-only preflight after renewal,
and the retained unique one-entry limit. Successes used synthetic private
fixtures/fake sockets only. Compileall and diff whitespace checks passed; no
full-suite or real renewed-authorization claim.

## Exact safe owner procedure

From the AutoTrader repository, in the owner's private controlling terminal:

```bash
.venv/bin/python -m ctrader_execution.harness status \
  --config "$HOME/.config/autotrader/ctrader/razor/config.json"
.venv/bin/python -m ctrader_execution.harness renew-test \
  --config "$HOME/.config/autotrader/ctrader/razor/config.json"
.venv/bin/python -m ctrader_execution.harness preflight \
  --config "$HOME/.config/autotrader/ctrader/razor/config.json"
```

`renew-test` does not submit an order. Give renewed consent only after reviewing
its same-test proposal. Expected success:
SAME_TEST_APPROVAL_RENEWED_PRIVATELY; JOURNAL_PRESERVED; NO_ORDER_SENT.
Then preflight should PASS if current evidence remains valid. Neither command
requests OAuth or performs an order operation. Use the existing view/trading
stores; no token is copied or broadened. Status and receipt inspection are local;
renewal/preflight use only the view token for broker reads.

If any claim, attempted mutation, unexplained evidence, binding conflict or
UNKNOWN result exists, stop: renewal is **NO-GO**, even if the broker book is empty.
Never reset the journal or issue a replacement approval to bypass that decision.
Report only sanitized stage/code, not private terminal contents. A fresh permit
and PASS are still not an instruction to execute: a future actual submission
requires separate explicit authorization and final local consent. **Do not call
run --execute as part of this renewal task.**

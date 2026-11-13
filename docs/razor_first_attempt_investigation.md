# Razor supervised attempt — pre-dispatch investigation

12 November 2026. **No broker entry was dispatched by this harness during the
reported attempts.** No execution/recovery CLI, OAuth, order approval creation,
broker mutation or strategy startup was performed during this investigation.
Private state was inspected read-only and preserved. Evidence:
[razor_first_attempt_evidence.json](razor_first_attempt_evidence.json).

## Durable evidence and broker reconciliation

Existing private journal: PRESENT, NOT_STARTED; entry claims=0, mutations=0,
sent/consumed flags=0, observations=0; submission-claim audit count=0 and send-fence
audit count=0. Profile and approval-ID bindings match the private configuration
and existing approval. The only recorded diagnostics were two
CLI_ATTENTION_REQUIRED audit entries; these did not retain exception causes.

In the actual implementation, `OneTest.run` must commit `Journal.claim` before
calling `TradeClient.mutate`. Mutate requires the entry claim, and the wire must
consume the matching durable send fence before writing mutation bytes. There is
no fallback unclaimed order-send path in this harness. Zero claims and zero
mutations therefore establish no entry was transmitted **through this harness**,
assuming the preserved original journal and normal durability boundary. This is
not a claim about unrelated/manual broker clients, and it is not inferred merely
from an empty account book. No record was deleted or reset during inspection.

The pinned DEMO/USD identity was revalidated with the existing view-only token.
A bounded history window from 22:56:42.683 through 23:08:13.579 UTC covered five
minutes before this approval through the read-only check. Complete order and
deal history each contained zero records; consistent before/after reconciliation
showed zero pending orders and zero open positions. **No exposure was observed.**
Checksums taken privately before/after collection confirmed the journal,
approval and trading-token file were unchanged; token values/hashes were not
exported. No raw broker responses or account identifiers appear in the report.

Classification: local pre-claim failure, not a broker rejection, fill, close or
UNKNOWN transmitted operation. There is no claimed entry to recover and no
positive broker execution evidence. A failure to read this evidence would instead
require UNKNOWN/CRITICAL operator handling, not an empty-book inference.

## Actual stage ordering and demonstrated defect

The implementation's order is:

1. Validate baseline and existing test approval.
2. Initialize/open the bound journal and acquire execution ownership.
3. Load/validate the separate trading token; authorize pinned DEMO broker account.
4. Refresh broker snapshot and initial market/risk checks.
5. Ask for final exact local confirmation.
6. Recheck controls, market data, approval/risk and encode the entry.
7. Commit the unique entry claim, then consume the send fence, then transmit.
8. Correlate acknowledgement and reconcile order/deal/position/protection/closure.

Thus journal creation, ownership and token authorization occur **before** the
final prompt, but the durable entry claim occurs **after** it. A PRESENT empty
journal is consistent with a failure after the prompt and before dispatch.

The final prompt retained an already subscribed broker connection without a
receiver or heartbeat while the user typed. A synthetic reproduction against the
old code paused 12 seconds and queued an old quote: `market_state` then raised
**STALE_QUOTE before any entry claim or broker call**. The precise predicate in
`Reader._event` is `-2 <= wall_time - spot_timestamp <= 10`. A pause beyond the
heartbeat budget can instead raise HEARTBEAT_TIMEOUT (`clock-last_rx > 30`).
This is the same class of interactive-idle defect fixed in approval earlier,
now demonstrated in the execution confirmation path.

The historical CLI_ATTENTION_REQUIRED records cannot prove whether the user's
specific error was stale quote, heartbeat timeout, a declined confirmation or
another pre-claim predicate. No original private input/exception was recorded.
The demonstrated final-prompt defect is fixed; a precise historical exception is
not fabricated from the generic error. The entry count conclusively locates the
failure before claim under this harness's ordering.

## Targeted correction and diagnostics

The final entry prompt now closes the initial session before waiting for consent.
After consent, it rechecks controls, reauthorizes the same pinned account with the
existing token, obtains a fresh snapshot and repeats market/risk/approval checks
before claiming the entry. No stale event is accepted or retimestamped; no
freshness, heartbeat, risk, margin, volume or approval limit was relaxed.
No new token/OAuth is requested. Execution remains separately approved and gated.

`OneTest.stage` identifies initial/post-confirmation authorization and snapshots,
local confirmation, risk validation, durable claim, submission, reconciliation,
protection checks, remainder cancellation and emergency-close reconciliation.
The CLI adds token-validation and journal-ownership stages. Fixed safe codes
cover declines, expiry, stale data, identity mismatches, ownership conflict and
uncertain outcomes without exposing exception text, private phrases or IDs.

Execution failure diagnostics inspect durable history **read-only**:

- No entry claim: LOCAL_FAILURE_BEFORE_DISPATCH / NOT_CLAIMED, no_order_attempted.
- Claim with unconsumed send: CLAIMED_NOT_PROVEN_SENT; never silently retry it.
- Consumed send: CONSUMED_NOT_PROOF_OF_TRANSMISSION; distinguish stored rejection,
  accepted acknowledgement, UNKNOWN or completed-history evidence.
- Unreadable/corrupt/locked evidence: UNKNOWN / EVIDENCE_UNAVAILABLE, inspect the
  pinned demo UI immediately. Never assert no-send if inspection fails.

Accepted acknowledgement alone does not mean filled or protected. A protection
failure/partial fill/emergency-close failure still invokes the existing bounded,
explicitly approved policy only during a future authorized execution; none was
invoked here. Future execution failures preserve the existing generic attention
audit and print sanitized stage/code/evidence, rather than misleadingly telling
a pre-claim failure it has an UNKNOWN order.

## Tests and safe recovery decision

```bash
.venv/bin/python -m pytest -q tests/test_execution_diagnostics.py tests/test_approval_input.py tests/test_execution_status.py tests/test_baseline_confirmation.py tests/test_phase31.py
# 112 passed in 0.78s
```

Twelve new focused cases cover real code paths with synthetic broker/socket data:
12/35-second final prompts, no reuse of old traffic, fresh remote identity checks,
confirmation decline, expired token/entry permit, account mismatch, no-claim
classification, claimed versus consumed sends, accepted/rejected/closed evidence,
and unreadable/private error redaction. Tests do not invoke the execution CLI or
use real credentials. Compileall and git diff --check passed. No full-suite claim.

The existing journal is **unspent**, not permanently consumed and not recovery-only.
`resume` is not appropriate with zero entries: there is no owned entry to recover.
It remains bound to the existing approval ID. Eligibility is not authorization:
a separately authorized first attempt still requires a current matching permit,
all checks and final local consent. The existing entry permit was valid at the
23:10:59 UTC local read, but its ten-minute lifetime may expire during review.
The final read confirmed the entry permit has now expired; management authority
remains current. Management authority cannot authorize a new entry.

If the permit expires, **NO-GO** until a separately reviewed renewal can preserve
that original unspent journal and its ownership/binding guarantees. The current
approve-test command creates a new approval ID; an existing journal will reject
that new binding. Do not delete/reset/recreate the journal, hand-edit approval IDs
or overwrite permits to evade this guard. This scoped investigation did not add
a renewal feature or issue another approval.

**Immediate safe next command (local, no network or mutation):**

```bash
.venv/bin/python -m ctrader_execution.harness status \
  --config "$HOME/.config/autotrader/ctrader/razor/config.json"
```

No emergency broker action is indicated by the completed read-only evidence.
If the owner sees any discrepant pending/open exposure in the Razor demo UI,
stop and treat it as CRITICAL pending read-only reconciliation; do not infer
ownership or submit a compensating order automatically. **NO-GO for any retry in
this task.** The demonstrated defect is corrected; a future test needs separate
explicit authorization and a valid bound permit, not a blind retry.

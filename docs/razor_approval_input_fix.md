# Razor approval input / preflight fix

12 November 2026. No OAuth, private approval creation, execution, journal reset or
bot/service startup was performed. Real private status remains token-store
present, no test approval, journal ABSENT, zero entry claims.

## Diagnosis

Two confirmed defects:

1. `approve-test` collected a market snapshot **before** hidden UI input. `prepare`
   required that snapshot to be at most five seconds old. A normal human delay
   could therefore reject otherwise correct input as STALE_TEST_PREFLIGHT. A
   deterministic 12-second input-delay regression reproduces this condition;
   the fixed path refreshes read-only market data after input/evidence consent.
   It does not widen the freshness limit.
2. The catch-all hid all approval/input failures behind execution-UNKNOWN/journal
   advice. `preflight` then tried reading the absent approval, producing the same
   misleading message. No approval is saved when input/validation/consent fails.

The original hidden input and underlying exception were not recorded. It cannot
be determined retrospectively whether that specific attempt additionally had
empty input, the wrong field count, malformed numbers, EOF, a mismatch or another
failure. No raw input logging was added to resolve that uncertainty. Current
private state proves only that no approval persisted and no entry was claimed.

## Changes

`read_ui_facts` explicitly distinguishes UI_INPUT_EMPTY, UI_INPUT_FIELD_COUNT,
UI_INPUT_MISSING_VALUE, UI_INPUT_INVALID_NUMBER, UI_INPUT_EOF and missing terminal.
It accepts six comma-separated finite numeric facts, with no defaults. Commission
and stop fields cannot be omitted or substituted from the $3.50 label. The minimum,
maximum, step and contract values continue to be checked against fresh API data.

Explicit evidence consent now precedes marking the commission basis verified:
round-trip basis, minimum charge/rounding at 0.01 lots must be independently
verified and covered by the reviewed allowance; stop-distance floor must be
reviewed. Unknown evidence means cancel. This consent does not save an approval;
the final separate exact local test confirmation is still mandatory. No actual
approval was created by this task. The first-test journal guard remains read-only.

Approval/preflight failures report fixed stage/code fields, execution_enabled=false
and no_order_attempted=true. Stages distinguish baseline, controls, existing
journal, read-only market checks, input, evidence confirmation, UI/API/risk
validation, final confirmation, persistence and approval validation. Errors include
API_UI_SPEC_MISMATCH, OWNER_APPROVAL_EXPIRED, BASELINE_CONFIRMATION_STALE,
PRIVATE_FILE_MODE_REQUIRED and PRIVATE_FILE_ACCESS_DENIED. Exception text, private
answers, confirmation phrases, tokens and broker payloads are not printed.

A missing approval makes non-executing preflight return AWAITING_TEST_APPROVAL /
TEST_APPROVAL_MISSING without any network call. Existing uncertain journal evidence
instead remains UNKNOWN and unchanged. Status is still the read-only source for
historical outcomes; these commands do not recover, reset or resend anything.

## Verification

```bash
.venv/bin/python -m pytest -q tests/test_approval_input.py tests/test_execution_status.py tests/test_baseline_confirmation.py tests/test_phase31.py
# 94 passed in 0.71s
```

Nineteen new cases cover parser success/empty/malformed/missing/nonfinite fields,
EOF, input delay and refreshed evidence, successful synthetic explicit consent,
unknown-cost/declined consent, UI/API mismatch, permission failure, expired
approval, missing approval and preservation of uncertain history. Synthetic
command tests exercise approval creation only in disposable test directories;
no real private approval or broker mutation occurs. Existing 75 focused cases
also pass. Compileall and git diff --check passed; no full-suite claim.

Actual non-executing private-profile command:

```bash
.venv/bin/python -m ctrader_execution.harness preflight \
  --config /home/SJH/.config/autotrader/ctrader/razor/config.json
```

Exit 1 (preflight is incomplete), safe output:

```json
{"state":"AWAITING_TEST_APPROVAL","stage":"TEST_APPROVAL_READ","code":"TEST_APPROVAL_MISSING","execution_enabled":false,"no_order_attempted":true}
```

Status remains AWAITING_TEST_APPROVAL, journal ABSENT, zero entry claims. Existing
private approvals/journals were not deleted, replaced or recreated.

## Safe next step

Confirm the account-specific commission round-trip basis/minimum charge/rounding
and reviewed stop floor first. Known min/max/step 0.01/50/0.01 lots, 100 oz/lot,
$3.50 label and zero displayed stop distance are retained as evidence; the label
alone cannot supply the unresolved cost basis. Zero stop minimum is not a promise
that broker protection will be accepted. Do not guess missing values.

When that evidence is established, the owner can retry the non-trading approval
command in the private local AutoTrader terminal:

```bash
.venv/bin/python -m ctrader_execution.harness approve-test \
  --config "$HOME/.config/autotrader/ctrader/razor/config.json" \
  --controls-database .phase3/ops.sqlite --side BUY --order-type MARKET
```

This can save an approval only after successful evidence validation and explicit
owner consent; it cannot submit an order. It was not run against real data during
this fix. Do not invoke run --execute as part of diagnosis. Stop on any new stage/
code and report only that sanitized diagnostic, never private terminal contents.

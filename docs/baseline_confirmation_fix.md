# Razor baseline confirmation fix

12 November 2026. Scope: terminal confirmation and safe baseline diagnostics only.
No trading OAuth, order approval, execution, bot startup or service change.

## Confirmed root cause

`ctrader_execution/approvals.py:local_confirmation` opened `/dev/tty` using `r+`.
Python's buffered read/write mode requires a seekable stream. A real terminal is
not seekable: an isolated pseudo-terminal reproduced `UnsupportedOperation` before
the user could answer. That became `LOCAL_OWNER_TTY_REQUIRED`, then the CLI's
catch-all hid it behind misleading execution/journal/retry advice. There was no
order attempt and this was not an UNKNOWN broker outcome.

Changed the prompt stream to write-only `w`; `getpass` still reads directly from
the controlling terminal with echo disabled. Exact typed consent is still required;
no stdin/pipeline fallback was added. The same defective stream opening in the UI
facts prompt was corrected; that approval command was not invoked.

`confirm-baseline` now reports only whitelisted reason codes and one of these
stages: CONFIGURATION, BASELINE_PENDING_READ, BASELINE_READ_ONLY_VERIFY,
BASELINE_LOCAL_CONFIRMATION, BASELINE_PRIVATE_PERSISTENCE. Failure includes
`NO_ORDER_ATTEMPTED`, without execution journal/retry advice. Provider text, private
paths, account IDs, balances and confirmation values are never interpolated into
these diagnostics. Interrupts also receive baseline-specific handling.

The pending file's owner-only permissions and profile binding passed inspection;
its intended capital matches the approved $10,000. No real confirmation record
existed at diagnosis. This was not a pending-file permission or expiry failure.
Confirmation refreshes broker identity/balance through the existing view-only
client. Pending profile/numeric validation is now explicit. It validates the
result before private atomic persistence and validates a readback afterward.
Confirmed baseline validity remains 24 hours, with future/stale times rejected.
An old pending observation cannot substitute for the fresh broker check.

## Focused verification

```bash
.venv/bin/python -m pytest -q tests/test_baseline_confirmation.py tests/test_phase31.py
# 59 passed in 0.34s
```

Nine new cases cover a real controlling pseudo-terminal, hidden input/no answer
echo, successful complete command/private record persistence, missing file, unsafe
permissions, wrong profile, missing terminal, declined confirmation, denied write,
network failure and stale/future confirmation times (some checks share a case).
The existing 50 transport/safety cases also passed. Tests use synthetic accounts
only; no real credential is accessed. No new journal/token/test approval is
created by baseline confirmation. No full-suite claim is made for this fix.

## Owner interaction and real persistence

Run in the owner's private local AutoTrader terminal:

```bash
.venv/bin/python -m ctrader_execution.harness confirm-baseline \
  --config /home/SJH/.config/autotrader/ctrader/razor/config.json
```

Complete the exact hidden confirmation shown there; do not paste its account
identifier, phrase or terminal contents into chat/logs. Expected public result:
`BASELINE_CONFIRMED_PRIVATELY; NO_EXECUTION_PERMISSION`.

This creates `baseline-confirmed.json` in the private Razor directory with 600
permissions, pinned profile, fresh actual/intended capital and confirmed_at. It
neither grants trading permission nor creates an order journal. The interactive
path and persistence are verified with a synthetic PTY; actual owner confirmation
and real-file readback must be verified separately after the owner completes it.
No agent-generated answer is used to manufacture owner consent.

## Remaining account-specific evidence

The previously observed UI minimum 0.01 lots, maximum 50 lots, 100 oz/lot and
$3.50/lot commission label are retained; they need not be re-entered to diagnose
or confirm the baseline. Separate future execution verification still needs:

- Commission: whether the $3.50 label is per side (consistent with $7 round trip),
  any minimum charge and actual 0.01-lot rounding. API precise minimum is zero;
  that alone does not prove billed rounding.
- Volume step: UI confirmation of the API's 0.01-lot increment on this account.
- Instrument leverage: matching XAUUSD UI leverage, distinct from account maximum.
  Sampled margin implies about 1:20; raw dynamic leverage 2000 is not interpreted
  as literal 1:2000.
- Stops: matching-account minimum SL/TP distance and units/reviewed stop floor.
  API 0/0 POINTS does not guarantee that the proposed $2/$4 native protection will
  be accepted. Acceptance/protection must be verified in the separately approved
  bounded demo test; no order is needed or permitted in this diagnosis.

Baseline confirmation remains separate from all those specification checks,
trading OAuth and test approval. Preserve all existing execution gates.

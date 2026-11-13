# Razor status command fix

12 November 2026. Narrow local inspection fix; no broker connection, OAuth flow,
order approval, entry, management request or strategy startup.

## Root cause

`harness status` incorrectly passed through the baseline/test-approval execution
prerequisites. With no `first-test-approval.json`, its private read raised
FileNotFoundError, which the generic handler converted into misleading execution
uncertainty/journal advice. Private inspection confirmed the trading token store
exists with owner-only permissions, while approval and journal are absent. This
is the expected post-OAuth/pre-test state, not an uncertain order.

The old status branch also constructed `Journal`: that constructor creates files
and schema, binds an approval and changes SUBMITTING rows to UNKNOWN on recovery.
Those operations are appropriate for an execution owner, not read-only status.

## Fix

Status now branches before baseline/approval expiry gates and never constructs
Journal or loads a trading token. `ledger.read_status` uses SQLite `mode=ro`,
query_only and a consistent read transaction, then closes the connection. It
checks private file permissions and profile binding. Missing files stay missing;
no schema creation, recovery update, approval write, ownership-lock creation or
entry claim occurs. Token presence is metadata only, not a remote authorization
or token-validity claim. No token/account values appear in output.

States follow durable evidence:

- NOT_STARTED: no entry evidence. Journal is separately ABSENT, UNINITIALIZED or
  PRESENT; approval presence is reported separately.
- AWAITING_TEST_APPROVAL: no entry evidence, local trading-token store present,
  no order approval. This does not itself authorize execution.
- UNKNOWN: stored SUBMITTING/UNKNOWN entry or management outcome; original rows
  remain unchanged. Status does not perform recovery or resend.
- SUBMITTED: stored accepted acknowledgement; not inferred to be a fill.
- EXECUTED: stored CLOSED entry plus HISTORY_CONFIRMED_FLAT audit evidence.
  This describes the journal, not a fresh broker reconciliation.
- REJECTED/RECONCILED: corresponding stored entry outcome; entry slot stays spent.
- UNCONFIRMED: closed label without the required history-confirmation audit.
- UNAVAILABLE: unreadable, locked, corrupt, mismatched or unsafe local evidence,
  with a fixed sanitized reason code. Never confused with a broker UNKNOWN or a
  new/empty journal. Nothing is reset or replaced to make inspection succeed.

## Verification

```bash
.venv/bin/python -m pytest -q tests/test_execution_status.py tests/test_baseline_confirmation.py tests/test_phase31.py
# 75 passed in 0.64s (16 new status cases, 59 existing focused cases)
.venv/bin/python -m ctrader_execution.harness status \
  --config /home/SJH/.config/autotrader/ctrader/razor/config.json
```

Actual private-profile result, exit 0:

```json
{
  "journal": "ABSENT",
  "entry_claims": 0,
  "mutations": [],
  "state": "AWAITING_TEST_APPROVAL",
  "execution_enabled": false,
  "test_approval_present": false,
  "trading_token_store_present": true
}
```

Tests cover pre-approval/post-OAuth, absent/empty journals, expired/missing
approvals, simulated uncertain entry/management, accepted versus executed,
rejection, corrupt/schema/profile/permission failures and database locks. Byte
comparisons verify existing databases remain unchanged; connection/initializer/
token-loading paths are forbidden in status tests. Compileall and diff whitespace
checks passed. No full-suite result is claimed for this scoped fix.

No OAuth renewal or trading action is required to use status. All execution
approval, demo/account/identity, ownership, native-risk and one-entry gates remain.

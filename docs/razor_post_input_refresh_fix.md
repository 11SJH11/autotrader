# Razor post-input refresh diagnosis and fix

12 November 2026, real read-only reproduction at approximately 21:58 UTC.
No approval/journal creation, OAuth request, trading-token access, broker mutation
or bot/service startup. Evidence: [razor_refresh_diagnostic.json](razor_refresh_diagnostic.json).

## Exact reproduced failure

The connection had subscribed to quotes before interactive UI input. No receive
loop or heartbeat runs while the main thread waits for input. After a 12-second
pause, refreshing that same real view-only connection raised **STALE_QUOTE**.

The failing predicate is `Reader._event` for spot events:
`-2 <= wall_time - quote_timestamp_seconds <= 10`.
An old queued spot event exceeds the ten-second age bound before fresh snapshot
requests can finish. This is not rejection of the entered specifications or of
$2/$4 protection. For a longer pause, `Reader._heartbeat` can additionally reject
`clock - last_rx > 30` as HEARTBEAT_TIMEOUT. Both previously fell outside the
approval diagnostic whitelist and became APPROVAL_OR_PREFLIGHT_VALIDATION_FAILED.

The earlier generic error did not retain its underlying reason; this cannot prove
whether that particular past attempt hit stale-quote or heartbeat timeout. The
real reproduction establishes STALE_QUOTE as a concrete failing post-input path;
a deterministic test also covers a 35-second pause. New diagnostics preserve both
codes instead of guessing which occurred.

At READ_ONLY_MARKET_REFRESH no test approval exists yet and the UI/API/risk
validation has not run. Approval expiry, UI/API mismatch, risk ceiling and spread
cap validation occur at their own stages; they cannot explain this reproduced
reader failure. Fresh identity, book, commission and margin data below passed.

## Real read-only results

| Check | Before pause | Fresh connection afterward |
|---|---:|---:|
| Quote age | 0.052s | 0.550s |
| Spread | $0.17 | $0.17 |
| BUY expected margin | $193.90 | $193.87 |
| SELL expected margin | $193.89 | $193.86 |
| Health | HEALTHY | HEALTHY |
| Book | Empty | Empty |
| Trading mode | ENABLED (0) | ENABLED (0) |

Spread passed $0.50; native margin passed $250. The returned weekly schedule was
open at the sampled time, in America/New_York; holiday overrides were not
independently evaluated. Fresh quotes and successful reads support an active
session then. A market closure or excessive spread/margin was **not** the
reproduced cause. This is a timestamped observation, not assurance the session
stays open or quotes remain fresh when the owner retries.

Rate=350000000 (USD_PER_LOT), precise minimum=explicit 0, minimum type=CURRENCY
were reconfirmed. The prior independent commission check established USD asset.
No commission assumption caused the reader's stale-event rejection.

## Small fix, unchanged limits

The approval flow now closes its initial read-only connection before human input
and opens a fresh view-only, pinned-demo identity-checked connection afterward.
This clears obsolete socket traffic through normal connection establishment;
no stale event is accepted, discarded selectively or assigned a fresh timestamp.
The ten-second reader guard, five-second preflight guard, heartbeat policy,
spread/risk/margin caps, identity checks and explicit consent remain unchanged.
No broker order operation exists in this path. No automatic OAuth or approval.

Diagnostic codes now include STALE_QUOTE, HEARTBEAT_TIMEOUT, SNAPSHOT_CHANGED_RETRY,
account/currency/mode/symbol mismatches, response errors and incomplete history.
They are a fixed whitelist; raw error messages, IDs, token values, confirmation
phrases and broker payloads remain excluded. Later spread, margin, specification
and expiry predicates retain their separate stage/reason codes.

## Commission wording corrected

The evidence prompt now distinguishes verified rate/configured minimum and
round-trip basis from **unobserved actual rounding/posting**. It asks the owner
to review the conservative cost allowance and stop floor, not to falsely attest
to observed billing. Saved future approvals explicitly record
actual_rounding_posting_observed=false. None was created here.

For the verified $7/lot round trip and zero configured minimum, the nominal
0.01-lot fee is $0.07. The unchanged proposal budgets $0.08 rounded commission
plus a separate $0.50 fee buffer, and $0.25 slippage: $2.83 planned risk versus
$5 cap. The rounded allowance covers ordinary per-side USD cent rounding under
the verified schedule. It is not a guarantee against arbitrary future fee
changes, slippage, gaps or partial-fill billing. If evidence is missing or the
owner finds the allowance inadequate, consent must be declined; the program
neither increases the allowance nor claims actual broker billing was verified.

## Focused regression results and decision

```bash
.venv/bin/python -m pytest -q tests/test_approval_input.py tests/test_execution_status.py tests/test_baseline_confirmation.py tests/test_phase31.py
# 100 passed in 0.76s
```

Six additional cases cover old queued quote rejection, fresh post-input sessions
after 12/35-second pauses, precise post-input reason codes and corrected wording/
unobserved-posting flag. Existing focused guards continue to pass. Compileall and
git diff --check passed. No full-suite claim is made for this scoped fix.

Actual private status still reports AWAITING_TEST_APPROVAL, approval absent,
journal ABSENT, zero entry claims, execution disabled. Nothing was reset.

**GO to retry approval only while fresh read-only checks pass and the market is
open.** The reproduced idle-socket defect is fixed; no limit was relaxed. If the
market has closed, quotes are stale, spread exceeds $0.50, margin exceeds $250,
specifications/identity differ or a snapshot changes, remain NO-GO and use the
new exact code; do not force acceptance. Confirm only the reviewed allowance and
known evidence. Actual broker execution remains separately gated and untested.

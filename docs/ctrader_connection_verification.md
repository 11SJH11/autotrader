# cTrader connection verification — 12 November 2026

Status: **Confirmed private broker configuration updated; demo view-only account
verification and full read-only snapshot succeeded. Observer running on loopback
port 18769. XAUUSD min/max volume anomaly remains unresolved and blocks any
claim of execution readiness.** See the latest observer section below; earlier
attempts and pending confirmations are historical.

| Requirement | Result |
|---|---|
| Approved application | Owner reports approved; private flag true. Remote application authorization not yet established. |
| Private configuration | VERIFIED owner-only checks; required application fields present, values suppressed. |
| OAuth redirect | Local shape valid; an actual callback passed state validation on retry. Exact provider registration was not independently inspected. |
| View-only OAuth | Authorization URL fixes accounts scope. Callback validation and token exchange completed on retry. Server-reported SCOPE_VIEW remains NOT YET VERIFIED. |
| Demo transport | Only fixed wss://demo.ctraderapi.com:5036 is implemented. Discovery reached grant verification and rejected an unexpected response schema. No live host or account was authorized. |
| Account identity/broker/currency/mode | NOT YET VERIFIED. No explicit local selection was reached. Configured broker is an expectation, not verified identity. |
| XAUUSD bid/ask, specs, volume/stops | NOT YET VERIFIED; no symbol snapshot obtained. |
| Quote timestamps/freshness | NOT YET VERIFIED against broker; offline checks only. |
| Balance/equity/margin | NOT YET VERIFIED; no private financial data published. |
| Pending orders/positions/history | NOT ATTEMPTED in this discovery flow. |
| Heartbeat/reconnect/reconciliation | Offline tests only; no reliability claim from mocks. |

Observed attempts failed closed. Initial output was OAUTH_AUTHORIZATION_FAILED;
stage diagnostics then established GRANT_VERIFICATION, and a subsequent attempt
reported UNEXPECTED_RESPONSE_SCHEMA. Token exchange succeeded before that error.
A separate demo-only application-auth probe then confirmed a successful type
2101 response with matching correlation and omitted payload. Only fixed
field-presence/type booleans were printed. The client now accepts omission
only for this empty acknowledgement; every other response still requires an
object payload, and type/correlation checks remain mandatory. Two regressions
cover success and wrong correlation. Raw responses were never printed or persisted. No account ID was selected/saved and no verified
access token was saved by these failed attempts. No trade request was sent.

The new explicit discovery flow is documented in ctrader_discovery.md. It filters
isLive=false and exact broker, displays broker/login locally, requires a radio
selection plus typed login and consent, revalidates grant/environment/identity,
then saves the API ID and token in private files only. Never infer an API ID from
an MT5 or cTrader displayed login number. No automatic fallback selection exists.

The owner confirmed the exact configured loopback callback is registered.
Fresh accounts-only OAuth was launched after fixing the confirmed empty-ack
compatibility issue. Its final outcome is recorded below. Any further schema
issue must be inspected using allowlisted value-free classification. Do not log raw frames or weaken scope, state,
correlation, broker or environment checks to accommodate an unknown response.
Do not keep repeating browser authorization until the shape issue is isolated.

Before any controlled demo execution: finish explicit demo selection; verify
server-reported scope, broker/account identity, currency/mode, real XAUUSD specs,
UTC timestamps and freshness, complete history and reconciliation; exercise
read-only disconnect/reconnect; collect prospective strategy evidence; separately
review durable execution/risk safeguards and obtain explicit execution approval.
This reader has no trade methods and enabling execution is outside this task.

Official references checked:
- https://help.ctrader.com/open-api/account-authentication/
- https://help.ctrader.com/open-api/sending-receiving-json/

## Confirmed compatibility fix and fresh retry

A real demo application-auth shape probe confirmed response type 2101, matching
clientMsgId and absent payload. This empty acknowledgement now permits omitted
payload only; explicit null/malformed payloads and other missing payloads still
reject. Correlation checks are unchanged. The targeted suite passed 100 cases.
A fresh OAuth retry passed token exchange and grant retrieval, then reported
NO_MATCHING_DEMO_ACCOUNTS. This establishes progress beyond the original schema
failure; it does not establish intended account identity.

Discovery now distinguishes NO_AUTHORIZED_DEMO_ACCOUNTS,
BROKER_IDENTITY_MISMATCH and DISPLAY_LOGIN_MISMATCH. A broker mismatch displays
configured and authorized demo broker names in the local callback page only,
without automatic substitution, account IDs, tokens or log output. The owner
must verify/correct private configuration. Another fresh attempt was launched
with these diagnostics. No security checks were relaxed.

Full regression result after compatibility and safe filter diagnostics:
**244 passed in 29.89s** using .venv/bin/python -m pytest -q. compileall and
whitespace checks passed. This verifies offline behavior, not broker reliability.

Latest fresh authorization result: **BROKER_IDENTITY_MISMATCH** at
GRANT_VERIFICATION. OAuth state validation, token exchange, demo application
acknowledgement, account-list retrieval and explicit view-scope validation passed
far enough to reach exact broker matching. Authorized demo broker names were
shown only in the local browser. No matching account was selected, no account
auth request was sent and no token/config pin was saved. The owner must verify
and correct expected_broker privately before another fresh authorization.

## Owner broker update and identity-only verification

The owner confirmed the authorized demo account-list broker as Pepperstone -
Europe and updated expected_broker privately. A subsequent flow reached local
selection but failed before pin/token persistence (SELECTION_FAILED). Added
fixed selection-stage and allowlisted reason diagnostics and launched a fresh
accounts-only retry; no automatic account selection or configuration substitution
was introduced. Identity and currency remain unverified until successful explicit
selection and the identity-only check.

Added the verify CLI command: after revalidating view-only grant and the demo
pin, it reads trader/assets and returns a pin-match boolean, brokerName and
currency. Raw account IDs and financial values are excluded. A differing
expected_broker_name yields exit 2 without changing configuration or treating a
full snapshot as healthy. The owner must confirm the returned brokerName before
any configuration change. Full suite: 245 passed in 30.42s; after selection-stage
diagnostics the 104 targeted cTrader/discovery tests passed in 0.12s.

The final fresh retry expired with LOCAL_FLOW_NOT_COMPLETED; no successful
confirmation was recorded. No further retries are launched pending owner input.
Final regression result: 245 passed in 30.47s. BrokerName, account identity and
currency have not yet been verified by the identity-only command.

## Final verification after CSRF correction

The cause was Referrer-Policy: no-referrer on the selection form causing a null
Origin on browser form POSTs, conflicting with strict Origin validation. See
ctrader_csrf_fix.md for exact trace, browser reproduction and unchanged security
checks. The selection-form policy is now origin; callback query data stays out
of Referer. No null-Origin exception was introduced.

One fresh actual OAuth attempt completed successfully with
DEMO_SELECTED_VIEW_ONLY. Explicit local selection saved the API account pin,
displayed-login pin and remotely verified accounts-scope token in private
owner-only files outside Git. Local private_read/Token.load validation passed
for both files, including directory/file ownership and modes, saved scope and
current token expiry. No account ID, login, token, secret or raw response is
recorded here.

A separate verify command reauthenticated only the pinned demo account, required
remote view-only scope and read trader/assets. Observed results:

- environment DEMO, read_only true, view_authorized true;
- pinned_account_id_matches true;
- account-list broker matched owner-configured Pepperstone - Europe;
- trader brokerName `pepperstoneuk`;
- deposit currency GBP;
- broker_name_matches_configuration false (exit 2, expected fail-closed result).

The different account-list and trader broker strings are separate API fields,
not grounds for automatic substitution. The owner was asked to confirm setting
expected_broker_name to `pepperstoneuk`; configuration remains unchanged pending
that answer. This identity check does not establish XAUUSD specs, quotes, margin,
positions/history completeness or reconnect reliability. No full snapshot or
trading request was made by this verification command. No trading scope was
requested or accepted.

Final tests: 256 passed in 31.00s; 33 discovery tests passed; the isolated Firefox
old/new policy reproduction passed. No claim of broker reliability is inferred
from offline tests. The actual success above is limited to OAuth, explicit demo
selection, saved private state and identity/currency reads.

## Latest observer verification after owner confirmation

Owner explicitly confirmed expected_broker_name=`pepperstoneuk`. The private
configuration was updated outside Git; expected_broker remains
`Pepperstone - Europe`. Identity-only verification returned exit 0, DEMO,
view_authorized=true, pinned_account_id_matches=true, GBP currency and the
matching detailed broker name. No identifier/token was printed or committed.

The default status port 8768 was already occupied. Its listener was not changed.
Our observer uses http://127.0.0.1:18769/status and was launched with:

```bash
.venv/bin/python -u -m ctrader_readonly observe --config /home/SJH/.config/autotrader/ctrader/config.json --status-port 18769
```

This is a task-owned process, not an installed/enabled systemd service. Existing
Ledger/Hurricane/T02 and other services were not modified or restarted. Status
remains read-only and contains no credentials, identifiers or account finances.
The CLI has no broker trading methods and transport retains its outbound schema
allowlist.

### Actual first successful snapshot

- Demo account mode HEDGED; currency GBP; token scope VIEW_ONLY. Account-owner
  FULL_ACCESS is distinct from OAuth permission and does not permit this client
  to trade.
- XAUUSD bid 3876.79, ask 3876.87 at
  2026-11-12T16:14:53.778000+00:00; measured age about 0.373 seconds at collection.
  These are historical samples, not current-price promises. UTC conversion uses
  broker Unix milliseconds and does not apply MT5/London server offsets.
- Application/grant/account auth, trader/assets, symbol list/detail,
  reconciliation, bounded order/deal history, P&L, expected margin and spot
  subscription operations all succeeded.
- Before/after reconciliation snapshots matched: zero pending orders and zero
  positions. Last-24-hour order/deal history returned zero rows and passed the
  implemented completeness checks. This does not validate non-empty books or
  all historical pagination cases against the real broker.
- Margin information was retrieved but account finances remain memory-only.
  Available/free margin is a derived non-authoritative estimate, not a broker
  guarantee and cannot authorize trading.

| XAUUSD field | Returned/normalized value |
|---|---|
| Price digits / pip position | 2 / 1 |
| Lot size | 100 units |
| Minimum units | 999999999999 — anomalous, unresolved |
| Maximum units | 999999999999 — anomalous, unresolved |
| Step units | 0.01 |
| Stop/target minimum distance | 0 / 0 POINTS |
| Symbol trading mode | ENABLED (metadata only; client trading disabled) |

The official symbol schema expresses volume and lotSize in hundredths of units:
https://help.ctrader.com/open-api/model-messages/#protooasymbol
No MT5 contract specification was copied and no plausible-looking replacement
minimum was invented. Equal enormous min/max volume needs independent broker
clarification or a read-only comparison with the cTrader symbol information UI.
Zero returned stop constraints do not establish actual accepted order protection.
Connection HEALTHY describes implemented read-side liveness/freshness checks,
not execution suitability.

### Repeated refresh defect and correction

The first multi-sample check observed HEALTHY -> DISCONNECTED with
BROKER_REQUEST_REJECTED on spots -> HEALTHY after reconnect. All other recorded
operations succeeded. The code was resubscribing on every periodic snapshot.
Now the reader retains a successful symbol subscription within a connection,
resubscribes after reconnect and rejects a changed subscribed symbol. It does
not send unsubscribe/trade requests. Regression uses a fake broker that rejects
duplicate subscriptions and verifies both repeated refresh and reconnect.
Only our port-18769 process was gracefully stopped/restarted to load this fix.

Full test suite after this change: **257 passed in 29.45s**. Targeted cTrader
suite: 116 passed in 0.18s before adding explicit duplicate-rejection injection;
the final full run includes that injection. Fake-broker tests do not prove
production broker reliability. Original and post-fix sanitized samples are
retained separately in ctrader_observer_verification.json and
ctrader_observer_after_subscription_fix.json. Post-fix check command:

```bash
.venv/bin/python tools/check_observer_status.py
```

Remaining gates before even considering controlled demo execution: resolve real
volume constraints; independently check costs, margin and protection semantics;
exercise nonempty-book reconciliation and reconnects; collect longer prospective
strategy/quote evidence; review durable risk/ownership safeguards; obtain
separate explicit execution authorization. All execution remains disabled.

Post-fix actual result: four HEALTHY samples from 2026-11-12T16:18:41.007925+00:00 to 2026-11-12T16:19:26.011435+00:00. All snapshots, quotes and heartbeats were fresh; UTC quote timestamps advanced. Quote ages were 0.291, 0.262, 0.221 and 0.188 seconds. All last_error values were null. Reconciliation/history remained complete for the requested window. This is a short live read-only observation, not a long-term reliability claim. The observer remains running on port 18769.

## Phase 2.8 continuation

See phase28_handover.md for the exact raw-volume probe, indicative GBP/margin
checks and incremental monitor. The raw response confirms min/max=99999999999900
hundredth-units, not a client scaling error. Matching-account independent volume
evidence is still missing. Execution remains NO_GO. The separate monitor targets
24 hours; its saved cutoff is a short observation and does not establish
completed long-term reliability. Existing observer/services were left unchanged.

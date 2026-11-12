# Read-only authentication longevity — Phase 2.8

## Current implementation and unattended limit

`oauth.Token.from_response` retains access token and expiry only. The provider's
refreshToken is intentionally discarded; it is neither stored nor logged.
`Token.save/load` accept exactly access_token/expires_at/scope=accounts in an
owner-only file outside Git. A token is rejected 30 seconds before expiry.
Each reconnect requires the remote accounts grant to report SCOPE_VIEW and the
pinned account to remain demo-only with matching broker/displayed login.

`Reader.run` retries with bounded backoff but never opens a browser, refreshes a
token or broadens scope. An expired token therefore causes a persistent
reauthorization requirement, not unattended recovery. Revocation can require it
earlier. Phase 2.8 preserves the fixed TOKEN_EXPIRED_REAUTHORIZE reason when the
failure occurs before network I/O. Public status includes remaining seconds and
reauthorization_required without token values. The monitor persists remaining
seconds, so an operator can warn at one day and one hour remaining; no outbound
notification service is configured.

The official authentication guide documents the usual access-token lifetime as
2,628,000 seconds and a non-expiring refresh token. Actual stored expires_at is
authoritative for this client; never assume a new 30-day lifetime after restart.
https://help.ctrader.com/open-api/account-authentication/

## Supported secure renewal now

Deliberate accounts-only OAuth reauthorization remains the supported method.
Use the existing local `authorize` command for a pinned account, or `discover`
only when explicit account reselection is intended. Retain callback state,
selection CSRF/cookie/Origin/Host/expiry protections and the broker/demo pin.
Never paste codes/tokens into chat or a shell command. Reauthorization overwrites
the verified access token atomically in the existing private file; the running
reader uses the new token on its next reconnect. A controlled restart of the
task-owned monitor reloads it immediately and preserves observation gaps.

## Design for optional future unattended refresh — NOT ENABLED

1. Make refresh storage explicit opt-in for this accounts-only application.
   Store the token in an owner-only file or tested local keyring outside Git,
   independently of public status/SQLite/logs. Reject symlinks, wrong ownership,
   modes and unknown schema. Never reuse any old-bot credential store.
2. Have one refresh owner with a durable local lock. Schedule a bounded refresh
   ahead of expires_at with jitter; prevent concurrent refresh/token rotation.
   The only target is the documented fixed HTTPS token endpoint, with TLS,
   no redirects, ambient proxies, HTTP debug logging or URL/error output.
3. Keep the candidate grant in memory. Reject any explicit non-accounts scope.
   On a fresh demo-only connection verify remote SCOPE_VIEW, token echo, pinned
   demo ID, broker and displayed login. A successful HTTP response alone cannot
   make the candidate token usable. Do not authenticate any live account.
4. Atomically replace the private credential bundle only after verification;
   fsync file and directory, with a generation number/CAS against the previous
   bundle. Store any rotated refresh token with the access token in the same
   atomic bundle, never in separately committed files. Reject a stale owner.
5. Lost refresh acknowledgements/one-use refresh rotation create an unknown
   outcome: fail closed and require deliberate OAuth. Do not loop a possibly
   consumed refresh grant or fall back to trading scope/live accounts.
6. On invalid/revoked/unknown scope or any identity/environment change, invalidate
   the candidate and require the owner. Do not change pinned account selection
   or broker configuration automatically. Keep existing verified state only if
   it is still current and remains remotely valid; never mark stale data healthy.
7. Tests must cover expired/revoked tokens, rotation, lost acknowledgement,
   concurrent owners, disk failure/crash before and after replacement, reused
   old generations, wrong account/broker/live/trading scope, and redaction at
   HTTP/client/process/SQLite/dashboard boundaries before enabling refresh.

No refresh endpoint/request type or refresh-token persistence was enabled in
Phase 2.8. Human reauthorization remains necessary for indefinite operation.

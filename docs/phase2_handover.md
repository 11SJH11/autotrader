# Phase 2 — cTrader read-only observer

Date: 2026-11-11. Status: **offline implementation complete; broker authentication
and capability verification blocked pending a confirmed approved application and
new project-specific private configuration.** Approval status was requested but
not supplied during this task. No application approval, account, currency,
broker identity, XAUUSD specification, margin or history was observed from an
actual broker. Do not interpret synthetic fixtures as broker evidence.

## Scope and isolation

Added `ctrader_readonly/`, a separate Python 3.11 JSON/WebSocket observer and
loopback OAuth/status interface. The Phase 0/1 engine and its 49 tests were not
modified. There is no bridge from this reader to execution, no trade method,
no live host selection and no service deployment. Ledger, Hurricane, T02 and
existing services/configuration/credentials were not accessed or changed.
Only public reference pages and package metadata were accessed over the network;
no cTrader authentication or broker connection was attempted.

The fixed broker endpoint is `wss://demo.ctraderapi.com:5036`, using verified TLS.
Proxy inheritance and redirects are disabled. An HTTP 101 upgrade is required
before sending application credentials. The official
[endpoint reference](https://help.ctrader.com/open-api/proxies-endpoints/)
specifies the JSON port and WebSocket support. This uses the documented
[JSON envelope](https://help.ctrader.com/open-api/sending-receiving-json/), rather
than the official Python SDK's Protobuf transport.

`protocol.py` contains a closed outbound schema allowlist for application auth,
granted-account discovery, account auth, trader/assets/symbol metadata, current
reconciliation, bounded order/deal history, P&L, expected margin, spot
subscription and heartbeats. It checks request types and field shapes again at
the actual transport boundary. Trade requests and token-refresh requests are
absent. Numeric types and response correlation/account/symbol identities are
checked; malformed or unexpected responses disconnect without logging content.
Request/response definitions are drawn from the official
[messages reference](https://help.ctrader.com/open-api/messages/), with payload
numbers/enums from the official
[model reference](https://help.ctrader.com/open-api/model-messages/).

## OAuth and private state

The browser authorization URL fixes `scope=accounts`. A returned explicit
non-accounts scope is rejected. Tokens are opaque: before persistence or account
authorization, the demo connection probes the granted-account list and requires
an explicit server-reported SCOPE_VIEW, an exact token echo, the configured
account allowlist match, `isLive=false`, and the expected broker short title.
Missing scope/environment evidence fails closed. A SCOPE_TRADE result closes the
session without account authorization or token storage. An opaque bad grant can
only be identified after this read-only scope probe; it is never used for trades.
The [official authentication guide](https://help.ctrader.com/open-api/account-authentication/)
distinguishes accounts and trading authorization. Account `accessRights` are a
separate concept: an account with FULL_ACCESS can be observed using a VIEW_ONLY
token. NO_LOGIN and unknown rights are rejected.

OAuth binds only `127.0.0.1`, on the exact configured callback port/path. The
server binds before opening the browser. A cryptographically random state,
three-minute deadline, one-use callback, Host validation and duplicate-parameter
checks protect the callback. Responses contain only fixed success/failure text,
with no-store/no-referrer headers. Request URLs, codes and provider error bodies
are never logged. The token exchange uses the documented fixed HTTPS GET token
endpoint without redirect following or ambient proxies. The official endpoint
requires secrets in its encrypted query string; do not add HTTP debugging,
intercepting proxies, request logging or URL tracing.

**Unverified OAuth compatibility:** the official guide does not document whether
custom `state` is echoed or whether this particular loopback redirect URI is
accepted at registration. Both must be verified with the approved application.
A missing state must remain a failed callback; do not remove CSRF checks as a
workaround. No PKCE or undocumented token introspection endpoint is assumed.
Only the access token and expiry are persisted after verification; the returned
refresh token is discarded. Expiry/revocation requires deliberate reauthorization,
not an automatic browser or refresh flow. Scope is verified again on reconnect.

Configuration and tokens must be outside this repository and any actual Git
working tree, in an owner-only 0700 directory with 0600 files. Symlink paths and
unknown configuration keys are rejected. Reads use O_NOFOLLOW and check owner,
type, size and mode. Token writes use a 0600 temporary file, fsync, atomic replace
and directory fsync. State is plaintext protected by local filesystem permissions,
not a keyring or encrypted vault. The CLI accepts only a configuration **path**;
never pass a token or client secret on the command line. No existing private
configuration was searched for or read during implementation.

## Later local setup, after approval

Create a **new** private directory outside Git using a local editor; do not reuse
existing services' credentials. The following is a template, not valid config:

```json
{
  "application_approved": false,
  "scope": "accounts",
  "client_id": "ENTER_NEW_PROJECT_CLIENT_ID_LOCALLY",
  "client_secret": "ENTER_NEW_PROJECT_SECRET_LOCALLY",
  "redirect_uri": "http://127.0.0.1:18768/oauth/callback",
  "demo_account_id": null,
  "expected_broker": "ENTER_EXACT_CTRADER_BROKER_SHORT_TITLE",
  "expected_broker_name": "ENTER_EXACT_CTRADER_TRADER_BROKER_NAME",
  "token_path": "/absolute/private/directory/tokens.json"
}
```

Supply the actual **cTrader Open API** account ID as an integer locally, not an
MT5 ID or a cTrader display login. The account must already be explicitly pinned;
there is no broad account-dump endpoint. Broker short title and trader broker
name are matched separately; the latter defaults to expected_broker if omitted.
Set application_approved=true only after approval and callback registration are
confirmed. Mode/owner checks are enforced before browser or broker activity.

These commands are implemented but **were not executed** with private state:

```bash
.venv/bin/python -m ctrader_readonly authorize --config /absolute/private/directory/config.json
.venv/bin/python -m ctrader_readonly observe --config /absolute/private/directory/config.json
# Read-only, local browser: http://127.0.0.1:8768/status
```

No persistent service is installed. Stop the foreground observer with Ctrl-C.
Application approval is an explicit local gate, followed by actual server app
authorization; the configuration flag alone is not proof of provider approval.

## Reads, monitoring and status

A refresh validates broker name, account permissions/mode and deposit currency
against returned asset metadata. XAUUSD (or XAU/USD) must be unique and backed by
XAU/USD asset IDs. Other suffixes/ambiguous symbols fail rather than guessing.
The observer reads minimum/maximum/step volume, lot size if supplied, price/pip
precision, symbol trading mode and stop/target distance values with their stated
unit. Integer volume fields are converted from hundredths of units, never
assumed to be MT5 lots. Stop values are retained with their unit; no order-sizing
or stop-placement conversion is offered.

Current orders/positions and a default rolling 24-hour history are memory-only.
History may be requested for at most seven days. There is no invented page cursor:
when hasMore=true, the client recursively splits inclusive millisecond windows
and requires every subwindow to be complete. Missing completeness flags, a
saturated single millisecond, exhausted 64-request budget, conflicting records
or inconsistent timestamps block the snapshot. Completeness covers that time
window only, not lifetime history. Before/after reconciliation snapshots and
execution/account/symbol events detect changes during reads. They do not prove
a server-atomic snapshot or recover events that the network never delivered.

Expected margin for the symbol's minimum volume, balance, per-position used
margin and unrealized P&L are retrieved and scaled with their own moneyDigits.
Missing required evidence fails closed. `balance + net unrealized P&L - used
margin` is retained only as a **non-authoritative estimate**: bonuses, credit,
account margin rules and timing can change actual available margin. No financial
values, account IDs, order/position/deal IDs, labels or raw responses are exposed
by status or persisted. Status reports only whether margin information exists.

One serialized I/O owner correlates each request by connection epoch and sequence.
A response timeout is five seconds; an old/mismatched response disconnects.
There are at most 10 total requests and 2 history requests per rolling second.
The observer sends heartbeats every ten seconds and disconnects after 30 seconds
without valid inbound traffic. It reconnects with exponential 1–60 second backoff
plus jitter and reauthorizes/resnapshots completely. The
[connection guide](https://help.ctrader.com/open-api/connection/) documents heartbeat
behavior; these timeout/backoff choices are local policy, not broker guarantees.

Refreshes run every 15 seconds or after an event marks data dirty. A snapshot
older than 30 seconds is stale. Both quote sides need independent receipt and
server timestamps no older than ten seconds; a fresh bid cannot freshen an old
ask. Future, out-of-order, crossed and mismatched quotes fail closed. Idle/closed
markets can therefore display DEGRADED. No trade execution depends on this data.

The loopback GET `/status` projection contains health, freshness/completeness
flags, fixed error codes, broker/currency/account-mode/access metadata, XAUUSD
specifications and entity counts. Retained metadata/counts after disconnection
are historical; freshness flags remain false. No write routes exist. The server
has a short per-connection timeout, no access logs and no remote bind option.
It is not a remotely authenticated API. Credentials/tokens and raw provider
exceptions are excluded from response data and CLI error output.

## Commands and observed results

Used existing project-local CPython **3.11.17**. Installed pinned
websocket-client **1.8.0**, whose package metadata requires Python >=3.8, into
`.venv`. Its
[release metadata](https://pypi.org/project/websocket-client/1.8.0/)
includes Python 3.11 support. This is a small JSON transport dependency, not an
execution SDK. Simulator runtime dependencies remain unchanged; the optional
ctrader extra and full test requirements pin the dependency. No external Ledger
virtual environment is used.

```bash
uv pip install --python .venv/bin/python -r requirements-ctrader.txt
.venv/bin/python -m pytest -q tests/test_ctrader.py
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q control_plane ctrader_readonly tests
git diff --check
.venv/bin/python -m ctrader_readonly --help
```

The initial dependency command failed because the sandbox could not write the
uv cache; the approved retry installed the pinned package. Tests use mocked
HTTP/WebSocket transports and a virtual clock; they never bind a real callback
port, open a browser or contact a broker. Synthetic private-file tests create
and remove owner-only temporary directories outside Git, because the production
storage rule deliberately rejects repository-local state. All account/symbol
identities and credentials in fixtures are invented, not observed broker data.

Final results: **131 tests passed in 11.45s** (82 observer tests plus 49 unchanged simulator
tests); compileall, CLI help and whitespace checks passed. Cases include approval,
OAuth denial/state/expiry/replay, trade/unknown scope, environment/broker/account
mismatch, expired tokens, private-file permissions, redirect prevention, outbound
trade rejection, invalid schemas, network failures, request deadlines/correlation,
heartbeat loss, reconnect/backoff, revoked sessions, incomplete history,
changing snapshots, stale/partial/crossed quotes and status/callback redaction.
No real TLS handshake, consent redirect or broker capability was validated.

## Remaining risks and next steps

1. Confirm application approval, registered loopback callback and state echo,
   then create new private configuration locally. Until then, leave authentication
   unattempted. If registration/state behavior differs, obtain an approved secure
   callback design before changing the flow; never accept an unbound callback.
2. Run only the view-scope demo observer. Confirm returned broker/account identity,
   field presence/default serialization, symbol naming, history pagination and
   view-scope availability of P&L/expected-margin requests. Conservative checks
   may reject providers omitting optional fields; do not substitute guessed facts.
3. Observe disconnects and market closures; review broker rate-limit/error behavior,
   TLS/frame-size resilience and long-running memory use. The WebSocket receive
   size check happens after library frame assembly; it is not a streaming memory
   bound. Server retryAfter handling and per-provider timeout tuning remain open.
4. Review secure storage/keyring integration, optional refresh-token lifecycle,
   long-lived spot-subscription behavior and windowed history retention. Raw
   broker records intentionally have no export/persistence path in this phase.

This reader is not suitable evidence for execution reliability or live-account
support. Native protective-stop behavior, actual margin correctness, fill quality
and partial-close behavior remain unverified and would require a separately
authorized trading phase. This phase grants no trading permission.

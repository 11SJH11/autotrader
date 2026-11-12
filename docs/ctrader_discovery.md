# Explicit read-only demo discovery

Run from the project with Python 3.11:

```bash
.venv/bin/python -m ctrader_readonly discover --config /home/SJH/.config/autotrader/ctrader/config.json
```

Use an owner-only directory (0700) and JSON file (0600) outside any Git tree.
The existing application client ID, secret, approved flag, accounts scope,
loopback redirect URI, expected broker and token path are required. The demo
account ID may be absent only for this explicit discovery command. Never paste
credentials, tokens, callback URLs or account identifiers into chat or issues.
Register the exact configured redirect URI in the approved application's portal.

The browser requests accounts scope exclusively. Grant access only to the
intended demo account at the provider. The observer connects exclusively to the
fixed demo WebSocket endpoint. It validates application auth and server-reported
SCOPE_VIEW before showing matching demo candidates. Live entries and broker
mismatches are excluded. The local form shows broker and displayed login; the
API account ID stays server-side. Nothing is preselected, including a sole match.
Select a radio option, type its displayed login and tick explicit consent.

The form requires a private browser cookie, matching Origin/Host, random CSRF
nonce and a 180-second selection deadline. OAuth still requires the original
state nonce and a one-use callback within 180 seconds. Provider errors are never
printed. Missing state, unknown scope and unexpected metadata fail closed.

After confirmation, the observer rechecks scope, demo environment, broker and
login before account authorization. Only then does it atomically replace the
private config with demo_account_id (the ctidTraderAccountId) and
expected_demo_login, and save the verified access token privately. These two file
replacements are individually atomic, not a cross-file transaction: an I/O crash
can leave a confirmed pin without a token. Repeat deliberate authorization; no
trade can result. Changes made to config during selection reject the operation.

The discovery flow does not request order/position/symbol snapshots or issue
trade requests. The closed transport allowlist remains enforced. Scope metadata
must be present and explicit even when a provider schema declares it optional.
A successful OAuth exchange alone does not establish broker authorization.

Offline coverage: tests/test_discovery.py uses fabricated credentials and
accounts. It covers explicit selection, live/broker filtering, altered grants,
unknown/trading scopes, expiry, CSRF/Origin/Host/cookie checks, config changes,
private file modes, redaction and prohibited automatic account authorization.
These tests do not establish real broker reliability.

Official flow and scope reference:
https://help.ctrader.com/open-api/account-authentication/

After explicit selection, check only account identity/currency without taking a
full trading snapshot:

```bash
.venv/bin/python -m ctrader_readonly verify --config /home/SJH/.config/autotrader/ctrader/config.json
```

This verifies the stored grant and demo pin again, reads trader/assets, and
reports currency, brokerName and a pin-match boolean. It never prints account
IDs, balances, secrets or tokens. A differing brokerName is reported with exit
code 2; configuration remains unchanged pending owner confirmation. The account
list brokerTitleShort and trader brokerName are separately validated fields.

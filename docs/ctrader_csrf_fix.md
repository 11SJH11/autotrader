# Discovery selection CSRF correction — 12 November 2026

## Root cause and reproduction

The selection form response set Referrer-Policy: no-referrer while its POST
handler required Origin equal to the configured loopback origin. Browser form
POSTs under that policy send Origin: null. The combined HTTP guard reported
SELECTION_CSRF_MISMATCH before hidden-token validation, obscuring the real cause.

An isolated headless Firefox test using fresh disposable profiles and synthetic
forms reproduced both behaviors: old policy -> null Origin despite correctly
transmitted cookie and hidden token; corrected policy -> exact Origin with an
origin-only Referer, excluding the synthetic callback query. It accessed no
broker, private config or existing browser profile. Run:

```bash
.venv/bin/python tools/check_discovery_browser.py
```

The output contains four fixed booleans only. The browser fixture is separate
from pytest because it requires installed Firefox and local process/network
permissions. The HTTP regressions cover the actual production handler/form.

The relevant browser rule is the Fetch Standard's algorithm for appending Origin:
https://fetch.spec.whatwg.org/#append-a-request-origin-header

## Trace and smallest compatibility correction

DiscoveryFlow.__init__ generates separate random OAuth state (in OAuthFlow),
selection CSRF token, browser capability and launcher capability per attempt.
The selection form embeds the selection token in a hidden csrf input; a response
cookie carries the separate browser capability (HttpOnly, SameSite=Strict,
Path=/discovery). They are never printed or logged. Form re-rendering/multiple
tabs within an attempt do not rotate them. Every new attempt uses fresh values.

DiscoveryFlow.handler validates exact Host, browser cookie and exact Origin
before parsing the form and checking its hidden token with constant-time hashed
comparison. Selection expiry is 180 seconds after discovery; OAuth state remains
one-use with its independent 180-second deadline. The broker reauthorization
happens after local validation and does not change any expected CSRF value.

Only selection-form responses now use Referrer-Policy: origin. Other responses
retain no-referrer. Exact Origin validation remains mandatory; null/absent Origin
is rejected. Origin-only Referer cannot carry a callback path, authorization
code or state query. CSP remains default-src none, form-action self,
frame-ancestors none; every response remains no-store. No scripts or external
resources are added to the production form.

## Diagnostics and replay boundaries

The POST guard now distinguishes SELECTION_HOST_MISMATCH,
SELECTION_SESSION_MISSING/MISMATCH and SELECTION_ORIGIN_MISSING/NULL/MISMATCH.
Hidden-token failures distinguish SELECTION_TOKEN_MISSING/MISMATCH/REPLAYED;
expiry remains DEMO_SELECTION_EXPIRED. Only fixed codes and stages are shown,
never input/header/token values. Local validation does not consume a token on
invalid consent/input. A valid confirmation consumes it before the broker
recheck, including if that recheck fails: another broker attempt requires fresh
OAuth. One successful tab cannot be replayed by another.

A stale tab after a new attempt either has the old cookie (session mismatch),
or the browser has replaced the cookie but its hidden token is old (token
mismatch). localhost is not treated as 127.0.0.1. Browser cache cannot make a
previous token valid; no-store remains enforced. The loopback socket binds before
opening the browser, preventing concurrent processes owning the same callback
port. No service was modified/restarted and no existing private browser state
was inspected.

## Regression coverage

Actual handler/form tests cover callback token rendering, unchanged token across
re-renders, correct policy/cookie/cache headers, null/missing/cross-origin Origin,
missing/wrong cookie, multiple tabs, stale prior-attempt tokens, expiration,
missing token, successful confirmation, successful token replay and replay after
failed broker recheck. Existing scope/demo/broker/config-redaction tests remain.
No broker reliability claim follows from mock tests.

Validation: 33 discovery tests passed in 0.10s; the full AutoTrader suite passed
**256 tests in 31.00s** using the project-local Python 3.11 environment. The
isolated Firefox reproduction passed all four boolean assertions. git diff
--check passed. One fresh read-only discovery attempt was then launched;
its actual result is recorded in ctrader_connection_verification.md.

## Fresh real attempt

The single fresh attempt after the fix succeeded: DEMO_SELECTED_VIEW_ONLY.
Saved config/token passed owner-only validation; token scope is accounts and
current. Reauthorization verified the pinned demo account, GBP currency and
returned brokerName `pepperstoneuk`. That detailed broker-name expectation is
awaiting owner confirmation; it was not automatically changed. No trade was sent.

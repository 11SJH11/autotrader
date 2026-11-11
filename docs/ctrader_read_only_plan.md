# cTrader read-only integration plan — 2026-11-11

This is a proposal, not an implemented connection. Only public official
reference pages were read. No authentication, application registration, secret
retrieval, account discovery or broker requests were performed. No existing
service was inspected or changed during Phase 1. Execution remains simulator-only.

## Proposed boundary and authorization gate

A future, separately authorized spike should use a separate read-only process,
without importing the execution engine. Request OAuth `accounts` scope: the
[official authentication guide](https://help.ctrader.com/open-api/account-authentication/)
distinguishes view-only account access from `trading` permission. Require
`SCOPE_VIEW` and reject trading scope, as defined by the
[permission model](https://help.ctrader.com/open-api/model-messages/#protooaclientpermissionscope).
Do not reuse credentials or sessions belonging to Ledger, Hurricane or T02.
Application registration and credential storage need a separate approved design.

Permit only the demo host in the
[official endpoint list](https://help.ctrader.com/open-api/proxies-endpoints/).
Independently verify returned account demo status and an explicit demo account
allowlist; hostname alone is insufficient. Abort on mismatch. No live host or
trade request type should exist in the spike's outbound dispatcher.

## Proposed read sequence

After explicit authorization, implement an outbound message allowlist for app
and account authorization, account listing, trader/account data, symbol listing
and details, expected margin, current reconciliation, and bounded order/deal
history. Check exact schemas and read-only availability against the
[official messages reference](https://help.ctrader.com/open-api/messages/)
before implementing each request. `ProtoOAReconcileReq` returns current open
positions and pending orders; historical requests are needed to investigate
missing or closed entities. Follow history pagination rather than treating a
single page as complete evidence. Never send NewOrder, AmendOrder,
AmendPositionSLTP, CancelOrder or ClosePosition requests.

Use the [connection guide](https://help.ctrader.com/open-api/connection/) for
transport and heartbeat behavior: it specifies a heartbeat every ten seconds
and notes that the Python SDK does not support WebSockets. Select and pin the
SDK only after checking Python support in its actual dependency metadata.
Design reconnect backoff, a bounded queue, and request correlation; a reconnect
requires a new complete read snapshot before reporting healthy state. Proposed
spike caps are 10 non-history and 2 history requests per second, below the
[documented limits](https://help.ctrader.com/open-api/) of 50 and 5 respectively.

## Evidence and acceptance

Produce sanitized fixtures with pseudonymous identifiers, retrieval timestamps,
completeness indicators and request/response correlations. Exclude tokens,
account numbers, balances and personal information from the public diagnostics.
Verify Pepperstone account eligibility separately; it is not established here.
Record XAUUSD symbol availability, account hedging/netting type, volume units,
minimum/step/max volume, price digits, stop distances, protection fields and
expected margin from returned symbol/account metadata. Do not infer lot mapping
from SIM-XAU. The
[model reference](https://help.ctrader.com/open-api/model-messages/)
describes symbol, order, deal and position fields; confirm conversions with
explicit dimensional examples before any later trading proposal.

Offline contract tests must prove outbound allowlisting, live/demo mismatch
rejection, token redaction, partial history handling, reconnect gaps, event
ordering and request timeouts. Compare repeated snapshots and intervening
execution observations; do not assume a network snapshot is atomic like this
simulator's journal. This spike cannot prove partial-close behavior, stop
execution, fill quality, demo lifespan or trading reliability. Those require a
separate authorized broker-confirmed execution phase after legacy writer
ownership is resolved. This plan grants no permission to run that phase.

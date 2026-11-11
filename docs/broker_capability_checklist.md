# Future broker capability gate

All items below are UNVERIFIED. No API or provider website was contacted in this
task. cTrader/Pepperstone is only the candidate from the supplied blueprint.

- Explicit authorization for read-only demo connection; credential handling design.
- Actual demo identity/environment and server-side demo account allowlist.
- API eligibility, authentication lifecycle, heartbeat, reconnect and demo expiry.
- XAUUSD symbol availability, tick/contract size, currency, volume minimum/step,
  lot-to-unit conversion, stops/freeze distances and supported order types.
- Margin/equity, commissions, spread, slippage, hedging/netting and partial closes.
- Broker-native protective stops and confirmed protection after reconnect.
- Stable client command correlation across orders, positions and deal history;
  retention, pagination, delayed visibility and unknown-outcome reconciliation.
- Controlled approved minimum-volume open/modify/partial-close/close evidence.
- Account-wide ownership: identify and explicitly retire overlapping writers
  before arming a future adapter. This task changed no legacy services.

Tests here prove only deterministic offline behavior. No broker account, order,
position, deal, fill or real demo capability has been verified.

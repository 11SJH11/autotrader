# Read-only inventory — 2026-11-11

Scope: inspected selected Python source via AST and bounded source reads in
`~/Projects/telegram_mt5_demo_bot`, `~/Projects/t02_mt5_demo_bot`, and
`~/Projects/Investment-Tool/ops_center{,_v2}`. No legacy code was imported or
executed. No credentials, sessions, account records, logs or environment files
were opened. No old project was changed. `local_inventory.txt` is an earlier
snapshot; the service observations below were collected for this task.

| User service | Observed state | Role / boundary |
|---|---|---|
| telegram-mt5-bot | inactive/dead | Hurricane ingestion and MT5 writer |
| t02-mt5-demo | inactive/dead | T02 strategy and MT5 writer |
| ledger-backend | active/running | Ledger backend; separate owner |
| ledger-mt5-bridge | active/running | Separate MT5 bridge process |
| ledger-mt5-live-bridge | active/running | Live-named bridge; identity not investigated |
| mt5-demo-terminal | active/running | Shared terminal dependency |
| mt5-demo-monitor | failed/failed | Monitoring unavailable in this snapshot |
| ops-control-center | active/running | Independent status/control service |

Read-only command: `systemctl --user show` for these units, restricted to Id,
ActiveState, SubState, MainPID, Requires and After. Sandbox bus access initially
failed; the same read-only command succeeded after approval. All services retain
basic/app slice dependencies; the bots and terminal order after network-online;
bridges order after network and graphical session. These are ordering
relationships, not proof of account identity, readiness or process health.
No command lines or service environments were read. Port numbers in the supplied
inventory are not attributed to specific processes. No account overlap is proven.

Reusable components, by inspection:

* Hurricane `lifecycle.py`: SQLite unique source claims and structured outcomes.
  Reimplement stable setup/leg identities; do not copy source/account data.
* `broker_reconciliation.py`: bounded positions/orders/deals evidence reads;
  absent evidence does not authorize retry. Preserve this conservative rule.
* `serialized_mt5.py`: native-call and submission mutexes; explicitly excludes
  Ledger/manual trading. Locks cannot establish a universal execution owner.
* Hurricane `bot.py` imports Telethon, native MT5, lifecycle, and staged stop
  management; broker and ingestion lifetimes are coupled in that process.
* T02 `t02_strategy.py`: pure completed-bar aggregation, contiguous 15-minute
  checks, New York session/DST handling and first-signal session consumption.
  Candidate for later fixture parity; no strategy port claimed here.
* T02 `t02_demo_bot.py` imports Hurricane's DemoMT5, submission guard and
  reconciliation helpers. This is a cross-project execution dependency.
* Ops v1/v2: SQLite event-key deduplication, bounded recent-event reads, service
  polling and separate telemetry validation. Reuse presentation concepts later;
  do not inherit subprocess controls or existing auth/configuration.

Failure boundaries: terminal/native API hangs affect MT5 clients; bot locks
exclude independent Ledger clients; loss of acknowledgement must not trigger a
second submission; strategy and execution failures are coupled in legacy bot
processes. Ops visibility does not prove broker execution or worker health.
Existing secrets, historical trade data and logs remain in their original
projects, under their existing retention policies (not audited here).

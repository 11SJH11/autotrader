# Hurricane timing correctness audit — 12 November 2026

**Finding:** aware Telegram publication timestamps are compared as absolute UTC
instants. The available evidence does **not** verify a constant broker/chart
UK+2 relationship across DST. One stored 9 November tick/reference pair supports
UK+2 for that observation. Naive UK summer timestamps are incorrectly interpreted
as UTC by the existing freshness gate. No old bot fixes were made.

VERIFIED means reproduced from identified source/data within the stated scope.
FAILED identifies a demonstrated mismatch, not a failed pytest. NOT YET VERIFIED
means evidence is absent or insufficient. None of these labels authorizes trading.

## Inspected code and sources of authority

`H/` = `/home/SJH/Projects/telegram_mt5_demo_bot/`.
`T/` = `/home/SJH/Projects/t02_mt5_demo_bot/`.
`L/` = `/home/SJH/Projects/Investment-Tool/`.
`A/` = this AutoTrader repository. Original source locations and SHA-256 hashes
are in `tests/fixtures/phase25/source_manifest.json`.

| Responsibility | Actual code | Authority/limit |
|---|---|---|
| Telegram ingestion | H/bot.py:121–155, 211–267; H/telegram_collect.py:18–24 | Uses Telegram message.date/edit_date; collector distinguishes collected_at. No Telegram client was started. |
| Parser | H/rules.py:10–37; H/export_parser.py:44–115 | Structured setup grammar and numeric validation. Parser preserves supplied date, does not interpret a body wall-clock time. |
| Age and epoch gate | H/bot.py:181–187, 234–244, 299–350, 445–449, 460–464 | Publication age, not receipt age; default max_age=90 at line 139. Reconnect epoch deliberately rejects backlog. Actual secret-bearing environment overrides were not read. |
| Broker clock | H/tick_normalization.py:13–97, 100–186; H/mt5_bridge.py:180–208 | Explicit +7200/+10800 candidate conventions, independent UTC-clock check, advancing-tick calibration. Not a broker timezone calendar. |
| Price eligibility | H/mt5_bridge.py:211–230, 373–440, 459–510 | Current executable bid/ask and stop/spread guards; not publication-time market replay. |
| Risk/volume | H/mt5_bridge.py:373–440; H/tp_allocator.py:71 onward | Current FIXED3 policy: three 0.01 legs; risk diagnostic rather than percentage sizing in this mode. Defaults/policy source, not proof of historical deployment parameters. |
| Duplicate/edit lifecycle | H/bot.py:82–119, 234–244, 270–297; H/lifecycle.py:16–57 | Durable seen/accepted/claim identity; edits cancel unfilled orders in DEMO and never open an entry. These methods were not run against a broker. |
| TP1/breakeven | H/staged3_be.py:14 onward | Registered broker-confirmed leg management; not inferred from Telegram TP announcements. |

The user's clarification is the authority for interpreting **group-local wall
clock references as Europe/London**. Existing parser code does not establish a
UTC+2/+3 seasonal rule. Source code/tests show implemented behavior, not proof of
the trader's original intended price/timing policy.

## End-to-end trace

1. `new_message` receives `message.date` (publication), `edit_date` and text;
   SOURCE_AUTHENTICATED is timestamped at local UTC handling time. This is a
   processing observation, not necessarily the network-arrival instant.
2. `from_live_message` delegates to the export parser and preserves the ISO date.
   Raw messages and peer/sender/message identifiers were excluded from fixtures.
3. `process_message` marks the message seen **before** checking age. The age gate
   rejects publication older than max_age, over three seconds in the future, or
   earlier than the connection epoch. A rejected first delivery cannot later
   become a fresh entry through a duplicate or edit.
4. `execute` rechecks publication age, then enters gateway preparation/calibration,
   and checks it again after readiness. Calibration time therefore cannot silently
   turn a signal older than the configured bound into an entry.
5. `DemoMT5.quote` requires valid identity/connectivity and a calibrated tick.
   UTCClock requires at least two usable HTTPS Date references, agreement within
   two seconds, low RTT/no cache and local wall-clock sanity within two seconds.
   TickEstimator requires six advancing timestamps over at least two seconds,
   tick age <=15 seconds and future tolerance <=3 seconds. It resets on failed
   observations, convention changes, reconnect/terminal changes and periodic
   revalidation. Permitted offsets are hard-coded **candidates**, not a verified
   season schedule. A frozen feed cannot earn calibration.
6. `entry_plan` uses the quote **at evaluation**. BUY ask inside zone -> market;
   above zone but below TP1 -> limit at zone high; at/past TP1 -> reject. SELL is
   mirrored using bid. BUY below the zone / SELL above the zone are also rejected.
   Later send/preflight rechecks account, quote and protection. There is no read
   of a quote, minute bar or market path at publication time.

## Timestamp and historical evidence

| Requirement | Status | Concrete result |
|---|---|---|
| UTC-aware live publication arithmetic | VERIFIED | Frozen `too_old` body passes boundary tests at -3, 0, 90 seconds and rejects -4/91; aware offsets compare as instants. |
| Naive UK local publication/export inputs | FAILED | In July, naive 09:00 UK should be 08:00 UTC. The old gate replaces missing tzinfo with UTC; a fresh input appears ~1 hour future and is rejected. This is an input-boundary defect, not evidence that Telegram currently delivers naive dates. |
| London spring/fall DST conversion | VERIFIED | Audit-only ZoneInfo oracle tests 29 March and 25 October 2026; nonexistent 01:30 spring time rejects, fall 01:30 requires fold. This oracle is not connected to the bots. |
| Broker tick UK+2 on 9 November | VERIFIED, sample-scoped | Stored calibrated reference UTC 07:08:14.073750; raw epoch label 09:08:13.840; observed offset 7200; adjusted tick age 0.233750s. Clock audit says VALIDATED, local delta 0.136983s, 9 advancing samples over 2.007s. |
| Broker chart/candle timezone, especially across DST | NOT YET VERIFIED | Only one paired tick/reference record found. No paired pre-25-October tick/bar evidence or broker M1 history spanning DST was available. Tick encoding is not itself proof of chart display/candle conventions. |
| Publication vs processing latency | VERIFIED, sampled | 169 sanitized authenticated source-event pairs, median 1.838770s, max 273.890108s. 33 correspond to parsed signals: min 0.448507s, median 1.860523s, max 67.704617s. Both events over 90s were **not parsed signals**. Do not label them missed trades. |
| Real historical parsed-message regression | VERIFIED, fields only | Nine October parsed-audit records round-trip through original parser bodies using generated grammar carriers. Original Telegram date/text are unavailable; all nine publication dates are null. Receipt cannot replace missing publication. |
| Out-of-order/duplicate/edit protections | VERIFIED, bounded offline scope | Publication-age/reconnect tests, duplicate tick tests and original edited_message body verify no edit entry. Source shows seen-before-age and persistent claim guards. No real broker cancellation was attempted. |
| Tick stale/future/drift/reset checks | VERIFIED, mocked clocks | Original estimator/UTCClock methods reject stale and regressing ticks, duplicate-only calibration and unsound wall clock; convention changes require new samples. This is not a historical independent clock measurement. |
| Price at publication vs evaluation | NOT YET VERIFIED | Numeric price eligibility was reproduced with mocked quotes. No historical broker bid/ask path for these messages was available. OANDA cache stops before the audited messages and is not TradeQuo executable pricing. |
| Actual incorrectly blocked or late entries | NOT YET VERIFIED | Cannot infer them from a stale/calibration failure, message age, reported TP, or parsed setup alone. The naive-input defect and conservative reconnect exclusion are reproducible; realized missed/late trading opportunities are not established. |

Full numeric evidence: `broker_clock_evidence.json`, `hurricane_delays.json` and
`hurricane_parsed.json` in the Phase 2.5 fixture directory. No raw Telegram
message was exported. The reconstruction fixture's source name includes 27
October, but its retained timestamps are audit observations, not publication.

## Why UK+2 cannot become a constant UTC offset

If UK time is UTC+1 and a broker encodes UTC+3, the difference is two hours.
After UK returns to UTC, UTC+2 also differs by two hours. That is compatible
with the user's observation, but does not identify which transition dates the
broker uses. If the broker instead follows US transition dates, UK/broker
relationships can temporarily differ during the March and October/November
mismatch weeks. No such broker schedule was inferred here.

Independently tested NY Asia 20:00 starts at 01:00 UTC before US spring DST,
00:00 UTC after 8 March, and 01:00 UTC after 1 November. UK changes on 29 March
and 25 October instead. In the intervening weeks NY session opening and UK wall
time shift independently. Neither timezone supplies evidence for the broker.

T02 currently applies its **current calibrated tick offset to all 3,200 fetched
M1 bars** (T/t02_demo_bot.py:167–190, 513–514). A synthetic +3 historical epoch
normalized with a current +2 offset shifts that candle one hour forward. This
is a demonstrated mixed-offset failure mode; whether the actual broker's rate
encoding changes that way remains NOT YET VERIFIED. Do not apply the tick
inference to historical candles without per-period evidence.

## Recommended changes, not implemented

1. Define input contracts: Telegram aware publication instants stay UTC; explicitly
   local group text/export times use Europe/London. Reject naive/ambiguous times
   until their source convention and fall overlap are resolved. Do not blanket
   reinterpret already-aware Telegram timestamps as London wall time.
2. Preserve publication, edit, receipt, parse, readiness, evaluation and dispatch
   timestamps separately, plus calibrated raw tick/reference evidence. Record
   the effective age and specific blocked gate rather than merging clock/history
   causes into a single reason. Keep identifiers/private text outside reports.
3. Obtain retained, authorized broker tick/bar evidence and independent UTC anchors
   on both sides of each transition, without using existing credentials in this
   audit. Verify tick and candle conventions separately before specifying a dated
   conversion map. Reject an unverified interval; do not assume a European/US zone.
4. Review whether the deliberately conservative reconnect/no-catch-up exclusion is
   desired. It can block a still-under-90-second setup. Removing it without durable
   replay/risk/provenance policy would increase late-entry risk.
5. Decide whether eligibility requires a publication-to-evaluation market-path
   check (e.g. already touched target/invalidated zone before a retrace). Current
   price alone cannot answer that. Historical mid OHLC cannot prove executable
   fills or a tick path.

No operational policy change is justified by a single post-DST observation.

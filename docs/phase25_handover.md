# Phase 2.5 handover — 12 November 2026

## Outcome

Completed the read-only Hurricane/T02 correctness audit and offline regression
bundle. **175 tests passed in 13.79s**: all 131 pre-audit AutoTrader tests plus
44 new audit tests. Existing engine/observer code and old bots were not changed.
No broker authentication, broker API, Telegram connection, native trade,
service inspection/restart or existing credential access occurred. The cTrader
application remains awaiting approval; no authentication was attempted.

Reports:

- [Hurricane timestamp audit](hurricane_timing_audit.md)
- [T02 signal parity](t02_signal_parity.md)
- [Detailed matching/missing/extra results](phase25_comparison.json)

VERIFIED below means evidence within stated scope, FAILED means a demonstrated
correctness/parity mismatch, and NOT YET VERIFIED means insufficient evidence.
Passing an audit test that reproduces a defect does not make that defect correct.

| Requested work | Status/result |
|---|---|
| Inspect all four projects and actual parser/strategy/risk/execution code | VERIFIED. Locations and authority distinctions in both reports. AutoTrader retains generic simulation/observer infrastructure; its Hurricane/T02 adapters are not implemented. No old project module/startup was imported. |
| Trace Hurricane publication -> processing -> parser -> quote -> decision | VERIFIED source trace and selected retained metadata. 169 publication/processing pairs, 33 parsed-signal pairs and nine reconstructed parsed-audit fixtures. No raw private Telegram messages/identifiers retained. |
| UTC/London/DST handling | VERIFIED aware arithmetic and audit-only timezone tests; FAILED naive-UK-summer handling at legacy age boundary. No evidence that current Telegram deliveries are naive. |
| Verify broker/chart UK+2 convention historically | VERIFIED only for one stored 9 November encoded-tick/reference pair; NOT YET VERIFIED for chart/candle conventions or across transitions. No fixed seasonal rule was assumed or installed. |
| Delays, edits, stale/future quotes and clock drift | VERIFIED bounded mocked behaviors and measured receipt metadata. NOT YET VERIFIED actual publication-time price paths or realized missed/late entries. Two >90s source events were commentary, not parsed trade signals. |
| T02 deliberate BUY-only and saved-run selection | VERIFIED current code deliberately rejects shorts; FAILED parity against the located named T02 run 214 (both). NOT YET VERIFIED that run 214 is the currently selected UI run or that a direction change was user-authorized. |
| Replay historical M1/M15 and compare signals | VERIFIED entry-signal comparison on retained OANDA inputs with explicit settings; no full broker/fill/P&L parity claim. |
| Investigate 27 October / 9 November | VERIFIED recorded candidates, ratio arithmetic and selected rejection reasons; NOT YET VERIFIED underlying detection from original candles, which are unavailable after 21 October. |
| Repeatable sanitized regression suite | VERIFIED. UTC/London and NY DST, missing/duplicate/out-of-order candles, delays, stale/partial quotes, calibration reset, duplicate boundaries and bootstrap/recovery without catch-up. |
| Implement fixes | Intentionally not done. Discrepancies were reported before any operational change. Recommended changes are proposals in the reports. |

## Main findings

1. **Direction and weekday mismatch.** Saved run 214 is 1 March–17 October,
   direction both, NY weekdays Monday–Friday, zero-cost next-M1-open research
   fills and 1% risk sizing. Matched detector replay: 64 matching, 0 missing,
   0 extra entry signals. Projected current worker policy (BUY-only and no weekday
   filter): 30 matching, 34 missing shorts, 7 extra Sunday-session long candidates.
   This projection does not bypass or model the worker's runtime execution gates.
2. **Narrow price/stop evidence, not full parity.** All 64 saved entry prices match
   the next retained M1 open; stops/ratios match too. Canonical Ledger method bodies
   and standalone detector agree on all 164 nonempty 285-minute windows. Retained
   M1-to-M15 reaggregation matches 14,869 complete buckets exactly. Full engine
   portfolio/exit/margin state was not rerun; original effective timezone/data
   fingerprint and full historical deployment environment remain unverified.
3. **Broker clock evidence is sparse.** One stored 9 November observation has
   reference UTC 07:08:14.073750 and raw tick label 09:08:13.840. A 7200-second
   correction gives age 0.233750s, consistent with UK+2 on that date. It does not
   prove constant UK+2 or that all broker candles share tick conventions.
4. **Historical offset reuse is unsafe without evidence.** T02 applies the current
   tick-derived offset to its entire 3,200-bar window. Synthetic mixed-convention
   tests demonstrate a one-hour displacement if historical +3 epochs are processed
   under current +2. Actual broker transition conventions remain unknown.
5. **Historical policy/version must be distinguished.** The 27 October long
   candidate was accepted in diagnostics, then minimum-lot/risk-budget blocked.
   That older decision must not be explained using today's fixed 0.05-lot rule.
   The 9 November 04:30 UTC candidate fails the .20 ratio. The NY 9 November
   session's 10 November 02:00 UTC candidate is a short rejected by long_only.
6. **Clock/feed discrepancies need evidence, not reinterpretation.** A 6 November
   ~3-hour candle lag and later timestamp jump are recorded. Error counters show
   many stale/future failures, but are not counts of missed trades and do not prove
   normalization versus source-feed root cause. Relevant broker epochs/candles
   and original effective configuration are not retained in this bundle.

## Implementation and repeatability

`audit_phase25/replay.py` compiles selected frozen source definitions, not old
application modules. It removes imports/registration/startup/spec construction
and supplies explicit dataframe context, observation and dataclass/spec shims.
Ledger's original on_bar, ratio/BE method and FVG bodies are evaluated alongside
the standalone T02 source. Flat-session contexts intentionally exclude full
execution/portfolio state. Synthetic worker tests run its original one_iteration
in **signal-only mode**, with all broker reads, persistence and event interfaces
replaced by in-memory stubs; the entry function fails the test if called.

`audit_phase25/timing.py` is an audit-only London wall-time oracle with
nonexistent/ambiguous-time rejection. It is not imported by any trading runtime.
`tests/test_phase25.py` reproduces both desired guards and known mismatches.
Frozen fixtures include generated grammar carriers from real **parsed levels**,
never raw private messages. Receipt timestamps never stand in for missing
publication timestamps. Source/fixture hashes and definition line spans are in
source_manifest.json and fixture_integrity.json.

The compressed retained public-market OHLC subset is approximately 3.8MB. It
supports repeatable offline signal tests without opening old projects. Raw broker
orders/deals, account identifiers, credentials and Telegram peer/sender/message
IDs are absent. `tools/phase25_extract.py` is an optional local re-extraction tool:
its input paths are explicit, it reads only known source files, structured event
columns, selected saved-run summary/trade fields and market Parquet. It writes
only under this repository. It does not open the private Ledger database,
config/env files, health/state identity files, sessions or credential stores.
Hurricane SQLite uses URI mode=ro, query_only and a read transaction. Re-extraction
is not part of normal tests and may capture later diagnostics from running bots.

## Environment, commands and results

Used the project-local `.venv`, **CPython 3.11.17**. Pinned replay/extraction
requirements are in requirements-audit.txt, included by requirements-dev.txt:
pandas 2.3.3 (Python >=3.9), pyarrow 22.0.0 (>=3.10), numpy 2.4.6 (>=3.11),
python-dateutil 4.0.0 (>=3.11), pytz 2026.6 and tzdata 2026.6. Installed metadata
confirms compatibility with 3.11. These do not become simulator runtime
requirements. Existing pytest/WebSocket pins remain unchanged.

From AutoTrader:

```bash
uv pip install --python .venv/bin/python -r requirements-dev.txt
# Re-extraction is optional and reads only allowlisted old project evidence:
.venv/bin/python tools/phase25_extract.py
# Regenerate the detailed comparison using only local frozen fixtures:
.venv/bin/python -m audit_phase25.replay
.venv/bin/python -m pytest -q tests/test_phase25.py
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q control_plane ctrader_readonly audit_phase25 tests tools
git diff --check
```

Observed commands:

| Command | Result |
|---|---|
| `uv pip install --python .venv/bin/python pandas==2.3.3 pyarrow==22.0.0` | Installed these packages and the pinned resolved dependencies listed above. |
| `uv pip install --python .venv/bin/python -r requirements-audit.txt` | Initial cache access denied by sandbox; approved retry checked all six pinned packages successfully. |
| `.venv/bin/python tools/phase25_extract.py` | Extracted 11 source snapshots, 64 saved trades, 169 timing pairs, one tick/reference pair and three recorded candidates. |
| `.venv/bin/python -m audit_phase25.replay` | Produced detailed comparison; aligned 64/0/0; projected worker 30/34/7; zero canonical/detector and entry/stop/ratio mismatches in measured scope. |
| `.venv/bin/python -m pytest -q tests/test_phase25.py` | 44 passed in 2.90s. |
| `.venv/bin/python -m pytest -q` | 175 passed in 13.79s, including the existing 131 tests. |
| `compileall` and whitespace checks | Passed. |

No external Ledger test interpreter/venv was used. No old tests were treated as
proof of correctness or run in a way that could import their startup/configuration.
No existing broker/Telegram client was invoked. The dependency install and local
metadata reads are not broker authentication.

## Remaining gates and next steps

Confirm the intended saved-run reference and document whether BUY-only, Sunday
entries and fixed lots are authorized variants. Review proposed changes together
rather than silently enabling shorts or altering sizing. Retain effective version,
parameter and timestamp provenance for future decisions.

Obtain an authorized, sanitized historical broker candle/tick export covering
both candidates and both sides of UK/US transition periods, with independent UTC
anchors and documented epoch semantics. This audit does not authorize credential
reuse, broker authentication or new market-data retrieval from existing services.
The available OANDA cache cannot establish TradeQuo timing/fill parity.

Verify the complete original engine/data/configuration before extending the
signal-only conclusion to management, overlapping positions, costs and P&L.
Address naive-time boundaries and historical epoch mapping only after the input
conventions are evidenced and the proposed fixes reviewed. cTrader approval is
still pending; continue entirely offline until that separate gate is cleared.

# T02 signal parity audit — 12 November 2026

**Finding:** current T02 BUY-only execution does not match the located saved
Ledger T02 run's `direction=both`. On identical retained OANDA inputs, applying
the saved run's New York weekday settings gives **64 matching signal times and
directions, 0 missing and 0 extra** for the pure detector. Projecting the current
BUY-only worker without a weekday filter gives **30 matching, 34 missing shorts
and 7 extra Sunday-session candidates**. This is not live broker/fill/P&L parity.

VERIFIED is scope-limited evidence; FAILED is a demonstrated mismatch;
NOT YET VERIFIED means the original authority/configuration/data is insufficient.
H, T, L and A abbreviations are defined in `hurricane_timing_audit.md`.

## Source of truth and source locations

The only located saved summary for key `gold_ifvg_v2_fvg_ratio_020` is
L/backend/research_output/run-214-summary.json, named **T02**, with 64 saved
trades in run-214-trades.csv. Dates are 1 March–17 October 2026; direction both;
1m primary; OANDA XAUUSD; 1% risk-based sizing; 20x leverage; zero spread,
slippage and commission; stop-first ambiguity; weekdays Monday–Friday;
allow_overnight=true. These are the comparison parameters, not assumptions about
MT5 or cTrader. The named run is the best available reference; whether it is the
user's currently selected UI run is **NOT YET VERIFIED**. No private Ledger
database/UI selection or configuration was accessed to guess that choice.

The current child module SHA-256 matches the saved run's child module hash.
The archive description explicitly excludes engine/base/dependencies/market
state, so it is not proof of a fully identical historical execution environment.
The saved config omits exchange_timezone; current BacktestConfig defaults to
America/New_York (L/backend/app/backtesting/models.py:143), and engine weekday
checks use it (engine.py:539–549). Replaying that default explains all 16 Sunday
signal exclusions. A separately archived original effective timezone is absent.

| Rule/component | Actual source |
|---|---|
| Frozen mechanical baseline and its explicit source ambiguities | L/backend/app/backtesting/strategies/gold_asia_15m_ifvg_research_v1.py:1–41 |
| FVG concept | L/backend/app/market_concepts/builtins.py:43–57 |
| Detection, newest-gap tie-break, consume-first, structural stop | L/.../gold_asia_15m_ifvg_research_v1.py:134–267, 317–397 |
| T02 ratio child and BE | L/.../gold_asia_15m_ifvg_loss_tests_v2.py:48–84, 86–143, 170–177 |
| Completed-bar context, next-open/exit/risk handling | L/backend/app/backtesting/context.py:18–99; engine.py:22–36, 539–549, 633–688, 691–810 |
| Standalone pure reconstruction | T/t02_strategy.py:1–138; exact definition spans/hashes in source_manifest.json |
| Candle normalization/history/recovery | T/t02_demo_bot.py:149–190, 480–618 |
| BUY-only/fixed-lot policy | T/t02_demo_bot.py:264–278, 288–294, 598–600 |
| Attempt fence, quote preflight, native entry | T/t02_demo_bot.py:327–364 |
| Position TP and BE management | T/t02_demo_bot.py:400–477 |
| Separate Ledger forward/runtime and risk/execution | L/backend/app/forward/mt5_t01_runtime.py:40–59; brokers/mt5_sizing.py; brokers/mt5_execution_ledger.py:20–80; brokers/mt5_demo_submission.py:214–300 |

The baseline explicitly calls its Asia hours, inversion, stop, entry and BE
interpretation a reconstruction of underspecified source material. **Matching it
does not prove those are the original trader's private rules.** Current worker
long-only checks and dedicated tests show that BUY-only is deliberate in code;
a user-approved rule change from both directions was not located in non-secret
artifacts. That original authorization remains NOT YET VERIFIED.

## Replay method and data quality

Frozen source excerpts, original file/definition locations and hashes are inside
`tests/fixtures/phase25/`. The loader compiles selected original function/method
bodies via AST. Imports, registration, startup and spec construction are removed.
Small context/observation/spec shims implement the interfaces; no Ledger engine,
MT5 module, Telegram module, service or credential loader is imported. Canonical
Ledger method bodies and its actual FVG function are executed, not a rewritten
strategy. The standalone detector is executed from its frozen definitions too.

The bundle contains 224,864 historical M1 and 15,036 M15 OANDA cache rows for
warmup plus the saved run interval. Timestamps are explicitly UTC and duplicates
are absent. Reaggregating all 14,869 complete 15-minute M1 buckets matches stored
M15 OHLC exactly (maximum difference zero). Other cached M15 bars need not have
15 retained M1 constituents; this matters outside the tested windows. All 164
nonempty tested Asia windows contain the required 285 minutes from 19:15 to
midnight NY. First-gap warmup is retained, and no future M1 close or M15 bar is
exposed before confirmation.

The original full cache covers 21 October 2022 through **21 October 2026 03:59
UTC** for M1, and through 03:45 UTC for M15. Neither OANDA nor retained broker
candles cover 27 October or 9 November. All copied source/data are hashed;
`market_coverage.json` identifies full-file hashes. The stored run's independent
market_data_fingerprint has not been recomputed through its original preparation
pipeline, so byte-identical original run inputs are NOT YET VERIFIED. Price/stop/
ratio agreement below is narrower, directly measured evidence.

Each session is replayed flat to compare first entry signals, not full portfolio
state. Direction and NY weekday filters are applied in explicit separate layers.
For all 64 saved trades, next M1 OPEN at confirmation equals saved entry_price,
and structural initial stop plus signal-time ratio also agree within 1e-8.
Saved TP distances are 1R from the actual saved fill. Exit paths, position overlap,
margin, risk allocation and broker slippage are not rerun by this harness.

## Comparison

Machine-readable details, including every missing/extra timestamp and direction,
are in `docs/phase25_comparison.json`.

| Replay layer vs saved 64 trades | Matching | Missing | Extra | Status |
|---|---:|---:|---:|---|
| Canonical Ledger method bodies, same direction/NY weekdays | 64 | 0 | 0 | VERIFIED, entry signals only |
| Standalone T02 detector, same direction/NY weekdays | 64 | 0 | 0 | VERIFIED, entry signals only |
| BUY-only filter with matched NY weekdays | 30 | 34 | 0 | FAILED parity: all 34 missing are shorts |
| Standalone detector without weekday restriction | 64 | 0 | 16 | FAILED parameter parity: all 16 extras are Sunday NY sessions |
| Current worker's BUY-only/no-weekday policy, projected onto input candles | 30 | 34 | 7 | FAILED policy parity; not a claim of 7 actual broker submissions |

Seven extra long candidate confirmations (UTC): 2 March 03:45, 9 March 01:15,
4 May 02:00, 11 May 01:15, 18 May 02:00, 3 August 02:00 and 10 August 00:15.
Each is on Sunday evening in New York, despite having a Monday UTC date.
The worker's connectivity, bootstrap, history, stale-data, ownership, quote,
spread, lot and margin gates can further reject these projected candidates.

## Requirement-by-requirement findings

| Requirement | Status | Evidence and limitation |
|---|---|---|
| Intentionally BUY-only and matching selected backtest | VERIFIED code intent / FAILED against run 214 | Worker, risk_volume and _open_demo all reject shorts; saved direction is both. Selection/approval of that change is NOT YET VERIFIED. |
| M15 IFVG and completed confirmation | VERIFIED on retained windows | Three-candle far-boundary gaps; later completed close first crosses far edge. Newest source gap wins simultaneous inversions. No body rewrite. |
| NY session and DST | VERIFIED in offline clock tests | 20:00 inclusive to 00:00 exclusive, keyed to NY session start date, independently tested US and UK transition dates. |
| First valid setup per session | VERIFIED baseline semantics | It is actually **first detected inversion**, even if direction/ratio rejects; no replacement by a later qualifying setup. Whether 'first valid' was intended to mean after filters remains NOT YET VERIFIED from original source. |
| Entry at next M1 open | VERIFIED saved replay / NOT YET VERIFIED broker parity | 64 next opens match saved entries; current worker uses a quote after completion within a 15s candle-age bound, not a guaranteed historical open. |
| Structural stop and FVG/stop <=0.20 | VERIFIED in compared windows | Stop spans source FVG's third candle through inversion; ratio uses completed signal-minute close, not next fill. 64 ratios/stops match saved trades. |
| TP and breakeven | VERIFIED source/offline thresholds / NOT YET VERIFIED full paths | 1R target; +0.68R completed-bar touch creates next-open management in Ledger. Tests exercise its method. MT5 manages after polling, may defer for stops/freeze rules, and can only infer an entire candle's high/low after a mid-minute fill. No tick-level path or broker-native behavior proved. |
| Missing/stale candles | VERIFIED guards / known baseline difference | Worker rejects incomplete M15 buckets, duplicate minutes, non-UTC inputs, discontinuous paths and stale >15s closes. Ledger FVG concept uses dataframe adjacency and can span gaps. Clean-window agreement does not prove agreement on missing data. |
| Duplicate prevention/reconnect | VERIFIED, mocked worker | Original one_iteration in signal-only harness consumes one session before dispatch, repeated/reloaded same boundary does not repeat signal, bootstrap never catches up, recovery with earlier IFVG consumes without order. Disk/native crash safety not re-proved here. |
| Sizing parity | FAILED by explicit policy difference | Current risk_volume always 0.05 lots regardless of risk_pct; 0.15 aggregate own-lot ceiling. Saved run uses 1% risk sizing and portfolio leverage. No percentage risk cap in fixed-lot policy. |
| Original source/configuration completeness | NOT YET VERIFIED | Mechanical assumptions, effective historical exchange timezone, full archived engine/data environment, historical worker version and approval for direction change are incomplete. |
| Actual broker signals/fills match Ledger | NOT YET VERIFIED | Different feed/symbol, missing relevant broker candles, spreads/fill paths, timing and configuration prevent that claim. |

## 27 October and 9 November investigations

The retained diagnostics contain three distinct date interpretations. They must
not be collapsed into a single '9 November trade'.

| Candidate | Recorded facts | Conclusion |
|---|---|---|
| **27 October 00:15 UTC**, NY session 26 October | Long, FVG 4009.99–4011.12, stop 4004.08, signal close 4016.74. Ratio 1.13/12.66 = 0.0892575, accepted. first_ifvg logged at 00:15:02.401005; abort at 00:15:02.403005: minimum authorized/broker lot exceeds risk budget. | VERIFIED recorded acceptance/arithmetic and historical block reason. Today's 0.05-lot policy cannot explain that older percentage-risk decision. Candles and the exact then-running version/config are NOT YET VERIFIED. No confirmed fill is inferred. |
| **9 November 04:30 UTC**, NY session 8 November | Long, FVG 4122.01–4124.25, stop 4114.35, signal close 4124.75. Ratio 2.24/10.40 = 0.2153846 >0.20; rejected. evaluation gate session_blocked; earlier 03:31:23 stale close was 23.140015s late. | VERIFIED stored ratio rejection and recorded feed block. Even without the block, these recorded fields fail the filter. Underlying M1/M15 reconstruction is NOT YET VERIFIED. |
| **NY session 9 November**, confirmation 10 November 02:00 UTC | Short, FVG 4128.50–4128.57, stop 4141.46, close 4127.58, ratio 0.00504323. first_ifvg logged at 02:00:03.576719, then long_only at 02:00:03.588719. | VERIFIED recorded direction rejection. It would pass the both-direction arithmetic filter, but absence of original candles prevents an independent detection claim. |

Runtime diagnostics also show a 6 November candle-close lag of 10,882.961594s,
followed by an approximately three-hour jump to the current minute and a 28s
late-close rejection. This is a timestamp/feed discrepancy; without raw epochs
and deployed normalization metadata it is **not proof** of its root cause or
broker DST schedule. Recorded cycle-error categories include 35,694 stale/future
errors, 11 calibration timeouts and one unavailable UTC reference. Repeated
errors are not counts of missed trades, and these categories do not prove that
the clock/feed itself, rather than normalization, was wrong.

## Recommended changes, not implemented

Version BUY-only/fixed-size behavior as a distinct policy and confirm which saved
run is the reference. If both-direction run 214 is intended, propose direction
and NY weekday changes together; do not silently enable shorts. Preserve demo
controls. Keep current fixed-volume authorization separate from risk-sizing
research; record both without implying risk parity.

Retain sanitized, verified-UTC broker M1/M15 candles and offset provenance for
27 October, 9 November and transition periods. Compare the broker feed to itself
first, then measure OANDA/feed differences. Resolve historical candle timezone
conventions before changing normalization. Archive effective strategy/worker/
engine configuration and source hashes at each decision. Add an explicit,
reviewed candle-gap policy to the canonical baseline if operational parity with
the stricter worker is desired. Full fill/P&L/management parity remains a separate
offline validation gate; it cannot be inferred from matching entry signals.

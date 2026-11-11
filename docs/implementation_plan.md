# Implementation plan and acceptance gates

1. Completed: read-only source/service inventory, typed contracts, synthetic
   broker, SQLite queue/audit, policy gates, restart/uncertainty tests.
2. Expand synthetic lifecycle: cancellation/fill races, partial fills, closes,
   protection failures, account snapshots and loss/margin/campaign/rate limits.
   Gate: durable lifecycle and fail-closed fault tests across every send boundary.
3. Production persistence/owner design: versioned PostgreSQL migrations if moving
   beyond a local worker, backup/recovery, fencing and crash injection. Add a
   separate authenticated read-only health/timeline API with explicit simulation
   labels. Gate: database recovery and external-position divergence evidence.
4. Port Hurricane parser and pure T02 logic using sanitized frozen fixtures;
   enforce edits/cancellations and completed-candle/DST parity. No legacy imports
   or services are required for the simulator.
5. Only with next authorization: broker read-only capability check. Before any
   approved demo execution, verify identity, ownership, units, native stops and
   margin; fulfill all capability checklist items. Do not alter services now.
6. Synergy: authorized virtual display evidence, zone registry, repost handling,
   TP2/SL reentry lock and review-first shadow classification. No direct Telegram
   integration. Future dashboard outage must not interrupt runtime workers.

No approval is needed to run these offline tests. Real credentials, broker
connections, demo orders and service changes remain outside this assignment.

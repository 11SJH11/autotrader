# Synergy-specific requirements (updated 2026-11-11)

Synergy does **not** permit practical direct Telegram/API ingestion in this user's setup. Treat **virtual display screenshot capture** as the only viable input, not a fallback.

Future requirements (not Phase 0 implementation):

- Run the authorized messaging app on a persistent virtual graphical desktop. Physical display need not be on, but OS must stay awake; detect capture freezes, focus changes, scroll state, app logout and visual content truncation.
- Preserve timestamped screenshots (and hashes) as evidence; link extracted zones and campaign actions back to screenshots.
- OCR/vision interpretation must never execute an order directly. Emit versioned, confidence-labelled events; ambiguous cases require review.
- Build a persistent zone registry with: original post, side, entry range, layer prices, SL/TP1/TP2, historical first touch, filled/unfilled legs, invalidation, campaign re-entry eligibility, and linked source screenshots.
- Distinguish historical untouched zones, reposted zones, previously touched active campaigns, new setups, TP/SL updates and commentary. No reopening after SL or TP2. User may choose a stricter rule to cancel *unfilled* orders at TP1.
- Maintain a virtual pending-order book. Physical broker-pending orders are only sent after source, zone status, quote, risk, margin and account reconcilation checks.
- Cancel unfilled orders on condition (e.g. TP1/TP2 hit, invalidation, source update), with explicit race-condition testing: the broker may fill before cancellation is processed.
- If screen capture fails or becomes stale, disable new Synergy entries and apply a defined safe policy to unfilled broker orders; keep already-filled positions under broker-side protective stops.
- Shadow-mode classification and sample-by-sample user review must precede demo execution.

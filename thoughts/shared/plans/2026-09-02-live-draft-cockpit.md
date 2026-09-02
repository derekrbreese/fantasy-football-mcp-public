# Live draft cockpit implementation plan

## Outcome

Deliver all ten requested UX capabilities in one draft PR without weakening authoritative-ledger, privacy, exact-session, or recommendation-only boundaries.

## Phase 1: deterministic cockpit response

- Add bounded strategy sensitivity derived from the already evaluated candidate frontier.
- Add per-position boards with remaining tier counts and the next visible tier drop.
- Add aggregate eight-pick position-run alerts.
- Add roster slot current/required/open summaries and bounded construction warnings.
- Add position-aware fallback tiers.
- Add structured readiness checks.
- Add draft progress and ADP-based value/reach recap.
- Return explanatory readiness/recap data for blocked responses while suppressing candidate availability data.

Acceptance:

- No additional network calls or specialist scoring passes.
- Every new array is bounded and deterministic.
- Strategy scores use the same risk-data weight renormalization as the primary board.
- Blocked ledgers expose no position board, strategy pick, or fallback candidate.

## Phase 2: shared cockpit preference state

- Create a shared pure JS module with bounded allowlisting.
- Key preferences by validated exact league ID/session.
- Support add, remove, move, drafted reconciliation, and up-to-three comparison selections.
- Add opt-in notification settings and revision-based deduplication.

Acceptance:

- Malformed or oversized stored data is discarded or clamped.
- Drafted status requires conservative normalized name + position + team matching.
- No arbitrary server fields enter storage.

## Phase 3: full dashboard cockpit

- Add readiness checklist.
- Add reorderable watchlist with drafted markers.
- Add Overall/QB/RB/WR/TE/FLEX position filters and tier-drop context.
- Add strategy consensus/sensitivity cards.
- Add position-run alerts and fallback tiers.
- Expand roster construction with open slots and warnings.
- Add two-to-three-player comparison table.
- Add progressive/post-draft recap.

Acceptance:

- All controls use text-safe DOM APIs and accessible labels.
- The dashboard remains useful with zero queued or compared players.
- Blocked state retains recovery guidance and hides untrustworthy availability.

## Phase 4: sidebar queue and notifications

- Continue requesting five detailed cards while consuming the server-computed position boards from its already evaluated ranking frontier.
- Add compact exact-session queue controls.
- Keep the queue visible while a refresh is in flight.
- Add an opt-in notification toggle for next/on-clock states.
- Add cross-browser notification wrapper behavior and deduplication.

Acceptance:

- Notifications default off and ignore other leagues, repeated revisions, and non-urgent states.
- Queue operations never trigger a pick or Yahoo mutation.
- Narrow sidebar has no horizontal overflow.

## Phase 5: documentation, validation, and PR

- Update README, extension guide, improvements ledger, manifest/package version, and store disclosure if needed.
- Run all prescribed Python, JS, Ruff, compilation, and diff checks.
- Perform browser visual QA at dashboard and sidebar widths.
- Commit focused changes, push the existing branch, and open one draft PR against `main`.

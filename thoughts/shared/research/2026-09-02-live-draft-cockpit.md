# Live draft cockpit research

## Scope

Implement the ten requested live-draft UX capabilities in one PR: watchlist/queue, position and tier board, strategy comparison, position-run alerts, roster construction, fallback tiers, readiness, turn notifications, quick comparison, and post-draft recap.

## Existing strengths

- The numbered Yahoo Round-by-Round ledger is already authoritative and fail-closed.
- `reconcile_live_draft` returns the exact picks, user roster, current pick, next user pick, pick distance, team count, and structured health.
- The deterministic engine already evaluates every available ranking with value, roster, draft-dynamics, opponent, risk/news, and scenario scores before truncating the response.
- The response already includes all picks and component scores, so the cockpit does not need new Yahoo calls or another external provider.
- The sidebar already auto-refreshes exact-session recommendations and discards cross-league or stale responses.
- The dashboard already renders roster counts, recent history, detailed candidate scores, and data-quality checks.

## Gaps

- Only the selected strategy's truncated board is returned; sensitivity cannot be computed accurately from a small client-side subset.
- Position-run evidence exists only inside each candidate's specialist detail, not as an at-a-glance alert.
- Roster slots are scored but not summarized as current/required/open starter slots.
- The contingency contract names one alternate rather than bounded position-aware tiers.
- There is no exact-league user preference model for a queue or comparison selections.
- Browser notifications are not exposed through the cross-browser wrapper and are not opt-in.
- Draft completion and value/reach decisions are not summarized.

## Architecture findings

1. Compute all evidence-heavy cockpit sections inside the deterministic engine while the full evaluated frontier is available. This preserves consistency, bounds output, and adds zero network calls.
2. Keep user-authored queue and comparison state client-local and keyed by the exact `sport:leagueId` session identity. The dashboard and extension have separate browser origins, so preferences are intentionally surface-local rather than pretending to synchronize. Do not migrate earlier numeric-league-only keys because their sport provenance is unknowable.
3. Add one shared pure JavaScript cockpit-state module for sanitization, identity matching, queue ordering, drafted-state reconciliation, comparison limits, and notification deduplication.
4. Put the dense cockpit in the full dashboard. Keep the sidebar draft-clock focused: decision brief, compact queue, and opt-in turn notifications.
5. Continue suppressing candidate-derived cockpit sections whenever the ledger is blocked. Readiness and recap progress may still explain the block without inventing availability.

## Safety constraints

- No new Yahoo or third-party calls.
- No URLs, cookies, credentials, raw browser fields, or arbitrary response fields in preferences.
- Queue identity uses bounded normalized player name, position, and NFL team; at most twenty entries.
- Notifications are disabled by default, deduplicated per exact session revision/turn state, and never draft a player.
- Strategy and return probabilities remain explicitly uncalibrated.
- Post-draft recap labels ADP decisions as heuristic and does not predict team performance.

## Testing implications

- Python tests should validate deterministic cockpit contracts, blocking, bounds, strategy sensitivity, runs, roster slots, fallbacks, readiness, and recap.
- JavaScript tests should validate preference sanitization, exact-league storage keys, queue movement, drafted reconciliation, comparison bounds, notification deduplication, and inert rendering.
- Browser QA should cover the 380px sidebar and responsive dashboard with long player names and partial/complete draft states.

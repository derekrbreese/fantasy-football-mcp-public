# Improvement Backlog

This list separates the delivered live-draft baseline from prioritized follow-up work. Safety gates should remain in place while recommendation quality evolves.

## Delivered baseline

- Firefox/Chrome Manifest V3 recorder with authoritative Round-by-Round backfill and last-pick capture.
- Private loopback sync plus sanitized JSON/CSV export.
- Complete pick, fantasy-team, order, and user-roster context.
- League-specific state validation and rollback-resistant private persistence.
- Deterministic value, roster, dynamics, opponent, risk/news, scenario, and critic specialists.
- Yahoo roster-slot support, including Superflex aliases.
- Conservative player resolution, stale-state warnings, and incomplete-ledger blocking.
- Popup ledger diagnostics with exact missing, duplicate, and sanitized unnumbered-pick details, plus guarded authoritative full repair.
- Per-league browser storage, durable repair/reset reconciliation, and a cross-browser extension-background lock broker with bounded heartbeats, expiring session fences, and cooperative lease cancellation instead of Firefox content-script Web Locks.
- Persistent Firefox Draft Assistant sidebar with explicit league selection, shared fail-closed recommendation components, and no Yahoo-page injection or draft actions.
- Loopback-only local dashboard with a configurable recommendation board, roster and draft-history views, specialist comparisons, critic checks, and data-source diagnostics.
- Exact-session local DraftSheets/JSON profile import with bounded parsing, private atomic storage, and zero Yahoo API calls when a matching profile is available.
- Optional bounded FantasyPros injury/news enrichment with explicit API-tier coverage, one-request-per-second pacing, a private persistent 95-call UTC-day budget, FantasyPros attribution, rate-limit backoff, freshness, and per-player unknown-data weighting. Normalized base catalog/injury/news snapshots persist in a private SQLite cache (24-hour complete catalog; five-minute partial-catalog/injury/news retry), so fresh data survives restart without another request; failed refreshes can use stale identity data only while recommendation status/headlines remain unknown.
- Strict local recommendation UI boundary: allowlisted bounded requests, exact saved/Yahoo league resolution, authenticated-team verification, serialized Yahoo calls, post-scoring snapshot revalidation, and no-store responses.
- Uncalibrated labels for heuristic probabilities and confidence.
- Beginner-facing local usage documentation covering API-free live drafts, Instant Mock Draft profile reuse, Firefox/Chrome loading, state-before-recommendation diagnosis, reset versus repair, and privacy-safe troubleshooting.
- Passing Python and extension test suites for the delivered paths.

## P0 — correctness and evidence

1. **Historical calibration suite**
   - Replay completed drafts and measure top-pick hit rate, regret versus later availability, Brier score, and calibration error.
   - Keep all probability outputs labeled uncalibrated until thresholds are defined and met.

2. **Stable Yahoo player identity**
   - Capture and propagate only the extracted Yahoo `player_key` identifier where available; never persist or transmit the containing player URL.
   - Use IDs before conservative normalized-name matching; add fixtures for suffixes, same-surname players, trades, and DST aliases.

3. **Auditable injury/news enrichment**
   - Delivered the optional provider contract with normalized status, source/item/snapshot timestamps, bounded persistent base snapshots and in-memory targeted identity lookups, a private fail-closed daily request budget, explicit limited-coverage warnings, and unknown-data semantics.
   - Next, evaluate identity coverage, cache hit/refresh behavior, and freshness against completed draft snapshots before treating provider coverage as comprehensive.

4. **Scoring-settings fidelity**
   - Roster-slot and team-count import is delivered for the local DraftSheets path.
   - Incorporate passing-touchdown values, reception scoring, TE premiums, keeper costs, and custom roster eligibility into value and roster scoring; imported point values are not yet modeled directly.

## P1 — recommendation quality and latency

1. **Value-over-replacement and tier scarcity**
   - Replace rank-only value components with position-specific replacement levels and tier-drop penalties.

2. **Opponent roster model**
   - Model each intervening team's open starters, position depth, and observed drafting tendencies instead of relying only on ADP.

3. **Scenario performance budget**
   - Pre-index drafted identities and limit simulation to a preselected candidate frontier.
   - Add a draft-clock latency benchmark and enforce p50/p95 budgets for maximum supported inputs.

4. **Sensitivity reporting**
   - Report when the primary pick changes across conservative, balanced, and aggressive weights.
   - Make critic checks actionable rather than merely descriptive.

5. **Contingency depth**
   - Generate position-aware fallback tiers for each pick before the user's next turn, not only one alternate sentence.

## P2 — operations and usability

1. **Signed Firefox distribution**
   - Package and sign the extension so it survives browser restarts without temporary installation.

2. **Local session management**
   - The extension popup now provides an explicitly confirmed, exact-active-session mock reset with durable browser/server tombstones and profile preservation.
   - The loopback dashboard now lists privacy-minimal saved profile summaries with validated sport and source/import dates, explicitly rebinds a chosen ranking/settings profile to a new mock identity without copying picks or silently choosing a source, rejects cross-sport reuse server-side, and bounds list/bind waits.
   - Add explicit list/delete/reset MCP tools for other saved local draft sessions with league-scoped confirmation and no caller-controlled filesystem paths.
   - Add retention limits for old completed drafts.

3. **End-to-end protocol tests**
   - Start FastMCP on an ephemeral port, post a sanitized recorder fixture, invoke both live-draft MCP tools, and verify privacy and blocking behavior.

4. **Recorder health UI**
   - Surface last successful sync time, server version, state age, ledger completeness, and actionable recovery instructions.

5. **Observability without sensitive data**
   - Add local structured timing and error categories while excluding player URLs, credentials, cookies, and raw page content.

6. **Legacy modernization**
   - Migrate Pydantic V1 validators/configuration and modernize typing incrementally.
   - Enable broader Ruff rules only after existing debt is reduced without unrelated formatting churn.

## Acceptance principles

- A quality improvement must not weaken ledger completeness, privacy, league isolation, or uncertainty labeling.
- New probability claims require replay evidence and documented calibration metrics.
- New external data must have attribution and freshness semantics.
- Maximum-input recommendation latency must remain bounded and tested.

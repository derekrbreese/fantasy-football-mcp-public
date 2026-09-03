# Improvement Backlog

This list separates the delivered live-draft baseline from prioritized follow-up work. Safety gates should remain in place while recommendation quality evolves.

## Delivered baseline

- Firefox/Chrome Manifest V3 recorder with authoritative Round-by-Round backfill plus strict rendered-card capture from the active Picks tab and last-pick banner. Secondary capture accumulates virtualized windows without auto-scrolling, cannot repair or replace the ledger, and stays recommendation-blocked until an authoritative baseline exists.
- Private loopback sync plus sanitized JSON/CSV export.
- Complete pick, fantasy-team, order, and user-roster context.
- League-specific state validation and rollback-resistant private persistence.
- Deterministic value, roster, dynamics, opponent, risk/news, scenario, and critic specialists.
- Yahoo roster-slot support, including Superflex aliases.
- Conservative player resolution with explicit equivalent NFL team-code aliases, stale-state warnings, and incomplete-ledger blocking.
- Popup ledger diagnostics with exact missing, duplicate, and sanitized unnumbered-pick details, plus guarded authoritative full repair that distinguishes strictly numbered pre-rendered future rows using stable current-pick evidence. Unsafe or ambiguous capture persists a privacy-safe per-league server blocker until a verified coherent scan or accepted repair clears it.
- Per-league browser storage, durable repair/reset reconciliation, and a cross-browser extension-background lock broker with bounded heartbeats, expiring session fences, and cooperative lease cancellation instead of Firefox content-script Web Locks.
- Bounded automatic live updates: Yahoo DOM bursts coalesce behind a quiet window with a maximum-delay scan and one serialized dirty replay; sidebar sync-metadata writes cannot cancel same-revision work; and the visible loopback dashboard polls a privacy-minimal exact-league revision with latest-only rendering, quiet retries, and bounded backoff.
- Persistent Firefox Draft Assistant sidebar with explicit league selection, shared fail-closed recommendation components, and no Yahoo-page injection or draft actions.
- Clock-aware at-a-glance decision brief in the sidebar and dashboard with explicit on-clock/next/picks-away states, a primary pick, two immediate fallbacks, and a guarded plain-R refresh shortcut. It uses reliable pick distance and does not invent a countdown timer.
- Transparent uncalibrated Value, Sleeper Watch, Fade, and take-now/can-wait signals shared by the sidebar and dashboard. They require real ADP plus dated same-season provenance, use league-round thresholds, fail closed for defective ledgers or unresolved drafted identities, preserve raw metrics, surface only fresh attributed risk cautions, and explain bounded exclusions without claiming breakout performance.
- Bounded deterministic next-two-selections planning that preserves the primary recommendation, proposes position-aware fallback/next-turn combinations, labels actual-ADP availability as uncalibrated, and omits or degrades future projections when ledger, order, freshness, identity, team-count, or ADP evidence is uncertain.
- Evidence-gated Breakout Watch labels from strict local CSV/JSON projection fields. Labels require fresh, explicitly attributed projected points plus position-appropriate touches, targets, or receptions; use a stable full-ranking same-source cohort; remain uncalibrated; and are never inferred from ADP or headline sentiment. Missing evidence does not degrade ordinary recommendations.
- Exact-session-scoped personal queues in the dashboard and Firefox sidebar, with bounded reorder/removal controls, conservative drafted-player reconciliation, and no profile-to-mock copying.
- Full live draft cockpit with position/tier filtering, three-strategy sensitivity, recent position-run alerts, roster-slot gap warnings, position-aware fallback tiers, readiness checks, bounded quick comparison, and value/reach recap.
- Explicit opt-in, authoritative-ledger-only, deduplicated browser alerts when the user is next or on the clock; notifications remain advisory and never initiate a draft action.
- Packaged cross-browser football icons plus compact, overflow-safe FantasyPros source and recent-news presentation in the sidebar and dashboard.
- Loopback-only local dashboard with a configurable recommendation board, roster and draft-history views, specialist comparisons, critic checks, and data-source diagnostics.
- Exact-session local DraftSheets/JSON profile import with bounded parsing, private atomic storage, zero Yahoo API calls when a matching profile is available, and current-season per-sport defaults for future profileless recorder drafts without overwriting exact profiles or copying picks.
- Optional bounded FantasyPros injury/news enrichment with factual bounded-snapshot disclosures, one-request-per-second pacing, a private persistent 95-call UTC-day budget, FantasyPros attribution, rate-limit backoff, freshness, and per-player unknown-data weighting. Normalized base catalog/injury/news snapshots persist in a private SQLite cache (24-hour complete catalog; five-minute partial-catalog/injury/news retry), so fresh data survives restart without another request; failed refreshes can use stale identity data only while recommendation status/headlines remain unknown. Targeted recent-news identity calls are skipped once every requested ranking already has an exact identity, avoiding irrelevant warnings and request-budget use.
- Official scoring-specific FantasyPros preseason RB/WR/TE projection snapshots with strictly normalized points and opportunity volume, same-season explicit STD/PPR catalog ADP, cached official HALF-PPR consensus ADP, a 10-second enrichment deadline, private bounded cache migration, and stale provenance. HALF-PPR uses the provider's `type=ADP` consensus response rather than interpolation or ECR substitution. Conservative matches against a normalized Sleeper active-player catalog add genuine `years_exp` evidence, cached privately for 24 hours with a 45-day stale fallback, so complete FantasyPros + Sleeper cohorts can produce Breakout Watch labels without changing deterministic scores. Both providers share a provider-neutral SQLite cache; legacy FantasyPros SQLite and Sleeper JSON stores migrate automatically. A metadata-only CLI can warm or explicitly refresh the Sleeper snapshot for scheduled jobs.
- Optional disabled-by-default Databricks advisory critic with public OSS SDK dependencies, unified authentication, a strict identity-free outbound allowlist, bounded timeout/output, coalescing and in-memory-only caching, fail-open behavior, and no authority to reorder, score, select, or draft a player.
- Strict local recommendation UI boundary: allowlisted bounded requests, exact saved/Yahoo league resolution, authenticated-team verification, serialized Yahoo calls, post-scoring snapshot revalidation, and no-store responses.
- Uncalibrated labels for heuristic probabilities and confidence.
- Beginner-facing local usage documentation covering API-free live drafts, Instant Mock Draft profile reuse, Firefox/Chrome loading, state-before-recommendation diagnosis, reset versus repair, and privacy-safe troubleshooting.
- Passing Python and extension test suites for the delivered paths.

## P0 — correctness and evidence

1. **Historical calibration suite**
   - Replay completed drafts and measure top-pick hit rate, regret versus later availability, Brier score, and calibration error.
   - Keep all probability outputs labeled uncalibrated until thresholds are defined and met.

2. **Stable Yahoo player identity — delivered**
   - The recorder captures and propagates only a validated numeric Yahoo `player_key` where Yahoo exposes one; the containing URL, query parameters, and arbitrary attributes are never persisted or transmitted.
   - Equal keys take precedence over conservative normalized-name matching, unequal keys fail closed, and one-sided missing keys retain the position/team-aware fallback for old sessions and profiles. Sanitized fixtures cover suffixes, same-surname players, DST aliases, hostile attributes, external hosts, and malformed URLs.
   - Remaining evidence work: measure key availability across live Yahoo layouts and completed drafts before removing the conservative fallback.

3. **Auditable injury/news enrichment**
   - Delivered the optional provider contract with normalized status, source/item/snapshot timestamps, bounded persistent base snapshots and in-memory targeted identity lookups, a private fail-closed daily request budget, explicit limited-coverage warnings, and unknown-data semantics.
   - Next, evaluate identity coverage, cache hit/refresh behavior, and freshness against completed draft snapshots before treating provider coverage as comprehensive.

4. **Scoring-settings fidelity**
   - Roster-slot, team-count, and strict `STD`/`HALF`/`PPR` reception-format import are delivered for the local DraftSheets path.
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
   - Delivered deterministic conservative, balanced, and aggressive primary-pick comparison in the live cockpit.
   - Next, make critic checks actionable rather than merely descriptive.

5. **Contingency depth**
   - Delivered bounded position-aware fallback tiers from the current trustworthy ranking pool and a deterministic primary-now plus next-turn combination planner.
   - Next, evaluate fallback and two-pick usefulness in draft replays, calibrate any availability estimates, and model opponent-specific depletion before the user's next turn.

## P2 — operations and usability

1. **Signed Firefox distribution**
   - Package and sign the extension so it survives browser restarts without temporary installation.

2. **Local session management**
   - The extension popup now provides an explicitly confirmed, exact-active-session mock reset with durable browser/server tombstones and profile preservation.
   - The loopback dashboard now lists privacy-minimal saved profile summaries with validated sport and source/import dates, explicitly rebinds a chosen ranking/settings profile to a new draft identity without copying picks, rejects cross-sport reuse server-side, and bounds list/bind waits. Users may also explicitly set or clear one per-sport default for future profileless drafts; existing exact profiles always win.
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

# Yahoo Fantasy Draft Recorder

A Firefox- and Chrome-compatible Manifest V3 extension that records completed Yahoo Fantasy Football picks in the current browser profile, syncs sanitized context to the private local MCP server, exports CSV/JSON, and presents local recommendations. It does not change the Yahoo page or select a player for you.

For the complete server, `.env`, Yahoo API, and FantasyPros setup, start with the [main usage guide](../README.md).

## Before loading the extension

Install the project dependencies and start the loopback server from the repository root:

```bash
HOST=127.0.0.1 PORT=8765 uv run python fastmcp_server.py
```

Leave it running throughout the draft. Always use `127.0.0.1` for desktop use; the MCP transport has no local authentication boundary. Restart this process whenever `.env` credentials or API keys change.

The recorder still saves picks and can export Agent JSON when the server is offline, but recommendations and server-side reset/repair synchronization need this process.

## Install in Firefox

Firefox 142 or newer is required.

1. Open `about:debugging`.
2. Select **This Firefox**.
3. Select **Load Temporary Add-on…**.
4. Choose this directory's `manifest.json`.
5. Open or reload the exact Yahoo draft tab.

Use **Reload** in `about:debugging` after extension code changes, then reload the Yahoo tab. A temporary add-on disappears when Firefox restarts. Permanent installation in standard Firefox requires a Mozilla-signed package.

## Install in Chrome

Chrome 121 or newer is required.

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Select **Load unpacked**.
4. Choose the `chrome-extension` directory.
5. Open or reload the exact Yahoo draft tab.

Use **Reload** on the extension card after code changes, then reload the Yahoo tab.

## Draft-day workflow

Follow this order for each live draft or Yahoo mock:

1. Start the local server.
2. Load/reload the extension and the exact active draft tab.
3. In Yahoo, open **Results → Round by Round**.
4. Open the recorder popup and select **Rescan page**.
5. Check that the popup shows the expected league and pick count, no ledger defects, and **agent sync connected**.
6. Select **Full dashboard** from this popup so it opens with the exact selected league.
7. Import a local rankings profile or explicitly reuse one already saved for another mock.
8. Open Firefox's **Draft Assistant** sidebar or remain in the dashboard, then refresh recommendations.
9. When diagnosing any recommendation, inspect live draft state before requesting a next pick.

The smaller Yahoo last-pick banner and the active Yahoo **Picks** tab continue supplying new observations while other draft panels are visible. Picks-tab capture accepts only strict, currently rendered numbered cards from the semantic Queue/Picks tab control; Queue entries are ignored. It never scrolls the panel automatically; virtualized windows accumulate as Yahoo renders them and on manual rescan. Return to **Results → Round by Round** and rescan when anything looks incomplete; its numbered table remains the only authoritative ledger and repair source.

## What is recorded

When Yahoo exposes each field, a pick contains:

- Overall pick number
- Round and pick within the round
- Player name
- Extracted numeric Yahoo player key, when Yahoo exposes one
- NFL position and team
- Fantasy team/manager label
- Whether Yahoo labeled it **Your Team**
- Local recording timestamp

The extension records only sanitized draft context. A Yahoo player link may be inspected ephemerally only on the expected Yahoo host and a recognized player path; only its validated `game_key.p.player_id` value is retained. The link, query parameters, and other attributes are discarded. The extension does not record Yahoo credentials, cookies, chat, arbitrary page text, or the draft's page location.

## Popup, sidebar, and dashboard

### Recorder popup

The popup is the operational control surface:

- **Rescan page** asks the active Yahoo tab for current picks and forces loopback sync.
- **Full rescan & repair** safely replaces a defective saved ledger from the complete visible Round-by-Round table.
- **Save diagnostics** downloads privacy-minimal structural counters for Yahoo layout troubleshooting.
- **Export CSV** downloads the picks for spreadsheet use.
- **Agent JSON** downloads recommendation-ready state for a manual agent handoff.
- **Reset mock draft** clears only the exact active mock/session while preserving its imported profile.
- **Open Draft Assistant** opens Firefox's persistent recommendation sidebar.
- **Full dashboard** opens the wider local web UI; Chrome users use this instead of the Firefox sidebar.

The popup displays picks and recorder health, not recommendations, and it cannot make a draft selection.

### Firefox Draft Assistant

The sidebar shows up to five recommendations beside Yahoo. Above the detailed cards, an at-a-glance decision brief shows whether you are on the clock, next, a known number of picks away, or missing reliable turn timing; it also keeps the primary recommendation and two immediate fallbacks visible together. Its queue and alert preference are stored only for the exact Yahoo session identity (sport plus league ID) in extension storage. Numeric-league-only preference keys from earlier development builds are intentionally not migrated because their sport provenance cannot be proven. Optional browser alerts fire only for a complete, current ledger when you are next or on the clock, and are deduplicated per recorded revision. Press plain **R** outside a form control for a manual refresh. The sidebar does not invent a countdown clock from pick distance.

It selects a league only from the active draft tab or an explicit saved-league choice; it never silently chooses the newest saved session. After the initial request, a newer pick for that exact league cancels stale work, debounces duplicate events, and refreshes automatically. Another league's storage updates do not affect it.

Cards show roster fit, rank/ADP/tier/bye context, reasoning, injury/news risk, and explicitly uncalibrated confidence and return/simulation probabilities. They also show compact **Value**, **Sleeper Watch**, and **Fade** market badges plus **Take now**, **Can wait**, or **Timing unknown** reasoning. Value is at least one league round past real ADP; Sleeper Watch is real ADP in Round 7 or later with source rank at least one league round ahead; Fade is source rank at least one league round behind real ADP. Sleeper Watch is a market discount, not a breakout or performance prediction, and Fade is a caution rather than a command. Take now uses an uncalibrated below-50% return estimate; Can wait uses 50% or above. Raw rank, ADP, discount, and return metrics remain visible.

Market labels fail closed unless ADP was explicitly supplied, the ranking source has a same-season source date (or Yahoo retrieval date), the authoritative ledger is complete, and drafted identities resolve conservatively. A local import timestamp is shown for diagnostics but cannot certify an undated sheet's ADP. Missing ADP may use rank only inside legacy scoring; the UI says ADP is unavailable and never relabels rank as market data. The bounded Sleeper Watch shows at most five deterministically ordered players from the supplied ranking frontier and discloses known drafted, unresolved-identity, no-real-ADP, and hidden-qualifier counts. Fresh attributed risk evidence adds a caution; stale or missing evidence remains unknown. Stale state, inferred team counts, unavailable roster settings, and unknown injury/news data are also visibly degraded. FantasyPros coverage markers are described as bounded snapshots; when its feed works but no fresh injury record matches the player pool, the UI says that missing status does not mean healthy instead of calling the feed unavailable. When the server's optional Databricks critic is enabled and available, a separate advisory-only summary appears after the unchanged deterministic recommendations.

Cards may also show an evidence-backed **Breakout Watch** label. A bounded **Next two selections** section keeps the deterministic primary, adds up to two immediate fallbacks, and—when snake order is known—shows up to three position-aware next-turn combinations. Its availability percentages are uncalibrated actual-ADP heuristics, not promises; missing ADP is unknown, opponent-specific rosters/tendencies are not yet modeled, and users should refresh after every recorded pick. An incomplete ledger blocks planning. Breakout Watch requires fresh, explicitly attributed projected points plus position-appropriate opportunities and experience evidence; it is never inferred from ADP or news. Missing breakout evidence does not degrade ordinary recommendations.

### Full dashboard

Open it from the popup or visit `http://127.0.0.1:8765/draft-dashboard` while the server is running. Opening it from the popup carries the exact league ID in a browser fragment, which is not sent in the initial dashboard GET.

The dashboard shows the same clock-aware decision brief, market badges, take-now/can-wait reasoning, and bounded Sleeper Watch, plus a broader live cockpit: personal queue, position/tier filters, three-strategy sensitivity, recent position-run alerts, configured roster-slot gaps, fallback tiers, readiness checks, quick comparison, and a value/reach recap. The prior cockpit remains available while a refresh is computing. It also includes up to twenty detailed candidates, recent draft history, specialist scores, critic checks, simulations, and source/quality diagnostics. Expand the market definitions, trust checks, and bounded-exclusion details when auditing a label. It and the sidebar use text-only DOM rendering and never inject controls into Yahoo.

## Import a local rankings profile

Importing a profile is the recommended live-draft path when Yahoo has not approved Fantasy API access or when an Instant Mock Draft cannot be resolved as a normal league.

1. Open and scan the target draft first.
2. Open **Full dashboard** from its popup.
3. Confirm the league ID, team count, and roster slots.
4. Choose one supported local file:
   - DraftSheets 2026 `.xlsx`, up to 2 MB
   - ECR `.csv` with Rank/ECR, Player Name, and Position; Team, ADP, and Bye are optional
   - Strict `schemaVersion: 1` `.json`
5. Select **Import profile**, then refresh recommendations.

The server keeps only the top 500 sanitized rows, roster settings, and safe provenance for that exact sport/league/team/session identity. Raw workbook bytes are parsed in memory and discarded. Filenames, formulas, notes, URLs, and arbitrary cells are not stored.

Generic CSV and strict JSON may also supply optional, explicit Breakout Watch evidence for RB/WR/TE players. CSV uses the six all-or-none columns `Projection Source`, `Projection As Of` (or `Projection Date`), `Projected Points`, `Projected Opportunities`, `Opportunity Kind`, and `Experience Years`; JSON uses the equivalent complete `breakout_evidence` object. RB uses projected `touches`; WR/TE may use projected `targets` or `receptions`. The source must be a safe attribution label rather than a URL, and the evidence date must match the profile season. DraftSheets `.xlsx` does not import these optional fields.

A label appears only for fresh evidence at most 45 days old, a same-position/source/opportunity-kind cohort of at least five players, no more than three experience years, and projected points plus opportunities at or above the cohort's 60th percentile. It is marked uncalibrated, remains stable as players are drafted because the full bounded ranking cohort is used, and is never inferred from ADP or headlines. Missing evidence omits the label without degrading normal recommendations. FantasyPros news remains risk-only and does not create a breakout label.

Google Drive is optional storage only, not a runtime integration. Download a local `.xlsx` from Drive or export the ECR tab from Google Sheets as CSV, then import that file through the dashboard.

With an exact local profile, recommendations use the imported rankings and settings and make zero Yahoo API calls. The extension remains the source for every drafted player.

## Instant Mock Drafts and profile reuse

Yahoo Instant Mock Drafts use the normal scan/sync flow. Scan first and bind a local profile or configure a per-sport default before requesting advice. If the dashboard says the Yahoo league identity could not be resolved, use the local-profile path; refreshing OAuth is not a fix for a mock that Yahoo's Fantasy API does not list as a league.

Each newly created mock has a new identity. To reuse rankings without uploading them again:

1. Open and scan the new mock.
2. Open **Full dashboard** from the new mock's popup.
3. Under **Reuse a saved profile**, explicitly choose the prior source.
4. Select **Use for this draft & refresh**.

For repeated mocks, the dashboard's **Default for future drafts** form can store one explicit saved source per sport. A first recommendation for a new profileless recorder identity binds that source before Yahoo fallback. Existing exact profiles always win, changing or clearing the default affects only future unbound drafts, and the server copies only sanitized rankings, roster settings, and provenance—never picks. The source must match the current UTC year when selected and when bound; replace or clear it after a season rollover.

Yahoo uses the same draft-client URL shape for mocks and real drafts, so the extension cannot safely infer mock status without retaining unreliable page data. The sport default therefore applies to future profileless real drafts as well as mocks. The dashboard labels that scope; keep manual binding if your Yahoo leagues use different settings.

## Reset versus ledger repair

Use these controls for different outcomes.

### Reset mock draft

Use Reset when starting the same exact mock over:

1. Keep the server running and close older tabs for that same mock/session.
2. Open the exact active mock tab and rescan it.
3. Select **Reset mock draft** and confirm the destructive action.
4. Wait for the popup to rescan after the server's reset timestamp.

The reset clears only that exact browser/server draft session and preserves its separate imported profile. If the server reports that the draft changed, no deletion occurs; rescan and confirm Reset again. Reset is disabled when the popup can show only a saved/latest draft instead of the exact active identity.

### Full rescan & repair

Use Repair when keeping the current draft but fixing missing, duplicate, unnumbered, or phantom high picks:

1. In Yahoo, show the complete current **Results → Round by Round** table.
2. Open the popup and select **Full rescan & repair**.
3. Confirm that you intend to replace the saved picks from that authoritative table.

Repair proceeds only when every completed row has the expected safe shape, responsive copies agree, numbered picks are contiguous and unique, and the ledger is current. Yahoo can pre-render numbered rows for later picks; the recorder ignores an unparsed future row only when its pick text is a positive integer at or beyond one stable, rendered, non-conflicting Yahoo current-pick marker. Any earlier unparsed row, ambiguous or malformed row, unavailable or conflicting current-pick marker, or marker change during the scan fails closed. Lowering a saved maximum additionally requires Yahoo's current-pick evidence. The server must accept the staged replacement before the browser writes it; failure leaves the existing exact session unchanged.

Ordinary automatic scans never replace a saved authoritative ledger with a shorter visible prefix. An unsafe authoritative evaluation cannot replace the numbered ledger, while safely parsed ordinary observations continue through the conservative merge and sync path so newly visible gaps can still block recommendations. Picks-tab cards are secondary observations only: they never enter repair or authoritative replacement, never clear capture uncertainty, and their Yahoo injury badges are not treated as injury/news evidence. Numbered secondary observations recorded before a verified Round-by-Round baseline are retained but set one allowlisted boolean capture blocker for that exact league. It contains no DOM text or error details and makes the server block recommendations even when the picks look contiguous. A coherent authoritative scan clears it only when one stable positive Yahoo current-pick marker exactly follows the ledger maximum; without that evidence, the scan may update picks but preserves the prior capture decision. An accepted repair may also clear it. A scan with no Round-by-Round table leaves the prior server decision unchanged, so old clients and non-authoritative views cannot accidentally clear a block. After a baseline exists, equivalent panel/banner/ledger identities deduplicate; incompatible identities at the same overall pick remain explicit duplicates. If secondary capture later fills an already saved gap, the pick is retained but capture remains blocked until Round-by-Round verification or repair.

## FantasyPros evidence

When `FANTASY_PROS_API` is set on the server, recommendations receive FantasyPros-attributed status, recent headlines, and cached preseason RB/WR/TE projection evidence. Matching candidate cards render the allowlisted projected points and position-appropriate opportunities as inert, attributed evidence with explicit season, scoring, freshness, and retrieval provenance. The extension never receives the key. FantasyPros does not supply experience years, so its projection evidence alone does not create a Breakout Watch label.

The first FantasyPros-enabled recommendation creates or safely migrates the private SQLite cache automatically; there is no separate prefetch step. A complete normalized catalog and preseason projection snapshot lasts 24 hours; a provider-limited partial catalog and injury/news snapshots are retried after five minutes. The full enrichment phase has a 10-second outer deadline. Projection scoring uses an explicit imported setting when available and otherwise reports a `HALF` default; a missing league season similarly uses the current UTC year with a warning. `STD` and `PPR` can use their explicit same-season catalog ADP fields; half-PPR ADP remains unavailable rather than guessed. Projections do not establish NFL experience, so they cannot independently create a breakout label.

See [FantasyPros evidence cache](../README.md#fantasypros-evidence-cache) for request limits, terms/attribution, cache contents, and privacy behavior.

## Optional Databricks advisory critic

The sidebar and dashboard can render the server's optional Databricks advisory section, but the extension never authenticates to Databricks and stores no model response or credential. Install and enable it on the local server as described in [Databricks advisory critic](../README.md#databricks-advisory-critic); it remains disabled by default.

The deterministic recommendation order and scores are authoritative. The advisory model cannot reorder candidates, select or draft a player, or feed values back into scoring. It is skipped for an incomplete or defective authoritative ledger. Stale state can still yield degraded deterministic candidates; staleness is sent as a bounded quality flag and remains visibly degraded in the response. Disabled/skipped output is omitted; provider failure is shown as bounded unavailable context while the deterministic cards remain usable.

Only an identity-free allowlist leaves the local server: anonymous candidate ordinal and position, overall/component scores, explicitly uncalibrated return probability, normalized risk status, aggregate roster position counts, recent pick positions, current/next overall-pick numbers, and bounded quality flags. It excludes player names/keys, league/session/team IDs, the pick ledger, news/headlines, URLs, browser fields, and credentials. Results use a bounded in-memory-only cache and are rendered as inert text after the recommendation cards.

## Agent handoff

### Automatic MCP sync

After a changed scan, the extension posts sanitized context to the loopback-only `/draft-sync` route. An MCP client should:

1. Call `ff_get_live_draft_state` with the exact `league_id`.
2. Check ledger, state-age, and identity warnings.
3. Call `ff_get_live_draft_recommendation` with the same `league_id` only when the state is ready.

The sidebar/dashboard sends only an allowlisted league ID and bounded strategy/count/ranking/simulation settings to the local recommendation route. The server independently loads the exact saved session and profile, then rechecks both after scoring. If a pick or profile changed mid-request, it discards the candidates and asks for a refresh.

### Manual export

Select **Agent JSON** to download:

- The complete ordered pick ledger
- Your roster
- Rosters grouped by fantasy team
- Current and next overall-pick numbers
- Sanitized draft identifiers

CSV remains available for spreadsheet use.

## Troubleshooting

### Agent sync is offline

- Confirm the server is running at `127.0.0.1:8765`.
- Reload the extension and exact Yahoo tab.
- Open **Results → Round by Round** and select **Rescan page**.
- If an `.env` key just changed, restart the server before rescanning.

### Firefox says `Permission denied to access property "then"`

The current extension avoids Firefox's content-script Promise boundary by serializing same-draft work through its background broker; no additional website permission is needed. Reload the temporary add-on in `about:debugging`, close stale tabs for that mock, reload the exact active tab, show **Results → Round by Round**, and rescan. If the error persists on the current checkout, use **Save diagnostics** before reporting it.

### Yahoo league identity could not be resolved

- Open and rescan the exact active tab rather than choosing a latest saved draft.
- Open the dashboard from that popup so its league ID is selected.
- For an Instant Mock Draft or unavailable Yahoo API, import, explicitly bind, or configure a saved local profile as the sport default, then refresh. This path should make zero Yahoo calls.
- For a normal league using Yahoo fallback, first verify `ff_get_leagues` returns the matching league and authenticated team.

### Popup reports ledger defects

It lists the exact missing/duplicate numbers and sanitized unnumbered details. Show the complete Round-by-Round table and use **Full rescan & repair**. Conflicting or malformed Yahoo table copies produce a recovery error instead of being treated as healthy.

### Yahoo rows were found but not parsed

Yahoo may have changed its layout. Select **Save diagnostics**. The report contains structural counters and allowlisted field-presence counts only; it excludes raw page text, CSS classes, test IDs, ARIA text, page locations, chat, and manager text.

## Correctness and privacy guarantees

- Numbered **Results → Round by Round** data is authoritative; gaps, duplicates, and unnumbered picks block recommendations.
- The active **Picks** tab is a rendered-card observation feed only; it never replaces or repairs the authoritative ledger and never auto-scrolls Yahoo.
- Per-league browser keys and server validation prevent one league from overwriting another.
- Durable repair/reset journals reconcile interrupted work before stale scanning or sync can resume.
- A packaged background lock broker serializes same-league scans, repairs, and resets across tabs; a lost lease aborts protected work and leaves the journal recoverable.
- Loopback endpoints allowlist fields, validate the exact session, cap payloads, restrict origins, and return recommendation responses with `Cache-Control: no-store`.
- Server-side draft/profile/cache files use user-only permissions.
- The extension stores no Yahoo credentials, cookies, OAuth parameters, full page locations, or arbitrary browser fields.
- Matching prefers equal validated Yahoo player keys. Unequal keys fail closed; a missing key uses the existing conservative name, position, and NFL-team fallback.
- Optional Databricks review receives no player identity, league/session/team identity, ledger, headline, URL, or credential; it cannot mutate recommendation order or scores.
- Recommendations never draft a player or inject controls into Yahoo.

## Store preparation

`manifest.json` declares a stable Firefox add-on ID, Firefox 142 and Chrome 121 minimum versions, no Firefox data collection, the `storage` and `notifications` permissions, narrowly scoped Yahoo draft-page access, and loopback access. Notifications are disabled until the user opts in from the sidebar and contain only turn status plus the current advisory recommendation. Transparent football icons are packaged at the browser toolbar, sidebar, add-on manager, and 128-pixel store sizes. [CHROMEWEBSTORE.md](CHROMEWEBSTORE.md) tracks listing copy, permission justifications, disclosures, and the remaining Chrome submission checklist.

## Tests

No package installation is required for extension tests; they use Node's built-in test runner:

```bash
npm --prefix chrome-extension test
```

Tests cover authoritative parsing, duplicate/gap persistence, privacy-minimal diagnostics, guarded repair/reset, cross-league storage, background-broker serialization/recovery, loopback sync and recommendation requests, explicit league selection, profile import/reuse, real-ADP market guards, bounded inert Sleeper Watch rendering, compact risk/news presentation, packaged icon dimensions, Firefox/Chrome compatibility, and export safety.

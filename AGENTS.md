# Agent Guide

## Project purpose

This repository provides a Yahoo Fantasy Football MCP server and a Firefox/Chrome draft recorder. The extension captures an authoritative draft ledger; the server combines that private local state with Yahoo league settings and rankings.

## Important entry points

- `fastmcp_server.py` — FastMCP HTTP server, loopback draft-sync route, and public tool registration.
- `fantasy_football_multi_league.py` — Yahoo API-backed legacy tools and stdio entry point.
- `chrome-extension/` — Manifest V3 draft recorder. Yahoo **Results → Round by Round** is the authoritative pick source.
- `src/services/live_draft_store.py` — sanitized, atomic, user-private live-state persistence.
- `src/services/live_draft_recommendation_service.py` — joins one league's local state with serialized Yahoo calls.
- `src/agents/live_draft_recommender.py` — deterministic specialist scorers and orchestrator.

## Safety and correctness invariants

- Never persist or log Yahoo credentials, cookies, full page URLs, query parameters, or arbitrary browser fields.
- Keep `/draft-sync` loopback-only. Preserve origin checks, payload limits, field allowlisting, atomic writes, `0700` directories, and `0600` files.
- Bind local live-draft HTTP sessions to `127.0.0.1`; `0.0.0.0` is only for an intentionally secured container or cloud deployment. The MCP transport itself has no local authentication boundary.
- Never mix state between leagues. Derive and validate `league_id` against `league_key` before loading a session.
- Treat the numbered Round-by-Round ledger as authoritative. Block recommendations for gaps, duplicate pick numbers, or unnumbered picks.
- Treat missing injury/news data as **unknown**, never healthy.
- Label confidence, opponent return probability, and scenario probability as uncalibrated until an evaluation demonstrates calibration.
- Degrade the critic when state is stale, team count is inferred, Yahoo roster slots are unavailable, or drafted identities are unresolved.
- Keep identity matching conservative. Initialed names require matching position and NFL team; DST may match by position and NFL team.
- Do not add network calls to specialist scorers. Yahoo calls belong in the service layer and remain serialized to avoid token-refresh races.
- Keep draft-clock work bounded. Clamp tool inputs and run CPU scoring with `asyncio.to_thread`.

## Development workflow

- Use Python 3.10 or newer.
- Add behavior through tests first for non-trivial fixes and features.
- Prefer focused changes over broad formatting of legacy files.
- Keep the deterministic recommendation engine credential-free and independently testable.
- Update `README.md`, `chrome-extension/README.md`, and `IMPROVEMENTS.md` when user-visible behavior or known limitations change.

## Validation

Run before committing:

```bash
uv run --extra dev pytest -q
npm --prefix chrome-extension test
uv run ruff check src/agents/live_draft_recommender.py \
  src/services/live_draft_recommendation_service.py \
  tests/unit/test_live_draft_recommender.py \
  tests/unit/test_live_draft_recommendation_service.py
python -m py_compile fastmcp_server.py fantasy_football_multi_league.py \
  src/agents/live_draft_recommender.py \
  src/services/live_draft_recommendation_service.py \
  src/services/live_draft_store.py

git diff --check
```

The legacy codebase has broader Ruff modernization debt. For modified legacy files, at minimum run fatal checks:

```bash
uv run ruff check --select E9,F63,F7,F82 <modified-files>
```

## Local live-draft use

Port 8000 may already be occupied. For recorder sync, run:

```bash
HOST=127.0.0.1 PORT=8765 python fastmcp_server.py
```

Then reload the temporary extension in Firefox, open Yahoo **Results → Round by Round**, and rescan. Call `ff_get_live_draft_state` before `ff_get_live_draft_recommendation` when diagnosing a blocked or degraded answer.

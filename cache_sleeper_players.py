"""Warm the private normalized Sleeper NFL player cache."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence

from src.services.sleeper_player_provider import SleeperPlayerProvider


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Cache normalized Sleeper RB/WR/TE identity and experience data. "
            "No draft, league, or ranking data is sent."
        )
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="refresh from Sleeper even when the existing 24-hour cache is fresh",
    )
    return parser


async def _warm(*, force: bool) -> tuple[int, dict[str, object]]:
    result = await SleeperPlayerProvider().warm_player_cache(force_refresh=force)
    status = result.get("status")
    output: dict[str, object] = {
        "status": status if isinstance(status, str) else "unavailable",
        "provider": "Sleeper",
        "catalogFetchedAt": result.get("catalogFetchedAt"),
        "cacheStale": result.get("cacheStale") is True,
        "refreshFailed": result.get("refreshFailed") is True,
        "catalogPlayers": (
            result.get("catalogPlayers")
            if type(result.get("catalogPlayers")) is int
            else 0
        ),
    }
    return (0 if output["status"] in {"success", "degraded"} else 1), output


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    exit_code, output = asyncio.run(_warm(force=arguments.force))
    print(json.dumps(output, separators=(",", ":"), sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

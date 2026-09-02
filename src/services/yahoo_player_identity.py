"""Strict normalization for allowlisted Yahoo Fantasy player identifiers."""

from __future__ import annotations

import re
from typing import Any

_YAHOO_PLAYER_KEY = re.compile(r"^[1-9][0-9]{0,9}\.p\.[1-9][0-9]{0,9}$")


def normalize_yahoo_player_key(value: Any) -> str | None:
    """Return a canonical Yahoo ``player_key`` or ``None`` without guessing."""

    if not isinstance(value, str):
        return None
    player_key = value.strip()
    return player_key if _YAHOO_PLAYER_KEY.fullmatch(player_key) else None

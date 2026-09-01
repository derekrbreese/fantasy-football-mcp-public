"""HTTP safety and dashboard contract tests for the local draft UI."""

import json

import pytest
from starlette.requests import Request

import fastmcp_server


def request_for(
    method: str,
    path: str,
    *,
    payload: object | None = None,
    body: bytes | None = None,
    origin: str | None = "moz-extension://draft-recorder",
    client: str = "127.0.0.1",
    content_type: str | None = "application/json",
    ui_header: str | None = "1",
    content_length: str | None = None,
    host: str = "127.0.0.1:8765",
    extra_headers: dict[str, str] | None = None,
) -> Request:
    encoded = body if body is not None else json.dumps(payload if payload is not None else {}).encode()
    headers = [(b"host", host.encode())]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if content_type is not None:
        headers.append((b"content-type", content_type.encode()))
    if ui_header is not None:
        headers.append((b"x-fantasy-draft-ui", ui_header.encode()))
    for name, value in (extra_headers or {}).items():
        headers.append((name.lower().encode(), value.encode()))
    length = str(len(encoded)) if content_length is None else content_length
    headers.append((b"content-length", length.encode()))
    sent = False

    async def receive() -> dict:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": encoded, "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": (client, 49152),
        "server": ("127.0.0.1", 8765),
    }
    return Request(scope, receive)


def response_json(response) -> dict:
    return json.loads(response.body)


def live_state_for_profile() -> dict:
    return {
        "schemaVersion": 1,
        "source": "yahoo-draft-recorder",
        "generatedAt": "2026-09-01T16:00:00Z",
        "draft": {
            "sport": "nfl",
            "leagueId": "498589",
            "teamId": "6",
            "sessionKey": "nfl:498589",
        },
        "picks": [],
    }


def structured_profile_payload() -> dict:
    return {
        "schemaVersion": 1,
        "leagueId": "498589",
        "importedAt": "2026-09-01T16:00:00Z",
        "format": "csv",
        "asOf": "2026-09-01T12:00:00Z",
        "rankings": [
            {"name": "Player One", "position": "RB", "team": "SF", "rank": 1},
            {"name": "Player Two", "position": "WR", "rank": 2},
        ],
        "leagueSettings": {
            "teams": 12,
            "rosterPositions": [
                {"position": "QB", "count": 1},
                {"position": "RB", "count": 2},
                {"position": "WR", "count": 2},
                {"position": "TE", "count": 1},
                {"position": "FLEX", "count": 1},
                {"position": "K", "count": 1},
                {"position": "DST", "count": 1},
                {"position": "BN", "count": 6},
                {"position": "IR", "count": 1},
            ],
        },
    }


def profile_summary(league_id: str = "498589") -> dict:
    return {
        "sport": "nfl",
        "leagueId": league_id,
        "importedAt": "2026-09-01T16:00:00Z",
        "asOf": "2026-09-01",
        "format": "csv",
        "rankingCount": 2,
    }


def reset_payload() -> dict:
    state = live_state_for_profile()
    return {
        "schemaVersion": 1,
        "source": "yahoo-draft-recorder",
        "expectedGeneratedAt": state["generatedAt"],
        "draft": {
            field: state["draft"][field]
            for field in ("sport", "leagueId", "teamId", "sessionKey")
        },
    }


@pytest.mark.asyncio
async def test_reset_route_clears_exact_session_and_reports_profile_preserved(
    monkeypatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        fastmcp_server,
        "reset_live_draft",
        lambda payload: calls.append(payload)
        or {
            "sessionKey": "nfl:498589",
            "resetAt": "2026-09-01T17:00:00Z",
            "profilePreserved": True,
        },
    )

    response = await fastmcp_server.receive_live_draft_reset(
        request_for(
            "POST",
            "/draft-reset",
            payload=reset_payload(),
            ui_header=None,
            extra_headers={"x-yahoo-draft-recorder": "1"},
        )
    )

    assert response.status_code == 200
    assert response_json(response) == {
        "status": "ok",
        "sessionKey": "nfl:498589",
        "resetAt": "2026-09-01T17:00:00Z",
        "profilePreserved": True,
    }
    assert calls == [reset_payload()]
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_reset_route_end_to_end_preserves_profile_store(
    tmp_path, monkeypatch
) -> None:
    live_path = tmp_path / "private" / "live-drafts.json"
    profile_path = tmp_path / "private" / "draft-profiles.json"
    profile_path.parent.mkdir(parents=True)
    profile_sentinel = b'{"profile":"preserved"}\n'
    profile_path.write_bytes(profile_sentinel)
    monkeypatch.setenv("FANTASY_FOOTBALL_LIVE_DRAFT_PATH", str(live_path))
    monkeypatch.setenv("FANTASY_FOOTBALL_DRAFT_PROFILE_PATH", str(profile_path))
    state = live_state_for_profile()
    fastmcp_server.save_live_draft(state)

    response = await fastmcp_server.receive_live_draft_reset(
        request_for(
            "POST",
            "/draft-reset",
            payload=reset_payload(),
            ui_header=None,
            extra_headers={"x-yahoo-draft-recorder": "1"},
        )
    )

    assert response.status_code == 200
    assert response_json(response)["profilePreserved"] is True
    assert fastmcp_server.load_live_draft("498589") is None
    assert profile_path.read_bytes() == profile_sentinel
    stored = json.loads(live_path.read_text())
    assert "nfl:498589" not in stored
    assert stored["__resetTombstones"]["nfl:498589"]["draft"]["teamId"] == "6"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_overrides, expected_status, expected_message",
    [
        ({"client": "192.168.1.20"}, 403, "Loopback access required"),
        (
            {"origin": "https://football.fantasysports.yahoo.com"},
            403,
            "Extension origin required",
        ),
        ({"origin": None}, 403, "Extension origin required"),
        ({"extra_headers": {}}, 403, "Recorder header required"),
        ({"content_type": "text/plain"}, 415, "Content-Type must be application/json"),
        ({"content_length": "4097"}, 413, "Payload too large"),
        ({"content_length": "invalid"}, 400, "Invalid content length"),
    ],
)
async def test_reset_route_rejects_unsafe_transport_before_mutation(
    monkeypatch, request_overrides, expected_status, expected_message
) -> None:
    monkeypatch.setattr(
        fastmcp_server,
        "reset_live_draft",
        lambda _payload: (_ for _ in ()).throw(AssertionError("must not reset")),
    )
    defaults = {
        "payload": reset_payload(),
        "ui_header": None,
        "extra_headers": {"x-yahoo-draft-recorder": "1"},
    }
    defaults.update(request_overrides)

    response = await fastmcp_server.receive_live_draft_reset(
        request_for("POST", "/draft-reset", **defaults)
    )

    assert response.status_code == expected_status
    assert response_json(response)["message"] == expected_message


@pytest.mark.asyncio
async def test_reset_route_rejects_oversized_actual_body_and_unknown_fields(
    monkeypatch,
) -> None:
    calls = []

    def reject_unknown(payload):
        calls.append(payload)
        raise fastmcp_server.LiveDraftValidationError("reset request fields are invalid")

    monkeypatch.setattr(
        fastmcp_server,
        "reset_live_draft",
        reject_unknown,
    )
    oversized = await fastmcp_server.receive_live_draft_reset(
        request_for(
            "POST",
            "/draft-reset",
            body=b"{" + b" " * 4_096 + b"}",
            content_length="1",
            ui_header=None,
            extra_headers={"x-yahoo-draft-recorder": "1"},
        )
    )
    unknown = reset_payload()
    unknown["url"] = "https://example.test/?auth=secret"
    invalid = await fastmcp_server.receive_live_draft_reset(
        request_for(
            "POST",
            "/draft-reset",
            payload=unknown,
            ui_header=None,
            extra_headers={"x-yahoo-draft-recorder": "1"},
        )
    )

    assert oversized.status_code == 413
    assert invalid.status_code == 400
    assert "secret" not in json.dumps(response_json(invalid))
    assert calls == [unknown]


@pytest.mark.asyncio
async def test_reset_route_maps_store_conflicts_and_sanitizes_unexpected_errors(
    monkeypatch,
) -> None:
    request = request_for(
        "POST",
        "/draft-reset",
        payload=reset_payload(),
        ui_header=None,
        extra_headers={"x-yahoo-draft-recorder": "1"},
    )
    monkeypatch.setattr(
        fastmcp_server,
        "reset_live_draft",
        lambda _payload: (_ for _ in ()).throw(
            fastmcp_server.LiveDraftConflictError(
                "live draft changed; rescan before resetting"
            )
        ),
    )

    conflict = await fastmcp_server.receive_live_draft_reset(request)

    assert conflict.status_code == 409
    assert response_json(conflict)["message"] == (
        "live draft changed; rescan before resetting"
    )

    private_detail = "secret token at /Users/private/live-drafts.json"
    request = request_for(
        "POST",
        "/draft-reset",
        payload=reset_payload(),
        ui_header=None,
        extra_headers={"x-yahoo-draft-recorder": "1"},
    )
    monkeypatch.setattr(
        fastmcp_server,
        "reset_live_draft",
        lambda _payload: (_ for _ in ()).throw(OSError(private_detail)),
    )

    unavailable = await fastmcp_server.receive_live_draft_reset(request)

    assert unavailable.status_code == 500
    assert response_json(unavailable) == {
        "status": "error",
        "message": "Draft reset service unavailable",
    }
    assert private_detail not in json.dumps(response_json(unavailable))


@pytest.mark.asyncio
async def test_reset_preflight_is_extension_origin_and_loopback_only() -> None:
    allowed = await fastmcp_server.receive_live_draft_reset(
        request_for(
            "OPTIONS",
            "/draft-reset",
            origin="moz-extension://draft-recorder",
            ui_header=None,
            extra_headers={"x-yahoo-draft-recorder": "1"},
        )
    )
    yahoo = await fastmcp_server.receive_live_draft_reset(
        request_for(
            "OPTIONS",
            "/draft-reset",
            origin="https://football.fantasysports.yahoo.com",
            ui_header=None,
            extra_headers={"x-yahoo-draft-recorder": "1"},
        )
    )
    lan = await fastmcp_server.receive_live_draft_reset(
        request_for(
            "OPTIONS",
            "/draft-reset",
            origin="moz-extension://draft-recorder",
            client="192.168.1.20",
            ui_header=None,
            extra_headers={"x-yahoo-draft-recorder": "1"},
        )
    )

    assert allowed.status_code == 204
    assert allowed.headers["access-control-allow-origin"] == (
        "moz-extension://draft-recorder"
    )
    assert yahoo.status_code == 403
    assert lan.status_code == 403


@pytest.mark.asyncio
async def test_structured_profile_route_binds_live_identity_and_saves_allowlist(
    monkeypatch,
) -> None:
    saved = []
    monkeypatch.setattr(
        fastmcp_server,
        "load_live_draft",
        lambda **arguments: live_state_for_profile(),
    )
    monkeypatch.setattr(
        fastmcp_server,
        "save_local_draft_profile",
        lambda profile: saved.append(profile) or profile,
    )

    response = await fastmcp_server.receive_draft_profile(
        request_for("POST", "/draft-profile", payload=structured_profile_payload())
    )

    assert response.status_code == 200
    assert response_json(response) == {
        "status": "success",
        "leagueId": "498589",
        "rankingCount": 2,
        "asOf": "2026-09-01",
        "format": "csv",
    }
    assert saved == [
        {
            "schemaVersion": 1,
            "source": "local-draft-profile",
            "season": 2026,
            "importedAt": "2026-09-01T16:00:00Z",
            "draft": live_state_for_profile()["draft"],
            "rankings": structured_profile_payload()["rankings"],
            "leagueSettings": structured_profile_payload()["leagueSettings"],
            "provenance": {
                "kind": "user-import",
                "format": "csv",
                "asOf": "2026-09-01",
            },
        }
    ]
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_profile_summary_route_returns_only_safe_metadata(monkeypatch) -> None:
    monkeypatch.setattr(
        fastmcp_server,
        "list_local_draft_profile_summaries",
        lambda: [profile_summary()],
    )

    response = await fastmcp_server.list_draft_profiles(
        request_for(
            "GET",
            "/draft-profiles",
            body=b"",
            content_type=None,
            origin=None,
        )
    )

    assert response.status_code == 200
    assert response_json(response) == {
        "status": "success",
        "profiles": [profile_summary()],
    }
    assert b"rankings" not in response.body
    assert b"teamId" not in response.body
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_profile_bind_route_clones_profile_onto_exact_live_identity(
    monkeypatch,
) -> None:
    source_profile = {
        "schemaVersion": 1,
        "source": "local-draft-profile",
        "season": 2026,
        "importedAt": "2026-09-01T16:00:00Z",
        "draft": live_state_for_profile()["draft"],
        "rankings": structured_profile_payload()["rankings"],
        "leagueSettings": structured_profile_payload()["leagueSettings"],
        "provenance": {
            "kind": "user-import",
            "format": "csv",
            "asOf": "2026-09-01",
        },
    }
    target_state = live_state_for_profile()
    target_state["draft"] = {
        "sport": "nfl",
        "leagueId": "777777",
        "teamId": "9",
        "sessionKey": "nfl:777777",
    }
    bound = {**source_profile, "draft": target_state["draft"]}
    calls = []
    monkeypatch.setattr(
        fastmcp_server,
        "load_live_draft",
        lambda **arguments: target_state,
    )
    monkeypatch.setattr(
        fastmcp_server,
        "bind_local_draft_profile",
        lambda source_league_id, target_identity: calls.append(
            (source_league_id, target_identity)
        )
        or bound,
    )
    monkeypatch.setattr(
        fastmcp_server,
        "load_local_draft_profile",
        lambda identity: bound if identity == target_state["draft"] else None,
    )

    response = await fastmcp_server.bind_draft_profile(
        request_for(
            "POST",
            "/draft-profile-bind",
            payload={
                "schemaVersion": 1,
                "sourceLeagueId": "498589",
                "leagueId": "777777",
            },
        )
    )

    assert response.status_code == 200
    assert response_json(response) == {
        "status": "success",
        "leagueId": "777777",
        "sourceLeagueId": "498589",
        "rankingCount": 2,
        "asOf": "2026-09-01",
        "format": "csv",
    }
    assert calls == [("498589", target_state["draft"])]
    assert b"Player One" not in response.body


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status", "message"),
    [
        (
            fastmcp_server.LocalDraftProfileNotFoundError(
                "selected local draft profile was not found"
            ),
            404,
            "not found",
        ),
        (
            fastmcp_server.LocalDraftProfileConflictError(
                "selected local profile belongs to a different sport"
            ),
            409,
            "different sport",
        ),
    ],
)
async def test_profile_bind_route_maps_actionable_source_errors(
    monkeypatch, error, status, message
) -> None:
    monkeypatch.setattr(
        fastmcp_server,
        "load_live_draft",
        lambda **arguments: live_state_for_profile(),
    )
    monkeypatch.setattr(
        fastmcp_server,
        "bind_local_draft_profile",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )

    response = await fastmcp_server.bind_draft_profile(
        request_for(
            "POST",
            "/draft-profile-bind",
            payload={
                "schemaVersion": 1,
                "sourceLeagueId": "111111",
                "leagueId": "498589",
            },
        )
    )

    assert response.status_code == status
    assert message in response_json(response)["message"]


@pytest.mark.asyncio
async def test_profile_bind_route_requires_synced_target(monkeypatch) -> None:
    monkeypatch.setattr(fastmcp_server, "load_live_draft", lambda **arguments: None)
    monkeypatch.setattr(
        fastmcp_server,
        "bind_local_draft_profile",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not bind")),
    )

    response = await fastmcp_server.bind_draft_profile(
        request_for(
            "POST",
            "/draft-profile-bind",
            payload={
                "schemaVersion": 1,
                "sourceLeagueId": "111111",
                "leagueId": "498589",
            },
        )
    )

    assert response.status_code == 404
    assert "synced live draft" in response_json(response)["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"origin": None, "host": "hostile.example"}, "Origin required"),
        ({"origin": "https://evil.example"}, "Origin not allowed"),
        ({"client": "192.168.1.20"}, "Loopback access required"),
        ({"ui_header": None}, "UI header required"),
    ],
)
async def test_profile_summary_route_rejects_unsafe_requests(
    monkeypatch, changes, message
) -> None:
    monkeypatch.setattr(
        fastmcp_server,
        "list_local_draft_profile_summaries",
        lambda: (_ for _ in ()).throw(AssertionError("must not list")),
    )

    response = await fastmcp_server.list_draft_profiles(
        request_for(
            "GET",
            "/draft-profiles",
            body=b"",
            content_type=None,
            **changes,
        )
    )

    assert response.status_code == 403
    assert message in response_json(response)["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "status", "message"),
    [
        ({"origin": None}, 403, "Origin required"),
        ({"origin": "https://evil.example"}, 403, "Origin not allowed"),
        ({"client": "192.168.1.20"}, 403, "Loopback access required"),
        ({"ui_header": None}, 403, "UI header required"),
        ({"content_type": "text/plain"}, 415, "application/json"),
        ({"content_length": "4097"}, 413, "Payload too large"),
    ],
)
async def test_profile_bind_route_rejects_unsafe_requests(
    monkeypatch, changes, status, message
) -> None:
    monkeypatch.setattr(
        fastmcp_server,
        "bind_local_draft_profile",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not bind")),
        raising=False,
    )
    response = await fastmcp_server.bind_draft_profile(
        request_for(
            "POST",
            "/draft-profile-bind",
            payload={
                "schemaVersion": 1,
                "sourceLeagueId": "498589",
                "leagueId": "777777",
            },
            **changes,
        )
    )

    assert response.status_code == status
    assert message in response_json(response)["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"schemaVersion": 1, "sourceLeagueId": "498589"}, "fields"),
        (
            {
                "schemaVersion": 1,
                "sourceLeagueId": "498589",
                "leagueId": "777777",
                "reuseAutomatically": True,
            },
            "fields",
        ),
        (
            {
                "schemaVersion": True,
                "sourceLeagueId": "498589",
                "leagueId": "777777",
            },
            "schemaVersion 1",
        ),
        (
            {
                "schemaVersion": 1,
                "sourceLeagueId": "../498589",
                "leagueId": "777777",
            },
            "sourceLeagueId",
        ),
    ],
)
async def test_profile_bind_route_requires_exact_allowlisted_identity(
    monkeypatch, payload, message
) -> None:
    monkeypatch.setattr(
        fastmcp_server,
        "bind_local_draft_profile",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not bind")),
        raising=False,
    )

    response = await fastmcp_server.bind_draft_profile(
        request_for("POST", "/draft-profile-bind", payload=payload)
    )

    assert response.status_code == 400
    assert message in response_json(response)["message"]


@pytest.mark.asyncio
async def test_xlsx_profile_route_uses_strict_identity_and_roster_headers(
    monkeypatch,
) -> None:
    calls = []
    saved = []
    workbook = b"PK\x03\x04bounded-workbook"
    parsed = {
        "schemaVersion": 1,
        "source": "local-draft-profile",
        "season": 2026,
        "importedAt": "2026-09-01T16:00:00Z",
        "draft": live_state_for_profile()["draft"],
        "rankings": [{"name": "Player One", "position": "RB", "team": "SF", "rank": 1}],
        "leagueSettings": {
            "teams": 10,
            "rosterPositions": [{"position": "QB", "count": 1}],
        },
        "provenance": {
            "kind": "user-import",
            "format": "draftsheets-2026",
            "asOf": "2026-08-31",
        },
    }

    monkeypatch.setattr(
        fastmcp_server,
        "load_live_draft",
        lambda **arguments: live_state_for_profile(),
    )

    def parse(body, **arguments):
        calls.append((body, arguments))
        return parsed

    monkeypatch.setattr(fastmcp_server, "profile_from_draftsheets_xlsx", parse)
    monkeypatch.setattr(
        fastmcp_server,
        "save_local_draft_profile",
        lambda profile: saved.append(profile) or profile,
    )

    response = await fastmcp_server.receive_draft_profile_xlsx(
        request_for(
            "POST",
            "/draft-profile-xlsx",
            body=workbook,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            extra_headers={
                "X-Fantasy-League-ID": "498589",
                "X-Fantasy-Team-Count": "12",
                "X-Fantasy-Roster-Positions": (
                    "QB=1,RB=2,WR=2,TE=1,FLEX=1,K=1,DST=1,BN=6,IR=1"
                ),
            },
        )
    )

    assert response.status_code == 200
    assert response_json(response) == {
        "status": "success",
        "leagueId": "498589",
        "rankingCount": 1,
        "asOf": "2026-08-31",
        "format": "draftsheets-2026",
    }
    assert calls[0][0] == workbook
    assert calls[0][1]["draft"] == live_state_for_profile()["draft"]
    assert calls[0][1]["season"] == 2026
    assert calls[0][1]["roster_overrides"] == {
        "QB": 1,
        "RB": 2,
        "WR": 2,
        "TE": 1,
        "FLEX": 1,
        "K": 1,
        "DST": 1,
        "BN": 6,
        "IR": 1,
    }
    assert saved[0]["leagueSettings"] == {
        "teams": 12,
        "rosterPositions": [
            {"position": "QB", "count": 1},
            {"position": "RB", "count": 2},
            {"position": "WR", "count": 2},
            {"position": "TE", "count": 1},
            {"position": "FLEX", "count": 1},
            {"position": "K", "count": 1},
            {"position": "DST", "count": 1},
            {"position": "BN", "count": 6},
            {"position": "IR", "count": 1},
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "status", "message"),
    [
        ({"client": "192.168.1.2"}, 403, "Loopback"),
        ({"origin": "https://evil.example"}, 403, "Origin"),
        ({"ui_header": None}, 403, "UI header"),
        ({"content_type": "application/json"}, 415, "Content-Type"),
        ({"content_length": "2000001"}, 413, "too large"),
        ({"extra_headers": {}}, 400, "league"),
        (
            {
                "extra_headers": {
                    "X-Fantasy-League-ID": "498589",
                    "X-Fantasy-Team-Count": "12",
                    "X-Fantasy-Roster-Positions": "QB=1,QB=2",
                }
            },
            400,
            "roster",
        ),
    ],
)
async def test_xlsx_profile_route_rejects_unsafe_or_malformed_requests(
    monkeypatch, changes, status, message
) -> None:
    monkeypatch.setattr(
        fastmcp_server,
        "profile_from_draftsheets_xlsx",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not parse")),
    )
    defaults = {
        "body": b"PK\x03\x04data",
        "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "extra_headers": {
            "X-Fantasy-League-ID": "498589",
            "X-Fantasy-Team-Count": "12",
            "X-Fantasy-Roster-Positions": "QB=1,RB=2",
        },
    }
    defaults.update(changes)

    response = await fastmcp_server.receive_draft_profile_xlsx(
        request_for("POST", "/draft-profile-xlsx", **defaults)
    )

    assert response.status_code == status
    assert message.lower() in response_json(response)["message"].lower()


@pytest.mark.asyncio
async def test_profile_route_never_accepts_unbound_or_cross_league_import(
    monkeypatch,
) -> None:
    monkeypatch.setattr(fastmcp_server, "load_live_draft", lambda **arguments: None)
    monkeypatch.setattr(
        fastmcp_server,
        "save_local_draft_profile",
        lambda profile: (_ for _ in ()).throw(AssertionError("must not save")),
    )

    response = await fastmcp_server.receive_draft_profile(
        request_for("POST", "/draft-profile", payload=structured_profile_payload())
    )

    assert response.status_code == 404
    assert "synced" in response_json(response)["message"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("route_name", ["structured", "xlsx"])
async def test_profile_routes_sanitize_unexpected_parser_or_storage_failures(
    monkeypatch, route_name
) -> None:
    private_detail = "secret token at /Users/private/draft-profiles.json"
    monkeypatch.setattr(
        fastmcp_server,
        "load_live_draft",
        lambda **arguments: live_state_for_profile(),
    )
    if route_name == "structured":
        monkeypatch.setattr(
            fastmcp_server,
            "save_local_draft_profile",
            lambda profile: (_ for _ in ()).throw(OSError(private_detail)),
        )
        request = request_for(
            "POST", "/draft-profile", payload=structured_profile_payload()
        )
        response = await fastmcp_server.receive_draft_profile(request)
    else:
        monkeypatch.setattr(
            fastmcp_server,
            "profile_from_draftsheets_xlsx",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(private_detail)),
        )
        request = request_for(
            "POST",
            "/draft-profile-xlsx",
            body=b"PK\x03\x04bounded-workbook",
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            extra_headers={
                "X-Fantasy-League-ID": "498589",
                "X-Fantasy-Team-Count": "12",
                "X-Fantasy-Roster-Positions": "QB=1,RB=2",
            },
        )
        response = await fastmcp_server.receive_draft_profile_xlsx(request)

    assert response.status_code == 500
    assert response_json(response) == {
        "status": "error",
        "message": "Draft profile service unavailable",
    }
    assert private_detail.encode() not in response.body
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_recommendation_route_derives_identity_and_clamps_inputs(monkeypatch) -> None:
    calls = []

    async def recommend(call_tool, **arguments):
        calls.append(arguments)
        return {"status": "success", "recommendations": []}

    monkeypatch.setattr(fastmcp_server, "get_live_draft_recommendation", recommend)
    request = request_for(
        "POST",
        "/draft-recommendation",
        payload={
            "schemaVersion": 1,
            "leagueId": "10462193",
            "strategy": "aggressive",
            "count": 999,
            "rankingCount": 9999,
            "simulations": 9999,
        },
    )

    response = await fastmcp_server.receive_live_draft_recommendation(request)

    assert response.status_code == 200
    assert response_json(response)["status"] == "success"
    assert calls == [
        {
            "league_key": None,
            "league_id": "10462193",
            "strategy": "aggressive",
            "count": 20,
            "ranking_count": 500,
            "simulations": 512,
            "require_authenticated_team": True,
        }
    ]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["access-control-allow-origin"] == "moz-extension://draft-recorder"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in response.headers["content-security-policy"]


@pytest.mark.asyncio
async def test_recommendation_route_clamps_lower_input_bounds(monkeypatch) -> None:
    calls = []

    async def recommend(call_tool, **arguments):
        calls.append(arguments)
        return {"status": "blocked", "leagueId": "10462193"}

    monkeypatch.setattr(fastmcp_server, "get_live_draft_recommendation", recommend)

    response = await fastmcp_server.receive_live_draft_recommendation(
        request_for(
            "POST",
            "/draft-recommendation",
            payload={
                "schemaVersion": 1,
                "leagueId": "10462193",
                "count": -10,
                "rankingCount": -10,
                "simulations": -10,
            },
        )
    )

    assert response.status_code == 200
    assert calls[0]["count"] == 1
    assert calls[0]["ranking_count"] == 25
    assert calls[0]["simulations"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "status", "message"),
    [
        ({"origin": None}, 403, "Origin required"),
        ({"origin": "https://evil.example"}, 403, "Origin not allowed"),
        ({"client": "192.168.1.20"}, 403, "Loopback access required"),
        ({"content_type": "text/plain"}, 415, "application/json"),
        ({"ui_header": None}, 403, "UI header required"),
        ({"content_length": "not-a-number"}, 400, "Invalid content length"),
        ({"content_length": "-1"}, 400, "Invalid content length"),
        ({"content_length": "4097"}, 413, "Payload too large"),
        (
            {"origin": "http://localhost:8765", "host": "127.0.0.1:8765"},
            403,
            "Origin not allowed",
        ),
        (
            {"origin": "http://127.0.0.1:9999", "host": "127.0.0.1:8765"},
            403,
            "Origin not allowed",
        ),
        ({"origin": "http://127.0.0.1:8765/"}, 403, "Origin not allowed"),
        ({"origin": "moz-extension://draft-recorder/path"}, 403, "Origin not allowed"),
    ],
)
async def test_recommendation_route_rejects_unsafe_requests(
    monkeypatch, changes, status, message
) -> None:
    async def unexpected(*args, **kwargs):
        raise AssertionError("recommendation service must not be called")

    monkeypatch.setattr(fastmcp_server, "get_live_draft_recommendation", unexpected)
    request = request_for(
        "POST",
        "/draft-recommendation",
        payload={"schemaVersion": 1, "leagueId": "10462193"},
        **changes,
    )

    response = await fastmcp_server.receive_live_draft_recommendation(request)

    assert response.status_code == status
    assert message in response_json(response)["message"]
    assert response.headers["cache-control"] == "no-store"
    if changes.get("origin") == "https://evil.example":
        assert "access-control-allow-origin" not in response.headers


@pytest.mark.asyncio
async def test_recommendation_route_bounds_streamed_body(monkeypatch) -> None:
    async def unexpected(*args, **kwargs):
        raise AssertionError("recommendation service must not be called")

    monkeypatch.setattr(fastmcp_server, "get_live_draft_recommendation", unexpected)
    request = request_for(
        "POST",
        "/draft-recommendation",
        body=b"{" + b" " * 4096 + b"}",
        content_length="0",
    )

    response = await fastmcp_server.receive_live_draft_recommendation(request)

    assert response.status_code == 413
    assert response_json(response)["message"] == "Payload too large"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"schemaVersion": 1, "leagueId": "10462193", "leagueKey": "nfl.l.999"}, "Unsupported field"),
        ({"schemaVersion": 1, "leagueId": "../10462193"}, "leagueId"),
        ({"schemaVersion": 1, "leagueId": "10462193", "strategy": "reckless"}, "strategy"),
        ({"schemaVersion": 1, "leagueId": "10462193", "count": True}, "count"),
        ({"schemaVersion": 1, "leagueId": "10462193", "rankingCount": True}, "rankingCount"),
        ({"schemaVersion": 1, "leagueId": "10462193", "simulations": False}, "simulations"),
        ({"schemaVersion": True, "leagueId": "10462193"}, "schemaVersion 1"),
        ({"leagueId": "10462193"}, "schemaVersion 1"),
        (["10462193"], "JSON object"),
    ],
)
async def test_recommendation_route_allowlists_json(monkeypatch, payload, message) -> None:
    async def unexpected(*args, **kwargs):
        raise AssertionError("recommendation service must not be called")

    monkeypatch.setattr(fastmcp_server, "get_live_draft_recommendation", unexpected)

    response = await fastmcp_server.receive_live_draft_recommendation(
        request_for("POST", "/draft-recommendation", payload=payload)
    )

    assert response.status_code == 400
    assert message in response_json(response)["message"]


@pytest.mark.asyncio
async def test_recommendation_route_accepts_same_origin_dashboard(monkeypatch) -> None:
    async def recommend(call_tool, **arguments):
        return {"status": "blocked", "warnings": ["Missing pick 4"]}

    monkeypatch.setattr(fastmcp_server, "get_live_draft_recommendation", recommend)
    request = request_for(
        "POST",
        "/draft-recommendation",
        payload={"schemaVersion": 1, "leagueId": "10462193"},
        origin="http://127.0.0.1:8765",
    )

    response = await fastmcp_server.receive_live_draft_recommendation(request)

    assert response.status_code == 200
    assert response_json(response) == {
        "status": "blocked",
        "warnings": ["Missing pick 4"],
    }


@pytest.mark.asyncio
async def test_recommendation_route_passes_through_refresh_required_without_candidates(
    monkeypatch,
) -> None:
    async def recommend(call_tool, **arguments):
        return {
            "status": "error",
            "errorCode": "draft_state_changed",
            "refreshRequired": True,
            "message": "The synced draft changed. Refresh recommendations.",
            "leagueId": "10462193",
            "primaryRecommendation": None,
            "alternatives": [],
            "recommendations": [],
            "contingency": None,
        }

    monkeypatch.setattr(fastmcp_server, "get_live_draft_recommendation", recommend)

    response = await fastmcp_server.receive_live_draft_recommendation(
        request_for(
            "POST",
            "/draft-recommendation",
            payload={"schemaVersion": 1, "leagueId": "10462193"},
        )
    )

    assert response.status_code == 200
    result = response_json(response)
    assert result["refreshRequired"] is True
    assert result["recommendations"] == []
    assert result["alternatives"] == []
    assert result["primaryRecommendation"] is None
    assert result["contingency"] is None


@pytest.mark.asyncio
async def test_recommendation_route_sanitizes_unexpected_service_failures(monkeypatch) -> None:
    async def fail(*args, **kwargs):
        raise RuntimeError("secret Yahoo transport detail")

    monkeypatch.setattr(fastmcp_server, "get_live_draft_recommendation", fail)

    response = await fastmcp_server.receive_live_draft_recommendation(
        request_for(
            "POST",
            "/draft-recommendation",
            payload={"schemaVersion": 1, "leagueId": "10462193"},
        )
    )

    assert response.status_code == 502
    assert response_json(response) == {
        "status": "error",
        "message": "Recommendation service unavailable",
    }
    assert b"secret" not in response.body


@pytest.mark.asyncio
async def test_recommendation_preflight_is_strict_and_private_network_safe() -> None:
    request = request_for(
        "OPTIONS",
        "/draft-recommendation",
        origin="chrome-extension://abcdefghijklmnop",
        content_type=None,
        ui_header=None,
    )

    response = await fastmcp_server.receive_live_draft_recommendation(request)

    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == (
        "chrome-extension://abcdefghijklmnop"
    )
    assert response.headers["access-control-allow-private-network"] == "true"
    assert "X-Fantasy-Draft-UI" in response.headers["access-control-allow-headers"]


@pytest.mark.asyncio
async def test_recommendation_preflight_rejects_hostile_origin_without_cors() -> None:
    response = await fastmcp_server.receive_live_draft_recommendation(
        request_for(
            "OPTIONS",
            "/draft-recommendation",
            origin="https://hostile.example",
            content_type=None,
            ui_header=None,
        )
    )

    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.asyncio
async def test_dashboard_assets_are_loopback_only_and_no_store() -> None:
    page = await fastmcp_server.serve_draft_dashboard(
        request_for("GET", "/draft-dashboard", body=b"", content_type=None, ui_header=None)
    )
    script = await fastmcp_server.serve_draft_dashboard_script(
        request_for(
            "GET", "/draft-dashboard/app.js", body=b"", content_type=None, ui_header=None
        )
    )
    profile_client = await fastmcp_server.serve_draft_profile_client(
        request_for(
            "GET",
            "/draft-dashboard/draft-profile-client.js",
            body=b"",
            content_type=None,
            ui_header=None,
        )
    )
    shared = await fastmcp_server.serve_draft_recommendation_client(
        request_for(
            "GET",
            "/draft-dashboard/shared/recommendation-client.js",
            body=b"",
            content_type=None,
            ui_header=None,
        )
    )
    rejected = await fastmcp_server.serve_draft_dashboard(
        request_for(
            "GET",
            "/draft-dashboard",
            body=b"",
            content_type=None,
            ui_header=None,
            client="10.0.0.8",
        )
    )
    shared_rejected = await fastmcp_server.serve_draft_recommendation_client(
        request_for(
            "GET",
            "/draft-dashboard/shared/recommendation-client.js",
            body=b"",
            content_type=None,
            ui_header=None,
            client="10.0.0.8",
        )
    )

    assert page.status_code == 200
    assert b"Live Draft Assistant" in page.body
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["content-security-policy"].startswith("default-src 'none'")
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")
    assert profile_client.status_code == 200
    assert b"saveDraftProfileXlsx" in profile_client.body
    assert shared.status_code == 200
    assert shared.body == (
        fastmcp_server._DRAFT_SHARED_UI_DIRECTORY / "recommendation-client.js"
    ).read_bytes()
    assert rejected.status_code == 403
    assert shared_rejected.status_code == 403


def test_http_server_defaults_to_loopback(monkeypatch) -> None:
    calls = []
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setattr(fastmcp_server.server, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    fastmcp_server.run_http_server(show_banner=False)

    assert calls == [(("http",), {"host": "127.0.0.1", "port": 8000, "show_banner": False})]


def test_container_and_render_deployments_explicitly_bind_external_interface() -> None:
    root = fastmcp_server._DRAFT_DASHBOARD_DIRECTORY.parents[1]

    assert "ENV HOST=0.0.0.0" in (root / "Dockerfile").read_text(encoding="utf-8")
    render = (root / "render.yaml").read_text(encoding="utf-8")
    assert "- key: HOST\n        value: 0.0.0.0" in render


def test_dashboard_uses_shared_ui_contract_without_inline_or_remote_code() -> None:
    dashboard = fastmcp_server._DRAFT_DASHBOARD_DIRECTORY
    index = (dashboard / "index.html").read_text(encoding="utf-8")
    script = (dashboard / "app.js").read_text(encoding="utf-8")
    shared_scripts = [
        "/draft-dashboard/shared/recommendation-client.js",
        "/draft-dashboard/shared/recommendation-view-model.js",
        "/draft-dashboard/shared/recommendation-renderer.js",
    ]

    assert all(name in index for name in shared_scripts)
    assert "/draft-dashboard/draft-profile-client.js" in index
    assert max(index.index(name) for name in shared_scripts) < index.index(
        "/draft-dashboard/app.js"
    )
    assert "fetchDraftRecommendationsForLeagueId" in script
    assert "createRecommendationViewModel" in script
    assert "renderRecommendationView" in script
    assert "function renderScenarios(data, model)" in script
    assert "renderScenarios(data, model);" in script
    assert "const model = render(data, leagueId);" in script
    assert "model?.mode === 'success' || model?.mode === 'degraded'" in script
    assert "visibleRecommendationCount" in script
    assert "data.recommendations.slice(0, visibleRecommendationCount)" in script
    assert "model.mode === 'success'" in script
    assert "setStatus(...requestStatusForModel(model, leagueId));" in script
    assert "Recommendations are blocked for league" in script
    assert "data.status === 'success'" not in script
    assert "model.recommendations.length" in script
    assert "window.location.hash" in script
    assert "window.location.search" not in script
    assert "innerHTML" not in script
    assert "function resetAnalysisPanels()" in script
    assert script.count("resetAnalysisPanels();") >= 3
    assert "setControlsDisabled(true);" in script
    assert "leagueInput.value.trim() !== leagueId" in script
    assert "typeof value === 'number' && Number.isFinite(value)" in script
    assert "scoring latency unavailable" in script
    assert "unavailable-score" in script
    assert "<script>" not in index
    assert "http://" not in index and "https://" not in index

    csp = fastmcp_server._draft_dashboard_headers()["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "script-src 'self'" in csp
    assert "'unsafe-inline'" not in csp
    assert "http:" not in csp and "https:" not in csp

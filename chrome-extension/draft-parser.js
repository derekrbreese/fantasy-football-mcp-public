(function initDraftParser(globalScope) {
  'use strict';

  const POSITION_PATTERN = '(QB|RB|WR|TE|K|DEF|DST)';
  const TEAM_PATTERN = '([A-Z]{2,3})';

  function normalizeText(value) {
    return String(value ?? '')
      .replace(/\u00a0/g, ' ')
      .replace(/[ \t]+/g, ' ')
      .trim();
  }

  function positiveInteger(value) {
    const match = String(value ?? '').match(/\d+/);
    if (!match) return undefined;
    const number = Number.parseInt(match[0], 10);
    return number > 0 ? number : undefined;
  }

  function normalizePosition(value) {
    const normalized = normalizeText(value).toUpperCase();
    if (!normalized) return undefined;
    return normalized === 'DST' ? 'DEF' : normalized;
  }

  function parseDraftUrl(value) {
    try {
      const url = new URL(value);
      if (url.hostname !== 'football.fantasysports.yahoo.com') return null;
      const match = url.pathname.match(/^\/draftclient\/([^/]+)\/([^/]+)\/([^/]+)/);
      if (!match) return null;
      const [, sport, leagueId, teamId] = match;
      return { sport, leagueId, teamId, sessionKey: `${sport}:${leagueId}` };
    } catch (_error) {
      return null;
    }
  }

  function firstValue(...values) {
    for (const value of values) {
      const normalized = normalizeText(value);
      if (normalized) return normalized;
    }
    return undefined;
  }

  function attributeValue(attributes, ...names) {
    for (const name of names) {
      if (attributes && attributes[name] != null) return attributes[name];
    }
    return undefined;
  }

  function parseTeamAndPosition(text) {
    const teamThenPosition = text.match(
      new RegExp(`\\b${TEAM_PATTERN}\\s*[-–|/,]?\\s*${POSITION_PATTERN}\\b`, 'i'),
    );
    if (teamThenPosition) {
      return {
        nflTeam: teamThenPosition[1].toUpperCase(),
        position: normalizePosition(teamThenPosition[2]),
      };
    }

    const positionThenTeam = text.match(
      new RegExp(`\\b${POSITION_PATTERN}\\s*[-–|/,]?\\s*${TEAM_PATTERN}\\b`, 'i'),
    );
    if (positionThenTeam) {
      return {
        position: normalizePosition(positionThenTeam[1]),
        nflTeam: positionThenTeam[2].toUpperCase(),
      };
    }
    return {};
  }

  function parsePickSnapshot(snapshot) {
    const text = normalizeText(snapshot?.text);
    const lines = String(snapshot?.text ?? '')
      .split(/\r?\n/)
      .map(normalizeText)
      .filter(Boolean);
    const labels = snapshot?.labels || {};
    const attributes = snapshot?.attributes || {};

    const pick = {};
    const pickNumber = positiveInteger(
      firstValue(
        labels.pickNumber,
        attributeValue(attributes, 'data-pick-number', 'data-pick', 'data-overall-pick'),
        text.match(/(?:\(|\b)(\d+)(?:st|nd|rd|th)?\s+overall\b/i)?.[1],
        text.match(/\b(?:overall\s+)?pick\s*#?\s*(\d+)\b/i)?.[1],
        lines[0]?.match(/^#?\s*(\d+)(?:[.)]|$)/)?.[1],
      ),
    );
    if (pickNumber) pick.pickNumber = pickNumber;

    const roundNumber = positiveInteger(
      firstValue(
        labels.roundNumber,
        attributeValue(attributes, 'data-round-number', 'data-round'),
        text.match(/\bround\s*#?\s*(\d+)\b/i)?.[1],
      ),
    );
    if (roundNumber) pick.roundNumber = roundNumber;

    const roundPick = positiveInteger(
      firstValue(
        labels.roundPick,
        attributeValue(attributes, 'data-round-pick', 'data-pick-in-round'),
        text.match(/\bround\s+\d+\s*[,;:-]\s*pick\s*#?\s*(\d+)\b/i)?.[1],
      ),
    );
    if (roundPick) pick.roundPick = roundPick;

    let player = firstValue(
      labels.player,
      attributeValue(attributes, 'data-player-name', 'data-player'),
    );
    let fantasyTeam = firstValue(
      labels.fantasyTeam,
      attributeValue(attributes, 'data-team-name', 'data-fantasy-team', 'data-manager-name'),
    );
    let position = firstValue(labels.position, attributeValue(attributes, 'data-position'));
    let nflTeam = firstValue(labels.nflTeam, attributeValue(attributes, 'data-nfl-team'));

    const sentenceMatch = text.match(
      /\bpick\s*#?\s*\d+\s*[:.-]\s*(.+?)\s*\(([A-Z]{2,3})\s*[-–|/,]\s*(QB|RB|WR|TE|K|DEF|DST)\)\s*(?:-|–|—)?\s*(?:drafted\s+by|selected\s+by|team\s*:?)\s*(.+)$/i,
    );
    if (sentenceMatch) {
      player ||= normalizeText(sentenceMatch[1]);
      nflTeam ||= sentenceMatch[2];
      position ||= sentenceMatch[3];
      fantasyTeam ||= normalizeText(sentenceMatch[4]);
    }

    const detailedAnnouncement = text.match(
      /:\s*(.+?)\s*\(([A-Z]{2,3})\s*[-–|/,]\s*(QB|RB|WR|TE|K|DEF|DST)\)\s*(?:-|–|—)?\s*(?:drafted\s+by|selected\s+by|team\s*:?)\s*(.+)$/i,
    );
    if (detailedAnnouncement) {
      player ||= normalizeText(detailedAnnouncement[1]);
      nflTeam ||= detailedAnnouncement[2];
      position ||= detailedAnnouncement[3];
      fantasyTeam ||= normalizeText(detailedAnnouncement[4]);
    }

    const compactRow = text.match(
      /^#?\s*\d+[.)]?\s*(?:\(\d+\)\s*)?(.+?)\s*\(([A-Z]{2,3})\s*[-–|/,]\s*(QB|RB|WR|TE|K|DEF|DST)\)\s+(.+)$/i,
    );
    if (compactRow) {
      player ||= normalizeText(compactRow[1]);
      nflTeam ||= compactRow[2];
      position ||= compactRow[3];
      fantasyTeam ||= normalizeText(compactRow[4]);
    }

    if (!player && lines.length >= 3 && /^#?\s*\d+(?:[.)]|$)/.test(lines[0])) {
      const detailsIndex = lines.findIndex((line, index) => index > 0 && Object.keys(parseTeamAndPosition(line)).length > 0);
      if (detailsIndex > 1) {
        player = lines[1].replace(/^\d+\.?\s*/, '');
        fantasyTeam ||= lines[detailsIndex + 1];
      }
    }

    const parsedTeamAndPosition = parseTeamAndPosition(
      firstValue(position && nflTeam ? `${nflTeam} ${position}` : undefined, text) || '',
    );
    position ||= parsedTeamAndPosition.position;
    nflTeam ||= parsedTeamAndPosition.nflTeam;

    player = normalizeText(player);
    fantasyTeam = normalizeText(fantasyTeam);
    position = normalizePosition(position);
    nflTeam = normalizeText(nflTeam).toUpperCase();

    if (!player || !pick.pickNumber) return null;
    if (!/\p{L}/u.test(player) || player.length > 100) return null;

    pick.player = player;
    if (position) pick.position = position;
    if (nflTeam) pick.nflTeam = nflTeam;
    if (fantasyTeam) pick.fantasyTeam = fantasyTeam;
    return pick;
  }

  function parseRoundByRoundSnapshot({ roundText, pickText, playerText, fantasyTeamText }) {
    const pickNumber = positiveInteger(pickText);
    const roundNumber = positiveInteger(roundText);
    const lines = String(playerText ?? '')
      .split(/\r?\n/)
      .map(normalizeText)
      .filter(Boolean);
    const positionIndex = lines.findIndex((line) => /^(QB|RB|WR|TE|K|DEF|DST)$/i.test(line));
    const player = normalizeText(lines[0]);
    const fantasyTeam = normalizeText(fantasyTeamText);

    if (!pickNumber || positionIndex < 1 || !player || !fantasyTeam) return null;
    const position = normalizePosition(lines[positionIndex]);
    const nflTeam = normalizeText(lines[positionIndex + 1]).toUpperCase();
    if (!nflTeam || !/^[A-Z]{2,3}$/.test(nflTeam)) return null;

    const pick = {
      pickNumber,
      player,
      position,
      nflTeam,
      fantasyTeam,
      isUserPick: /^Your Team$/i.test(fantasyTeam),
    };
    if (roundNumber) pick.roundNumber = roundNumber;
    return pick;
  }

  function parseLiveDraftSnapshot({ statusText, lastPickText }) {
    const currentPick = positiveInteger(
      normalizeText(statusText).match(/\bROUND\s+\d+\s*[,•·-]?\s*PICK\s*#?\s*(\d+)\b/i)?.[1],
    );
    const lastPick = normalizeText(lastPickText).match(
      /^Last:\s*(.+?)\s*\(\s*(QB|RB|WR|TE|K|DEF|DST)\s*[·•|/,–-]\s*([A-Z]{2,3})\s*\)\s*(.+)$/i,
    );
    if (!lastPick) return null;

    const pick = {
      player: normalizeText(lastPick[1]),
      position: normalizePosition(lastPick[2]),
      nflTeam: lastPick[3].toUpperCase(),
      fantasyTeam: normalizeText(lastPick[4]),
    };
    if (/^Your Team$/i.test(pick.fantasyTeam)) pick.isUserPick = true;
    if (currentPick && currentPick > 1) pick.pickNumber = currentPick - 1;
    return pick;
  }

  function slug(value) {
    return normalizeText(value).toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-|-$/g, '');
  }

  function buildPickKey(sessionKey, pick) {
    if (positiveInteger(pick?.pickNumber)) {
      return `${sessionKey}:pick:${positiveInteger(pick.pickNumber)}`;
    }
    return `${sessionKey}:player:${slug(pick?.player)}:team:${slug(pick?.fantasyTeam)}`;
  }

  function richerMerge(existing, incoming) {
    const merged = { ...existing };
    for (const [key, value] of Object.entries(incoming)) {
      if (value !== undefined && value !== null && value !== '') merged[key] = value;
    }
    if (existing.recordedAt) merged.recordedAt = existing.recordedAt;
    return merged;
  }

  function upsertPicks(sessionKey, existing, incoming) {
    const picksByKey = new Map();
    for (const pick of existing || []) picksByKey.set(buildPickKey(sessionKey, pick), { ...pick });
    for (const pick of incoming || []) {
      const key = buildPickKey(sessionKey, pick);
      let targetKey = key;
      let current = picksByKey.get(key);
      if (positiveInteger(pick.pickNumber)) {
        const fallbackKey = buildPickKey(sessionKey, { ...pick, pickNumber: undefined });
        if (picksByKey.has(fallbackKey)) {
          const fallback = picksByKey.get(fallbackKey);
          current = current ? richerMerge(fallback, current) : fallback;
          picksByKey.delete(fallbackKey);
        }
      } else if (!current) {
        for (const [existingKey, existingPick] of picksByKey) {
          const fallbackKey = buildPickKey(sessionKey, { ...existingPick, pickNumber: undefined });
          if (fallbackKey === key) {
            targetKey = existingKey;
            current = existingPick;
            break;
          }
        }
      }
      picksByKey.set(targetKey, current ? richerMerge(current, pick) : { ...pick });
    }
    return [...picksByKey.values()].sort((left, right) => {
      const leftNumber = positiveInteger(left.pickNumber) ?? Number.MAX_SAFE_INTEGER;
      const rightNumber = positiveInteger(right.pickNumber) ?? Number.MAX_SAFE_INTEGER;
      return leftNumber - rightNumber || String(left.recordedAt || '').localeCompare(String(right.recordedAt || ''));
    });
  }

  const api = {
    buildPickKey,
    normalizeText,
    parseDraftUrl,
    parseLiveDraftSnapshot,
    parsePickSnapshot,
    parseRoundByRoundSnapshot,
    upsertPicks,
  };

  globalScope.YahooDraftParser = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);

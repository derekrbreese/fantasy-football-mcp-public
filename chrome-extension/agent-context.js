(function initAgentContext(globalScope) {
  'use strict';

  const PICK_FIELDS = [
    'pickNumber',
    'roundNumber',
    'roundPick',
    'player',
    'position',
    'nflTeam',
    'fantasyTeam',
    'isUserPick',
    'recordedAt',
  ];

  function safePick(pick) {
    const result = {};
    for (const field of PICK_FIELDS) {
      const value = pick?.[field];
      if (value !== undefined && value !== null && value !== '') result[field] = value;
    }
    return result;
  }

  function sessionToAgentContext(session, generatedAt = new Date().toISOString()) {
    const picks = (session?.picks || [])
      .map(safePick)
      .sort((left, right) => Number(left.pickNumber || Number.MAX_SAFE_INTEGER) - Number(right.pickNumber || Number.MAX_SAFE_INTEGER));
    const userRoster = picks.filter((pick) => pick.isUserPick === true || /^Your Team$/i.test(pick.fantasyTeam || ''));
    const rosterEntries = new Map();
    for (const pick of picks) {
      const team = pick.fantasyTeam || 'Unknown team';
      if (!rosterEntries.has(team)) rosterEntries.set(team, []);
      rosterEntries.get(team).push(pick);
    }
    const numberedPicks = picks.map((pick) => Number(pick.pickNumber)).filter(Number.isFinite);
    const latestOverallPick = numberedPicks.length ? Math.max(...numberedPicks) : 0;

    return {
      schemaVersion: 1,
      source: 'yahoo-draft-recorder',
      generatedAt,
      draft: {
        sport: session?.sport,
        leagueId: session?.leagueId,
        teamId: session?.teamId,
        sessionKey: session?.sessionKey,
        updatedAt: session?.updatedAt,
      },
      summary: {
        totalPicks: picks.length,
        latestOverallPick,
        nextOverallPick: latestOverallPick + 1,
        userPickCount: userRoster.length,
      },
      userRoster,
      teamRosters: Object.fromEntries(rosterEntries),
      picks,
    };
  }

  const api = { sessionToAgentContext };
  globalScope.YahooDraftAgentContext = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);

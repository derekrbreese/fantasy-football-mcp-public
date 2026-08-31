(function initSessionStore(globalScope) {
  'use strict';

  const parser =
    globalScope.YahooDraftParser ||
    (typeof require === 'function' ? require('./draft-parser.js') : null);

  function updateDraftSession(existing, metadata, observedPicks, timestamp) {
    const existingPicks = existing?.picks || [];
    const observedWithTimestamps = (observedPicks || []).map((pick) => ({
      ...pick,
      recordedAt: pick.recordedAt || timestamp,
    }));

    return {
      ...(existing || {}),
      sport: metadata.sport,
      leagueId: metadata.leagueId,
      teamId: metadata.teamId,
      sessionKey: metadata.sessionKey,
      picks: parser.upsertPicks(metadata.sessionKey, existingPicks, observedWithTimestamps),
      updatedAt: timestamp,
    };
  }

  const api = { updateDraftSession };
  globalScope.YahooDraftSessionStore = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);

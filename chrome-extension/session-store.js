(function initSessionStore(globalScope) {
  'use strict';

  const parser =
    globalScope.YahooDraftParser ||
    (typeof require === 'function' ? require('./draft-parser.js') : null);
  const ledgerHealth =
    globalScope.YahooDraftLedgerHealth ||
    (typeof require === 'function' ? require('./ledger-health.js') : null);

  function updateDraftSession(existing, metadata, observedPicks, timestamp) {
    if (existing?.numberedLedgerAuthoritative === true) {
      const existingNumbered = (existing.picks || []).filter(hasPickNumber);
      const existingNumbers = new Set(existingNumbered.map((pick) => Number(pick.pickNumber)));
      const newlyNumbered = (observedPicks || []).filter((pick) => (
        hasPickNumber(pick) && !existingNumbers.has(Number(pick.pickNumber))
      ));
      return updateDraftSessionFromAuthoritativeLedger(
        existing,
        metadata,
        [...existingNumbered, ...newlyNumbered],
        observedPicks,
        timestamp,
      );
    }
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

  function hasPickNumber(pick) {
    const number = Number(pick?.pickNumber);
    return Number.isInteger(number) && number > 0;
  }

  function identityKey(sessionKey, pick) {
    const baseKey = parser.buildPickKey(sessionKey, { ...pick, pickNumber: undefined });
    if (/(?:^|\s)\p{L}\./u.test(String(pick?.player || ''))) {
      return `${baseKey}:position:${String(pick?.position || '').toUpperCase()}:nfl:${String(pick?.nflTeam || '').toUpperCase()}`;
    }
    return baseKey;
  }

  function numberedIdentityKey(sessionKey, pick) {
    return `${Number(pick.pickNumber)}:${identityKey(sessionKey, pick)}`;
  }

  function updateDraftSessionFromAuthoritativeLedger(
    existing,
    metadata,
    authoritativePicks,
    observedNonLedgerPicks,
    timestamp,
  ) {
    const existingByIdentity = new Map();
    for (const pick of existing?.picks || []) {
      if (!hasPickNumber(pick)) continue;
      const key = numberedIdentityKey(metadata.sessionKey, pick);
      if (!existingByIdentity.has(key)) existingByIdentity.set(key, []);
      existingByIdentity.get(key).push(pick);
    }

    const numberedPicks = (authoritativePicks || []).map((pick) => {
      const existingMatches = existingByIdentity.get(numberedIdentityKey(metadata.sessionKey, pick));
      const matched = existingMatches?.shift();
      return {
        ...pick,
        recordedAt: pick.recordedAt || matched?.recordedAt || timestamp,
      };
    }).sort((left, right) => Number(left.pickNumber) - Number(right.pickNumber));

    const authoritativeIdentities = new Set(numberedPicks.map((pick) => identityKey(metadata.sessionKey, pick)));
    const unnumberedCandidates = [
      ...(existing?.picks || []).filter((pick) => !hasPickNumber(pick)),
      ...(observedNonLedgerPicks || []).filter((pick) => !hasPickNumber(pick)).map((pick) => ({
        ...pick,
        recordedAt: pick.recordedAt || timestamp,
      })),
    ];
    const unmatchedUnnumbered = parser
      .upsertPicks(metadata.sessionKey, [], unnumberedCandidates)
      .filter((pick) => !authoritativeIdentities.has(identityKey(metadata.sessionKey, pick)));

    return {
      ...(existing || {}),
      sport: metadata.sport,
      leagueId: metadata.leagueId,
      teamId: metadata.teamId,
      sessionKey: metadata.sessionKey,
      numberedLedgerAuthoritative: true,
      picks: [...numberedPicks, ...unmatchedUnnumbered],
      updatedAt: timestamp,
    };
  }

  function prepareAutomaticAuthoritativeUpdate(
    existing,
    metadata,
    authoritativePicks,
    observedNonLedgerPicks,
    timestamp,
    _options = {},
  ) {
    const savedHealth = ledgerHealth.analyzeLedger(existing?.picks || []);
    const visibleHealth = ledgerHealth.analyzeLedger(authoritativePicks || []);
    if (visibleHealth.highestPickNumber < savedHealth.highestPickNumber) {
      return {
        ok: false,
        session: existing,
        health: visibleHealth,
        error: `Saved Round-by-Round state reaches pick ${savedHealth.highestPickNumber}, but Yahoo’s visible ledger ends at pick ${visibleHealth.highestPickNumber}. Automatic replacement was blocked. Open the complete current ledger and use Full rescan & repair if a downward correction is intended.`,
      };
    }
    return {
      ok: true,
      health: visibleHealth,
      session: updateDraftSessionFromAuthoritativeLedger(
        existing,
        metadata,
        authoritativePicks,
        observedNonLedgerPicks,
        timestamp,
      ),
    };
  }

  function repairDraftSession(existing, metadata, authoritativePicks, timestamp) {
    const health = ledgerHealth.analyzeLedger(authoritativePicks);
    if (!health.isComplete) return { ok: false, session: existing, health };

    const session = updateDraftSession(
      { ...(existing || {}), picks: [] },
      metadata,
      authoritativePicks,
      timestamp,
    );
    return { ok: true, session, health };
  }

  async function commitDraftRepair(
    existing,
    metadata,
    authoritativePicks,
    timestamp,
    syncStagedSession,
    persistStagedSession,
    options = {},
  ) {
    const staged = prepareDraftRepair(existing, metadata, authoritativePicks, timestamp, options);
    if (!staged.ok) return staged;

    try {
      await syncStagedSession(staged.session);
    } catch (error) {
      return {
        ok: false,
        session: existing,
        health: staged.health,
        error: `Repair was rejected or unavailable; saved picks were not changed: ${String(error?.message || error)}`,
      };
    }

    try {
      await persistStagedSession(staged.session);
    } catch (error) {
      return {
        ok: false,
        session: existing,
        pendingSession: staged.session,
        serverAccepted: true,
        health: staged.health,
        error: `The server accepted repair, but browser storage failed: ${String(error?.message || error)}`,
      };
    }
    return staged;
  }

  function prepareDraftRepair(existing, metadata, authoritativePicks, timestamp, options = {}) {
    const staged = repairDraftSession(existing, metadata, authoritativePicks, timestamp);
    if (!staged.ok) return staged;
    const savedHealth = ledgerHealth.analyzeLedger(existing?.picks || []);
    const directionValidation = ledgerHealth.validateDownwardRepairEvidence(
      savedHealth,
      staged.health,
      options.currentPickNumber,
    );
    if (!directionValidation.ok) {
      return { ...directionValidation, session: existing, health: staged.health };
    }
    return staged;
  }

  function createDurableRepairCoordinator(dependencies) {
    async function begin(sessionKey, session) {
      await dependencies.writePending({
        schemaVersion: 1,
        state: 'intent',
        sessionKey,
        session,
      });
    }

    async function hasPending() {
      return Boolean(await dependencies.readPending());
    }

    function validatePending(pending) {
      return pending &&
        pending.schemaVersion === 1 &&
        (pending.state === 'intent' || pending.state === 'accepted') &&
        typeof pending.sessionKey === 'string' &&
        pending.session &&
        typeof pending.session === 'object' &&
        pending.session.sessionKey === pending.sessionKey;
    }

    async function reconcile() {
      const pending = await dependencies.readPending();
      if (!pending) return { ok: true, reconciled: false };
      if (!validatePending(pending)) {
        return { ok: false, error: 'Pending repair record is invalid; stale sync remains blocked.' };
      }

      let accepted = pending;
      if (pending.state === 'intent') {
        try {
          await dependencies.syncRepair(pending.session);
        } catch (error) {
          return {
            ok: false,
            error: `Repair is waiting for server acceptance: ${String(error?.message || error)}`,
          };
        }
        accepted = { ...pending, state: 'accepted' };
        try {
          await dependencies.writePending(accepted);
        } catch (error) {
          return {
            ok: false,
            error: `The server accepted repair, but its durable marker could not be saved; repair will retry safely: ${String(error?.message || error)}`,
          };
        }
      }

      try {
        await dependencies.persistSession(accepted.session);
        await dependencies.clearPending();
        return { ok: true, reconciled: true, session: accepted.session };
      } catch (error) {
        return {
          ok: false,
          error: `Server-accepted repair is waiting for browser storage: ${String(error?.message || error)}`,
        };
      }
    }

    async function runAfterReconcile(operation) {
      const result = await reconcile();
      if (!result.ok) return result;
      return { ok: true, reconciled: result.reconciled, value: await operation() };
    }

    return { begin, hasPending, reconcile, runAfterReconcile };
  }

  const api = {
    commitDraftRepair,
    createDurableRepairCoordinator,
    prepareAutomaticAuthoritativeUpdate,
    prepareDraftRepair,
    repairDraftSession,
    updateDraftSession,
    updateDraftSessionFromAuthoritativeLedger,
  };
  globalScope.YahooDraftSessionStore = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);

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

  function setAuthoritativeCaptureBlocked(session, blocked, timestamp) {
    const updated = { ...(session || {}) };
    if (typeof blocked === 'boolean') {
      updated.authoritativeCaptureBlocked = blocked;
    } else {
      delete updated.authoritativeCaptureBlocked;
    }
    if (timestamp) updated.updatedAt = timestamp;
    return updated;
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
    options = {},
  ) {
    const savedHealth = ledgerHealth.analyzeLedger(existing?.picks || []);
    const visibleHealth = ledgerHealth.analyzeLedger(authoritativePicks || []);
    if (visibleHealth.highestPickNumber < savedHealth.highestPickNumber) {
      return {
        ok: false,
        reason: 'downward-prefix',
        session: existing,
        health: visibleHealth,
        error: `Saved Round-by-Round state reaches pick ${savedHealth.highestPickNumber}, but Yahoo’s visible ledger ends at pick ${visibleHealth.highestPickNumber}. Automatic replacement was blocked. Open the complete current ledger and use Full rescan & repair if a downward correction is intended.`,
      };
    }
    const currentPickValidation = ledgerHealth.validateLedgerAgainstCurrentPick(
      visibleHealth,
      options.currentPickNumber,
    );
    if (!currentPickValidation.ok) {
      return {
        ...currentPickValidation,
        reason: 'current-pick-mismatch',
        session: existing,
        health: visibleHealth,
      };
    }
    const hasPositiveCurrentPickEvidence = Number.isInteger(options.currentPickNumber) &&
      options.currentPickNumber > 0;
    const nextCaptureState = hasPositiveCurrentPickEvidence
      ? false
      : existing?.authoritativeCaptureBlocked;
    return {
      ok: true,
      health: visibleHealth,
      session: setAuthoritativeCaptureBlocked(
        updateDraftSessionFromAuthoritativeLedger(
          existing,
          metadata,
          authoritativePicks,
          observedNonLedgerPicks,
          timestamp,
        ),
        nextCaptureState,
      ),
    };
  }

  function repairDraftSession(existing, metadata, authoritativePicks, timestamp) {
    const health = ledgerHealth.analyzeLedger(authoritativePicks);
    if (!health.isComplete) return { ok: false, session: existing, health };

    const session = setAuthoritativeCaptureBlocked(
      updateDraftSession(
        { ...(existing || {}), picks: [] },
        metadata,
        authoritativePicks,
        timestamp,
      ),
      false,
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
      if (dependencies.isSessionReset) {
        let discardedByReset;
        try {
          discardedByReset = await dependencies.isSessionReset(pending.session);
        } catch (error) {
          return {
            ok: false,
            error: `Pending repair could not be compared with reset state: ${String(error?.message || error)}`,
          };
        }
        if (discardedByReset) {
          try {
            await dependencies.clearPending();
          } catch (error) {
            return {
              ok: false,
              error: `Reset superseded a stale repair, but its journal could not be cleared: ${String(error?.message || error)}`,
            };
          }
          return { ok: true, reconciled: true, discardedByReset: true };
        }
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
        const syncedSession = validResetTimestamp(accepted.session?.updatedAt)
          ? { ...accepted.session, lastSyncedAt: accepted.session.updatedAt }
          : accepted.session;
        await dependencies.persistSession(syncedSession);
        await dependencies.clearPending();
        return { ok: true, reconciled: true, session: syncedSession };
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

  function validResetTimestamp(value) {
    return typeof value === 'string' &&
      value.length <= 40 &&
      /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) &&
      Number.isFinite(Date.parse(value));
  }

  function sameDraftIdentity(left, right) {
    const fields = ['sport', 'leagueId', 'teamId', 'sessionKey'];
    if (!left || !right || fields.some((field) => (
      typeof left[field] !== 'string' ||
      !left[field] ||
      left[field] !== right[field]
    ))) return false;
    return left.sessionKey === `${left.sport}:${left.leagueId}`;
  }

  function resetIdentity(session) {
    const sport = typeof session?.sport === 'string' ? session.sport : '';
    const leagueId = typeof session?.leagueId === 'string' ? session.leagueId : '';
    const teamId = typeof session?.teamId === 'string' ? session.teamId : '';
    const sessionKey = typeof session?.sessionKey === 'string' ? session.sessionKey : '';
    if (
      !/^[a-z0-9_-]{1,16}$/i.test(sport) ||
      !/^\d{1,32}$/.test(leagueId) ||
      !/^\d{1,32}$/.test(teamId) ||
      sessionKey !== `${sport}:${leagueId}`
    ) {
      throw new Error('The active Yahoo session identity is missing or inconsistent.');
    }
    const resetRevision = session?.lastSyncedAt === undefined
      ? session?.updatedAt
      : session.lastSyncedAt;
    if (!validResetTimestamp(resetRevision)) {
      throw new Error('The active Yahoo session must be synced before it can be reset. Rescan first.');
    }
    return {
      sport,
      leagueId,
      teamId,
      sessionKey,
      updatedAt: resetRevision,
    };
  }

  function createDurableResetCoordinator(dependencies) {
    async function begin(session) {
      const snapshot = resetIdentity(session);
      await dependencies.writePending({
        schemaVersion: 1,
        state: 'intent',
        sessionKey: snapshot.sessionKey,
        expectedGeneratedAt: snapshot.updatedAt,
        draft: {
          sport: snapshot.sport,
          leagueId: snapshot.leagueId,
          teamId: snapshot.teamId,
          sessionKey: snapshot.sessionKey,
        },
      });
    }

    function validPending(pending) {
      if (
        !pending ||
        pending.schemaVersion !== 1 ||
        (pending.state !== 'intent' && pending.state !== 'accepted') ||
        !validResetTimestamp(pending.expectedGeneratedAt)
      ) return false;
      try {
        const snapshot = resetIdentity({
          ...pending.draft,
          updatedAt: pending.expectedGeneratedAt,
        });
        if (snapshot.sessionKey !== pending.sessionKey) return false;
      } catch (_error) {
        return false;
      }
      return pending.state !== 'accepted' || (
        pending.profilePreserved === true && validResetTimestamp(pending.resetAt)
      );
    }

    async function hasPending() {
      return Boolean(await dependencies.readPending());
    }

    async function reconcile() {
      const pending = await dependencies.readPending();
      if (!pending) return { ok: true, reconciled: false };
      if (!validPending(pending)) {
        return { ok: false, error: 'Pending reset record is invalid; scans and sync remain blocked.' };
      }

      let accepted = pending;
      if (pending.state === 'intent') {
        let response;
        try {
          response = await dependencies.resetServer({
            ...pending.draft,
            updatedAt: pending.expectedGeneratedAt,
          });
        } catch (error) {
          if (error?.status === 404 || error?.status === 409) {
            try {
              await dependencies.clearPending();
            } catch (clearError) {
              return {
                ok: false,
                error: `Draft changed before reset, and the reset journal could not be cleared: ${String(clearError?.message || clearError)}`,
              };
            }
            return {
              ok: false,
              retryAfterRescan: true,
              error: String(error?.message || error),
            };
          }
          return {
            ok: false,
            error: `Reset is waiting for server acceptance: ${String(error?.message || error)}`,
          };
        }
        if (
          response?.status !== 'ok' ||
          response.sessionKey !== pending.sessionKey ||
          response.profilePreserved !== true ||
          !validResetTimestamp(response.resetAt)
        ) {
          return {
            ok: false,
            error: 'The local server returned an invalid reset acknowledgement; browser state was not cleared.',
          };
        }
        accepted = {
          ...pending,
          state: 'accepted',
          resetAt: response.resetAt,
          profilePreserved: true,
        };
        try {
          await dependencies.writePending(accepted);
        } catch (error) {
          return {
            ok: false,
            serverAccepted: true,
            error: `The server reset the draft, but its durable marker could not be saved; retry Reset safely: ${String(error?.message || error)}`,
          };
        }
      }

      try {
        await dependencies.finalizeReset(accepted.sessionKey, accepted.resetAt);
      } catch (error) {
        return {
          ok: false,
          serverAccepted: true,
          error: `The server reset the draft, but browser cleanup failed; retry Reset safely: ${String(error?.message || error)}`,
        };
      }
      try {
        await dependencies.clearPending();
      } catch (error) {
        return {
          ok: false,
          serverAccepted: true,
          error: `The draft was reset, but its browser journal could not be cleared; retry Reset safely: ${String(error?.message || error)}`,
        };
      }
      return {
        ok: true,
        reconciled: true,
        sessionKey: accepted.sessionKey,
        resetAt: accepted.resetAt,
        profilePreserved: true,
      };
    }

    return { begin, hasPending, reconcile };
  }

  const api = {
    commitDraftRepair,
    createDurableRepairCoordinator,
    createDurableResetCoordinator,
    prepareAutomaticAuthoritativeUpdate,
    prepareDraftRepair,
    repairDraftSession,
    sameDraftIdentity,
    setAuthoritativeCaptureBlocked,
    updateDraftSession,
    updateDraftSessionFromAuthoritativeLedger,
  };
  globalScope.YahooDraftSessionStore = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);

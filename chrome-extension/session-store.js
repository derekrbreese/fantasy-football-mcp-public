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
      const mergedNumbered = mergeSecondaryNumberedObservations(
        existingNumbered,
        observedPicks,
        timestamp,
      );
      return updateDraftSessionFromAuthoritativeLedger(
        existing,
        metadata,
        mergedNumbered,
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
    const playerKey = parser.normalizePlayerKey(pick?.playerKey);
    if (playerKey) return `${sessionKey}:yahoo-player:${playerKey}`;
    const isUserPick = pick?.isUserPick === true || /^(?:My|Your) Team$/i.test(String(pick?.fantasyTeam || ''));
    const canonicalPick = {
      ...pick,
      pickNumber: undefined,
      fantasyTeam: isUserPick ? 'Your Team' : pick?.fantasyTeam,
    };
    const position = String(canonicalPick.position || '').toUpperCase();
    const nflTeam = String(canonicalPick.nflTeam || '').toUpperCase();
    if (position === 'DEF' && nflTeam) {
      return `${sessionKey}:dst:${nflTeam}:team:${String(canonicalPick.fantasyTeam || '').toLocaleLowerCase()}`;
    }
    const baseKey = parser.buildPickKey(sessionKey, canonicalPick);
    if (/(?:^|\s)\p{L}\./u.test(String(pick?.player || ''))) {
      return `${baseKey}:position:${position}:nfl:${nflTeam}`;
    }
    return baseKey;
  }

  function sameIdentityObservation(sessionKey, left, right) {
    const leftPlayerKey = parser.normalizePlayerKey(left?.playerKey);
    const rightPlayerKey = parser.normalizePlayerKey(right?.playerKey);
    if (leftPlayerKey && rightPlayerKey) return leftPlayerKey === rightPlayerKey;
    return identityKey(sessionKey, { ...left, playerKey: undefined }) ===
      identityKey(sessionKey, { ...right, playerKey: undefined });
  }

  function fillMissingPickFields(existing, incoming) {
    const merged = { ...existing };
    for (const [key, value] of Object.entries(incoming || {})) {
      if (key === 'isUserPick' || key === 'fantasyTeam') continue;
      if (
        (merged[key] === undefined || merged[key] === null || merged[key] === '') &&
        value !== undefined && value !== null && value !== ''
      ) merged[key] = value;
    }
    if (!merged.fantasyTeam && incoming?.fantasyTeam) merged.fantasyTeam = incoming.fantasyTeam;
    if (existing?.isUserPick === true || incoming?.isUserPick === true) {
      merged.isUserPick = true;
      if (/^(?:My|Your) Team$/i.test(String(incoming?.fantasyTeam || ''))) {
        merged.fantasyTeam = 'Your Team';
      }
    } else if (existing?.isUserPick === false || incoming?.isUserPick === false) {
      merged.isUserPick = false;
    }
    if (existing?.recordedAt) merged.recordedAt = existing.recordedAt;
    return merged;
  }

  function normalizedPlayerName(value) {
    return parser.normalizeText(value)
      .toLocaleLowerCase()
      .replace(/[’']/g, "'")
      .replace(/\s+/g, ' ');
  }

  function initialedNameParts(value) {
    const tokens = normalizedPlayerName(value).split(' ').filter(Boolean);
    if (tokens.length < 2 || !/^(?:\p{L}\.)+$/u.test(tokens[0])) return null;
    return {
      firstInitial: tokens[0][0],
      familyName: tokens.slice(1).join(' '),
    };
  }

  function normalizedObservedPosition(value) {
    const position = String(value || '').toUpperCase();
    return position === 'DST' || position === 'D/ST' ? 'DEF' : position;
  }

  function sameNumberObservation(left, right) {
    if (Number(left?.pickNumber) !== Number(right?.pickNumber)) return false;
    const leftPlayerKey = parser.normalizePlayerKey(left?.playerKey);
    const rightPlayerKey = parser.normalizePlayerKey(right?.playerKey);
    if (leftPlayerKey && rightPlayerKey) return leftPlayerKey === rightPlayerKey;
    const leftPosition = normalizedObservedPosition(left?.position);
    const rightPosition = normalizedObservedPosition(right?.position);
    const leftTeam = String(left?.nflTeam || '').toUpperCase();
    const rightTeam = String(right?.nflTeam || '').toUpperCase();
    if (leftPosition === 'DEF' && rightPosition === 'DEF') {
      return Boolean(leftTeam && leftTeam === rightTeam);
    }

    const leftName = normalizedPlayerName(left?.player);
    const rightName = normalizedPlayerName(right?.player);
    if (!leftName || !rightName) return false;
    const leftInitialed = initialedNameParts(leftName);
    const rightInitialed = initialedNameParts(rightName);
    if (!leftInitialed && !rightInitialed) return leftName === rightName;
    if (!leftPosition || leftPosition !== rightPosition || !leftTeam || leftTeam !== rightTeam) {
      return false;
    }
    if (leftName === rightName) return true;

    const initialed = leftInitialed || rightInitialed;
    const fullName = leftInitialed ? rightName : leftName;
    const fullTokens = fullName.split(' ').filter(Boolean);
    if (!initialed || fullTokens.length < 2 || initialedNameParts(fullName)) return false;
    return fullTokens[0][0] === initialed.firstInitial &&
      fullTokens.slice(1).join(' ') === initialed.familyName;
  }

  function mergeSecondaryNumberedObservations(
    existingNumbered,
    observedPicks,
    timestamp,
  ) {
    const merged = (existingNumbered || []).map((pick) => ({ ...pick }));
    for (const observed of observedPicks || []) {
      if (!hasPickNumber(observed)) continue;
      const matchingIndex = merged.findIndex((pick) => sameNumberObservation(pick, observed));
      if (matchingIndex >= 0) {
        merged[matchingIndex] = fillMissingPickFields(merged[matchingIndex], observed);
        continue;
      }
      merged.push({
        ...observed,
        recordedAt: observed.recordedAt || timestamp,
      });
    }
    return merged.sort((left, right) => Number(left.pickNumber) - Number(right.pickNumber));
  }

  function updateDraftSessionFromSecondaryObservations(
    existing,
    metadata,
    observedPicks,
    timestamp,
  ) {
    const hasNumberedObservation = (observedPicks || []).some(hasPickNumber);
    if (!hasNumberedObservation) {
      return updateDraftSession(existing, metadata, observedPicks, timestamp);
    }
    if (existing?.numberedLedgerAuthoritative === true) {
      const savedHealth = ledgerHealth.analyzeLedger(existing?.picks || []);
      const savedMissingNumbers = new Set(savedHealth.missingPickNumbers);
      const fillsSavedGap = (observedPicks || []).some((pick) => (
        hasPickNumber(pick) && savedMissingNumbers.has(Number(pick.pickNumber))
      ));
      const updated = updateDraftSession(existing, metadata, observedPicks, timestamp);
      const resolvesIncompleteSavedState = !savedHealth.isComplete &&
        ledgerHealth.analyzeLedger(updated.picks).isComplete;
      return fillsSavedGap || resolvesIncompleteSavedState
        ? setAuthoritativeCaptureBlocked(updated, true)
        : updated;
    }

    const mergedNumbered = mergeSecondaryNumberedObservations(
      (existing?.picks || []).filter(hasPickNumber),
      observedPicks,
      timestamp,
    );
    const updated = updateDraftSessionFromAuthoritativeLedger(
      existing,
      metadata,
      mergedNumbered,
      observedPicks,
      timestamp,
    );
    delete updated.numberedLedgerAuthoritative;
    return setAuthoritativeCaptureBlocked(updated, true);
  }

  function updateDraftSessionFromAuthoritativeLedger(
    existing,
    metadata,
    authoritativePicks,
    observedNonLedgerPicks,
    timestamp,
  ) {
    const unmatchedExistingNumbered = (existing?.picks || [])
      .filter(hasPickNumber)
      .map((pick) => ({ ...pick }));

    const numberedPicks = (authoritativePicks || []).map((pick) => {
      const matchedIndex = unmatchedExistingNumbered.findIndex((saved) => (
        sameNumberObservation(saved, pick)
      ));
      const matched = matchedIndex >= 0
        ? unmatchedExistingNumbered.splice(matchedIndex, 1)[0]
        : null;
      const matchedPlayerKey = parser.normalizePlayerKey(matched?.playerKey);
      const preserveMatchedPlayerKey = (
        !parser.normalizePlayerKey(pick?.playerKey) && matchedPlayerKey
      );
      return {
        ...pick,
        ...(preserveMatchedPlayerKey ? { playerKey: matchedPlayerKey } : {}),
        recordedAt: pick.recordedAt || matched?.recordedAt || timestamp,
      };
    }).sort((left, right) => Number(left.pickNumber) - Number(right.pickNumber));

    const unnumberedCandidates = [
      ...(existing?.picks || []).filter((pick) => !hasPickNumber(pick)),
      ...(observedNonLedgerPicks || []).filter((pick) => !hasPickNumber(pick)).map((pick) => ({
        ...pick,
        recordedAt: pick.recordedAt || timestamp,
      })),
    ];
    const unmatchedUnnumbered = parser
      .upsertPicks(metadata.sessionKey, [], unnumberedCandidates)
      .filter((pick) => !numberedPicks.some((numbered) => (
        sameIdentityObservation(metadata.sessionKey, numbered, pick)
      )));

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
    const authoritativeSession = updateDraftSessionFromAuthoritativeLedger(
      existing,
      metadata,
      authoritativePicks,
      observedNonLedgerPicks,
      timestamp,
    );
    const authoritativeNumbers = new Set(
      (authoritativePicks || []).filter(hasPickNumber).map((pick) => Number(pick.pickNumber)),
    );
    const boundedSecondaryObservations = (observedNonLedgerPicks || []).filter((pick) => (
      !hasPickNumber(pick) || authoritativeNumbers.has(Number(pick.pickNumber))
    ));
    return {
      ok: true,
      health: visibleHealth,
      session: setAuthoritativeCaptureBlocked(
        updateDraftSession(
          authoritativeSession,
          metadata,
          boundedSecondaryObservations,
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
      updateDraftSessionFromAuthoritativeLedger(
        { ...(existing || {}), picks: [] },
        metadata,
        authoritativePicks,
        [],
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
    updateDraftSessionFromSecondaryObservations,
  };
  globalScope.YahooDraftSessionStore = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);

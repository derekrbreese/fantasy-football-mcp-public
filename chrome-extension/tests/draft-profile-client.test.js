const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const {
  MAX_PROFILE_RANKINGS,
  bindDraftProfile,
  buildDraftProfileRequest,
  describeProfileFreshness,
  isProfileReuseRecommendationError,
  listDraftProfileCatalog,
  listDraftProfiles,
  parseDraftProfileFile,
  parseRosterPositions,
  profileChoiceLabel,
  profileSportLabel,
  saveDraftProfile,
  saveDraftProfileXlsx,
  setDefaultDraftProfile,
} = require('../../src/dashboard/draft-profile-client.js');

test('parses a DraftSheets ECR CSV locally and allowlists ranking fields', () => {
  const source = [
    'RK,PLAYER NAME,TEAM,POS,BYE WEEK,TIER,PRIVATE NOTES,PLAYER URL',
    '2,"Doe, John",nyj,RB1,9,1,manager secret,https://example.test/?token=secret',
    '1,Arizona Cardinals,ari,D/ST,8,1,cookie=value,https://example.test/private',
  ].join('\r\n');

  const parsed = parseDraftProfileFile(source, 'rankings.csv');

  assert.equal(parsed.format, 'draftsheets-2026');
  assert.deepEqual(parsed.rankings, [
    { name: 'Arizona Cardinals', position: 'DST', team: 'ARI', rank: 1, bye_week: 8 },
    { name: 'Doe, John', position: 'RB', team: 'NYJ', rank: 2, bye_week: 9 },
  ]);
  assert.equal(JSON.stringify(parsed).includes('secret'), false);
  assert.equal(JSON.stringify(parsed).includes('http'), false);
  assert.equal(JSON.stringify(parsed).includes('TIER'), false);
});

test('normalizes generic CSV aliases and optional ADP without inventing missing values', () => {
  const parsed = parseDraftProfileFile([
    'Rank,Name,Position,NFL Team,ADP,Bye',
    '1,Player One,QB,buf,3.25,7',
    '2,Player Two,WR,,,',
  ].join('\n'), 'board.CSV');

  assert.equal(parsed.format, 'csv');
  assert.deepEqual(parsed.rankings, [
    {
      name: 'Player One',
      position: 'QB',
      team: 'BUF',
      rank: 1,
      average_draft_position: 3.25,
      bye_week: 7,
    },
    { name: 'Player Two', position: 'WR', rank: 2 },
  ]);
});

test('rejects malformed, ambiguous, or oversized generic CSV instead of guessing', () => {
  assert.throws(
    () => parseDraftProfileFile('Rank,Name\n1,Player One', 'board.csv'),
    /Position/,
  );
  assert.throws(
    () => parseDraftProfileFile('Rank,Name,Position\n1,"Unclosed,QB', 'board.csv'),
    /quoted field/,
  );
  assert.throws(
    () => parseDraftProfileFile('Rank,Name,Position\n1,One,QB\n1,Two,RB', 'board.csv'),
    /duplicate rank 1/i,
  );

  const rows = ['Rank,Name,Position'];
  for (let rank = 1; rank <= MAX_PROFILE_RANKINGS + 1; rank += 1) {
    rows.push(`${rank},Player ${rank},WR`);
  }
  assert.throws(
    () => parseDraftProfileFile(rows.join('\n'), 'generic.csv'),
    /at most 500/,
  );
});

test('takes the top 500 ranked rows from a recognized DraftSheets export', () => {
  const rows = ['RK,PLAYER NAME,TEAM,POS,BYE WEEK'];
  for (let rank = 520; rank >= 1; rank -= 1) {
    rows.push(`${rank},Player ${rank},BUF,WR${rank},7`);
  }

  const parsed = parseDraftProfileFile(rows.join('\n'), 'ECR.csv');

  assert.equal(parsed.format, 'draftsheets-2026');
  assert.equal(parsed.rankings.length, 500);
  assert.equal(parsed.rankings[0].rank, 1);
  assert.equal(parsed.rankings.at(-1).rank, 500);
  assert.equal(parsed.truncatedCount, 20);
});

test('parses strict JSON while omitting unknown private fields', () => {
  const parsed = parseDraftProfileFile(JSON.stringify({
    schemaVersion: 1,
    asOf: '2026-08-31T00:00:00Z',
    pageUrl: 'https://example.test/?auth=secret',
    rankings: [{
      rank: 1,
      name: 'Player One',
      position: 'TE1',
      team: 'kc',
      average_draft_position: 4.5,
      bye_week: 10,
      notes: 'private manager note',
      injury_status: 'Healthy',
    }],
  }), 'profile.json');

  assert.deepEqual(parsed, {
    format: 'json',
    asOf: '2026-08-31T00:00:00.000Z',
    rankings: [{
      name: 'Player One',
      position: 'TE',
      team: 'KC',
      rank: 1,
      average_draft_position: 4.5,
      bye_week: 10,
    }],
    truncatedCount: 0,
  });
  assert.equal(JSON.stringify(parsed).includes('secret'), false);
  assert.equal(JSON.stringify(parsed).includes('private'), false);
  assert.equal(JSON.stringify(parsed).includes('Healthy'), false);
});

test('does not accept a URL or spreadsheet formula disguised as a player name', () => {
  for (const name of ['https://example.test/?token=secret', '=HYPERLINK("https://example.test")']) {
    assert.throws(
      () => parseDraftProfileFile(JSON.stringify({
        schemaVersion: 1,
        rankings: [{ rank: 1, name, position: 'QB', team: 'BUF' }],
      }), 'profile.json'),
      /Player name.*invalid/,
    );
  }
});

test('uploads bounded XLSX bytes without transmitting its filename', async () => {
  const bytes = Uint8Array.from([0x50, 0x4b, 0x03, 0x04, 1, 2, 3]);
  let captured;
  const result = await saveDraftProfileXlsx({
    size: bytes.byteLength,
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    name: 'private-manager-rankings.xlsx',
    arrayBuffer: async () => bytes.buffer,
  }, '498589', {
    leagueSettings: {
      teams: 12,
      rosterPositions: parseRosterPositions({
        QB: 1, RB: 2, WR: 2, TE: 1, FLEX: 1, K: 1, DST: 1, BN: 6, IR: 1,
      }),
    },
    fetchImpl: async (url, options) => {
      captured = { url, options };
      return {
        ok: true,
        json: async () => ({ status: 'success', leagueId: '498589', rankingCount: 500 }),
      };
    },
  });

  assert.equal(captured.url, '/draft-profile-xlsx');
  assert.equal(captured.options.headers['X-Fantasy-League-ID'], '498589');
  assert.equal(captured.options.headers['X-Fantasy-Team-Count'], '12');
  assert.equal(
    captured.options.headers['X-Fantasy-Roster-Positions'],
    'QB=1,RB=2,WR=2,TE=1,FLEX=1,K=1,DST=1,BN=6,IR=1',
  );
  assert.equal(
    captured.options.headers['Content-Type'],
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  );
  assert.deepEqual([...captured.options.body], [...bytes]);
  assert.equal(JSON.stringify(captured).includes('private-manager'), false);
  assert.equal(Object.hasOwn(captured.options.headers, 'Content-Disposition'), false);
  assert.equal(result.rankingCount, 500);
});

test('rejects malformed or oversized XLSX before network access', async () => {
  let calls = 0;
  const fetchImpl = async () => { calls += 1; };
  await assert.rejects(
    saveDraftProfileXlsx({
      size: 4,
      arrayBuffer: async () => Uint8Array.from([1, 2, 3, 4]).buffer,
    }, '498589', { fetchImpl }),
    /valid XLSX/,
  );
  await assert.rejects(
    saveDraftProfileXlsx({
      size: 2_000_001,
      arrayBuffer: async () => new ArrayBuffer(0),
    }, '498589', { fetchImpl }),
    /2 MB/,
  );
  assert.equal(calls, 0);
});

test('constructs the canonical allowlisted per-league profile request', () => {
  const request = buildDraftProfileRequest({
    leagueId: '498589',
    format: 'draftsheets-2026',
    asOf: '2026-08-31',
    rankings: [{
      rank: 1,
      name: 'Player One',
      position: 'WR',
      team: 'BUF',
      average_draft_position: 2.5,
      bye_week: 7,
      pageUrl: 'https://example.test/secret',
    }],
    leagueSettings: {
      teams: 12,
      rosterPositions: parseRosterPositions({
        QB: 1,
        RB: 2,
        WR: 2,
        TE: 1,
        FLEX: 1,
        K: 1,
        DST: 1,
        BN: 6,
        IR: 1,
      }),
      cookie: 'secret',
    },
    rawFile: 'must not leave the browser',
  }, new Date('2026-09-01T12:34:56Z'));

  assert.deepEqual(request, {
    schemaVersion: 1,
    leagueId: '498589',
    importedAt: '2026-09-01T12:34:56.000Z',
    format: 'draftsheets-2026',
    asOf: '2026-08-31T00:00:00.000Z',
    rankings: [{
      name: 'Player One',
      position: 'WR',
      team: 'BUF',
      rank: 1,
      average_draft_position: 2.5,
      bye_week: 7,
    }],
    leagueSettings: {
      teams: 12,
      rosterPositions: [
        { position: 'QB', count: 1 },
        { position: 'RB', count: 2 },
        { position: 'WR', count: 2 },
        { position: 'TE', count: 1 },
        { position: 'FLEX', count: 1 },
        { position: 'K', count: 1 },
        { position: 'DST', count: 1 },
        { position: 'BN', count: 6 },
        { position: 'IR', count: 1 },
      ],
    },
  });
  assert.equal(JSON.stringify(request).includes('secret'), false);
  assert.equal(JSON.stringify(request).includes('rawFile'), false);
});

test('posts only canonical JSON to the same-origin draft-profile route', async () => {
  let captured;
  const result = await saveDraftProfile({
    leagueId: '498589',
    format: 'csv',
    rankings: [{ rank: 1, name: 'Player One', position: 'QB' }],
    leagueSettings: {
      teams: 12,
      rosterPositions: [{ position: 'QB', count: 1 }],
    },
  }, {
    now: new Date('2026-09-01T12:34:56Z'),
    fetchImpl: async (url, options) => {
      captured = { url, options };
      return {
        ok: true,
        json: async () => ({ status: 'success', leagueId: '498589', rankingCount: 1 }),
      };
    },
  });

  assert.equal(captured.url, '/draft-profile');
  assert.equal(captured.options.method, 'POST');
  assert.equal(captured.options.cache, 'no-store');
  assert.equal(captured.options.credentials, 'omit');
  assert.equal(captured.options.headers['Content-Type'], 'application/json');
  assert.equal(captured.options.headers['X-Fantasy-Draft-UI'], '1');
  assert.equal(Object.hasOwn(JSON.parse(captured.options.body), 'rawFile'), false);
  assert.deepEqual(result, { status: 'success', leagueId: '498589', rankingCount: 1 });
});

test('lists only safe saved-profile summaries without choosing one', async () => {
  let captured;
  const profiles = await listDraftProfiles({
    fetchImpl: async (url, options) => {
      captured = { url, options };
      return {
        ok: true,
        json: async () => ({
          status: 'success',
          profiles: [{
            sport: 'f1',
            leagueId: '498589',
            importedAt: '2026-09-01T12:34:56Z',
            asOf: '2026-08-31',
            format: 'draftsheets-2026',
            rankingCount: 500,
            teamId: '6',
            rankings: [{ name: 'must not reach the dashboard' }],
          }],
          privatePath: '/Users/private/draft-profiles.json',
        }),
      };
    },
  });

  assert.equal(captured.url, '/draft-profiles');
  assert.equal(captured.options.method, 'GET');
  assert.equal(captured.options.cache, 'no-store');
  assert.equal(captured.options.credentials, 'omit');
  assert.equal(captured.options.headers['X-Fantasy-Draft-UI'], '1');
  assert.deepEqual(profiles, [{
    sport: 'f1',
    leagueId: '498589',
    importedAt: '2026-09-01T12:34:56.000Z',
    asOf: '2026-08-31',
    format: 'draftsheets-2026',
    rankingCount: 500,
  }]);
  assert.equal(JSON.stringify(profiles).includes('private'), false);
  assert.equal(JSON.stringify(profiles).includes('rankings'), false);
});

test('lists a strict profile catalog with explicit per-sport defaults', async () => {
  let captured;
  const catalog = await listDraftProfileCatalog({
    fetchImpl: async (url, options) => {
      captured = { url, options };
      return {
        ok: true,
        json: async () => ({
          status: 'success',
          profiles: [{
            sport: 'f1',
            leagueId: '498589',
            importedAt: '2026-09-01T12:34:56Z',
            asOf: '2026-08-31',
            format: 'draftsheets-2026',
            rankingCount: 500,
          }],
          defaults: [{ sport: 'f1', sourceLeagueId: '498589' }],
          privatePath: '/Users/private/draft-profile-defaults.json',
        }),
      };
    },
  });

  assert.equal(captured.url, '/draft-profiles');
  assert.equal(captured.options.method, 'GET');
  assert.deepEqual(catalog, {
    profiles: [{
      sport: 'f1',
      leagueId: '498589',
      importedAt: '2026-09-01T12:34:56.000Z',
      asOf: '2026-08-31',
      format: 'draftsheets-2026',
      rankingCount: 500,
    }],
    defaults: [{ sport: 'f1', sourceLeagueId: '498589' }],
  });
  assert.equal(JSON.stringify(catalog).includes('privatePath'), false);
});

test('sets and clears only a canonical same-origin sport default', async () => {
  const requests = [];
  const fetchImpl = async (url, options) => {
    const request = JSON.parse(options.body);
    requests.push({ url, options, request });
    return {
      ok: true,
      json: async () => ({
        status: 'success',
        sport: request.sport,
        sourceLeagueId: request.sourceLeagueId,
      }),
    };
  };

  assert.deepEqual(
    await setDefaultDraftProfile('f1', '498589', { fetchImpl }),
    { status: 'success', sport: 'f1', sourceLeagueId: '498589' },
  );
  assert.deepEqual(
    await setDefaultDraftProfile('f1', null, { fetchImpl }),
    { status: 'success', sport: 'f1', sourceLeagueId: null },
  );
  for (const captured of requests) {
    assert.equal(captured.url, '/draft-profile-default');
    assert.equal(captured.options.method, 'POST');
    assert.equal(captured.options.cache, 'no-store');
    assert.equal(captured.options.credentials, 'omit');
    assert.equal(captured.options.headers['Content-Type'], 'application/json');
    assert.equal(captured.options.headers['X-Fantasy-Draft-UI'], '1');
    assert.deepEqual(Object.keys(captured.request).sort(), [
      'schemaVersion', 'sourceLeagueId', 'sport',
    ]);
  }
  assert.deepEqual(requests.map(({ request }) => request), [
    { schemaVersion: 1, sport: 'f1', sourceLeagueId: '498589' },
    { schemaVersion: 1, sport: 'f1', sourceLeagueId: null },
  ]);
  assert.equal(JSON.stringify(requests).includes('picks'), false);
  assert.equal(JSON.stringify(requests).includes('auth'), false);
});

test('default profile client rejects unsafe inputs and mismatched confirmations', async () => {
  await assert.rejects(
    setDefaultDraftProfile('f1', '498589', {
      endpoint: 'https://example.test/draft-profile-default',
      fetchImpl: async () => ({ ok: true, json: async () => ({}) }),
    }),
    /same-origin/i,
  );
  await assert.rejects(
    setDefaultDraftProfile('https://private.example', '498589', {
      fetchImpl: async () => ({ ok: true, json: async () => ({}) }),
    }),
    /sport/i,
  );
  await assert.rejects(
    setDefaultDraftProfile('f1', '498589', {
      fetchImpl: async () => ({
        ok: true,
        json: async () => ({ status: 'success', sport: 'nfl', sourceLeagueId: '498589' }),
      }),
    }),
    /sport/i,
  );
});

test('accepts a safe orphan default pointer so the dashboard can recover it', async () => {
  const catalog = await listDraftProfileCatalog({
    fetchImpl: async () => ({
      ok: true,
      json: async () => ({
        status: 'success',
        profiles: [{
          sport: 'f1',
          leagueId: '498589',
          importedAt: '2026-09-01T12:34:56Z',
          format: 'json',
          rankingCount: 250,
        }],
        defaults: [{ sport: 'f1', sourceLeagueId: '999' }],
      }),
    }),
  });

  assert.deepEqual(catalog.defaults, [{ sport: 'f1', sourceLeagueId: '999' }]);
  assert.equal(catalog.profiles.some((profile) => profile.leagueId === '999'), false);
});

test('labels reusable profiles with validated sport and source or import date', () => {
  assert.equal(profileChoiceLabel({
    sport: 'f1',
    leagueId: '777777',
    importedAt: '2026-09-01T12:34:56Z',
    asOf: '2026-08-31',
    format: 'draftsheets-2026',
    rankingCount: 500,
  }), 'Yahoo Football · League 777777 · DraftSheets · source Aug 31, 2026 · 500 rankings');
  assert.equal(profileChoiceLabel({
    sport: 'nfl',
    leagueId: '498589',
    importedAt: '2026-09-01T12:34:56Z',
    format: 'json',
    rankingCount: 250,
  }), 'NFL · League 498589 · JSON · imported Sep 1, 2026 · 250 rankings');
  assert.equal(profileSportLabel('f1'), 'Yahoo Football');
  assert.equal(profileSportLabel('nfl'), 'NFL');
  assert.equal(profileSportLabel('other_slug'), 'Other Yahoo fantasy sport');
});

test('explicitly binds only rankings/settings from a chosen source profile', async () => {
  let captured;
  const result = await bindDraftProfile('498589', '777777', {
    fetchImpl: async (url, options) => {
      captured = { url, options };
      return {
        ok: true,
        json: async () => ({
          status: 'success',
          leagueId: '777777',
          sourceLeagueId: '498589',
          rankingCount: 500,
          format: 'draftsheets-2026',
        }),
      };
    },
  });

  assert.equal(captured.url, '/draft-profile-bind');
  assert.equal(captured.options.method, 'POST');
  assert.equal(captured.options.cache, 'no-store');
  assert.equal(captured.options.credentials, 'omit');
  assert.equal(captured.options.headers['Content-Type'], 'application/json');
  assert.equal(captured.options.headers['X-Fantasy-Draft-UI'], '1');
  assert.deepEqual(JSON.parse(captured.options.body), {
    schemaVersion: 1,
    sourceLeagueId: '498589',
    leagueId: '777777',
  });
  assert.equal(captured.options.body.includes('picks'), false);
  assert.equal(captured.options.body.includes('teamId'), false);
  assert.deepEqual(result, {
    status: 'success',
    leagueId: '777777',
    sourceLeagueId: '498589',
    rankingCount: 500,
    format: 'draftsheets-2026',
  });
});

test('saved-profile reuse rejects unsafe endpoints, invalid summaries, and changed identities', async () => {
  await assert.rejects(
    listDraftProfiles({
      endpoint: 'https://example.test/draft-profiles',
      fetchImpl: async () => ({ ok: true, json: async () => ({}) }),
    }),
    /same-origin/,
  );
  await assert.rejects(
    listDraftProfiles({
      fetchImpl: async () => ({
        ok: true,
        json: async () => ({
          status: 'success',
          profiles: [{
            sport: 'f1', leagueId: '498589', importedAt: 'not-a-date', format: 'csv', rankingCount: 1,
          }],
        }),
      }),
    }),
    /saved profile summary/i,
  );
  await assert.rejects(
    listDraftProfiles({
      fetchImpl: async () => ({
        ok: true,
        json: async () => ({
          status: 'success',
          profiles: [{
            sport: 'https://private.example',
            leagueId: '498589',
            importedAt: '2026-09-01T12:34:56Z',
            format: 'csv',
            rankingCount: 1,
          }],
        }),
      }),
    }),
    /saved profile summary/i,
  );
  await assert.rejects(
    bindDraftProfile('498589', '498589', {
      fetchImpl: async () => ({ ok: true, json: async () => ({}) }),
    }),
    /different draft/i,
  );
  await assert.rejects(
    bindDraftProfile('498589', '777777', {
      fetchImpl: async () => ({
        ok: true,
        json: async () => ({
          status: 'success', leagueId: '777777', sourceLeagueId: '999', rankingCount: 1,
        }),
      }),
    }),
    /source profile/i,
  );
  await assert.rejects(
    bindDraftProfile('498589', '777777', {
      fetchImpl: async () => ({
        ok: false,
        status: 409,
        json: async () => ({
          status: 'error', message: 'selected local profile belongs to a different sport',
        }),
      }),
    }),
    /different sport/i,
  );
});

test('bounds and clears saved-profile list, bind, and default timeouts', async () => {
  class FakeAbortSignal {
    constructor() {
      this.aborted = false;
      this.listeners = [];
    }

    addEventListener(type, listener) {
      if (type === 'abort') this.listeners.push(listener);
    }
  }

  class FakeAbortController {
    constructor() {
      this.signal = new FakeAbortSignal();
    }

    abort() {
      this.signal.aborted = true;
      this.signal.listeners.forEach((listener) => listener());
    }
  }

  async function expectTimeout(operation) {
    let timerCallback;
    let timerDelay;
    let clearedTimer;
    const request = operation({
      timeoutMs: 1,
      AbortControllerImpl: FakeAbortController,
      setTimeoutImpl: (callback, delay) => {
        timerCallback = callback;
        timerDelay = delay;
        return 17;
      },
      clearTimeoutImpl: (timer) => { clearedTimer = timer; },
      fetchImpl: async (_url, options) => new Promise((_resolve, reject) => {
        options.signal.addEventListener('abort', () => {
          const error = new Error('aborted');
          error.name = 'AbortError';
          reject(error);
        });
      }),
    });
    assert.equal(timerDelay, 250);
    timerCallback();
    await assert.rejects(request, /timed out/i);
    assert.equal(clearedTimer, 17);
  }

  await expectTimeout((options) => listDraftProfiles(options));
  await expectTimeout((options) => bindDraftProfile('498589', '777777', options));
  await expectTimeout((options) => setDefaultDraftProfile('f1', '498589', options));
});

test('recognizes both Yahoo fallback failures that explicit profile reuse can recover', () => {
  assert.equal(
    isProfileReuseRecommendationError(
      'Recommendation server returned HTTP 400: Yahoo league identity could not be resolved for the synced draft',
    ),
    true,
  );
  assert.equal(
    isProfileReuseRecommendationError(
      'Recommendation server returned HTTP 400: Yahoo league discovery is unavailable. Configure Yahoo credentials or explicitly bind a saved local profile to this draft.',
    ),
    true,
  );
  assert.equal(
    isProfileReuseRecommendationError('Recommendation server returned HTTP 400: ledger has a gap'),
    false,
  );
});

test('rejects unsafe endpoints and cross-league responses', async () => {
  const profile = {
    leagueId: '498589',
    format: 'json',
    rankings: [{ rank: 1, name: 'Player One', position: 'QB' }],
    leagueSettings: {
      teams: 12,
      rosterPositions: [{ position: 'QB', count: 1 }],
    },
  };

  await assert.rejects(
    saveDraftProfile(profile, {
      endpoint: 'https://example.test/draft-profile',
      fetchImpl: async () => ({ ok: true, json: async () => ({}) }),
    }),
    /same-origin/,
  );
  await assert.rejects(
    saveDraftProfile(profile, {
      fetchImpl: async () => ({
        ok: true,
        json: async () => ({ status: 'success', leagueId: '999' }),
      }),
    }),
    /did not match/,
  );
  await assert.rejects(
    saveDraftProfile(profile, {
      fetchImpl: async () => ({
        ok: true,
        json: async () => ({ status: 'error', leagueId: '498589' }),
      }),
    }),
    /did not confirm success/,
  );
});

test('labels freshness without claiming an undated source is current', () => {
  const now = new Date('2026-09-01T12:00:00Z');
  assert.deepEqual(describeProfileFreshness(null, now), {
    kind: 'unknown',
    label: 'Source date unknown',
  });
  assert.deepEqual(describeProfileFreshness('2026-08-31T00:00:00Z', now), {
    kind: 'fresh',
    label: 'Current · source dated Aug 31, 2026',
  });
  assert.deepEqual(describeProfileFreshness('2026-07-01T00:00:00Z', now), {
    kind: 'stale',
    label: 'Stale · source dated Jul 1, 2026',
  });
});

test('dashboard exposes a local import form while retaining recommendation controls', () => {
  const html = fs.readFileSync(
    path.join(__dirname, '../../src/dashboard/index.html'),
    'utf8',
  );
  const appSource = fs.readFileSync(
    path.join(__dirname, '../../src/dashboard/app.js'),
    'utf8',
  );

  assert.match(html, /id="draft-profile-form"/);
  assert.match(html, /id="draft-profile-file"/);
  assert.match(html, /accept="\.xlsx,\.csv,\.json/);
  assert.match(html, /id="profile-source-status"/);
  assert.match(html, /id="draft-profile-reuse-form"/);
  assert.match(html, /id="profile-source-league"/);
  assert.match(html, /Use for this draft/);
  assert.match(html, /rankings and league settings only/i);
  assert.match(html, /id="draft-profile-default-form"/);
  assert.match(html, /id="profile-default-sport"/);
  assert.match(html, /id="profile-default-source"/);
  assert.match(html, /id="clear-profile-default-button"/);
  assert.match(html, /future profileless Yahoo drafts/i);
  assert.match(html, /including real drafts and mocks/i);
  assert.match(html, /Exact profiles are never overwritten/i);
  assert.match(html, /picks are never copied/i);
  assert.doesNotMatch(html, /automatically detect(?:s|ed)? (?:an )?(?:instant )?mock/i);
  assert.match(html, /DraftSheets \.xlsx/);
  assert.match(html, /id="recommendation-form"/);
  assert.match(html, /draft-profile-client\.js/);
  assert.match(appSource, /listDraftProfileCatalog\(/);
  assert.match(appSource, /bindDraftProfile\(/);
  assert.match(appSource, /setDefaultDraftProfile\(/);
  assert.doesNotMatch(appSource, /detect[^\n]{0,40}(?:instant )?mock/i);
  const orphanGuard = appSource.indexOf('if (currentDefault && !sourceProfile)');
  const validDefaultGuard = appSource.indexOf('if (currentDefault && sourceProfile)', orphanGuard);
  const orphanBranch = appSource.slice(orphanGuard, validDefaultGuard);
  assert.ok(orphanGuard > 0);
  assert.match(orphanBranch, /source profile is missing/i);
  assert.match(orphanBranch, /Clear it or choose another saved profile/i);
  assert.match(orphanBranch, /'error'/);
  const defaultReadyBranch = appSource.slice(
    validDefaultGuard,
    appSource.indexOf('function renderProfileDefaultChoices', validDefaultGuard),
  );
  assert.match(
    defaultReadyBranch,
    /describeProfileFreshness\(\s*sourceProfile\.asOf \|\| sourceProfile\.importedAt,?\s*\)/,
  );
  assert.doesNotMatch(defaultReadyBranch, /setProfileDefaultStatus\([\s\S]{0,500}'fresh'/);
  assert.match(
    appSource,
    /new Set\(\[[\s\S]{0,160}savedProfiles\.map[\s\S]{0,160}savedProfileDefaults\.map/,
  );
  assert.match(
    appSource,
    /savedProfiles\.length === 0 && savedProfileDefaults\.length === 0/,
  );
  assert.match(
    appSource,
    /clear-profile-default-button'[\s\S]{0,120}disabled = profileControlsBusy \|\|\s*!currentDefault/,
  );
  assert.match(appSource, /profileChoiceLabel\(profile\)/);
  assert.match(appSource, /profileSportLabel\(sourceProfile\.sport\)/);
  assert.doesNotMatch(appSource, /sourceProfile\.sport\.toUpperCase\(\)/);
  assert.match(appSource, /different sport/i);
  assert.match(appSource, /await refreshAnalysis\(leagueId\)/);
  assert.match(appSource, /profileSourceLeague\.value\s*=\s*choices\.some/);
  assert.doesNotMatch(appSource, /profileSourceLeague\.value\s*=\s*(?:choices|savedProfiles)\[0\]/);
  assert.ok(
    appSource.indexOf('if (shouldRefresh) await refreshAnalysis(leagueId)') >
      appSource.indexOf('await profileClient.bindDraftProfile(sourceLeagueId, leagueId)'),
  );
  assert.match(appSource, /\.textContent\s*=/);
  assert.doesNotMatch(appSource, /profile-source-status[^\n]*innerHTML/);
});

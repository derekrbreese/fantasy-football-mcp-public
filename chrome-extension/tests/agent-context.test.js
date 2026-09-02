const test = require('node:test');
const assert = require('node:assert/strict');

const { sessionToAgentContext } = require('../agent-context.js');

test('builds recommendation-ready context with all picks and the user roster', () => {
  const context = sessionToAgentContext(
    {
      sport: 'f1',
      leagueId: '10462193',
      teamId: '6',
      sessionKey: 'f1:10462193',
      updatedAt: '2026-08-31T22:44:58.255Z',
      picks: [
        { pickNumber: 1, player: 'J. Gibbs', position: 'RB', nflTeam: 'DET', fantasyTeam: 'Team 1', isUserPick: false },
        { pickNumber: 6, player: 'P. Nacua', position: 'WR', nflTeam: 'LAR', fantasyTeam: 'Your Team', isUserPick: true },
        { pickNumber: 19, player: 'S. Barkley', position: 'RB', nflTeam: 'PHI', fantasyTeam: 'Your Team', isUserPick: true },
      ],
    },
    '2026-08-31T22:45:00.000Z',
  );

  assert.deepEqual(context.summary, {
    totalPicks: 3,
    latestOverallPick: 19,
    nextOverallPick: 20,
    userPickCount: 2,
  });
  assert.deepEqual(context.userRoster.map((pick) => pick.player), ['P. Nacua', 'S. Barkley']);
  assert.deepEqual(Object.keys(context.teamRosters), ['Team 1', 'Your Team']);
  assert.equal(context.generatedAt, '2026-08-31T22:45:00.000Z');
  assert.equal(context.draft.leagueId, '10462193');
  assert.equal(context.picks.length, 3);
});

test('does not include credentials, URLs, or arbitrary session properties', () => {
  const context = sessionToAgentContext({
    sport: 'f1',
    leagueId: '123',
    teamId: '6',
    sessionKey: 'f1:123',
    auth: 'secret',
    url: 'https://example.test/?auth=secret',
    picks: [],
  });

  assert.equal(JSON.stringify(context).includes('secret'), false);
  assert.equal('auth' in context.draft, false);
  assert.equal('url' in context.draft, false);
});

test('strips injected non-allowlisted fields from Picks-panel observations', () => {
  const context = sessionToAgentContext({
    sport: 'f1',
    leagueId: '123',
    teamId: '6',
    sessionKey: 'f1:123',
    picks: [{
      pickNumber: 1,
      player: 'J. Gibbs',
      position: 'RB',
      nflTeam: 'DET',
      fantasyTeam: 'Team 1',
      source: 'picks-panel',
      href: 'https://example.test/?auth=secret',
      ariaLabel: 'private manager text',
      injuryStatus: 'Q',
    }],
  }, '2026-08-31T22:45:00.000Z');

  assert.deepEqual(context.picks, [{
    pickNumber: 1,
    player: 'J. Gibbs',
    position: 'RB',
    nflTeam: 'DET',
    fantasyTeam: 'Team 1',
  }]);
  assert.equal(JSON.stringify(context).includes('example.test'), false);
  assert.equal(JSON.stringify(context).includes('private manager'), false);
  assert.equal(context.repair, undefined);
});

test('adds a top-level repair marker only for explicit repair sync', () => {
  const session = { sport: 'f1', leagueId: '123', teamId: '6', sessionKey: 'f1:123', picks: [] };

  assert.equal(sessionToAgentContext(session, '2026-08-31T22:45:00.000Z').repair, undefined);
  assert.equal(
    sessionToAgentContext(session, '2026-08-31T22:45:00.000Z', { repair: true }).repair,
    true,
  );
});

test('sends only the allowlisted authoritative-capture blocker', () => {
  const session = {
    sport: 'f1',
    leagueId: '123',
    teamId: '6',
    sessionKey: 'f1:123',
    authoritativeCaptureBlocked: true,
    authoritativeCaptureError: 'private page text https://example.test/?auth=secret',
    picks: [],
  };

  const blocked = sessionToAgentContext(session, '2026-08-31T22:45:00.000Z');
  assert.equal(blocked.captureBlocked, true);
  assert.equal(JSON.stringify(blocked).includes('private page text'), false);
  assert.equal(JSON.stringify(blocked).includes('secret'), false);

  const repaired = sessionToAgentContext(
    session,
    '2026-08-31T22:45:00.000Z',
    { repair: true },
  );
  assert.equal(repaired.captureBlocked, undefined);
});

test('emits only literal boolean capture states and preserves tri-state semantics', () => {
  const session = {
    sport: 'f1',
    leagueId: '123',
    teamId: '6',
    sessionKey: 'f1:123',
    picks: [],
  };

  assert.equal(sessionToAgentContext(session).captureBlocked, undefined);
  assert.equal(
    sessionToAgentContext({ ...session, authoritativeCaptureBlocked: false }).captureBlocked,
    false,
  );
  assert.equal(
    sessionToAgentContext({ ...session, authoritativeCaptureBlocked: { error: 'raw' } }).captureBlocked,
    undefined,
  );
});

test('defaults generatedAt to the validated session snapshot time', () => {
  const session = {
    sport: 'f1',
    leagueId: '123',
    teamId: '6',
    sessionKey: 'f1:123',
    updatedAt: '2026-08-31T22:44:58.255Z',
    picks: [],
  };

  assert.equal(sessionToAgentContext(session).generatedAt, session.updatedAt);
  assert.equal(sessionToAgentContext(session, 'not-an-iso-time').generatedAt, session.updatedAt);
});

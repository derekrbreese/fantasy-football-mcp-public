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

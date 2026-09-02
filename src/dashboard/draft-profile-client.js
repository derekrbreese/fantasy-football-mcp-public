(function initDraftProfileClient(globalScope) {
  'use strict';

  const MAX_PROFILE_RANKINGS = 500;
  const MAX_XLSX_BYTES = 2_000_000;
  const PROFILE_ENDPOINT = '/draft-profile';
  const XLSX_ENDPOINT = '/draft-profile-xlsx';
  const PROFILES_ENDPOINT = '/draft-profiles';
  const PROFILE_BIND_ENDPOINT = '/draft-profile-bind';
  const PROFILE_DEFAULT_ENDPOINT = '/draft-profile-default';
  const DEFAULT_PROFILE_REQUEST_TIMEOUT_MS = 5000;
  const MIN_PROFILE_REQUEST_TIMEOUT_MS = 250;
  const MAX_PROFILE_REQUEST_TIMEOUT_MS = 15000;
  const XLSX_CONTENT_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
  const POSITION_ORDER = ['QB', 'RB', 'WR', 'TE', 'FLEX', 'K', 'DST', 'BN', 'IR'];
  const PLAYER_POSITIONS = new Set(['QB', 'RB', 'WR', 'TE', 'K', 'DST']);
  const FORMATS = new Set(['draftsheets-2026', 'csv', 'json']);
  const BREAKOUT_EVIDENCE_KEYS = new Set([
    'source',
    'as_of',
    'projected_points',
    'projected_opportunities',
    'opportunity_kind',
    'experience_years',
  ]);
  const BREAKOUT_OPPORTUNITY_KINDS = {
    RB: new Set(['touches']),
    WR: new Set(['targets', 'receptions']),
    TE: new Set(['targets', 'receptions']),
  };

  function safeLeagueId(value) {
    const leagueId = typeof value === 'string' ? value.trim() : '';
    if (!/^\d{1,32}$/.test(leagueId)) {
      throw new Error('Choose a valid Yahoo league ID before importing a profile.');
    }
    return leagueId;
  }

  function safeSport(value) {
    const sport = typeof value === 'string' ? value.trim().toLowerCase() : '';
    if (!/^[a-z0-9_-]{1,16}$/.test(sport)) {
      throw new Error('Saved profile sport is missing or invalid.');
    }
    return sport;
  }

  function finiteNumber(value, label) {
    if (value === null || value === undefined || value === '') return null;
    const text = typeof value === 'string' ? value.trim() : value;
    const number = typeof text === 'number' ? text : Number(text);
    if (!Number.isFinite(number)) throw new Error(`${label} must be a finite number.`);
    return number;
  }

  function safeInteger(value, label, minimum, maximum) {
    const number = finiteNumber(value, label);
    if (number === null || !Number.isInteger(number) || number < minimum || number > maximum) {
      throw new Error(`${label} must be an integer from ${minimum} to ${maximum}.`);
    }
    return number;
  }

  function safeText(value, label, maximum) {
    const text = typeof value === 'string' ? value.trim() : '';
    if (
      !text ||
      text.length > maximum ||
      /[\u0000-\u001f\u007f]/.test(text) ||
      /(?:https?:\/\/|www\.|[?&](?:auth|token|key)=)/i.test(text) ||
      /^[=+@]/.test(text)
    ) {
      throw new Error(`${label} is missing or invalid.`);
    }
    return text;
  }

  function normalizePosition(value) {
    let position = String(value || '').trim().toUpperCase().replace(/\s+/g, '');
    position = position.replace(/\d+$/, '');
    if (position === 'DEF' || position === 'D/ST') position = 'DST';
    if (!PLAYER_POSITIONS.has(position)) {
      throw new Error(`Unsupported player position: ${String(value || 'missing').slice(0, 20)}.`);
    }
    return position;
  }

  function optionalTeam(value) {
    const team = String(value || '').trim().toUpperCase();
    if (!team || team === '-' || team === 'FA') return null;
    if (!/^[A-Z]{2,4}$/.test(team)) throw new Error('NFL team must be a 2–4 letter code.');
    return team;
  }

  function optionalPlayerKey(value, rowNumber) {
    if (value === null || value === undefined || value === '') return null;
    const playerKey = typeof value === 'string' ? value.trim() : '';
    if (!/^[1-9]\d{0,9}\.p\.[1-9]\d{0,9}$/.test(playerKey)) {
      throw new Error(`Yahoo player key in row ${rowNumber} is invalid.`);
    }
    return playerKey;
  }

  function optionalIsoDate(value, label = 'Source date') {
    if (value === null || value === undefined || value === '') return null;
    if (typeof value !== 'string' || value.length > 40) throw new Error(`${label} is invalid.`);
    const parsed = new Date(value);
    if (!Number.isFinite(parsed.getTime())) throw new Error(`${label} is invalid.`);
    return parsed.toISOString();
  }

  function sanitizeBreakoutEvidence(value, rowNumber, position) {
    if (value === null || value === undefined) return null;
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      throw new Error(`Breakout evidence in row ${rowNumber} must be a complete object.`);
    }
    const keys = Object.keys(value);
    if (keys.length !== BREAKOUT_EVIDENCE_KEYS.size || keys.some((key) => !BREAKOUT_EVIDENCE_KEYS.has(key))) {
      throw new Error(`Breakout evidence in row ${rowNumber} must include only the complete supported fields.`);
    }
    const expectedKinds = BREAKOUT_OPPORTUNITY_KINDS[position];
    if (!expectedKinds) {
      throw new Error(`Breakout evidence in row ${rowNumber} is supported only for RB, WR, and TE.`);
    }
    const source = safeText(value.source, `Projection source in row ${rowNumber}`, 80);
    if (!/^[A-Za-z0-9][A-Za-z0-9 ._()&'+-]{0,79}$/.test(source)) {
      throw new Error(`Projection source in row ${rowNumber} is missing or invalid.`);
    }
    const asOfValue = typeof value.as_of === 'string' ? value.as_of.trim() : '';
    if (!/^\d{4}-\d{2}-\d{2}$/.test(asOfValue)) {
      throw new Error(`Projection date in row ${rowNumber} must be YYYY-MM-DD.`);
    }
    const parsedDate = new Date(`${asOfValue}T00:00:00Z`);
    if (!Number.isFinite(parsedDate.getTime()) || parsedDate.toISOString().slice(0, 10) !== asOfValue) {
      throw new Error(`Projection date in row ${rowNumber} is invalid.`);
    }
    const projectedPoints = finiteNumber(
      value.projected_points,
      `Projected points in row ${rowNumber}`,
    );
    if (projectedPoints === null || projectedPoints <= 0 || projectedPoints > 1_000) {
      throw new Error(`Projected points in row ${rowNumber} must be greater than 0 and at most 1000.`);
    }
    const opportunities = finiteNumber(
      value.projected_opportunities,
      `Projected opportunities in row ${rowNumber}`,
    );
    if (opportunities === null || opportunities < 1 || opportunities > 1_000) {
      throw new Error(`Projected opportunities in row ${rowNumber} must be from 1 to 1000.`);
    }
    const opportunityKind = String(value.opportunity_kind || '').trim().toLowerCase();
    if (!expectedKinds.has(opportunityKind)) {
      throw new Error(`Opportunity kind in row ${rowNumber} must be ${[...expectedKinds].join(' or ')} for ${position}.`);
    }
    return {
      source,
      as_of: asOfValue,
      projected_points: projectedPoints,
      projected_opportunities: opportunities,
      opportunity_kind: opportunityKind,
      experience_years: safeInteger(
        value.experience_years,
        `Experience years in row ${rowNumber}`,
        0,
        30,
      ),
    };
  }

  function sanitizeRanking(value, rowNumber) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      throw new Error(`Ranking row ${rowNumber} must be an object.`);
    }
    const rank = safeInteger(value.rank, `Rank in row ${rowNumber}`, 1, 10_000);
    const position = normalizePosition(value.position);
    const result = {
      name: safeText(value.name, `Player name in row ${rowNumber}`, 120),
      position,
      rank,
    };
    const team = optionalTeam(value.team);
    if (team) result.team = team;
    const playerKey = optionalPlayerKey(value.player_key ?? value.playerKey, rowNumber);
    if (playerKey) result.player_key = playerKey;
    const adp = finiteNumber(value.average_draft_position, `ADP in row ${rowNumber}`);
    if (adp !== null) {
      if (adp <= 0 || adp > 10_000) throw new Error(`ADP in row ${rowNumber} is out of range.`);
      result.average_draft_position = adp;
    }
    const bye = finiteNumber(value.bye_week, `Bye week in row ${rowNumber}`);
    if (bye !== null) {
      if (!Number.isInteger(bye) || bye < 1 || bye > 18) {
        throw new Error(`Bye week in row ${rowNumber} must be an integer from 1 to 18.`);
      }
      result.bye_week = bye;
    }
    const breakoutEvidence = sanitizeBreakoutEvidence(
      value.breakout_evidence,
      rowNumber,
      position,
    );
    if (breakoutEvidence) result.breakout_evidence = breakoutEvidence;
    return result;
  }

  function validateAndSortRankings(values, { allowDraftSheetsTruncation = false } = {}) {
    if (!Array.isArray(values) || values.length === 0) {
      throw new Error('The profile must contain at least one ranking row.');
    }
    const rankings = values.map((value, index) => sanitizeRanking(value, index + 1));
    rankings.sort((left, right) => left.rank - right.rank || left.name.localeCompare(right.name));
    const ranks = new Set();
    rankings.forEach((ranking) => {
      if (ranks.has(ranking.rank)) throw new Error(`The ranking board contains duplicate rank ${ranking.rank}.`);
      ranks.add(ranking.rank);
    });
    if (rankings.length > MAX_PROFILE_RANKINGS && !allowDraftSheetsTruncation) {
      throw new Error(`A local draft profile can contain at most ${MAX_PROFILE_RANKINGS} rankings.`);
    }
    return {
      rankings: rankings.slice(0, MAX_PROFILE_RANKINGS),
      truncatedCount: Math.max(0, rankings.length - MAX_PROFILE_RANKINGS),
    };
  }

  function parseCsvRows(text) {
    if (typeof text !== 'string' || !text.trim()) throw new Error('The CSV file is empty.');
    const rows = [];
    let row = [];
    let field = '';
    let quoted = false;
    for (let index = 0; index < text.length; index += 1) {
      const character = text[index];
      if (quoted) {
        if (character === '"' && text[index + 1] === '"') {
          field += '"';
          index += 1;
        } else if (character === '"') {
          quoted = false;
        } else {
          field += character;
        }
      } else if (character === '"' && field === '') {
        quoted = true;
      } else if (character === ',') {
        row.push(field);
        field = '';
      } else if (character === '\n' || character === '\r') {
        if (character === '\r' && text[index + 1] === '\n') index += 1;
        row.push(field);
        if (row.some((item) => item.trim())) rows.push(row);
        row = [];
        field = '';
      } else {
        field += character;
      }
    }
    if (quoted) throw new Error('CSV contains an unclosed quoted field.');
    row.push(field);
    if (row.some((item) => item.trim())) rows.push(row);
    if (rows.length < 2) throw new Error('CSV must contain a header and at least one ranking row.');
    return rows;
  }

  function normalizedHeader(value) {
    return String(value || '')
      .replace(/^\uFEFF/, '')
      .trim()
      .toUpperCase()
      .replace(/[^A-Z0-9]+/g, ' ')
      .trim();
  }

  function headerIndex(headers, aliases, label, required = false) {
    const indexes = [];
    headers.forEach((header, index) => {
      if (aliases.has(header)) indexes.push(index);
    });
    if (indexes.length > 1) throw new Error(`CSV has ambiguous ${label} columns.`);
    if (required && indexes.length === 0) throw new Error(`CSV is missing the required ${label} column.`);
    return indexes.length ? indexes[0] : -1;
  }

  function parseCsvProfile(text) {
    const rows = parseCsvRows(text);
    const headers = rows[0].map(normalizedHeader);
    const indexes = {
      rank: headerIndex(headers, new Set(['RK', 'RANK', 'ECR']), 'Rank', true),
      name: headerIndex(headers, new Set(['PLAYER NAME', 'PLAYER', 'NAME']), 'Player Name', true),
      position: headerIndex(headers, new Set(['POS', 'POSITION']), 'Position', true),
      team: headerIndex(headers, new Set(['TEAM', 'NFL TEAM']), 'Team'),
      adp: headerIndex(headers, new Set(['ADP', 'AVG DRAFT POSITION', 'AVERAGE DRAFT POSITION']), 'ADP'),
      bye: headerIndex(headers, new Set(['BYE', 'BYE WEEK']), 'Bye Week'),
      playerKey: headerIndex(
        headers,
        new Set(['YAHOO PLAYER KEY', 'PLAYER KEY', 'PLAYERKEY']),
        'Yahoo Player Key',
      ),
      projectionSource: headerIndex(headers, new Set(['PROJECTION SOURCE']), 'Projection Source'),
      projectionAsOf: headerIndex(headers, new Set(['PROJECTION AS OF', 'PROJECTION DATE']), 'Projection As Of'),
      projectedPoints: headerIndex(headers, new Set(['PROJECTED POINTS']), 'Projected Points'),
      projectedOpportunities: headerIndex(headers, new Set(['PROJECTED OPPORTUNITIES']), 'Projected Opportunities'),
      opportunityKind: headerIndex(headers, new Set(['OPPORTUNITY KIND']), 'Opportunity Kind'),
      experienceYears: headerIndex(headers, new Set(['EXPERIENCE YEARS']), 'Experience Years'),
    };
    const draftSheetsHeaders = ['RK', 'PLAYER NAME', 'TEAM', 'POS', 'BYE WEEK']
      .every((header) => headers.includes(header));
    const evidenceIndexes = [
      indexes.projectionSource,
      indexes.projectionAsOf,
      indexes.projectedPoints,
      indexes.projectedOpportunities,
      indexes.opportunityKind,
      indexes.experienceYears,
    ];
    const values = rows.slice(1).map((columns, rowIndex) => {
      const evidenceValues = evidenceIndexes.map((index) => (
        index >= 0 ? String(columns[index] || '').trim() : ''
      ));
      const hasEvidence = evidenceValues.some(Boolean);
      if (hasEvidence && evidenceValues.some((value) => !value)) {
        throw new Error(`Breakout evidence in row ${rowIndex + 2} must be complete.`);
      }
      return {
        rank: columns[indexes.rank],
        name: columns[indexes.name],
        position: columns[indexes.position],
        team: indexes.team >= 0 ? columns[indexes.team] : undefined,
        average_draft_position: indexes.adp >= 0 ? columns[indexes.adp] : undefined,
        bye_week: indexes.bye >= 0 ? columns[indexes.bye] : undefined,
        player_key: indexes.playerKey >= 0 ? columns[indexes.playerKey] : undefined,
        breakout_evidence: hasEvidence ? {
          source: evidenceValues[0],
          as_of: evidenceValues[1],
          projected_points: evidenceValues[2],
          projected_opportunities: evidenceValues[3],
          opportunity_kind: evidenceValues[4],
          experience_years: evidenceValues[5],
        } : undefined,
        _rowNumber: rowIndex + 2,
      };
    });
    const validated = validateAndSortRankings(values, {
      allowDraftSheetsTruncation: draftSheetsHeaders,
    });
    return {
      format: draftSheetsHeaders ? 'draftsheets-2026' : 'csv',
      rankings: validated.rankings,
      truncatedCount: validated.truncatedCount,
    };
  }

  function parseJsonProfile(text) {
    let value;
    try {
      value = JSON.parse(text);
    } catch (_error) {
      throw new Error('The JSON profile is not valid JSON.');
    }
    if (!value || typeof value !== 'object' || Array.isArray(value) || value.schemaVersion !== 1) {
      throw new Error('JSON profile must be a schemaVersion 1 object.');
    }
    const validated = validateAndSortRankings(value.rankings);
    const result = {
      format: 'json',
      rankings: validated.rankings,
      truncatedCount: validated.truncatedCount,
    };
    const asOf = optionalIsoDate(value.asOf);
    if (asOf) result.asOf = asOf;
    return result;
  }

  function parseDraftProfileFile(text, filename) {
    const name = typeof filename === 'string' ? filename.toLowerCase() : '';
    if (name.endsWith('.json')) return parseJsonProfile(text);
    if (name.endsWith('.csv')) return parseCsvProfile(text);
    throw new Error('Choose a DraftSheets .xlsx, CSV, or schemaVersion 1 JSON profile.');
  }

  function parseRosterPositions(values) {
    if (!values || typeof values !== 'object' || Array.isArray(values)) {
      throw new Error('Roster positions are required.');
    }
    const positions = [];
    POSITION_ORDER.forEach((position) => {
      const raw = values[position];
      if (raw === undefined || raw === null || raw === '') return;
      const count = safeInteger(raw, `${position} roster count`, 0, 30);
      if (count > 0) positions.push({ position, count });
    });
    if (positions.length === 0) throw new Error('At least one roster position is required.');
    return positions;
  }

  function sanitizeLeagueSettings(settings) {
    if (!settings || typeof settings !== 'object' || Array.isArray(settings)) {
      throw new Error('League settings are required.');
    }
    const values = {};
    if (!Array.isArray(settings.rosterPositions)) {
      throw new Error('Roster positions are required.');
    }
    settings.rosterPositions.forEach((entry) => {
      if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return;
      const position = String(entry.position || '').trim().toUpperCase();
      if (POSITION_ORDER.includes(position)) values[position] = entry.count;
    });
    return {
      teams: safeInteger(settings.teams, 'Team count', 2, 20),
      rosterPositions: parseRosterPositions(values),
    };
  }

  function buildDraftProfileRequest(profile, now = new Date()) {
    if (!profile || typeof profile !== 'object' || Array.isArray(profile)) {
      throw new Error('A parsed local draft profile is required.');
    }
    const leagueId = safeLeagueId(profile.leagueId);
    const format = FORMATS.has(profile.format) ? profile.format : null;
    if (!format) throw new Error('Profile format is unsupported.');
    const validated = validateAndSortRankings(profile.rankings, {
      allowDraftSheetsTruncation: format === 'draftsheets-2026',
    });
    const importedAt = now instanceof Date && Number.isFinite(now.getTime())
      ? now.toISOString()
      : null;
    if (!importedAt) throw new Error('Import timestamp is invalid.');
    const result = {
      schemaVersion: 1,
      leagueId,
      importedAt,
      format,
      rankings: validated.rankings,
      leagueSettings: sanitizeLeagueSettings(profile.leagueSettings),
    };
    const asOf = optionalIsoDate(profile.asOf);
    if (asOf) result.asOf = asOf;
    return result;
  }

  function shortServerError(value) {
    const text = typeof value === 'string' ? value.trim() : '';
    return text ? text.slice(0, 240) : '';
  }

  function isProfileReuseRecommendationError(value) {
    const message = typeof value === 'string' ? value.toLowerCase() : '';
    return message.includes('yahoo league identity could not be resolved') ||
      (
        message.includes('yahoo league discovery is unavailable') &&
        message.includes('bind a saved local profile')
      );
  }

  async function responseObject(response, operation) {
    let result;
    try {
      result = await response.json();
    } catch (_error) {
      result = null;
    }
    if (!response.ok) {
      const detail = shortServerError(result?.message || result?.error);
      throw new Error(`${operation} returned HTTP ${response.status || 'error'}${detail ? `: ${detail}` : ''}`);
    }
    if (!result || typeof result !== 'object' || Array.isArray(result)) {
      throw new Error(`${operation} returned an invalid JSON response.`);
    }
    return result;
  }

  async function parseResponse(response, leagueId, operation) {
    const result = await responseObject(response, operation);
    if (String(result.leagueId || '') !== leagueId) {
      throw new Error(`${operation} response did not match the selected Yahoo league.`);
    }
    if (result.status !== 'success') {
      throw new Error(`${operation} did not confirm success.`);
    }
    return result;
  }

  function safeProfileSummary(value, index) {
    try {
      if (!value || typeof value !== 'object' || Array.isArray(value)) {
        throw new Error('summary must be an object');
      }
      const importedAt = optionalIsoDate(value.importedAt, 'Import timestamp');
      if (!importedAt) throw new Error('import timestamp is required');
      const format = FORMATS.has(value.format) ? value.format : null;
      if (!format) throw new Error('format is unsupported');
      const result = {
        sport: safeSport(value.sport),
        leagueId: safeLeagueId(value.leagueId),
        importedAt,
        format,
        rankingCount: safeInteger(
          value.rankingCount,
          'Ranking count',
          1,
          MAX_PROFILE_RANKINGS,
        ),
      };
      const asOf = optionalIsoDate(value.asOf, 'Source date');
      if (asOf) result.asOf = asOf.slice(0, 10);
      return result;
    } catch (_error) {
      throw new Error(`Saved profile summary ${index + 1} is invalid.`);
    }
  }

  function profileFormatLabel(format) {
    if (format === 'draftsheets-2026') return 'DraftSheets';
    if (format === 'json') return 'JSON';
    return 'CSV';
  }

  function profileSportLabel(value) {
    const sport = safeSport(value);
    if (sport === 'f1') return 'Yahoo Football';
    if (sport === 'nfl') return 'NFL';
    return 'Other Yahoo fantasy sport';
  }

  function profileChoiceLabel(value) {
    const profile = safeProfileSummary(value, 0);
    const usesSourceDate = Boolean(profile.asOf);
    const date = new Date(profile.asOf || profile.importedAt);
    const dateLabel = date.toLocaleDateString('en-US', {
      month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC',
    });
    return `${profileSportLabel(profile.sport)} · League ${profile.leagueId} · ` +
      `${profileFormatLabel(profile.format)} · ${usesSourceDate ? 'source' : 'imported'} ` +
      `${dateLabel} · ${profile.rankingCount} rankings`;
  }

  function profileRequestTimeoutMs(value) {
    let number;
    if (typeof value === 'number') number = value;
    else if (typeof value === 'string' && /^\d+$/.test(value.trim())) number = Number(value);
    else number = DEFAULT_PROFILE_REQUEST_TIMEOUT_MS;
    if (!Number.isFinite(number)) number = DEFAULT_PROFILE_REQUEST_TIMEOUT_MS;
    return Math.max(
      MIN_PROFILE_REQUEST_TIMEOUT_MS,
      Math.min(MAX_PROFILE_REQUEST_TIMEOUT_MS, Math.trunc(number)),
    );
  }

  async function withProfileRequestTimeout(options, operation, task) {
    const AbortControllerImpl = options.AbortControllerImpl || globalScope.AbortController;
    const controller = AbortControllerImpl ? new AbortControllerImpl() : null;
    const setTimeoutImpl = options.setTimeoutImpl || globalScope.setTimeout?.bind(globalScope);
    const clearTimeoutImpl = options.clearTimeoutImpl || globalScope.clearTimeout?.bind(globalScope);
    const timeout = controller && setTimeoutImpl
      ? setTimeoutImpl(() => controller.abort(), profileRequestTimeoutMs(options.timeoutMs))
      : undefined;
    try {
      return await task(controller?.signal);
    } catch (error) {
      if (error?.name === 'AbortError') {
        throw new Error(`${operation} timed out. Confirm the loopback server is running, then retry.`);
      }
      throw error;
    } finally {
      if (timeout !== undefined) clearTimeoutImpl?.(timeout);
    }
  }

  function safeProfileDefaults(values) {
    if (values === undefined) return [];
    if (!Array.isArray(values)) throw new Error('Saved profile defaults are invalid.');
    const sports = new Set();
    return values.map((value) => {
      if (!value || typeof value !== 'object' || Array.isArray(value)) {
        throw new Error('Saved profile default is invalid.');
      }
      const sport = safeSport(value.sport);
      const sourceLeagueId = safeLeagueId(value.sourceLeagueId);
      if (sports.has(sport)) throw new Error('Saved profile defaults contain a duplicate sport.');
      sports.add(sport);
      return { sport, sourceLeagueId };
    });
  }

  async function listDraftProfileCatalog(options = {}) {
    const endpoint = options.endpoint || PROFILES_ENDPOINT;
    if (endpoint !== PROFILES_ENDPOINT) throw new Error('Saved profiles endpoint must be same-origin.');
    const fetchImpl = options.fetchImpl || globalScope.fetch?.bind(globalScope);
    if (!fetchImpl) throw new Error('Fetch is unavailable.');
    return withProfileRequestTimeout(options, 'Saved profile list', async (signal) => {
      const response = await fetchImpl(endpoint, {
        method: 'GET',
        headers: { 'X-Fantasy-Draft-UI': '1' },
        cache: 'no-store',
        credentials: 'omit',
        signal,
      });
      const result = await responseObject(response, 'Saved profile list');
      if (result.status !== 'success' || !Array.isArray(result.profiles)) {
        throw new Error('Saved profile list did not confirm success.');
      }
      const profiles = result.profiles.map(safeProfileSummary);
      return {
        profiles,
        defaults: safeProfileDefaults(result.defaults),
      };
    });
  }

  async function listDraftProfiles(options = {}) {
    return (await listDraftProfileCatalog(options)).profiles;
  }

  async function setDefaultDraftProfile(sportValue, sourceLeagueIdValue, options = {}) {
    const endpoint = options.endpoint || PROFILE_DEFAULT_ENDPOINT;
    if (endpoint !== PROFILE_DEFAULT_ENDPOINT) {
      throw new Error('Draft profile default endpoint must be same-origin.');
    }
    const sport = safeSport(sportValue);
    const sourceLeagueId = sourceLeagueIdValue === null
      ? null
      : safeLeagueId(sourceLeagueIdValue);
    const fetchImpl = options.fetchImpl || globalScope.fetch?.bind(globalScope);
    if (!fetchImpl) throw new Error('Fetch is unavailable.');
    return withProfileRequestTimeout(options, 'Draft profile default', async (signal) => {
      const response = await fetchImpl(endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Fantasy-Draft-UI': '1',
        },
        body: JSON.stringify({ schemaVersion: 1, sport, sourceLeagueId }),
        cache: 'no-store',
        credentials: 'omit',
        signal,
      });
      const result = await responseObject(response, 'Draft profile default');
      if (
        result.status !== 'success' ||
        result.sport !== sport ||
        result.sourceLeagueId !== sourceLeagueId
      ) {
        throw new Error('Draft profile default response did not match the requested sport or source profile.');
      }
      return { status: 'success', sport, sourceLeagueId };
    });
  }

  async function bindDraftProfile(sourceLeagueIdValue, leagueIdValue, options = {}) {
    const endpoint = options.endpoint || PROFILE_BIND_ENDPOINT;
    if (endpoint !== PROFILE_BIND_ENDPOINT) throw new Error('Draft profile bind endpoint must be same-origin.');
    const sourceLeagueId = safeLeagueId(sourceLeagueIdValue);
    const leagueId = safeLeagueId(leagueIdValue);
    if (sourceLeagueId === leagueId) {
      throw new Error('Choose a saved profile from a different draft.');
    }
    const fetchImpl = options.fetchImpl || globalScope.fetch?.bind(globalScope);
    if (!fetchImpl) throw new Error('Fetch is unavailable.');
    return withProfileRequestTimeout(options, 'Draft profile reuse', async (signal) => {
      const response = await fetchImpl(endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Fantasy-Draft-UI': '1',
        },
        body: JSON.stringify({ schemaVersion: 1, sourceLeagueId, leagueId }),
        cache: 'no-store',
        credentials: 'omit',
        signal,
      });
      const result = await parseResponse(response, leagueId, 'Draft profile reuse');
      if (String(result.sourceLeagueId || '') !== sourceLeagueId) {
        throw new Error('Draft profile reuse response did not match the chosen source profile.');
      }
      return result;
    });
  }

  async function saveDraftProfile(profile, options = {}) {
    const endpoint = options.endpoint || PROFILE_ENDPOINT;
    if (endpoint !== PROFILE_ENDPOINT) throw new Error('Draft profile endpoint must be same-origin.');
    const request = buildDraftProfileRequest(profile, options.now || new Date());
    const fetchImpl = options.fetchImpl || globalScope.fetch?.bind(globalScope);
    if (!fetchImpl) throw new Error('Fetch is unavailable.');
    const response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Fantasy-Draft-UI': '1',
      },
      body: JSON.stringify(request),
      cache: 'no-store',
      credentials: 'omit',
    });
    return parseResponse(response, request.leagueId, 'Draft profile import');
  }

  async function saveDraftProfileXlsx(file, leagueIdValue, options = {}) {
    const endpoint = options.endpoint || XLSX_ENDPOINT;
    if (endpoint !== XLSX_ENDPOINT) throw new Error('XLSX profile endpoint must be same-origin.');
    const leagueId = safeLeagueId(leagueIdValue);
    const declaredSize = Number(file?.size);
    if (!file || typeof file.arrayBuffer !== 'function' || !Number.isFinite(declaredSize) || declaredSize <= 0) {
      throw new Error('Choose a valid XLSX workbook.');
    }
    if (declaredSize > MAX_XLSX_BYTES) throw new Error('The XLSX workbook must be 2 MB or smaller.');
    const bytes = new Uint8Array(await file.arrayBuffer());
    if (bytes.byteLength === 0 || bytes.byteLength > MAX_XLSX_BYTES) {
      throw new Error('The XLSX workbook must be between 1 byte and 2 MB.');
    }
    if (bytes.length < 4 || bytes[0] !== 0x50 || bytes[1] !== 0x4b || bytes[2] !== 0x03 || bytes[3] !== 0x04) {
      throw new Error('Choose a valid XLSX workbook.');
    }
    const settings = sanitizeLeagueSettings(options.leagueSettings);
    const rosterHeader = settings.rosterPositions
      .map((entry) => `${entry.position}=${entry.count}`)
      .join(',');
    if (rosterHeader.length > 160) throw new Error('Roster settings are too large.');
    const fetchImpl = options.fetchImpl || globalScope.fetch?.bind(globalScope);
    if (!fetchImpl) throw new Error('Fetch is unavailable.');
    const response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': XLSX_CONTENT_TYPE,
        'X-Fantasy-Draft-UI': '1',
        'X-Fantasy-League-ID': leagueId,
        'X-Fantasy-Team-Count': String(settings.teams),
        'X-Fantasy-Roster-Positions': rosterHeader,
      },
      body: bytes,
      cache: 'no-store',
      credentials: 'omit',
    });
    return parseResponse(response, leagueId, 'DraftSheets profile import');
  }

  function describeProfileFreshness(value, now = new Date()) {
    const asOf = typeof value === 'string' ? new Date(value) : null;
    if (!asOf || !Number.isFinite(asOf.getTime()) || !(now instanceof Date) || !Number.isFinite(now.getTime())) {
      return { kind: 'unknown', label: 'Source date unknown' };
    }
    const ageDays = (now.getTime() - asOf.getTime()) / 86_400_000;
    const dateLabel = asOf.toLocaleDateString('en-US', {
      month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC',
    });
    if (ageDays < -1) return { kind: 'unknown', label: `Future source date · ${dateLabel}` };
    if (ageDays <= 7) return { kind: 'fresh', label: `Current · source dated ${dateLabel}` };
    if (ageDays <= 30) return { kind: 'aging', label: `Aging · source dated ${dateLabel}` };
    return { kind: 'stale', label: `Stale · source dated ${dateLabel}` };
  }

  const api = {
    MAX_PROFILE_RANKINGS,
    MAX_XLSX_BYTES,
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
  };
  globalScope.YahooDraftProfileClient = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);

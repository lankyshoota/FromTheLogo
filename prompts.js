/* FromTheLogo prompt generator.
 *
 * Builds every prompt the data supports, keeps the ones with enough valid answers
 * (including real deep cuts), sorts them into easy / medium / hard, and picks a
 * 7-prompt pack per day. Works in the browser (window.FTLPrompts) and in Node
 * (require('./prompts.js')) so it can be tested against players.json.
 *
 * Prompt ids describe the prompt ("pair:LAL:MIA", "ppg:SAC:20", "col:Duke"), so a pack
 * stored in Supabase can always be rebuilt from its ids, even after players.json changes.
 *
 * Repeats: each prompt belongs to one day of a 28-day cycle (a hash of its id), so it
 * can't come back within 28 days, and its slot never moves when players.json changes.
 */
(function (root) {
  'use strict';

  const TEAM = {
    ATL: 'Hawks', BOS: 'Celtics', BKN: 'Nets', CHA: 'Hornets/Bobcats', CHI: 'Bulls', CLE: 'Cavaliers',
    DAL: 'Mavericks', DEN: 'Nuggets', DET: 'Pistons', GSW: 'Warriors', HOU: 'Rockets', IND: 'Pacers',
    LAC: 'Clippers', LAL: 'Lakers', MEM: 'Grizzlies', MIA: 'Heat', MIL: 'Bucks', MIN: 'Timberwolves',
    NOP: 'Pelicans', NYK: 'Knicks', OKC: 'Thunder/Sonics', ORL: 'Magic', PHI: '76ers', PHX: 'Suns',
    POR: 'Trail Blazers', SAC: 'Kings', SAS: 'Spurs', TOR: 'Raptors', UTA: 'Jazz', WAS: 'Wizards/Bullets',
  };
  const AWARDS = {   // key in players.json "aw" -> the winner, as a noun ("All-NBA pick who played for the Hawks")
    AS: 'All-Star', MVP: 'MVP', FMVP: 'Finals MVP', DPOY: 'Defensive Player of the Year',
    ROY: 'Rookie of the Year', '6MOY': 'Sixth Man of the Year', ALLNBA: 'All-NBA pick',
    ALLDEF: 'All-Defensive pick', ALLROOK: 'All-Rookie pick', POM: 'Player of the Month',
  };
  const AWARD_TEXT = {
    AS: 'Made an All-Star team', MVP: 'Won MVP', FMVP: 'Won Finals MVP', DPOY: 'Won Defensive Player of the Year',
    ROY: 'Won Rookie of the Year', '6MOY': 'Won Sixth Man of the Year', ALLNBA: 'Made an All-NBA team',
    ALLDEF: 'Made an All-Defensive team', ALLROOK: 'Made an All-Rookie team', POM: 'Won Player of the Month',
  };

  // A prompt is used only if it has at least MIN_ANSWERS valid players, MIN_DEEP deep cuts
  // and MIN_KNOWN names most fans would know (popularity 6+), so it's gettable.
  // A deep cut is an answer the game's simulated pick rates (popularity ^ 3.4, the same
  // weights index.html uses before real picks exist) would give under DEEP_SHARE of picks:
  // Gold /10 or 1 of 1 territory. Relative to the other answers, so a 1970s 20-PPG King is
  // a deep cut next to Webber and Fox even though he was a good player.
  const MIN_ANSWERS = 10, MIN_DEEP = 3, DEEP_SHARE = 0.02, MIN_KNOWN = 2, SIM_POWER = 3.4;
  const LEVELS = { easy: 20, medium: 8 };           // known answers: 20+ easy, 8-19 medium, 2-7 hard
  const PACK = ['easy', 'easy', 'medium', 'medium', 'medium', 'hard', 'hard'];
  const CYCLE = 28;                                   // days before a prompt can come back
  const AWARDS_READY = 0.99;                          // share of players whose awards are checked
  const DAY_EPOCH = Date.UTC(2026, 0, 1);

  // Champions by season start year (1970 = 1970-71), as current franchises. Later seasons
  // are worked out from the data (see prepare), so this never needs updating.
  const CHAMPIONS = {1970:'MIL',1971:'LAL',1972:'NYK',1973:'BOS',1974:'GSW',1975:'BOS',1976:'POR',1977:'WAS',1978:'OKC',1979:'LAL',1980:'BOS',1981:'LAL',1982:'PHI',1983:'BOS',1984:'LAL',1985:'BOS',1986:'LAL',1987:'LAL',1988:'DET',1989:'DET',1990:'CHI',1991:'CHI',1992:'CHI',1993:'HOU',1994:'HOU',1995:'CHI',1996:'CHI',1997:'CHI',1998:'SAS',1999:'LAL',2000:'LAL',2001:'LAL',2002:'SAS',2003:'DET',2004:'SAS',2005:'MIA',2006:'SAS',2007:'BOS',2008:'LAL',2009:'LAL',2010:'DAL',2011:'MIA',2012:'MIA',2013:'SAS',2014:'GSW',2015:'CLE',2016:'GSW',2017:'GSW',2018:'TOR',2019:'LAL',2020:'MIL',2021:'GSW',2022:'DEN',2023:'BOS',2024:'OKC'};

  const fmt = n => n.toLocaleString('en-US');
  const ft = inches => `${Math.floor(inches / 12)}'${inches % 12}"`;

  function h32(str) {   // small stable string hash
    let h = 2166136261;
    for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
    h ^= h >>> 13; h = Math.imul(h, 0x5bd1e995); h ^= h >>> 15;
    return h >>> 0;
  }

  /* ------------------------------------------------------------ player helpers */

  function prepare(players) {
    // champion per season: the known list, then for newer seasons the playoff team whose
    // players all have rings (ties: the team that played the most playoff games)
    const roster = new Map();   // "year|team" -> [players]
    for (const p of players) for (const s of (p.ps || [])) {
      const k = s[0] + '|' + s[1];
      if (!roster.has(k)) roster.set(k, []);
      roster.get(k).push(p);
    }
    const best = new Map();     // year -> [share, playoff games, team]
    for (const [k, ps] of roster) {
      const [y, t] = k.split('|');
      if (CHAMPIONS[y]) continue;
      const share = ps.filter(p => (p.aw && p.aw.CH) > 0).length / ps.length;
      const games = Math.max(...ps.map(p => p.ps.filter(s => s[0] === +y && s[1] === t).reduce((a, s) => a + s[2], 0)));
      const cur = best.get(y);
      if (ps.length >= 5 && share >= 0.9 && (!cur || share > cur[0] || (share === cur[0] && games > cur[1])))
        best.set(y, [share, games, t]);
    }
    const champs = Object.assign({}, CHAMPIONS);
    for (const [y, v] of best) champs[y] = v[2];

    for (const p of players) {
      p.s = p.s || []; p.ps = p.ps || []; p.aw = p.aw || {}; p.t = p.t || [];   // tolerate thin data (players.sample.json)
      const x = p._x = {};
      x.teams = new Set(p.t);
      x.seasons = new Set(p.s.map(s => s[0])).size;
      x.poGp = (p.ps || []).reduce((a, s) => a + s[2], 0);
      x.pts = p.s.reduce((a, s) => a + s[3], 0);
      x.titleTeams = new Set((p.ps || []).filter(s => champs[s[0]] === s[1]).map(s => s[1]));
      x.rings = (p.aw && p.aw.CH) || 0;
      x.undrafted = !p.dr;
      x.round2 = !!p.dr && p.dr[1] >= 2;
      x.intl = !!p.c && p.c.toUpperCase() !== 'USA';
      const tg = {};     // games per team
      for (const s of p.s) tg[s[1]] = (tg[s[1]] || 0) + s[2];
      x.teamGames = tg;
      x.decades = new Set(p.s.map(s => s[1] + '|' + Math.floor(s[0] / 10) * 10));
      // best single-team season averages (20+ games)
      x.best = {};
      for (const s of p.s) if (s[2] >= 20) for (const [i, k] of [[3, 'ppg'], [4, 'rpg'], [5, 'apg']]) {
        const v = s[i] / s[2], key = s[1] + '|' + k;
        if (!(x.best[key] >= v)) x.best[key] = v;
        if (!(x.best[k] >= v)) x.best[k] = v;
      }
    }
    // college vs. high school / pro club: a real college has US players
    const col = {};
    for (const p of players) if (p.col) (col[p.col] = col[p.col] || []).push(p);
    const colleges = new Set(Object.entries(col)
      .filter(([name, ps]) => !/\b(HS|High School|Academy|Prep)\b/i.test(name)
        && ps.filter(p => p.c === 'USA').length / ps.length >= 0.25)
      .map(([name]) => name));
    const byTeam = {}, byCol = {};
    for (const p of players) {
      for (const t of p.t) (byTeam[t] = byTeam[t] || []).push(p);
      if (p.col) (byCol[p.col] = byCol[p.col] || []).push(p);
    }
    const awardsChecked = players.filter(p => p.ad).length / Math.max(1, players.length);
    return { champs, colleges, byTeam, byCol, awardsReady: awardsChecked >= AWARDS_READY, awardsChecked };
  }

  /* ------------------------------------------------------------ prompt kinds
   * Each kind: family (for variety in a pack), teams it mentions, text, test(p).
   * make(args) returns null for ids that don't make sense. */

  const KINDS = {
    pair:   { family: 'pair',   make: ([a, b]) => TEAM[a] && TEAM[b] && a < b && {
                teams: [a, b], text: `Played for the ${TEAM[a]} and ${TEAM[b]}`,
                test: p => p._x.teams.has(a) && p._x.teams.has(b) } },
    ppg:    { family: 'tstat',  make: ([t, n]) => TEAM[t] && {
                teams: [t], text: `${n}+ PPG season with the ${TEAM[t]}`, test: p => p._x.best[t + '|ppg'] >= +n } },
    rpg:    { family: 'tstat',  make: ([t, n]) => TEAM[t] && {
                teams: [t], text: `${n}+ RPG season with the ${TEAM[t]}`, test: p => p._x.best[t + '|rpg'] >= +n } },
    apg:    { family: 'tstat',  make: ([t, n]) => TEAM[t] && {
                teams: [t], text: `${n}+ APG season with the ${TEAM[t]}`, test: p => p._x.best[t + '|apg'] >= +n } },
    tgp:    { family: 'tstat',  make: ([t, n]) => TEAM[t] && {
                teams: [t], text: `Played ${fmt(+n)}+ games for the ${TEAM[t]}`, test: p => (p._x.teamGames[t] || 0) >= +n } },
    dec:    { family: 'dec',    make: ([t, d]) => TEAM[t] && /^\d{4}$/.test(d) && {
                teams: [t], text: `Played for the ${TEAM[t]} in the ${d}s`, test: p => p._x.decades.has(t + '|' + d) } },
    tring:  { family: 'ring',   make: ([t]) => TEAM[t] && {
                teams: [t], text: `Won a title with the ${TEAM[t]}`, test: p => p._x.titleTeams.has(t) } },
    tund:   { family: 'tdraft', make: ([t]) => TEAM[t] && {
                teams: [t], text: `Went undrafted, played for the ${TEAM[t]}`, test: p => p._x.undrafted && p._x.teams.has(t) } },
    tr2:    { family: 'tdraft', make: ([t]) => TEAM[t] && {
                teams: [t], text: `Second-round pick who played for the ${TEAM[t]}`, test: p => p._x.round2 && p._x.teams.has(t) } },
    tintl:  { family: 'tintl',  make: ([t]) => TEAM[t] && {
                teams: [t], text: `International player for the ${TEAM[t]}`, test: p => p._x.intl && p._x.teams.has(t) } },
    tcol:   { family: 'col',    make: ([t, ...c]) => TEAM[t] && c.length && {
                teams: [t], text: `${c.join(':')} alum who played for the ${TEAM[t]}`,
                test: p => p.col === c.join(':') && p._x.teams.has(t) } },
    pick:   { family: 'draft',  make: ([n]) => +n >= 1 && +n <= 60 && {
                text: `No. ${n} overall pick`, test: p => !!p.dr && p.dr[1] === 1 && p.dr[2] === +n } },
    class:  { family: 'draft',  make: ([y]) => /^\d{4}$/.test(y) && {
                text: `Drafted in ${y}`, test: p => !!p.dr && p.dr[0] === +y } },
    r2long: { family: 'draft',  make: ([n]) => ({
                text: `Second-round pick with ${n}+ seasons`, test: p => p._x.round2 && p._x.seasons >= +n }) },
    undlong:{ family: 'draft',  make: ([n]) => ({
                text: `Undrafted, played ${n}+ seasons`, test: p => p._x.undrafted && p._x.seasons >= +n }) },
    col:    { family: 'col',    make: c => c.length && {
                text: `College: ${c.join(':')}`, test: p => p.col === c.join(':') } },
    ctry:   { family: 'ctry',   make: c => c.length && {
                text: `From ${c.join(':')}`, test: p => p.c === c.join(':') } },
    rings:  { family: 'ring',   make: ([n]) => ({
                text: `Won ${n}+ titles`, test: p => p._x.rings >= +n }) },
    ringund:{ family: 'ring',   make: () => ({
                text: 'Won a title, went undrafted', test: p => p._x.rings > 0 && p._x.undrafted }) },
    ringr2: { family: 'ring',   make: () => ({
                text: 'Second-round pick who won a title', test: p => p._x.rings > 0 && p._x.round2 }) },
    ringintl:{ family: 'ring',  make: () => ({
                text: 'International player who won a title', test: p => p._x.rings > 0 && p._x.intl }) },
    seasons:{ family: 'career', make: ([n]) => ({
                text: `Played ${n}+ seasons`, test: p => p._x.seasons >= +n }) },
    teams:  { family: 'career', make: ([n]) => ({
                text: `Played for ${n}+ teams`, test: p => p.t.length >= +n }) },
    oneteam:{ family: 'career', make: ([n]) => ({
                text: `${n}+ seasons, all with one team`, test: p => p.t.length === 1 && p._x.seasons >= +n }) },
    gp:     { family: 'career', make: ([n]) => ({
                text: `Played ${fmt(+n)}+ games`, test: p => p.gp >= +n }) },
    po:     { family: 'playoff',make: ([n]) => ({
                text: `Played ${n}+ playoff games`, test: p => p._x.poGp >= +n }) },
    nopo:   { family: 'playoff',make: ([n]) => ({
                text: `${n}+ games, never played a playoff game`, test: p => p.gp >= +n && p._x.poGp === 0 }) },
    cppg:   { family: 'stat',   make: ([n]) => ({
                text: `Career ${n}+ PPG (300+ games)`, test: p => p.gp >= 300 && p.ppg >= +n }) },
    sppg:   { family: 'stat',   make: ([n]) => ({
                text: `${n}+ PPG season`, test: p => p._x.best.ppg >= +n }) },
    srpg:   { family: 'stat',   make: ([n]) => ({
                text: `${n}+ RPG season`, test: p => p._x.best.rpg >= +n }) },
    sapg:   { family: 'stat',   make: ([n]) => ({
                text: `${n}+ APG season`, test: p => p._x.best.apg >= +n }) },
    pts:    { family: 'stat',   make: ([n]) => ({
                text: `${fmt(+n)}+ career points`, test: p => p._x.pts >= +n }) },
    tall:   { family: 'size',   make: ([n]) => ({
                text: `${ft(+n)} or taller`, test: p => p.ht >= +n }) },
    short:  { family: 'size',   make: ([n]) => ({
                text: `${ft(+n)} or shorter`, test: p => !!p.ht && p.ht <= +n }) },
    ttall:  { family: 'size',   make: ([t, n]) => TEAM[t] && {
                teams: [t], text: `${ft(+n)}+ and played for the ${TEAM[t]}`, test: p => p.ht >= +n && p._x.teams.has(t) } },
    // awards: only offered once awards are checked for (nearly) every player
    aw:     { family: 'award', award: true, make: ([k]) => AWARDS[k] && {
                text: AWARD_TEXT[k], test: p => (p.aw[k] || 0) > 0 } },
    awt:    { family: 'award', award: true, make: ([k, t]) => AWARDS[k] && TEAM[t] && {
                teams: [t], text: `${AWARDS[k]} who played for the ${TEAM[t]}`, test: p => (p.aw[k] || 0) > 0 && p._x.teams.has(t) } },
    awund:  { family: 'award', award: true, make: ([k]) => AWARDS[k] && {
                text: `${AWARDS[k]}, went undrafted`, test: p => (p.aw[k] || 0) > 0 && p._x.undrafted } },
    awr2:   { family: 'award', award: true, make: ([k]) => AWARDS[k] && {
                text: `${AWARDS[k]}, second-round pick`, test: p => (p.aw[k] || 0) > 0 && p._x.round2 } },
    awintl: { family: 'award', award: true, make: ([k]) => AWARDS[k] && {
                text: `International ${AWARDS[k]}`, test: p => (p.aw[k] || 0) > 0 && p._x.intl } },
    noas:   { family: 'award', award: true, make: ([n]) => ({
                text: `${n}+ seasons, never an All-Star`, test: p => p._x.seasons >= +n && !p.aw.AS }) },
  };

  function parse(id) {
    const [kind, ...args] = String(id).split(':');
    const k = KINDS[kind];
    const d = k && k.make(args);
    return d ? { id, kind, family: k.family, award: !!k.award, teams: d.teams || [], text: d.text, test: d.test,
                 college: kind === 'col' ? args.join(':') : kind === 'tcol' ? args.slice(1).join(':') : null } : null;
  }

  /* every id worth trying for this data */
  function candidateIds(players, info) {
    const teams = Object.keys(TEAM), ids = [];
    for (let i = 0; i < teams.length; i++) for (let j = i + 1; j < teams.length; j++) ids.push(`pair:${teams[i]}:${teams[j]}`);
    const decades = [...new Set(players.flatMap(p => p.s.map(s => Math.floor(s[0] / 10) * 10)))];
    const countries = [...new Set(players.map(p => p.c).filter(c => c && c !== 'USA'))];
    const years = [...new Set(players.filter(p => p.dr).map(p => p.dr[0]))].filter(y => y >= 1966);
    for (const t of teams) {
      for (const n of [20, 25]) ids.push(`ppg:${t}:${n}`);
      for (const n of [10, 12]) ids.push(`rpg:${t}:${n}`);
      for (const n of [7, 9]) ids.push(`apg:${t}:${n}`);
      ids.push(`tgp:${t}:500`);
      for (const d of decades) ids.push(`dec:${t}:${d}`);
      ids.push(`tring:${t}`, `tund:${t}`, `tr2:${t}`, `tintl:${t}`, `ttall:${t}:84`);
      for (const c of info.colleges) ids.push(`tcol:${t}:${c}`);
      for (const k of Object.keys(AWARDS)) ids.push(`awt:${k}:${t}`);
    }
    for (let n = 1; n <= 10; n++) ids.push(`pick:${n}`);
    for (const y of years) ids.push(`class:${y}`);
    for (const n of [8, 10, 12]) ids.push(`r2long:${n}`, `undlong:${n}`);
    for (const c of info.colleges) ids.push(`col:${c}`);
    for (const c of countries) ids.push(`ctry:${c}`);
    for (const n of [2, 3, 4, 5]) ids.push(`rings:${n}`);
    ids.push('ringund', 'ringr2', 'ringintl');
    for (const n of [15, 17, 19]) ids.push(`seasons:${n}`);
    for (const n of [7, 9, 11]) ids.push(`teams:${n}`);
    for (const n of [10, 13, 15]) ids.push(`oneteam:${n}`);
    for (const n of [1000, 1200, 1400]) ids.push(`gp:${n}`);
    for (const n of [100, 150, 200]) ids.push(`po:${n}`);
    for (const n of [400, 600]) ids.push(`nopo:${n}`);
    for (const n of [20, 25]) ids.push(`cppg:${n}`);
    for (const n of [28, 30, 32]) ids.push(`sppg:${n}`);
    for (const n of [13, 15]) ids.push(`srpg:${n}`);
    for (const n of [10, 11]) ids.push(`sapg:${n}`);
    for (const n of [15000, 20000, 25000]) ids.push(`pts:${n}`);
    for (const n of [85, 86]) ids.push(`tall:${n}`);
    for (const n of [70, 72]) ids.push(`short:${n}`);
    for (const k of Object.keys(AWARDS)) ids.push(`aw:${k}`, `awund:${k}`, `awr2:${k}`, `awintl:${k}`);
    for (const n of [12, 15]) ids.push(`noas:${n}`);
    return ids;
  }

  function level(known) { return known >= LEVELS.easy ? 'easy' : known >= LEVELS.medium ? 'medium' : 'hard'; }

  /* ------------------------------------------------------------ the generator */

  function create(players, opts = {}) {
    if (opts.levels) Object.assign(LEVELS, opts.levels);   // for tuning tests
    const info = prepare(players);
    const cache = new Map();

    function get(id) {
      if (cache.has(id)) return cache.get(id);
      const pr = parse(id);
      if (pr) {
        // only look at players who could match: the prompt's team or college
        let pool = players;
        for (const t of pr.teams) if ((info.byTeam[t] || []).length < pool.length) pool = info.byTeam[t] || [];
        if (pr.college !== null) pool = (info.byCol[pr.college] || []).filter(p => pool === players || pool.includes(p));
        const ans = pool.filter(pr.test);
        pr.answers = ans.length;
        const total = ans.reduce((a, p) => a + Math.pow(p.p, SIM_POWER), 0);
        pr.deep = ans.filter(p => Math.pow(p.p, SIM_POWER) / total < DEEP_SHARE).length;
        pr.known = ans.filter(p => p.p >= 6).length;
        pr.level = level(pr.known);
        pr.ok = pr.answers >= MIN_ANSWERS && pr.deep >= MIN_DEEP && pr.known >= MIN_KNOWN
          && (!pr.award || info.awardsReady);
        pr.slot = h32(id) % CYCLE;
      }
      cache.set(id, pr);
      return pr;
    }

    const usable = candidateIds(players, info)
      .filter(id => info.awardsReady || !(KINDS[id.split(':')[0]] || {}).award)
      .map(get).filter(pr => pr && pr.ok);
    const buckets = { easy: [], medium: [], hard: [] };
    for (const pr of usable) buckets[pr.level].push(pr);

    /* pick 7 for a pack: for each difficulty, walk the candidate lists in order, skipping any
       that would repeat a family (2 team-pair prompts allowed) or mention a team already used */
    const NEXT = { easy: ['easy', 'medium', 'hard'], medium: ['medium', 'easy', 'hard'], hard: ['hard', 'medium', 'easy'] };
    function pick(lists) {   // lists: array of { easy:[], medium:[], hard:[] }, tried in order
      const chosen = [], families = {}, teams = new Set();
      const fits = pr => !chosen.includes(pr) && (families[pr.family] || 0) < (pr.family === 'pair' ? 2 : 1)
        && !pr.teams.some(t => teams.has(t));
      for (const lv of PACK) {
        let pr;
        for (const l of lists) {
          for (const want of NEXT[lv]) if ((pr = l[want].find(fits))) break;
          if (pr) break;
        }
        if (!pr) continue;
        chosen.push(pr);
        families[pr.family] = (families[pr.family] || 0) + 1;
        pr.teams.forEach(t => teams.add(t));
      }
      // last resort (only with tiny data sets): fill to 7 without the variety rules
      for (const l of lists) for (const lv of ['easy', 'medium', 'hard']) for (const pr of l[lv]) {
        if (chosen.length < PACK.length && !chosen.includes(pr)) chosen.push(pr);
      }
      // keep the easy-to-hard order even when a slot had to borrow across difficulties
      const rank = { easy: 0, medium: 1, hard: 2 };
      return chosen.sort((a, b) => rank[a.level] - rank[b.level]).map(pr => pr.id);
    }

    function dayIndex(dateKey) {
      const [y, m, d] = dateKey.split('-').map(Number);
      return Math.round((Date.UTC(y, m - 1, d) - DAY_EPOCH) / 864e5);
    }

    const ordered = (prs, key) => prs.slice().sort((a, b) => key(a) - key(b));
    const split = (prs, key) => ({ easy: ordered(prs.filter(p => p.level === 'easy'), key),
      medium: ordered(prs.filter(p => p.level === 'medium'), key), hard: ordered(prs.filter(p => p.level === 'hard'), key) });

    /* the daily pack: only prompts in today's slot of the cycle, so nothing repeats within
       CYCLE days. Only if today's slot can't fill 7 does it borrow from the slot half a cycle
       away, then anything (logged by the tests as "borrowed"). */
    function daily(dateKey) {
      const slot = ((dayIndex(dateKey) % CYCLE) + CYCLE) % CYCLE, far = (slot + CYCLE / 2) % CYCLE;
      const key = pr => h32(dateKey + '|' + pr.id);
      return pick([split(usable.filter(p => p.slot === slot), key),
                   split(usable.filter(p => p.slot === far), key),
                   split(usable, key)]);
    }

    /* unlimited mode: any usable prompt, random order */
    function random(rnd = Math.random) {
      const keyed = new Map(usable.map(pr => [pr, rnd()]));
      return pick([split(usable, pr => keyed.get(pr))]);
    }

    return { get, daily, random, buckets, usable, info, TEAM };
  }

  const api = { create, parse, TEAM, AWARDS, CYCLE, MIN_ANSWERS, MIN_DEEP, MIN_KNOWN };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.FTLPrompts = api;
})(typeof window !== 'undefined' ? window : globalThis);

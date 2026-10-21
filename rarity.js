/* FromTheLogo rarity model: how likely is a typical player of this game to pick each answer?
 *
 * 1. Recognition (0-1) per player: a weighted mix of Wikipedia page views (most of it),
 *    playoff games, seasons, awards (rings until fill_details.py adds the rest) and
 *    recency. Every feature is turned into a percentile first, so they're comparable.
 * 2. Simulated fans: thousands per prompt. Each fan has their own knowledge level and their
 *    own appetite for deep cuts. A fan recalls each valid answer with a probability that
 *    rises with recognition, then picks one of the players they recalled: usually the most
 *    recognizable, sometimes (deep-cut hunters) the least. Each answer's share of all picks
 *    is its starting pick %.
 * 3. Real picks take over: rate = (picks + K * simulated share) / (all picks + K), K = 100.
 * 4. Self-correcting: after a day settles, update() compares predicted with actual shares and
 *    nudges the weights (and how strongly fans go for big names). The game stores each day's
 *    model in Supabase so everyone uses the same one.
 *
 * Works in the browser (window.FTLRarity) and Node (require('./rarity.js')).
 */
(function (root) {
  'use strict';

  const FEATURES = ['views', 'playoffs', 'seasons', 'awards', 'recency'];
  const DEFAULT_MODEL = {
    v: 1,
    w: { views: 0.6, playoffs: 0.12, seasons: 0.08, awards: 0.12, recency: 0.08 },
    center: 0.9,    // an average fan has a 50% chance to recall a player at this recognition percentile
    steep: 20,      // how fast recall drops off below that
    spread: 0.1,    // how much fans differ (some know far more players than others)
    pull: 12,       // how strongly a fan names the most recognizable player they thought of
  };
  const K = 100;    // real picks needed before they outweigh the simulation (Bayesian prior strength)
  const FANS = 4000;
  const BOUNDS = { w: [0.01, 1], center: [0.5, 0.95], steep: [4, 30], spread: [0.02, 0.3], pull: [1, 20] };

  const clamp = (x, [lo, hi]) => Math.min(hi, Math.max(lo, x));

  function h32(str) {
    let h = 2166136261;
    for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
    h ^= h >>> 13; h = Math.imul(h, 0x5bd1e995); h ^= h >>> 15;
    return h >>> 0;
  }
  function mulberry(seed) {
    let a = seed >>> 0;
    return () => { a = (a + 0x6D2B79F5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  }

  /* raw feature values for one player (log scales: 10x the views or playoff games is a real step up) */
  function raw(p) {
    const aw = p.aw || {};
    const s = p.s || [];
    return {
      views: Math.log10((p.fame > 0 ? p.fame : 0) + 100),
      playoffs: Math.log1p((p.ps || []).reduce((a, x) => a + x[2], 0)),
      seasons: new Set(s.map(x => x[0])).size,
      awards: Math.log1p((aw.AS || 0) + 1.5 * (aw.ALLNBA || 0) + 4 * (aw.MVP || 0) + 3 * (aw.FMVP || 0)
        + 2 * (aw.DPOY || 0) + 1.5 * (aw.ROY || 0) + (aw['6MOY'] || 0) + 0.5 * (aw.ALLDEF || 0) + 0.5 * (aw.CH || 0)),
      recency: s.length ? s[s.length - 1][0] : 1950,
    };
  }

  function percentile(sorted, q) { return sorted[Math.min(sorted.length - 1, Math.max(0, Math.round(q * (sorted.length - 1))))]; }

  /* rarity model for one set of players:
       r0(p)  recognition (0-1) under the default weights, and p._rp0 its percentile: what the
              prompt generator uses, so packs don't depend on the day's model
       sim(answers, seed)  simulated pick shares under the current model
       setModel(m)  switch to a newer (self-corrected) model */
  function create(players, model = DEFAULT_MODEL) {
    const raws = players.map(raw);
    const has = { views: players.some(p => p.fame > 0), playoffs: true, seasons: true, awards: true, recency: true };
    // scale each feature to 0-1 between its 1st and 99.9th percentile, so a few outliers don't squash the rest
    for (const f of FEATURES) {
      const vals = raws.map(r => r[f]).sort((a, b) => a - b);
      const lo = percentile(vals, 0.01), hi = percentile(vals, 0.999);
      players.forEach((p, i) => { (p._f = p._f || {})[f] = hi > lo ? Math.min(1, Math.max(0, (raws[i][f] - lo) / (hi - lo))) : 0; });
    }
    const recog = m => {
      const used = FEATURES.filter(f => has[f]);
      const total = used.reduce((a, f) => a + m.w[f], 0);
      return players.map(p => used.reduce((a, f) => a + m.w[f] * p._f[f], 0) / total);
    };
    const pct = vals => {
      const order = vals.map((v, i) => [v, i]).sort((a, b) => a[0] - b[0]), out = new Array(vals.length);
      order.forEach(([, i], k) => { out[i] = order.length > 1 ? k / (order.length - 1) : 0.5; });
      return out;
    };
    const base = recog(DEFAULT_MODEL), basePct = pct(base);
    players.forEach((p, i) => { p._r0 = base[i]; p._rp0 = basePct[i]; });

    let cur, R, centerR;
    function setModel(m) {
      cur = valid(m) ? m : DEFAULT_MODEL;
      const r = recog(cur);
      R = new Map(players.map((p, i) => [p.id, r[i]]));
      // model.center is a percentile ("an average fan knows the top 20%"); turn it into a recognition value
      centerR = percentile(r.slice().sort((a, b) => a - b), cur.center);
    }
    setModel(model);

    /* simulate fans on one prompt. Returns Map(player id -> share of picks, 0-1). */
    function sim(answers, seed = 'x', fans = FANS, m = cur) {
      const rnd = mulberry(h32(String(seed)));
      const n = answers.length, counts = new Float64Array(n), Rs = answers.map(p => R.get(p.id) ?? 0);
      const recalled = new Int32Array(n);
      let pickers = 0;
      for (let f = 0; f < fans; f++) {
        // this fan: how much they know (normal), and their appetite for deep cuts (most have little)
        const shift = m.spread * Math.sqrt(-2 * Math.log(rnd() || 1e-12)) * Math.cos(2 * Math.PI * rnd());
        const deep = Math.pow(rnd(), 3);              // 0 = names the biggest star, 1 = hunts deep cuts
        const lambda = m.pull * (1 - 2 * deep);
        let k = 0, best = -Infinity;
        for (let i = 0; i < n; i++) {
          if (rnd() < 1 / (1 + Math.exp(-m.steep * (Rs[i] - centerR + shift)))) {
            recalled[k++] = i;
            if (lambda * Rs[i] > best) best = lambda * Rs[i];
          }
        }
        if (!k) continue;                               // couldn't think of anyone: a brick
        let sum = 0;
        for (let j = 0; j < k; j++) sum += Math.exp(lambda * Rs[recalled[j]] - best);
        let u = rnd() * sum;
        for (let j = 0; j < k; j++) {
          u -= Math.exp(lambda * Rs[recalled[j]] - best);
          if (u <= 0 || j === k - 1) { counts[recalled[j]]++; break; }
        }
        pickers++;
      }
      const out = new Map();
      answers.forEach((p, i) => out.set(p.id, pickers ? counts[i] / pickers : 1 / n));
      return out;
    }

    /* fast, deterministic approximation (for the prompt generator), on the default model */
    const quickCenter = percentile(base.slice().sort((a, b) => a - b), DEFAULT_MODEL.center);
    function quick(answers) {
      const c = DEFAULT_MODEL, cR = quickCenter;
      const w = answers.map(p => Math.exp(c.pull * 0.6 * p._r0) / (1 + Math.exp(-c.steep * (p._r0 - cR))));
      const sum = w.reduce((a, b) => a + b, 0) || 1;
      return new Map(answers.map((p, i) => [p.id, w[i] / sum]));
    }

    /* one self-correcting step from a settled day: [{ answers, counts: Map(id -> picks), seed }] */
    function update(day) {
      const m = cur, next = JSON.parse(JSON.stringify(m));
      const grad = Object.fromEntries(FEATURES.map(f => [f, 0]));
      let used = 0, concAct = 0, concPred = 0;
      for (const pr of day) {
        const N = [...pr.counts.values()].reduce((a, b) => a + b, 0);
        if (N < 30 || pr.answers.length < 2) continue;   // too few picks to learn from
        const pred = sim(pr.answers, pr.seed, 2000);
        const A = pr.answers.length;
        for (const p of pr.answers) {
          const act = ((pr.counts.get(p.id) || 0) + 0.5) / (N + 0.5 * A);
          const prd = (pred.get(p.id) * 2000 + 0.5) / (2000 + 0.5 * A);
          // answers picked more than predicted pull the weights toward what they're strong in.
          // Recognition is a weighted average, so raising w[f] helps a player by (feature - recognition).
          const r = R.get(p.id);
          for (const f of FEATURES) if (has[f]) grad[f] += (act - prd) * (p._f[f] - r);
          concAct += act * act; concPred += prd * prd;
        }
        used++;
      }
      if (!used) return next;
      for (const f of FEATURES) {
        next.w[f] = clamp(m.w[f] + Math.max(-0.05, Math.min(0.05, 0.5 * grad[f] / used)), BOUNDS.w);
        next.w[f] = Math.round(next.w[f] * 1e4) / 1e4;
      }
      // picks more bunched on the top names than predicted -> fans pull harder toward stars
      next.pull = Math.round(clamp(m.pull * Math.max(0.85, Math.min(1.15, Math.sqrt(concAct / concPred))), BOUNDS.pull) * 1e3) / 1e3;
      return next;
    }

    return { sim, quick, update, setModel, model: () => cur, recognition: p => R.get(p.id), has };
  }

  /* blended rate in % (simShare 0-1): real picks take over as they come in */
  const blend = (picks, total, simShare) => 100 * (picks + K * simShare) / (total + K);

  /* one self-correcting step from a settled day.
     day: [{ answers: [players], counts: Map(id -> picks), seed }], players already prepare()d */
  function update(model, day, players, has) {
    const next = JSON.parse(JSON.stringify(model));
    const grad = Object.fromEntries(FEATURES.map(f => [f, 0]));
    let used = 0, concAct = 0, concPred = 0;
    for (const pr of day) {
      const N = [...pr.counts.values()].reduce((a, b) => a + b, 0);
      if (N < 30 || pr.answers.length < 2) continue;   // too few picks to learn from
      const pred = simulate(pr.answers, { seed: pr.seed, model, fans: 2000 });
      const A = pr.answers.length;
      for (const p of pr.answers) {
        const act = ((pr.counts.get(p.id) || 0) + 0.5) / (N + 0.5 * A);
        const prd = (pred.get(p.id) * 2000 + 0.5) / (2000 + 0.5 * A);
        // softmax gradient: answers picked more than predicted pull weight toward their features
        for (const f of FEATURES) if (has[f]) grad[f] += (act - prd) * p._f[f];
        concAct += act * act; concPred += prd * prd;
      }
      used++;
    }
    if (!used) return next;
    for (const f of FEATURES) {
      const step = Math.max(-0.05, Math.min(0.05, 0.5 * grad[f] / used));
      next.w[f] = clamp(model.w[f] + step, BOUNDS.w);
    }
    // more concentrated on the top names than predicted -> fans pull harder toward stars
    const ratio = Math.max(0.85, Math.min(1.15, Math.pow(concAct / concPred, 0.5)));
    next.pull = clamp(model.pull * ratio, BOUNDS.pull);
    for (const f of FEATURES) next.w[f] = Math.round(next.w[f] * 1e4) / 1e4;
    next.pull = Math.round(next.pull * 1e3) / 1e3;
    return next;
  }

  /* is this a sane model? (also checked by the server) */
  function valid(m) {
    if (!m || typeof m !== 'object' || !m.w) return false;
    if (!FEATURES.every(f => typeof m.w[f] === 'number' && m.w[f] >= BOUNDS.w[0] && m.w[f] <= BOUNDS.w[1])) return false;
    return ['center', 'steep', 'spread', 'pull'].every(k => typeof m[k] === 'number' && m[k] >= BOUNDS[k][0] && m[k] <= BOUNDS[k][1]);
  }

  const api = { DEFAULT_MODEL, FEATURES, K, FANS, BOUNDS, create, blend, valid };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.FTLRarity = api;
})(typeof window !== 'undefined' ? window : globalThis);

/* Rarity sanity test. Runs on every push (GitHub Actions) and locally: node test_rarity.js
 *
 * On every usable prompt with BIG+ answers:
 *  1. Stars and well-known role players who are among the 3 most recognizable answers must
 *     be Copper or Silver (picked by 10%+): nobody should score big for an obvious name.
 *     (Not "every star on every prompt": on "Played 15+ seasons" LeBron, Duncan, Kobe, Dirk
 *     and Garnett can't all be 10%+, the shares add up to 100%.)
 *  2. They must never be Refractor or 1 of 1 (under 2%) on any big prompt.
 *  3. The 3 least-recognizable answers must reach Refractor or 1 of 1 (under 2%).
 * Then prints 3 example prompts with their 5 most obvious and 5 deepest answers.
 * Exits 1 if anything fails.
 */
const fs = require('fs');
const path = require('path');
const R = require('./rarity.js');
const F = require('./prompts.js');

const BIG = 25;
const WELL_KNOWN = ['LeBron James', 'Stephen Curry', 'Kevin Durant', 'Kobe Bryant', "Shaquille O'Neal", 'Tim Duncan',
  'Michael Jordan', 'Dwyane Wade', 'Dirk Nowitzki', 'Chris Paul', 'Kevin Garnett', 'Allen Iverson', 'Magic Johnson',
  'Larry Bird', 'Kareem Abdul-Jabbar', 'Charles Barkley', 'DeAndre Jordan', 'Robert Horry', 'J.R. Smith',
  'Dennis Rodman', 'Steve Kerr', 'Derek Fisher', 'Kyle Korver', 'Udonis Haslem', 'Nate Robinson', 'Metta World Peace',
  'Boban Marjanovic', 'Brian Scalabrine', 'Rajon Rondo', 'Tony Allen', 'Draymond Green', 'Jeremy Lin'];
const TIERS = [[25, 'Copper'], [10, 'Silver'], [5, 'Gold'], [2, 'Emerald'], [0.5, 'Refractor'], [0, '1 of 1']];
const tier = pct => TIERS.find(([min]) => pct >= min)[1];

const file = process.argv[2] || path.join(__dirname, 'players.json');
const players = JSON.parse(fs.readFileSync(file));
const RM = R.create(players);
const G = F.create(players, { rarity: RM });
const known = new Set(WELL_KNOWN);
const fails = [];
let checked = 0;

for (const pr of G.usable) {
  if (pr.answers < BIG) continue;
  const answers = players.filter(pr.test);
  const share = RM.sim(answers, pr.id);
  const pct = p => 100 * share.get(p.id);
  const byRecog = answers.slice().sort((a, b) => RM.recognition(b) - RM.recognition(a));
  const top = new Set(byRecog.slice(0, 3));
  checked++;
  for (const p of answers) {
    if (!known.has(p.n)) continue;
    if (top.has(p) && pct(p) < 10) fails.push(`${p.n} is ${tier(pct(p))} (${pct(p).toFixed(1)}%) on "${pr.text}" but is one of its 3 most obvious answers`);
    else if (pct(p) < 2) fails.push(`${p.n} is ${tier(pct(p))} (${pct(p).toFixed(2)}%) on "${pr.text}"`);
  }
  for (const p of byRecog.slice(-3)) {
    if (pct(p) >= 2) fails.push(`deep cut ${p.n} is only ${tier(pct(p))} (${pct(p).toFixed(1)}%) on "${pr.text}"`);
  }
}

console.log(`Wikipedia views: ${RM.has.views ? `yes (${players.filter(p => p.fame > 0).length} players)` : 'NOT YET (run fetch_fame.py)'}`);
console.log(`Checked ${checked} prompts with ${BIG}+ answers: ${fails.length ? fails.length + ' problems' : 'all good'}.`);
const counts = {};
for (const f of fails) { const k = f.startsWith('deep cut') ? 'deep cuts too common' : f.split(' is ')[0]; counts[k] = (counts[k] || 0) + 1; }
if (fails.length) {
  console.log('By player:', Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([k, n]) => `${k} ${n}`).join(', '));
  fails.slice(0, 25).forEach(f => console.log('  ' + f));
}

// examples: a big team pair, a team + something prompt, and a draft/college/country prompt
const pickEx = (kinds, min) => G.usable.filter(p => kinds.includes(p.kind) && p.answers >= min)
  .sort((a, b) => b.answers - a.answers)[0];
const examples = [process.env.EX1 || (pickEx(['tr2'], 100) || {}).id, process.env.EX2 || (pickEx(['pair'], 60) || {}).id,
  process.env.EX3 || (pickEx(['ctry', 'col', 'class'], 30) || {}).id].filter(Boolean);
for (const id of examples) {
  const pr = G.get(id);
  const answers = players.filter(pr.test);
  const share = RM.sim(answers, id);
  const rows = answers.map(p => [p, 100 * share.get(p.id)]).sort((a, b) => b[1] - a[1] || RM.recognition(b[0]) - RM.recognition(a[0]));
  const line = ([p, v]) => `    ${p.n.padEnd(24)} ${v < 0.1 ? v.toFixed(2) : v.toFixed(1)}%  ${tier(v)}`;
  console.log(`\n"${pr.text}" (${answers.length} answers)\n  Most obvious:`);
  rows.slice(0, 5).forEach(r => console.log(line(r)));
  console.log('  Deepest:');
  rows.slice(-5).forEach(r => console.log(line(r)));
}
// Without Wikipedia views the model can't tell a fan favorite from a journeyman, so only warn.
if (fails.length && !RM.has.views) console.log('\n(Not failing: players.json has no Wikipedia views yet. Run fetch_fame.py, then this test is strict.)');
process.exit(fails.length && RM.has.views ? 1 : 0);

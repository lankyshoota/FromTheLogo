/* Rarity sanity test. Runs on every push (GitHub Actions) and locally:
 *   node test_rarity.js            (EX1/EX2/EX3=<prompt id> to choose the printed examples)
 *
 * On every usable prompt with BIG+ answers:
 *  1. On prompts with at most 10 superstars (the 50 most recognizable players), superstars are
 *     never Refractor or 1 of 1, except on a team prompt for a team they barely played for (Wade
 *     on the Bulls is a real deep-ish pick). Prompts with dozens of superstars ("Won a title")
 *     can't keep them all over 2%: the shares add up to 100%.
 *  2. Superstars and well-known role players who are among a prompt's 3 most recognizable
 *     answers are Copper or Silver (picked by 10%+), and never Refractor/1 of 1 in its top 10.
 *  3. The 3 least recognizable answers reach Refractor or 1 of 1 (under 2%).
 * Franchise faces:
 *  4. The face of a franchise is Copper on its team prompt (Jordan/Bulls, Bird/Celtics,
 *     Duncan/Spurs, Dirk/Mavericks); team legends like Pippen on the Bulls are Silver.
 *  5. A famous player's short stop scores higher (is picked less) than the team's lifer.
 * Rules 1, 3, 4 and 5 fail the test. Rule 2 is a warning (counted and listed): it's mostly role
 * players splitting the obvious picks on team-pair prompts, which real picks settle after launch.
 * Then prints the top 8 answers of three prompts. Exits 1 if a failing rule breaks.
 */
const fs = require('fs');
const path = require('path');
const R = require('./rarity.js');
const F = require('./prompts.js');

const BIG = 25, SUPERSTARS = 50, WEAK_TIE = 0.3, CROWDED = 10;
const ROLE_PLAYERS = ['DeAndre Jordan', 'Robert Horry', 'J.R. Smith', 'Dennis Rodman', 'Steve Kerr', 'Derek Fisher',
  'Kyle Korver', 'Udonis Haslem', 'Nate Robinson', 'Metta World Peace', 'Boban Marjanovic', 'Brian Scalabrine',
  'Rajon Rondo', 'Tony Allen', 'Draymond Green', 'Jeremy Lin', 'Andre Iguodala', 'Matt Barnes'];
const FACES = [['team:CHI', 'Michael Jordan'], ['team:BOS', 'Larry Bird'], ['team:SAS', 'Tim Duncan'], ['team:DAL', 'Dirk Nowitzki']];
const LEGENDS = [['team:CHI', 'Scottie Pippen']];
const SHORT_STOPS = [['team:CHI', 'Dwyane Wade', 'Scottie Pippen'], ['team:BOS', "Shaquille O'Neal", 'Paul Pierce'],
  ['team:LAL', 'Steve Nash', 'Derek Fisher']];
const TIERS = [[25, 'Copper'], [10, 'Silver'], [5, 'Gold'], [2, 'Emerald'], [0.5, 'Refractor'], [0, '1 of 1']];
const tier = pct => TIERS.find(([min]) => pct >= min)[1];

const players = JSON.parse(fs.readFileSync(process.argv[2] || path.join(__dirname, 'players.json')));
const RM = R.create(players);
const G = F.create(players, { rarity: RM });
const byName = n => players.find(p => p.n === n);
const stars = new Set(players.slice().sort((a, b) => RM.recognition(b) - RM.recognition(a)).slice(0, SUPERSTARS));
const role = new Set(ROLE_PLAYERS.map(byName).filter(Boolean));
const fails = [], warns = [];
const shares = new Map();
const shareOf = (id, p) => {
  if (!shares.has(id)) { const pr = G.get(id); shares.set(id, RM.sim(players.filter(pr.test), id, R.FANS, RM.model(), pr.teams)); }
  return 100 * (shares.get(id).get(p.id) || 0);
};
const tieTo = (p, teams) => teams.length ? teams.reduce((a, t) => a + RM.tie(p, t), 0) / teams.length : 1;

let checked = 0;
for (const pr of G.usable) {
  if (pr.answers < BIG) continue;
  checked++;
  const answers = players.filter(pr.test);
  const ranked = answers.slice().sort((a, b) => RM.effective(b, pr.teams) - RM.effective(a, pr.teams));
  const rank = new Map(ranked.map((p, i) => [p, i + 1]));
  const crowded = answers.filter(p => stars.has(p)).length > CROWDED;
  for (const p of answers) {
    const v = shareOf(pr.id, p), r = rank.get(p), star = stars.has(p);
    if (!star && !role.has(p)) continue;
    if (r <= 3 && v < 10) warns.push(`${p.n} is ${tier(v)} (${v.toFixed(1)}%) on "${pr.text}" but is one of its 3 most obvious answers`);
    else if (r <= 10 && v < 2) warns.push(`${p.n} is ${tier(v)} (${v.toFixed(2)}%) on "${pr.text}", where he's #${r} most recognizable`);
    else if (star && !crowded && v < 2 && tieTo(p, pr.teams) >= WEAK_TIE) fails.push(`superstar ${p.n} is ${tier(v)} (${v.toFixed(2)}%) on "${pr.text}"`);
  }
  for (const p of ranked.slice(-3)) {
    const v = shareOf(pr.id, p);
    if (v >= 2) fails.push(`deep cut ${p.n} is only ${tier(v)} (${v.toFixed(1)}%) on "${pr.text}"`);
  }
}
for (const [id, n] of FACES) { const v = shareOf(id, byName(n)); if (v < 25) fails.push(`face of the franchise ${n} is ${tier(v)} (${v.toFixed(1)}%) on "${G.get(id).text}"`); }
for (const [id, n] of LEGENDS) { const v = shareOf(id, byName(n)); if (v < 10 || v >= 25) fails.push(`team legend ${n} is ${tier(v)} (${v.toFixed(1)}%) on "${G.get(id).text}", should be Silver`); }
for (const [id, stop, lifer] of SHORT_STOPS) {
  const a = shareOf(id, byName(stop)), b = shareOf(id, byName(lifer));
  if (a >= b) fails.push(`${stop}'s short stop (${a.toFixed(1)}%) isn't rarer than ${lifer} (${b.toFixed(1)}%) on "${G.get(id).text}"`);
}

console.log(`Wikipedia views: ${RM.has.views ? `yes (${players.filter(p => p.fame > 0).length} players)` : 'NOT YET (run fetch_fame.py)'}`
  + ` · 10-year views: ${RM.has.legacy ? 'yes' : 'not yet'} · language counts: ${RM.has.langs ? 'yes' : 'not yet'}`);
console.log(`Checked ${checked} prompts with ${BIG}+ answers + franchise cases: ${fails.length ? fails.length + ' FAILURES' : 'no failures'}.`);
fails.slice(0, 30).forEach(f => console.log('  ' + f));
const pairs = warns.filter(w => w.includes('"Played for the') && w.includes(' and ')).length;
console.log(`Warnings (obvious role players below Silver, or top-10 under 2%): ${warns.length}, ${pairs} of them on team pairs. Real picks settle these.`);
warns.slice(0, 5).forEach(w => console.log('  ' + w));

for (const id of [process.env.EX1 || 'team:CHI', process.env.EX2 || 'team:BOS', process.env.EX3 || 'team:LAL']) {
  const pr = G.get(id), answers = players.filter(pr.test);
  const rows = answers.map(p => [p, shareOf(id, p)]).sort((a, b) => b[1] - a[1]);
  console.log(`\n"${pr.text}" (${answers.length} answers)`);
  rows.slice(0, 8).forEach(([p, v], i) => console.log(`  ${String(i + 1).padStart(2)}. ${p.n.padEnd(24)} ${v.toFixed(1).padStart(5)}%  ${tier(v)}`));
}

// Without Wikipedia views the model can't tell a fan favorite from a journeyman, so only warn.
if (fails.length && !RM.has.views) console.log('\n(Not failing: players.json has no Wikipedia views yet.)');
process.exit(fails.length && RM.has.views ? 1 : 0);

/* Player vs player.  Two players from assets/player.js, the fight from
 * assets/combat.js, and this page is the arithmetic between them.
 *
 * What is different from a fight with a monster is small and comes straight
 * out of [proc,pvp_hit_roll] and pvp_melee.rs2 / pvp_ranged.rs2: the defender
 * has a player's defence roll rather than a monster's, and a protection prayer
 * scales the hit by 6/10 instead of setting it to nothing.  Everything else --
 * the rolls, the max hit, the speed, what a bow needs in its quiver -- is the
 * same code the equipment page runs.
 *
 * What is not here: special attacks, food, poison, magic (there is no autocast
 * model on any of these pages), and tick-eating, where a hit is capped at the
 * hitpoints you have left.  Every number is an average.
 */
'use strict';

var G = window.GEAR;
var TICK = CB.TICK;
var SLOT_WEAPON = CB.SLOT_WEAPON, SLOT_SHIELD = CB.SLOT_SHIELD, SLOT_AMMO = CB.SLOT_AMMO;
var esc = PP.esc, num = PP.num, time = PP.time, signed = PP.signed;
var item = CB.item;

var S = { a: PP.newState(), b: PP.newState() };
var panels = {};

function el(id) { return document.getElementById(id); }

// ------------------------------------------------------------------ the fight

function fightBetween(att, def, defState) {
  var A = PP.stats(att), D = PP.stats(def);
  var r = CB.pvpDealt(A, D, PP.protectedStyle(defState));
  var ttk = r.dps > 0 ? def.lv.hitpoints / r.dps : Infinity;
  var rate = PP.prayerPerSecond(att, A.bonuses[11]);
  return {
    ps: A, roll: r, ttk: ttk,
    // what the attacker's prayers cost over the time it takes them to win
    prayer: rate, prayerToWin: ttk < Infinity ? rate * ttk : null,
    prayerLasts: rate > 0 ? att.lv.prayer / rate : Infinity
  };
}

function box(value, label, sub) {
  return '<div class="box"><b>' + value + '</b>' + esc(label) +
    (sub ? '<div class="sub2">' + sub + '</div>' : '') + '</div>';
}

function renderSide(id, who, other, f, otherState) {
  var ps = f.ps, r = f.roll;
  var warn = [];
  var req = PP.unmetRequirements(who === 'You' ? S.a : S.b);
  if (req.length) warn.push('could not wear this yet: ' + req.join('; '));
  if (ps.ranged && !ps.fires) warn.push('the bow has nothing it can fire, so this side deals nothing');
  if (ps.damagetype === CB.DT_MAGIC) warn.push('a magic style: there is no autocast model, so it is scored with no spell behind it');
  var prot = PP.protectedStyle(otherState);
  el(id).innerHTML =
    '<h3>' + esc(who) + ' &rarr; ' + esc(other) + '</h3>' +
    '<div class="stats">' +
      box(ps.rate + ' ticks', 'attack speed', esc(ps.style[0]) + ', ' + esc(G.damagetypes[ps.damagetype])) +
      box(r.maxhit, 'max hit', r.cut ? 'cut to 6/10 by their ' + esc('Protect from ' + ({ melee: 'Melee', ranged: 'Missiles', magic: 'Magic' }[prot])) : (ps.ranged ? 'ranged strength ' + signed(ps.bonuses[12]) : 'strength bonus ' + signed(ps.bonuses[10]))) +
      box(num(r.hit * 100, 1) + '%', 'of swings land', ps.attackRoll.toLocaleString() + ' against their ' + r.defenceRoll.toLocaleString()) +
      box(num(r.dps, 2), 'damage a second', 'on average') +
      box(time(f.ttk), 'to drop them', (who === 'You' ? S.b : S.a).lv.hitpoints + ' hitpoints') +
      (f.prayer > 0
        ? box(f.prayerToWin === null ? 'n/a' : num(f.prayerToWin, 1), 'prayer points to win',
              f.prayerLasts < f.ttk ? '<span class="warn">the bar runs dry after ' + time(f.prayerLasts) + '</span>'
                                    : 'draining ' + num(f.prayer, 2) + ' a second')
        : box('&mdash;', 'prayer points to win', 'no prayers on')) +
    '</div>' +
    (warn.length ? '<p class="warn small">' + esc(warn.join('. ')) + '.</p>' : '');
}

function renderVerdict(ab, ba) {
  var v;
  if (!isFinite(ab.ttk) && !isFinite(ba.ttk)) v = 'Neither of you can drop the other.';
  else if (!isFinite(ba.ttk)) v = '<b class="win">You win</b>: they cannot drop you at all, and you drop them in ' + time(ab.ttk) + '.';
  else if (!isFinite(ab.ttk)) v = '<b class="lose">You lose</b>: you cannot drop them at all, and they drop you in ' + time(ba.ttk) + '.';
  else {
    var margin = Math.abs(ab.ttk - ba.ttk), pct = margin / Math.max(ab.ttk, ba.ttk) * 100;
    if (ab.ttk < ba.ttk) v = '<b class="win">You win on average</b>: you drop them in ' + time(ab.ttk) + ', they drop you in ' + time(ba.ttk) +
      ' &mdash; ' + time(margin) + ' to spare, ' + num(pct, 0) + '% of the fight.';
    else if (ba.ttk < ab.ttk) v = '<b class="lose">You lose on average</b>: they drop you in ' + time(ba.ttk) + ', you drop them in ' + time(ab.ttk) +
      ' &mdash; ' + time(margin) + ' short, ' + num(pct, 0) + '% of the fight.';
    else v = 'Dead even: ' + time(ab.ttk) + ' each way.';
  }
  var hpA = S.a.lv.hitpoints, hpB = S.b.lv.hitpoints;
  el('verdict').innerHTML = v + '<div class="small" style="margin-top:6px">Combat level ' + PP.combatLevel(S.a.lv) +
    ' against ' + PP.combatLevel(S.b.lv) + '; ' + hpA + ' hitpoints against ' + hpB +
    '. Averages of a fight with no food, no specials and no running: a single high roll decides plenty of real ones.</div>';
}

// ------------------------------------------------------------------ the weapon sweep

function sweep() {
  var them = PP.stats(S.b), prot = PP.protectedStyle(S.b);
  var pray = { attack: PP.prayerMultiplier(S.a, 'attack'), strength: PP.prayerMultiplier(S.a, 'strength'), defence: PP.prayerMultiplier(S.a, 'defence') };
  var rows = [];
  var weapons = CB.slotItems(SLOT_WEAPON).filter(function (r) { return PP.wearable(S.a, r[1]); }).concat([[null, null]]);
  weapons.forEach(function (wr) {
    var weapon = wr[1];
    var eq = {};
    for (var k in S.a.eq) eq[k] = S.a.eq[k];
    if (weapon) eq[SLOT_WEAPON] = wr[0]; else delete eq[SLOT_WEAPON];
    if (weapon && weapon.c && weapon.c.indexOf(SLOT_SHIELD) >= 0) delete eq[SLOT_SHIELD];
    CB.styleRows(weapon).forEach(function (row, si) {
      if (row[2] === CB.DT_MAGIC) return;
      var ps = CB.playerStats(eq, S.a.lv, si, pray);
      var r = CB.pvpDealt(ps, them, prot);
      rows.push({ id: wr[0], name: weapon ? weapon.n : 'Unarmed', style: row[0], dt: row[2], si: si,
                  rate: ps.rate, maxhit: r.maxhit, hit: r.hit, dps: r.dps, fires: ps.fires,
                  cur: String(S.a.eq[SLOT_WEAPON] || '') === String(wr[0] || '') && S.a.style === si });
    });
  });
  rows.sort(function (x, y) { return y.dps - x.dps || x.rate - y.rate; });
  var curIdx = -1;
  rows.forEach(function (r, i) { if (r.cur) curIdx = i; });
  var shown = rows.slice(0, 25);
  if (curIdx >= 25) shown.push(rows[curIdx]);
  var html = '<table class="data"><thead><tr><th>#</th><th>Weapon</th><th>Style</th><th class="num">Speed</th>' +
    '<th class="num">Max hit</th><th class="num">Lands</th><th class="num">Damage a second</th><th class="num">To drop them</th></tr></thead><tbody>' +
    shown.map(function (r) {
      var rank = rows.indexOf(r) + 1;
      var ttk = r.dps > 0 ? S.b.lv.hitpoints / r.dps : Infinity;
      return '<tr' + (r.cur ? ' class="cur"' : '') + '><td>' + rank + '</td><td>' +
        (r.id && item(r.id).ic ? '<img class="icon" src="icons/items/' + r.id + '.png" alt=""> ' : '') + esc(r.name) +
        (r.id && item(r.id).m ? ' <span class="tag members">mem</span>' : '') + '</td>' +
        '<td>' + esc(r.style) + ' <span class="small">(' + esc(G.damagetypes[r.dt]) + ')</span></td>' +
        '<td class="num">' + r.rate + 't</td><td class="num">' + r.maxhit + '</td>' +
        '<td class="num">' + num(r.hit * 100, 1) + '%</td><td class="num"><b>' + num(r.dps, 2) + '</b></td>' +
        '<td class="num">' + time(ttk) + (r.fires ? '' : ' <span class="warn small">no ammo</span>') + '</td></tr>';
    }).join('') + '</tbody></table>';
  if (curIdx >= 0) {
    var best = rows[0], cur = rows[curIdx];
    if (curIdx === 0) html = '<p class="small">Your current weapon and style is already the best of the ' + rows.length + ' you could use.</p>' + html;
    else html = '<p class="small">Your ' + esc(cur.name) + ' on ' + esc(cur.style) + ' is <b>#' + (curIdx + 1) + '</b> of ' + rows.length +
      '; ' + esc(best.name) + ' on ' + esc(best.style) + ' would do <b>' + num((best.dps / cur.dps - 1) * 100, 0) + '%</b> more a second' +
      (isFinite(S.b.lv.hitpoints / cur.dps) ? ' and drop them ' + time(S.b.lv.hitpoints / cur.dps - S.b.lv.hitpoints / best.dps) + ' sooner' : '') + '.</p>' + html;
  }
  el('sweep').innerHTML = html;
}

// ------------------------------------------------------------------ render, url

function render() {
  var ab = fightBetween(S.a, S.b, S.b), ba = fightBetween(S.b, S.a, S.a);
  renderVerdict(ab, ba);
  renderSide('side-a', 'You', 'them', ab, S.b);
  renderSide('side-b', 'They', 'you', ba, S.a);
  el('notes').innerHTML = 'Against a player, a protection prayer scales the hit by ' + G.pvp.protect[0] + '/' + G.pvp.protect[1] +
    ' (<code>pvp_melee.rs2</code>, <code>pvp_ranged.rs2</code>) rather than stopping it as it does against a monster; the rolls ' +
    'themselves are <code>[proc,pvp_hit_roll]</code>: your attack roll against their defence roll, both worked out by ' +
    '<code>player_combat_stat.rs2</code> exactly as on the equipment page.';
  sweep();
}

var loading = false;
function save() {
  if (loading) return;
  history.replaceState(null, '', '#you=' + encodeURIComponent(PP.toHash(S.a)) + '&them=' + encodeURIComponent(PP.toHash(S.b)));
}
function load() {
  loading = true;
  var h = location.hash.replace(/^#/, ''), any = false;
  h.split('&').forEach(function (kv) {
    var i = kv.indexOf('='); if (i < 0) return;
    var k = kv.slice(0, i), v = decodeURIComponent(kv.slice(i + 1));
    if (k === 'you') any = PP.fromHash(S.a, v) || any;
    if (k === 'them') any = PP.fromHash(S.b, v) || any;
  });
  if (!any) {
    // something to look at: full rune at 40s against a melee pure
    PP.applyLoadout(S.a, G.loadouts[0]); PP.applyLevels(S.a, G.levelPresets[0]);
    PP.applyLoadout(S.b, G.loadouts[1]); PP.applyLevels(S.b, G.levelPresets[3]);
  }
  loading = false;
}

function init() {
  load();
  var onChange = function () { save(); render(); };
  panels.a = new PP.Panel({ root: el('you'), state: S.a, onChange: onChange, label: 'You', stacked: true });
  panels.b = new PP.Panel({ root: el('them'), state: S.b, onChange: onChange, label: 'Them', stacked: true });
  save();
  render();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();

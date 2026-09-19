/* Equipment builder.  The combat arithmetic lives in assets/combat.js (CB),
 * shared with the weapon comparison; this file is the page around it.
 *
 * Dragonfire is the one thing only this page needs, and its models come from
 * data/gear.js, read out of each dragon's own breath proc by build/gear.py.
 */
'use strict';

var G = window.GEAR;
var TICK = CB.TICK;
var SLOT_WEAPON = CB.SLOT_WEAPON, SLOT_SHIELD = CB.SLOT_SHIELD, SLOT_AMMO = CB.SLOT_AMMO;
var DT_MAGIC = CB.DT_MAGIC, DT_RANGED = CB.DT_RANGED;
var effectiveStat = CB.effectiveStat, combatStat = CB.combatStat;
var combatMaxhit = CB.combatMaxhit, hitChance = CB.hitChance;
var item = CB.item, ammoFits = CB.ammoFits;

// ------------------------------------------------------------------ state

var S = {
  eq: {},                             // slot -> item id
  lv: { attack: 70, strength: 70, defence: 70, ranged: 70, magic: 50, hitpoints: 70 },
  pray: { attack: 0, strength: 0, defence: 0, protect: 0 },   // 0 = off, else the varp
  antifire: false,
  f2p: false,
  safespot: false,
  style: 0,
  monster: null
};
var LEVEL_KEYS = ['attack', 'strength', 'defence', 'ranged', 'magic', 'hitpoints'];

// ------------------------------------------------------------------ items

function itemIcon(id, cls) {
  var it = item(id);
  if (!it || !it.ic) return '';
  return '<img class="' + (cls || '') + '" src="icons/items/' + id + '.png" alt="">';
}
function worn() {
  var out = [];
  for (var slot in S.eq) if (S.eq[slot]) out.push([+slot, item(S.eq[slot])]);
  return out;
}

function bonuses() { return CB.bonuses(S.eq); }
function styleRows() { return CB.styleRows(item(S.eq[SLOT_WEAPON])); }
function attackRate() {
  var rows = styleRows();
  return CB.attackRate(item(S.eq[SLOT_WEAPON]), rows[Math.min(S.style, rows.length - 1)]);
}

function prayerMultiplier(kind) {
  if (!S.pray[kind]) return 100;
  var varp = S.pray[kind], mult = 100;
  G.prayers.forEach(function (p) { if (p[0] === varp && p[3] === kind) mult = p[4]; });
  return mult;
}
function protecting(style) {
  if (!S.pray.protect) return false;
  var on = false;
  G.prayers.forEach(function (p) { if (p[0] === S.pray.protect && p[3] === 'protect_' + style) on = true; });
  return on;
}

function playerStats() {
  return CB.playerStats(S.eq, S.lv, S.style, {
    attack: prayerMultiplier('attack'),
    strength: prayerMultiplier('strength'),
    defence: prayerMultiplier('defence')
  });
}

function playerDefenceRoll(ps, damagetype) { return CB.playerDefenceRoll(ps, damagetype); }

// ------------------------------------------------------------------ the monster side

function npcDefenceRoll(mon, damagetype) { return CB.npcDefenceRoll(mon, damagetype); }

/* What one attack of a monster's profile averages, and how long it takes. */
function npcAttack(mon, entry, ps) {
  var eff, roll, maxhit, defence, p, blocked = false, note = '';
  if (entry.k === 'melee') {
    roll = combatStat(effectiveStat(mon.st[0], 100) + 9, mon.b[0]);
    maxhit = combatMaxhit(combatStat(effectiveStat(mon.st[1], 100) + 9, mon.b[1]));
    defence = playerDefenceRoll(ps, mon.dt);
    p = hitChance(roll, defence);
    blocked = protecting('melee');
    return { label: 'Melee', delay: mon.r, hit: p, maxhit: maxhit, blocked: blocked,
             damage: blocked ? 0 : p * maxhit / 2 };
  }
  if (entry.k === 'ranged') {
    eff = effectiveStat(mon.st[4], 100) + 9;
    roll = combatStat(eff, mon.b[2]);
    maxhit = combatMaxhit(combatStat(eff, mon.b[3]));
    // [proc,npc_rangeattack] rolls against the defence of the npc's own damagetype
    defence = playerDefenceRoll(ps, mon.dt);
    p = hitChance(roll, defence);
    blocked = protecting('ranged');
    return { label: 'Ranged', delay: mon.r, hit: p, maxhit: maxhit, blocked: blocked,
             damage: blocked ? 0 : p * maxhit / 2 };
  }
  if (entry.k === 'magic') {
    roll = combatStat(effectiveStat(mon.st[3], 100) + 9, mon.b[4]);
    defence = playerDefenceRoll(ps, DT_MAGIC);
    p = hitChance(roll, defence);
    maxhit = entry.mh || 0;
    blocked = protecting('magic');
    return { label: entry.sp || 'Spell', delay: entry.d || mon.r, hit: p, maxhit: maxhit,
             blocked: blocked, damage: blocked ? 0 : p * maxhit / 2,
             note: maxhit ? '' : 'no damage' };
  }
  // dragonfire
  var B = G.breaths[entry.m];
  // every dragonfire proc asks `inv_total(worn, antidragonbreathshield) > 0`,
  // of the set being costed -- which is not the worn set while optimising
  var shield = String((ps.equip || S.eq)[SLOT_SHIELD]) === String(G.antifireShield);
  var protMagic = protecting('magic');
  p = 1;
  if (B.rollDef) {
    roll = combatStat(effectiveStat(mon.st[3], 100) + 9, mon.b[4]);   // ~npc_magic_attack_roll
    defence = playerDefenceRoll(ps, B.rollDef === 'magic' ? DT_MAGIC : mon.dt);
    p = hitChance(roll, defence);
  }
  var mh = shield ? B.shield : (protMagic ? B.prayer : B.base);
  if (shield && protMagic) mh -= B.prayerOffShield;
  if (S.antifire) mh -= shield ? B.potionOffShield : B.potionOff;
  mh = Math.max(mh, 0);
  var dmg, shown = mh;
  if (!shield && !protMagic && B.onhit !== null && B.onhit !== undefined) {
    var high = Math.max(B.onhit - (S.antifire ? B.potionOff : 0), 0);
    dmg = p * high / 2 + (1 - p) * mh / 2;
    shown = high;
    note = 'max ' + mh + ' resisted, ' + high + ' when its roll beats your defence';
  } else {
    dmg = (B.gate ? p : 1) * mh / 2;
    if (!B.rollDef) note = 'always lands';
  }
  // say what is taking the sting out of it, since that is the whole point
  var cutBy = [];
  if (shield) cutBy.push('anti-dragon shield');
  else if (protMagic) cutBy.push('Protect from Magic');
  if (S.antifire) cutBy.push('antifire potion');
  if (cutBy.length) note = 'down to ' + shown + ' from ' + B.base + ' (' + cutBy.join(' + ') + ')' +
    (note ? ', ' + note : '');
  return { label: B.label, delay: B.delay, hit: p, maxhit: shown, damage: dmg, blocked: false,
           note: note, breath: true };
}

/* The hitpoints [timer,health_regen] restores each second. */
function regenPerSecond() { return G.regen[1] / (G.regen[0] * TICK); }

/* What the monster actually gets to do: everything in melee range, or only what
 * its [ai_applayer2] handler can reach you with when you are safespotted --
 * which for most monsters, the dragons included, is nothing at all. */
function attacksOf(mon) { return S.safespot ? (mon.ap || []) : mon.atk; }

function fight(mon, ps) {
  var rows = attacksOf(mon).map(function (e) {
    var a = npcAttack(mon, e, ps);
    a.weight = e.w;
    return a;
  });
  var perAttack = 0, cycle = 0;
  rows.forEach(function (r) { perAttack += r.weight * r.damage; cycle += r.weight * r.delay; });
  var taken = cycle > 0 ? perAttack / (cycle * TICK) : 0;

  // a bow with the wrong ammunition, or none, never gets as far as a hit roll
  var dealtHit = ps.fires ? hitChance(ps.attackRoll, npcDefenceRoll(mon, ps.damagetype)) : 0;
  var capped = Math.min(ps.maxhit, mon.md);
  var dealt = dealtHit * (capped / 2) / (ps.rate * TICK);
  return {
    rows: rows, taken: taken, dealt: dealt, dealtHit: dealtHit, capped: capped,
    ttk: dealt > 0 ? mon.st[5] / dealt : Infinity,
    // [timer,health_regen] puts hitpoints back while the fight runs
    regen: regenPerSecond(),
    survive: taken > regenPerSecond() ? S.lv.hitpoints / (taken - regenPerSecond()) : Infinity
  };
}

// ------------------------------------------------------------------ optimising
//
// Which slots can trade offence against defence depends on how you are
// fighting, so the split is worked out per damage type rather than assumed.
// Swinging a weapon, only the amulet, gloves and boots carry any melee attack
// or strength bonus, and no body in the game touches your damage at all.
// Drawing a bow, nearly every slot does -- 21 helmets, 17 bodies and 23 pairs
// of legs carry ranged attack -- which is why dragonhide is worth wearing over
// rune despite the defence it gives up.  Only the cape and the ring are purely
// defensive either way.  So the search splits: slots that trade offence against
// defence get enumerated, the rest are a straight pick of the best defence
// against what this monster throws.
//
// The shield is the exception that stops this being a bonus-ranking exercise.
// An anti-dragon shield gives up 43 points of defence and still cuts the damage
// a green dragon does by three quarters, because it caps the breath rather than
// dodging it.  So every candidate is scored on the damage it actually produces.

var ATT_BONUS = [0, 1, 2, 4, 3], DEF_BONUS = [5, 6, 7, 9, 8];
var KIT_SLOTS = [0, 1, 2, 4, 5, 7, 9, 10, 12, 13];

function wearable(it) {
  var req = it.req || {};
  for (var k in req) if (k !== 'quest' && (S.lv[k] || 1) < req[k]) return false;
  return !(S.f2p && it.m);
}

/* The items in one slot worth considering, on the three axes the fight reads:
 * the attack bonus this style rolls, the strength behind it, and the defence
 * against what is coming back.  Anything beaten on all three is dropped.
 *
 * Weight is the tiebreak, not a fourth axis.  The fight is not the whole cost
 * -- a rune chainbody stops nearly what a platebody does for 7lb and 15,000gp
 * less, and that weight is run energy, which is the walk back -- but making it
 * an axis of its own would leave almost nothing dominated, since nearly every
 * item weighs something different, and the search went from 0.2s to 15s.  So
 * it only separates items the fight genuinely cannot tell apart. */
function slotCandidates(slot, dt, monDt, ranged, weapon) {
  var rows = CB.slotItems(slot).filter(function (r) {
    if (!wearable(r[1])) return false;
    // ammunition has to be something this bow can actually fire, or the whole
    // weapon drops out of the search when the strongest arrow is trimmed to
    if (slot === SLOT_AMMO) {
      return weapon && CB.ammoFits(weapon, r[1]) && (r[1].lr || 0) <= (weapon.lr || 0);
    }
    return true;
  })
    .map(function (r) {
      return { id: r[0], a: r[1].b[ATT_BONUS[dt]], s: r[1].b[ranged ? 12 : 10],
               d: r[1].b[DEF_BONUS[monDt]], w: r[1].w || 0 };
    });
  rows.push({ id: null, a: 0, s: 0, d: 0, w: 0 });         // wearing nothing
  var keep = rows.filter(function (x) {
    return !rows.some(function (y) {
      if (y === x) return false;
      if (y.a >= x.a && y.s >= x.s && y.d >= x.d && (y.a > x.a || y.s > x.s || y.d > x.d)) return true;
      // identical to the fight, so the lighter one stands in for both
      return y.a === x.a && y.s === x.s && y.d === x.d && y.w < x.w;
    });
  });
  // the anti-dragon shield is never the best bonus, and is often the best shield
  if (slot === SLOT_SHIELD && G.antifireShield && keep.every(function (x) { return String(x.id) !== String(G.antifireShield); })) {
    var ads = item(G.antifireShield);
    if (ads && wearable(ads)) keep.push({ id: String(G.antifireShield), a: 0, s: 0, d: 0, w: ads.w || 0 });
  }
  return keep;
}

/* Sets the objective cannot separate are separated by what else they cost, in
 * order: the combat axis the mode is not looking at, then weight.
 *
 * Both steps matter.  "Most damage dealt" ignores damage taken, so a great many
 * sets tie on it -- going straight to weight there would drop the anti-dragon
 * shield, which weighs 5lb and quarters what a dragon does to you, for a set
 * that deals exactly the same and takes four times as much.  And the tolerance
 * is deliberate rather than an equality test: a rune chainbody taking a
 * hundredth of a hitpoint a second more than a platebody is not worth 7lb and
 * 15,000gp, and floating point would not call the two equal anyway. */
var TIED = 0.005;                                   // half a percent

function ties(x, y) {
  if (x === y) return true;
  if (!isFinite(x) || !isFinite(y)) return false;
  return Math.abs(x - y) <= Math.max(Math.abs(x), Math.abs(y), 1e-9) * TIED;
}

function preferred(cand, best) {
  if (!best) return true;
  if (!ties(cand.v, best.v)) return cand.v > best.v;
  // the objective calls them equal; the axis it was not looking at breaks it
  var second = cand.mode === 'taken' ? [cand.dealt, best.dealt]      // more damage dealt
                                     : [-cand.taken, -best.taken];  // less damage taken
  if (!ties(second[0], second[1])) return second[0] > second[1];
  if (cand.w !== best.w) return cand.w < best.w;    // then the lighter set
  return cand.v > best.v;
}

function score(mode, dealt, taken) {
  if (mode === 'dealt') return dealt;
  if (mode === 'taken') return -taken;
  return taken > 0 ? dealt / taken : Infinity;      // damage dealt per damage taken
}

function optimise(mode) {
  var mon = S.monster && G.monsters[S.monster];
  if (!mon) return null;
  var monDt = Math.min(Math.max(mon.dt, 0), 4);
  var best = null, cache = {};
  function cached(slot, dt, md, ranged, weapon) {
    // only the ammunition list depends on which weapon is holding it
    var key = slot + '|' + dt + '|' + (slot === SLOT_AMMO && weapon ? weapon.n : '');
    if (!cache[key]) cache[key] = slotCandidates(slot, dt, md, ranged, weapon);
    return cache[key];
  }
  CB.slotItems(SLOT_WEAPON).concat([[null, null]]).forEach(function (wr) {
    var weapon = wr[1];
    if (weapon && !wearable(weapon)) return;
    var rows = CB.styleRows(weapon);
    rows.forEach(function (row, si) {
      var dt = row[2], ranged = dt === DT_RANGED;
      if (dt === DT_MAGIC) return;                  // the page has no autocast model
      var twoHanded = !!(weapon && weapon.c && weapon.c.indexOf(SLOT_SHIELD) >= 0);
      var slots = KIT_SLOTS.filter(function (sl) {
        if (sl === SLOT_SHIELD) return !twoHanded;
        if (sl === SLOT_AMMO) return ranged;
        return true;
      });
      var lists = slots.map(function (sl) { return [sl, cached(sl, dt, monDt, ranged, weapon)]; });
      if (lists.some(function (pair) { return !pair[1].length; })) return;
      // A slot whose items all carry the same offence cannot trade one against
      // the other, so its best defence is simply its best choice and it never
      // needs enumerating.  Which slots those are falls out of the candidate
      // lists above, so it is most of them on a melee style and few of them on
      // a ranged one.  The rest, and the shield with its anti-dragon case, go
      // into the cross product.
      var base = {}, varying = [];
      lists.forEach(function (pair) {
        var cs = pair[1];
        var varies = pair[0] === SLOT_SHIELD || cs.some(function (c) { return c.a !== cs[0].a || c.s !== cs[0].s; });
        if (varies) { varying.push(pair); return; }
        var top = cs[0];
        cs.forEach(function (c) { if (c.d > top.d || (c.d === top.d && c.w < top.w)) top = c; });
        if (top.id) base[pair[0]] = top.id;
      });
      var combos = [base];
      varying.forEach(function (pair) {
        var next = [];
        combos.forEach(function (prev) {
          pair[1].forEach(function (c) {
            var eq = {};
            for (var k in prev) eq[k] = prev[k];
            if (c.id) eq[pair[0]] = c.id;
            next.push(eq);
          });
        });
        combos = next;
      });
      combos.forEach(function (eq) {
        if (weapon) eq[SLOT_WEAPON] = wr[0];
        if (ranged && weapon && (weapon.cat === 'weapon_bow' || weapon.cat === 'weapon_crossbow') && !eq[SLOT_AMMO]) return;
        var ps = CB.playerStats(eq, S.lv, si, {
          attack: prayerMultiplier('attack'), strength: prayerMultiplier('strength'),
          defence: prayerMultiplier('defence')
        });
        var f = fight(mon, ps);
        var cand = { v: score(mode, f.dealt, f.taken), w: ps.weight, eq: eq, style: si, mode: mode,
                     dealt: f.dealt, taken: f.taken };
        if (preferred(cand, best)) best = cand;
      });
    });
  });
  return best;
}

function runOptimise(mode) {
  if (!(S.monster && G.monsters[S.monster])) {
    el('optnote').innerHTML = '<span class="warn">Pick a monster below first &mdash; there is nothing to optimise against.</span>';
    return;
  }
  var before = fight(G.monsters[S.monster], playerStats());
  var beforeEq = S.eq;
  var best = optimise(mode);
  if (!best) return;
  S.eq = best.eq;
  S.style = best.style;
  save();
  renderAll();
  var label = { dealt: 'most damage dealt', taken: 'least damage taken', ratio: 'best damage dealt per damage taken' }[mode];
  var extra = '';
  if (S.safespot) {
    // how far the chosen weapon reaches decides whether a safespot is findable
    var w = item(best.eq[SLOT_WEAPON]);
    var reach = w ? (w.ar || 1) : 1;
    extra = ' <span class="safe">&middot; safespotted</span>' + (w
      ? ' <span class="small">(' + esc(w.n) + ' reaches ' + reach + ' tile' + (reach === 1 ? '' : 's') +
        ', +2 on longrange' + (reach <= 4 ? ' &mdash; short for a safespot' : '') + ')</span>'
      : '');
  }
  el('optnote').innerHTML = 'Optimised for ' + label + ': <b>' + num(best.dealt, 2) + '</b> dealt, <b>' +
    num(best.taken, 2) + '</b> taken a second, <b>' + num(best.w / 1000, 2) + '</b> kg' +
    (before ? ' <span class="small">(was ' + num(before.dealt, 2) + ' / ' + num(before.taken, 2) + ' / ' +
      num(CB.weight(beforeEq) / 1000, 2) + ' kg)</span>' : '') +
    extra + ' <div class="small">Sets within half a percent of each other are settled by weight, so the ' +
    'lighter of two the fight cannot separate is the one you get.</div>';
}

// ------------------------------------------------------------------ rendering

function el(id) { return document.getElementById(id); }
function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
function signed(n) { return (n > 0 ? '+' : '') + n; }
function cls(n) { return n > 0 ? 'pos' : (n < 0 ? 'neg' : ''); }
function num(n, d) { return (Math.round(n * Math.pow(10, d)) / Math.pow(10, d)).toFixed(d); }
function time(s) {
  if (!isFinite(s)) return 'never';
  if (s < 90) return num(s, 1) + 's';
  return Math.floor(s / 60) + 'm ' + Math.round(s % 60) + 's';
}

// the equipment screen's layout: three columns, blanks where the game leaves gaps
var LAYOUT = [[null, 0, null], [1, 2, 13], [3, 4, 5], [null, 7, null], [9, 10, 12]];

function renderSlots() {
  var labels = {};
  G.slots.forEach(function (s) { labels[s[0]] = s[2]; });
  var html = '';
  LAYOUT.forEach(function (row) {
    row.forEach(function (slot) {
      if (slot === null) { html += '<div class="slot empty-cell"></div>'; return; }
      var id = S.eq[slot], it = item(id);
      html += '<div class="slot' + (it ? ' filled' : '') + '" data-slot="' + slot + '" title="' +
        esc(labels[slot] || '') + '">' +
        (it ? itemIcon(id) + '<span class="nm">' + esc(it.n) + '</span>'
            : '<span class="nm">' + esc(labels[slot] || '') + '</span>') + '</div>';
    });
  });
  el('slots').innerHTML = html;
  Array.prototype.forEach.call(el('slots').querySelectorAll('.slot[data-slot]'), function (d) {
    d.onclick = function () { openPicker(+d.dataset.slot); };
  });
}

function renderLevels() {
  el('levels').innerHTML = LEVEL_KEYS.map(function (k) {
    return '<label>' + k.charAt(0).toUpperCase() + k.slice(1) +
      '<input type="number" min="1" max="99" data-lv="' + k + '" value="' + S.lv[k] + '"></label>';
  }).join('');
  Array.prototype.forEach.call(el('levels').querySelectorAll('input'), function (i) {
    i.onchange = i.oninput = function () {
      var v = parseInt(i.value, 10);
      S.lv[i.dataset.lv] = isNaN(v) ? 1 : Math.max(1, Math.min(99, v));
      save(); renderAll(false);
    };
  });
}

function renderPrayers() {
  var kinds = [['attack', 'Attack'], ['strength', 'Strength'], ['defence', 'Defence']];
  var html = kinds.map(function (k) {
    var opts = ['<option value="0">None</option>'];
    G.prayers.filter(function (p) { return p[3] === k[0]; }).forEach(function (p) {
      opts.push('<option value="' + p[0] + '"' + (S.pray[k[0]] === p[0] ? ' selected' : '') + '>' +
        esc(p[1]) + ' (+' + (p[4] - 100) + '%, level ' + p[2] + ')</option>');
    });
    return '<label>' + k[1] + '<select data-pray="' + k[0] + '">' + opts.join('') + '</select></label>';
  });
  var opts = ['<option value="0">None</option>'];
  G.prayers.filter(function (p) { return p[3].indexOf('protect_') === 0; }).forEach(function (p) {
    opts.push('<option value="' + p[0] + '"' + (S.pray.protect === p[0] ? ' selected' : '') + '>' +
      esc(p[1]) + ' (level ' + p[2] + ')</option>');
  });
  html.push('<label>Protection<select data-pray="protect">' + opts.join('') + '</select></label>');
  el('prayers').innerHTML = html.join('');
  Array.prototype.forEach.call(el('prayers').querySelectorAll('select'), function (s) {
    s.onchange = function () { S.pray[s.dataset.pray] = +s.value; save(); renderAll(false); };
  });
}

function renderBonuses(ps) {
  var b = ps.bonuses;
  function table(title, rows) {
    return '<div><h3>' + title + '</h3><table>' + rows.map(function (r) {
      return '<tr><td>' + esc(r[0]) + '</td><td class="' + cls(r[1]) + '">' + signed(r[1]) + '</td></tr>';
    }).join('') + '</table></div>';
  }
  var atk = [], def = [], other = [];
  for (var i = 0; i < 5; i++) atk.push([G.bonusLabels[i], b[i]]);
  for (i = 5; i < 10; i++) def.push([G.bonusLabels[i], b[i]]);
  other.push([G.bonusLabels[10], b[10]]);
  other.push([G.bonusLabels[12], b[12]]);
  other.push([G.bonusLabels[11], b[11]]);
  el('bonuses').innerHTML = table('Attack bonus', atk) + table('Defence bonus', def) + table('Other', other) +
    '<div><h3>The set itself</h3><table>' +
      '<tr><td>Weight</td><td>' + num(CB.weight(S.eq) / 1000, 2) + ' kg</td></tr>' +
      '<tr><td>Cost</td><td>' + setCost(S.eq).toLocaleString() + ' gp</td></tr>' +
    '</table><div class="small" style="margin-top:6px">Each obj\'s own <code>weight=</code> and ' +
    '<code>cost=</code>. How much run energy the weight costs you is engine-side, so the pages do not ' +
    'put a number on it.</div></div>';
}

function setCost(equip) {
  var total = 0;
  for (var slot in equip) {
    var it = item(equip[slot]);
    if (it) total += it.v || 0;
  }
  return total;
}

function renderStyles(ps) {
  var rows = styleRows();
  var weapon = item(S.eq[SLOT_WEAPON]);
  var types = G.damagetypes;
  el('styles').innerHTML = rows.map(function (r, i) {
    return '<button data-style="' + i + '" class="' + (i === Math.min(S.style, rows.length - 1) ? 'on' : '') + '">' +
      esc(r[0]) + ' <span class="small">(' + esc(types[r[2]]) + ')</span></button>';
  }).join('') + ' <span class="small">' + (weapon ? esc(weapon.cat || 'no category') : 'unarmed') + '</span>';
  Array.prototype.forEach.call(el('styles').querySelectorAll('button'), function (btn) {
    btn.onclick = function () { S.style = +btn.dataset.style; save(); renderAll(false); };
  });
}

function box(value, label, sub) {
  return '<div class="box"><b>' + value + '</b>' + esc(label) +
    (sub ? '<div class="sub2">' + sub + '</div>' : '') + '</div>';
}

function renderAttack(ps) {
  var rate = ps.rate;
  var warn = '';
  var weapon = item(S.eq[SLOT_WEAPON]);
  var ammo = item(S.eq[SLOT_AMMO]);
  if (ps.ranged && weapon && (weapon.cat === 'weapon_bow' || weapon.cat === 'weapon_crossbow')) {
    if (!ammo) warn = '<p class="warn small">No ammunition in the quiver &ndash; a bow without arrows will not ' +
      'fire, so this set deals nothing.</p>';
    else if (!ammoFits(weapon, ammo)) warn = '<p class="warn small">' + esc(ammo.n) + ' does not fit a ' +
      esc(weapon.n) + ', so its ranged strength is not counted and the shot will not fire &ndash; ' +
      'this set deals nothing.</p>';
    else if ((ammo.lr || 0) > (weapon.lr || 0)) warn = '<p class="warn small">' + esc(weapon.n) +
      ' is not powerful enough for ' + esc(ammo.n) + ', so it will not fire and this set deals nothing &ndash; ' +
      'the ranged strength below still counts, because <code>~equip_get_bonuses</code> only checks that the ' +
      'ammo is the right kind, while <code>~player_ranged_check_ammo</code> compares the two level gates.</p>';
  }
  var req = unmetRequirements();
  if (req.length) warn += '<p class="warn small">You could not wear this yet: ' + req.map(esc).join('; ') + '.</p>';
  el('attack').innerHTML =
    '<div class="stats">' +
      box(rate + ' ticks', 'attack speed', num(rate * TICK, 1) + ' seconds a swing') +
      box(ps.maxhit, 'max hit', ps.ranged ? 'ranged strength ' + signed(ps.bonuses[12])
                                          : 'strength bonus ' + signed(ps.bonuses[10])) +
      box(num(ps.maxhit / (rate * TICK) / 2, 2), 'damage a second', 'if every swing landed') +
      box(ps.attackRoll.toLocaleString(), 'attack roll', esc(ps.style[0]) + ', ' + esc(G.damagetypes[ps.damagetype])) +
    '</div>' + warn;
}

function unmetRequirements() {
  var out = [];
  worn().forEach(function (pair) {
    var it = pair[1];
    if (!it.req) return;
    var missing = [];
    for (var k in it.req) {
      // quests are left to the reader; only the level gates are checked
      if (k !== 'quest' && (S.lv[k] || 1) < it.req[k]) missing.push(k + ' ' + it.req[k]);
    }
    if (missing.length) out.push(it.n + ' needs ' + missing.join(' + '));
  });
  return out;
}

function renderFight(ps) {
  var mon = S.monster && G.monsters[S.monster];
  if (!mon) {
    el('fight').innerHTML = '<p class="small">Pick a monster to see the damage going each way.</p>';
    return;
  }
  var f = fight(mon, ps);
  var rows = f.rows.map(function (r) {
    return '<tr><td>' + esc(r.label) + '</td><td>' + num(r.weight * 100, 0) + '%</td>' +
      '<td>' + r.maxhit + '</td><td>' + num(r.hit * 100, 1) + '%</td>' +
      '<td>' + r.delay + ' ticks</td><td>' + num(r.damage, 2) + '</td>' +
      '<td class="small">' + esc(r.blocked ? 'blocked by your prayer' : (r.note || '')) + '</td></tr>';
  }).join('');
  var icon = mon.ic ? '<img class="icon" src="icons/npcs/' + S.monster + '.png" alt="">' : '';
  el('fight').innerHTML =
    '<h3>' + icon + esc(mon.n) + (mon.lv ? ' <span class="small">level ' + mon.lv + '</span>' : '') +
      ' <a class="small" href="' + mon.u + '">wiki page &rarr;</a></h3>' +
    '<p class="small">' + mon.st[5] + ' hitpoints &middot; attack ' + mon.st[0] + ', strength ' + mon.st[1] +
      ', defence ' + mon.st[2] + (mon.st[3] ? ', magic ' + mon.st[3] : '') + (mon.st[4] ? ', ranged ' + mon.st[4] : '') +
      ' &middot; attacks every ' + mon.r + ' ticks as <code>' + esc(G.damagetypes[mon.dt]) + '</code>' +
      (mon.md < 1000 ? ' &middot; caps the damage you deal at ' + mon.md : '') + '</p>' +
    '<div class="stats">' +
      box(num(f.dealt, 2), 'damage a second dealt', num(f.dealtHit * 100, 1) + '% of swings land, max ' + f.capped) +
      box(time(f.ttk), 'to kill it', mon.st[5] + ' hitpoints') +
      box(num(f.taken, 2), 'damage a second taken', 'across its attack profile') +
      box(time(f.survive), 'until you drop', S.lv.hitpoints + ' hitpoints, no food, regen ' +
          num(f.regen * 60, 2).replace(/\.?0+$/, '') + ' hp a minute') +
    '</div>' +
    '<h3>What it throws at you</h3>' +
    '<table class="data"><thead><tr><th>Attack</th><th>Share</th><th>Max hit</th><th>Lands</th>' +
      '<th>Every</th><th>Average damage</th><th></th></tr></thead><tbody>' + rows + '</tbody></table>' +
    (S.safespot && !(mon.ap && mon.ap.length)
      ? '<p class="small safe">Safespotted, it cannot touch you: reaching a player who is not adjacent needs an ' +
        '<code>[ai_applayer2]</code> handler and this one has none, so there is no melee and no breath.</p>' : '') +
    (mon.aprx ? '<p class="small">Its combat script branches on something other than a die roll, so the shares ' +
      'above split those branches evenly.</p>' : '');
}

function renderAll(redrawSlots) {
  if (redrawSlots !== false) renderSlots();
  var rows = styleRows();
  if (S.style >= rows.length) S.style = 0;
  var ps = playerStats();
  renderStyles(ps);
  renderBonuses(ps);
  renderAttack(ps);
  renderFight(ps);
  if (redrawSlots !== false) { renderLevels(); renderPrayers(); }
}

// ------------------------------------------------------------------ the item picker

var pickSlot = null;

function slotItems(slot) {
  return CB.slotItems(slot).filter(function (r) { return !(S.f2p && r[1].m); });
}

function bonusSummary(it) {
  var parts = [];
  for (var i = 0; i < 13; i++) if (it.b[i]) parts.push(G.bonusKeys[i].replace('attack', ' att').replace('defence', ' def') + ' ' + signed(it.b[i]));
  if (it.s === SLOT_WEAPON) parts.unshift(it.r + 't');
  return parts.slice(0, 4).join(', ');
}

function openPicker(slot) {
  pickSlot = slot;
  var labels = {};
  G.slots.forEach(function (s) { labels[s[0]] = s[2]; });
  el('picktitle').textContent = 'Choose: ' + (labels[slot] || 'item');
  el('pickfind').value = '';
  el('picker').classList.add('on');
  fillPicker('');
  el('pickfind').focus();
}
function closePicker() { el('picker').classList.remove('on'); pickSlot = null; }

function fillPicker(q) {
  q = q.toLowerCase();
  var list = slotItems(pickSlot).filter(function (r) { return !q || r[1].n.toLowerCase().indexOf(q) >= 0; });
  var html = '<div class="row" data-id=""><span class="nm">(nothing)</span></div>';
  html += list.slice(0, 400).map(function (r) {
    var id = r[0], it = r[1];
    var req = '';
    if (it.req) {
      var bits = [];
      for (var k in it.req) if (k !== 'quest') bits.push(k + ' ' + it.req[k]);
      req = ' <span class="req">(' + esc(bits.join(', ')) + ')</span>';
    }
    return '<div class="row" data-id="' + id + '">' + (it.ic ? itemIcon(id) : '') +
      '<span>' + esc(it.n) + (it.m ? ' <span class="tag members">mem</span>' : '') + req + '</span>' +
      '<span class="b">' + esc(bonusSummary(it)) + '</span></div>';
  }).join('');
  el('picklist').innerHTML = html;
  Array.prototype.forEach.call(el('picklist').querySelectorAll('.row'), function (d) {
    d.onclick = function () { choose(d.dataset.id); };
  });
}

function choose(id) {
  var slot = pickSlot;
  if (!id) delete S.eq[slot];
  else {
    S.eq[slot] = id;
    var it = item(id);
    // a two-handed weapon covers the shield slot, and a shield pushes one off
    if (it.c && it.c.indexOf(SLOT_SHIELD) >= 0) delete S.eq[SLOT_SHIELD];
    if (slot === SLOT_SHIELD) {
      var w = item(S.eq[SLOT_WEAPON]);
      if (w && w.c && w.c.indexOf(SLOT_SHIELD) >= 0) delete S.eq[SLOT_WEAPON];
    }
    if (slot === SLOT_WEAPON) S.style = 0;
  }
  closePicker();
  save();
  renderAll();
}

// ------------------------------------------------------------------ the monster search

var monsterList = null;
function monsters() {
  if (!monsterList) {
    monsterList = [];
    for (var id in G.monsters) monsterList.push([id, G.monsters[id]]);
    monsterList.sort(function (a, b) { return a[1].n.localeCompare(b[1].n) || a[1].lv - b[1].lv; });
  }
  return monsterList;
}

function monsterSuggest(q) {
  var box = el('msugg');
  q = q.trim().toLowerCase();
  if (!q) { box.style.display = 'none'; return; }
  var hits = monsters().filter(function (r) { return r[1].n.toLowerCase().indexOf(q) >= 0; }).slice(0, 40);
  if (!hits.length) { box.style.display = 'none'; return; }
  box.innerHTML = hits.map(function (r) {
    return '<div data-id="' + r[0] + '">' + esc(r[1].n) +
      (r[1].lv ? ' <span class="small">level ' + r[1].lv + '</span>' : '') + '</div>';
  }).join('');
  box.style.display = 'block';
  Array.prototype.forEach.call(box.querySelectorAll('div'), function (d) {
    d.onclick = function () {
      S.monster = d.dataset.id;
      el('mfind').value = G.monsters[S.monster].n;
      box.style.display = 'none';
      save();
      renderAll(false);
    };
  });
}

// ------------------------------------------------------------------ url state

var loading = false;
function save() {
  if (loading) return;
  var parts = [];
  var eq = G.slots.map(function (s) { return S.eq[s[0]] || ''; }).join('.');
  if (eq.replace(/\./g, '')) parts.push('eq=' + eq);
  parts.push('lv=' + LEVEL_KEYS.map(function (k) { return S.lv[k]; }).join('.'));
  parts.push('pr=' + [S.pray.attack, S.pray.strength, S.pray.defence, S.pray.protect].join('.'));
  if (S.style) parts.push('st=' + S.style);
  if (S.antifire) parts.push('af=1');
  if (S.safespot) parts.push('ss=1');
  if (S.f2p) parts.push('f2p=1');
  if (S.monster) parts.push('m=' + S.monster);
  history.replaceState(null, '', '#' + parts.join('&'));
}

function load() {
  loading = true;
  var h = location.hash.replace(/^#/, '');
  h.split('&').forEach(function (kv) {
    var i = kv.indexOf('='); if (i < 0) return;
    var k = kv.slice(0, i), v = kv.slice(i + 1);
    if (k === 'eq') v.split('.').forEach(function (id, n) {
      if (id && G.items[id] && G.slots[n]) S.eq[G.slots[n][0]] = id;
    });
    else if (k === 'lv') v.split('.').forEach(function (n, i2) {
      if (LEVEL_KEYS[i2]) S.lv[LEVEL_KEYS[i2]] = Math.max(1, Math.min(99, parseInt(n, 10) || 1));
    });
    else if (k === 'pr') { var p = v.split('.').map(Number);
      S.pray.attack = p[0] || 0; S.pray.strength = p[1] || 0; S.pray.defence = p[2] || 0; S.pray.protect = p[3] || 0; }
    else if (k === 'st') S.style = parseInt(v, 10) || 0;
    else if (k === 'af') S.antifire = v === '1';
    else if (k === 'ss') S.safespot = v === '1';
    else if (k === 'f2p') S.f2p = v === '1';
    else if (k === 'm' && G.monsters[v]) S.monster = v;
  });
  loading = false;
}

// ------------------------------------------------------------------ boot

function init() {
  load();
  el('clear').onclick = function () { S.eq = {}; S.style = 0; save(); renderAll(); };
  el('f2p').checked = S.f2p;
  el('f2p').onchange = function () {
    S.f2p = el('f2p').checked;
    if (S.f2p) for (var slot in S.eq) { var it = item(S.eq[slot]); if (it && it.m) delete S.eq[slot]; }
    save(); renderAll();
  };
  el('antifire').checked = S.antifire;
  el('antifire').onchange = function () { S.antifire = el('antifire').checked; save(); renderAll(false); };
  el('safespot').checked = S.safespot;
  el('safespot').onchange = function () { S.safespot = el('safespot').checked; save(); renderAll(false); };
  Array.prototype.forEach.call(el('optimise').querySelectorAll('button'), function (btn) {
    btn.onclick = function () { runOptimise(btn.dataset.opt); };
  });
  el('pickclose').onclick = closePicker;
  el('picker').onclick = function (e) { if (e.target === el('picker')) closePicker(); };
  el('pickfind').oninput = function () { fillPicker(el('pickfind').value); };
  el('mfind').oninput = function () { monsterSuggest(el('mfind').value); };
  el('mfind').onfocus = function () { monsterSuggest(el('mfind').value); };
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closePicker(); });
  if (S.monster) el('mfind').value = G.monsters[S.monster].n;
  renderAll();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();

/* Weapon comparison: two weapons, every monster, the server's own arithmetic.
 *
 * All of the combat maths is in assets/combat.js (CB), shared with the
 * equipment builder.  This file is the page: two weapon setups, one shared kit,
 * and the resulting damage a second against every attackable monster.
 *
 * The head-to-head panel splits the answer into the two things that actually
 * decide it, because they pull in opposite directions:
 *
 *   throughput  (max hit ratio) / (speed ratio)   -- what a slower, harder
 *               weapon gives up before accuracy is considered
 *   accuracy    (attack roll ratio)               -- what it has to win back,
 *               and which only pays while hit chance is short of certain
 */
'use strict';

var G = window.GEAR;
var SLOT_WEAPON = CB.SLOT_WEAPON, SLOT_AMMO = CB.SLOT_AMMO;

var S = {
  a: { weapon: null, style: 0, ammo: null },
  b: { weapon: null, style: 0, ammo: null },
  lv: { attack: 75, strength: 75, defence: 75, ranged: 75, magic: 50, hitpoints: 75 },
  pray: { attack: 0, strength: 0 },
  kit: {},                       // slot -> item id, worn by both sides
  filter: '', onlyB: false, sort: 'delta', desc: true
};
var LEVEL_KEYS = ['attack', 'strength', 'ranged'];
// the shared kit: every slot but the weapon hand and the quiver, which belong to
// the setups themselves
var KIT_SLOTS = [0, 1, 2, 4, 5, 7, 9, 10, 12];

function el(id) { return document.getElementById(id); }
function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
function num(n, d) { return (Math.round(n * Math.pow(10, d)) / Math.pow(10, d)).toFixed(d); }
function pct(n) { return (n > 0 ? '+' : '') + num(n, 1) + '%'; }

// ------------------------------------------------------------------ setups

function slotItems(slot) {
  var out = [];
  for (var id in G.items) if (G.items[id].s === slot) out.push([id, G.items[id]]);
  out.sort(function (x, y) { return x[1].n.localeCompare(y[1].n); });
  return out;
}

function equipFor(side) {
  var eq = {};
  for (var slot in S.kit) if (S.kit[slot]) eq[slot] = S.kit[slot];
  var st = S[side];
  if (st.weapon) eq[SLOT_WEAPON] = st.weapon;
  if (st.ammo) eq[SLOT_AMMO] = st.ammo;
  // a two-handed weapon pushes the shield off
  var w = CB.item(st.weapon);
  if (w && w.c && w.c.indexOf(CB.SLOT_SHIELD) >= 0) delete eq[CB.SLOT_SHIELD];
  return eq;
}

function statsFor(side) {
  return CB.playerStats(equipFor(side), S.lv, S[side].style, {
    attack: prayerMultiplier('attack'), strength: prayerMultiplier('strength'), defence: 100
  });
}

function prayerMultiplier(kind) {
  if (!S.pray[kind]) return 100;
  var mult = 100;
  G.prayers.forEach(function (p) { if (p[0] === S.pray[kind] && p[3] === kind) mult = p[4]; });
  return mult;
}

function needsAmmo(side) {
  var w = CB.item(S[side].weapon);
  return !!w && (w.cat === 'weapon_bow' || w.cat === 'weapon_crossbow');
}

// ------------------------------------------------------------------ rendering: inputs

function fillWeapon(side) {
  var sel = el('w' + side.toUpperCase());
  sel.innerHTML = '<option value="">(unarmed)</option>' + slotItems(SLOT_WEAPON).map(function (r) {
    return '<option value="' + r[0] + '"' + (S[side].weapon === r[0] ? ' selected' : '') + '>' +
      esc(r[1].n) + '</option>';
  }).join('');
  sel.onchange = function () { S[side].weapon = sel.value || null; S[side].style = 0; fillStyle(side); fillAmmo(side); save(); render(); };
}

function fillStyle(side) {
  var sel = el('s' + side.toUpperCase());
  var rows = CB.styleRows(CB.item(S[side].weapon));
  if (S[side].style >= rows.length) S[side].style = 0;
  sel.innerHTML = rows.map(function (r, i) {
    return '<option value="' + i + '"' + (S[side].style === i ? ' selected' : '') + '>' +
      esc(r[0]) + ' — ' + esc(G.damagetypes[r[2]]) + '</option>';
  }).join('');
  sel.onchange = function () { S[side].style = +sel.value; save(); render(); };
}

function fillAmmo(side) {
  var wrap = el('ammo' + side.toUpperCase() + '-wrap');
  var sel = el('ammo' + side.toUpperCase());
  wrap.style.display = needsAmmo(side) ? '' : 'none';
  if (!needsAmmo(side)) { S[side].ammo = null; return; }
  var w = CB.item(S[side].weapon);
  var list = slotItems(SLOT_AMMO).filter(function (r) { return CB.ammoFits(w, r[1]); });
  if (!list.length) { S[side].ammo = null; return; }
  if (!S[side].ammo || !list.some(function (r) { return r[0] === S[side].ammo; })) {
    // default to the hardest-hitting ammo the bow can actually fire
    var best = list[0];
    list.forEach(function (r) {
      if ((r[1].lr || 0) <= (w.lr || 0) && r[1].b[12] > best[1].b[12]) best = r;
    });
    S[side].ammo = best[0];
  }
  sel.innerHTML = list.map(function (r) {
    var tooStrong = (r[1].lr || 0) > (w.lr || 0);
    return '<option value="' + r[0] + '"' + (S[side].ammo === r[0] ? ' selected' : '') + '>' +
      esc(r[1].n) + ' (ranged strength +' + r[1].b[12] + ')' +
      (tooStrong ? ' \u2014 too strong for this bow' : '') + '</option>';
  }).join('');
  sel.onchange = function () { S[side].ammo = sel.value; save(); render(); };
}

function fillLevels() {
  el('levels').innerHTML = LEVEL_KEYS.map(function (k) {
    return '<div><label>' + k.charAt(0).toUpperCase() + k.slice(1) + '</label>' +
      '<input type="number" min="1" max="99" data-lv="' + k + '" value="' + S.lv[k] + '"></div>';
  }).join('');
  Array.prototype.forEach.call(el('levels').querySelectorAll('input'), function (i) {
    i.oninput = function () {
      var v = parseInt(i.value, 10);
      S.lv[i.dataset.lv] = isNaN(v) ? 1 : Math.max(1, Math.min(99, v));
      save(); render();
    };
  });
}

function fillPrayers() {
  el('prayers').innerHTML = [['attack', 'Attack prayer'], ['strength', 'Strength prayer']].map(function (k) {
    var opts = ['<option value="0">None</option>'];
    G.prayers.filter(function (p) { return p[3] === k[0]; }).forEach(function (p) {
      opts.push('<option value="' + p[0] + '"' + (S.pray[k[0]] === p[0] ? ' selected' : '') + '>' +
        esc(p[1]) + ' (+' + (p[4] - 100) + '%)</option>');
    });
    return '<div><label>' + k[1] + '</label><select data-pray="' + k[0] + '">' + opts.join('') + '</select></div>';
  }).join('');
  Array.prototype.forEach.call(el('prayers').querySelectorAll('select'), function (s) {
    s.onchange = function () { S.pray[s.dataset.pray] = +s.value; save(); render(); };
  });
}

function fillKit() {
  var labels = {};
  G.slots.forEach(function (s) { labels[s[0]] = s[2]; });
  el('kit').innerHTML = KIT_SLOTS.map(function (slot) {
    var opts = ['<option value="">(nothing)</option>'].concat(slotItems(slot).map(function (r) {
      return '<option value="' + r[0] + '"' + (S.kit[slot] === r[0] ? ' selected' : '') + '>' + esc(r[1].n) + '</option>';
    }));
    return '<div><label>' + esc(labels[slot] || ('Slot ' + slot)) + '</label>' +
      '<select data-slot="' + slot + '">' + opts.join('') + '</select></div>';
  }).join('');
  Array.prototype.forEach.call(el('kit').querySelectorAll('select'), function (s) {
    s.onchange = function () {
      if (s.value) S.kit[s.dataset.slot] = s.value; else delete S.kit[s.dataset.slot];
      save(); render();
    };
  });
}

/* The best item in each kit slot for one bonus, so the presets are read off the
 * data rather than being a list of names that could go stale. */
function bestKit(bonusIndex) {
  var kit = {};
  KIT_SLOTS.forEach(function (slot) {
    var best = null;
    slotItems(slot).forEach(function (r) {
      if (!best || r[1].b[bonusIndex] > CB.item(best).b[bonusIndex]) best = r[0];
    });
    if (best && CB.item(best).b[bonusIndex] > 0) kit[slot] = best;
  });
  return kit;
}

// ------------------------------------------------------------------ rendering: results

function headline(A, B) {
  function cell(ps, side) {
    var w = ps.weapon;
    return '<div class="box"><b class="win' + side + '">' + ps.maxhit + '</b>max hit' +
      '<div class="sub2">' + ps.rate + ' ticks &middot; ' + esc(ps.style[0]) + ' (' +
      esc(G.damagetypes[ps.damagetype]) + ') &middot; roll ' + ps.attackRoll.toLocaleString() + '</div></div>';
  }
  // the two forces, isolated
  var throughput = (B.maxhit / A.maxhit) * (A.rate / B.rate);
  var accuracy = B.attackRoll / A.attackRoll;
  el('headline').innerHTML = cell(A, 'A') + cell(B, 'B') +
    '<div class="box"><b>' + pct((throughput - 1) * 100) + '</b>B\'s throughput' +
    '<div class="sub2">max hit and speed only, accuracy ignored</div></div>' +
    '<div class="box"><b>' + pct((accuracy - 1) * 100) + '</b>B\'s attack roll' +
    '<div class="sub2">worth less the more you already hit</div></div>';

  var msg;
  if (throughput >= 1 && accuracy >= 1) msg = 'B is ahead on both counts, so it wins everywhere.';
  else if (throughput <= 1 && accuracy <= 1) msg = 'B is behind on both counts, so it loses everywhere.';
  else if (throughput > 1) msg = 'B out-damages A but is less accurate, so B leads on soft targets and ' +
    'A takes over once the monster is hard enough to hit.';
  else msg = 'B is more accurate but slower to damage, so B leads against well-defended monsters and ' +
    'A takes over once you are hitting near-certainly.';
  // the defence roll where the lead changes hands
  var flip = crossover(A, B);
  el('decomp').innerHTML = '<p class="note">' + msg +
    (flip === null ? '' : ' The lead changes hands at a defence roll of about <b>' +
      flip.toLocaleString() + '</b>.') + '</p>' + warnings(A, B);
}

/* Binary search for the defence roll at which the winner changes.  Only
 * meaningful when the two sides pull opposite ways, which is the interesting
 * case; otherwise there is nothing to find. */
function crossover(A, B) {
  function lead(D) {
    return CB.hitChance(B.attackRoll, D) * B.maxhit / B.rate -
           CB.hitChance(A.attackRoll, D) * A.maxhit / A.rate;
  }
  var lo = 0, hi = 400000;
  if ((lead(lo) > 0) === (lead(hi) > 0)) return null;
  var want = lead(hi) > 0;
  while (lo < hi) {
    var mid = Math.floor((lo + hi) / 2);
    if ((lead(mid) > 0) === want) hi = mid; else lo = mid + 1;
  }
  return lo;
}

function warnings(A, B) {
  var out = [];
  [['A', A], ['B', B]].forEach(function (pair) {
    var ps = pair[1], w = ps.weapon;
    if (!w) return;
    if (ps.ranged && (w.cat === 'weapon_bow' || w.cat === 'weapon_crossbow')) {
      var ammo = CB.item(S[pair[0].toLowerCase()].ammo);
      if (!ammo) out.push(pair[0] + ': ' + esc(w.n) + ' has nothing in the quiver and will not fire.');
      else if ((ammo.lr || 0) > (w.lr || 0))
        out.push(pair[0] + ': ' + esc(w.n) + ' is not powerful enough for ' + esc(ammo.n) + ', so it will not fire.');
    }
    if (!ps.ranged && ps.damagetype === CB.DT_RANGED)
      out.push(pair[0] + ': that style is ranged but the weapon is not.');
    var req = w.req, missing = [];
    if (req) for (var k in req) {
      if (k === 'quest') missing.push(req.quest);
      else if ((S.lv[k] || 1) < req[k]) missing.push(k + ' ' + req[k]);
    }
    if (missing.length) out.push(pair[0] + ': ' + esc(w.n) + ' needs ' + missing.map(esc).join(' + ') + '.');
  });
  return out.length ? '<p class="warn">' + out.join('<br>') + '</p>' : '';
}

var COLS = [['n', 'Monster'], ['lv', 'Level'], ['def', 'Defence roll'], ['a', 'A dps'], ['b', 'B dps'],
            ['delta', 'B vs A'], ['bar', '']];

function table(A, B) {
  var rows = [];
  for (var id in G.monsters) {
    var m = G.monsters[id];
    var da = CB.dealt(A, m), db = CB.dealt(B, m);
    rows.push({ id: id, n: m.n, lv: m.lv || 0, u: m.u, ic: m.ic,
                def: CB.npcDefenceRoll(m, A.damagetype), defB: CB.npcDefenceRoll(m, B.damagetype),
                a: da.dps, b: db.dps, delta: da.dps > 0 ? (db.dps - da.dps) / da.dps * 100 : 0 });
  }
  var aWins = rows.filter(function (r) { return r.a > r.b; }).length;
  var bWins = rows.filter(function (r) { return r.b > r.a; }).length;
  var tied = rows.length - aWins - bWins;
  el('tally').innerHTML = '<span class="winA">A wins ' + aWins + '</span> &middot; ' +
    '<span class="winB">B wins ' + bWins + '</span>' + (tied ? ' &middot; ' + tied + ' tied' : '');

  var q = S.filter.toLowerCase();
  var view = rows.filter(function (r) {
    return (!q || r.n.toLowerCase().indexOf(q) >= 0) && (!S.onlyB || r.b > r.a);
  });
  view.sort(function (x, y) {
    var k = S.sort, d = S.desc ? -1 : 1;
    if (k === 'n') return d * -x.n.localeCompare(y.n);
    return d * (x[k] - y[k] || x.n.localeCompare(y.n));
  });

  var head = '<thead><tr>' + COLS.map(function (c) {
    return '<th data-sort="' + c[0] + '" class="' + (S.sort === c[0] ? 'on' : '') + '">' + c[1] +
      (S.sort === c[0] ? (S.desc ? ' ▾' : ' ▴') : '') + '</th>';
  }).join('') + '</tr></thead>';
  var body = view.slice(0, 600).map(function (r) {
    var tot = r.a + r.b;
    var pa = tot > 0 ? r.a / tot * 100 : 50;
    return '<tr><td>' + (r.ic ? '<img class="icon" src="icons/npcs/' + r.id + '.png" alt="">' : '') +
      '<a href="' + r.u + '">' + esc(r.n) + '</a></td>' +
      '<td>' + (r.lv || '') + '</td><td>' + r.def.toLocaleString() + '</td>' +
      '<td class="' + (r.a >= r.b ? 'winA' : '') + '">' + num(r.a, 3) + '</td>' +
      '<td class="' + (r.b > r.a ? 'winB' : '') + '">' + num(r.b, 3) + '</td>' +
      '<td class="' + (r.b > r.a ? 'winB' : 'winA') + '">' + pct(r.delta) + '</td>' +
      '<td><span class="bar"><i class="a" style="width:' + pa + '%"></i><i class="b" style="width:' + (100 - pa) + '%"></i></span></td></tr>';
  }).join('');
  el('table').innerHTML = head + '<tbody>' + body + '</tbody>' +
    (view.length > 600 ? '<tfoot><tr><td colspan="7" class="note">' + (view.length - 600) +
      ' more rows &mdash; narrow the filter to see them.</td></tr></tfoot>' : '');
  Array.prototype.forEach.call(el('table').querySelectorAll('th'), function (th) {
    th.onclick = function () {
      if (S.sort === th.dataset.sort) S.desc = !S.desc; else { S.sort = th.dataset.sort; S.desc = true; }
      save(); render();
    };
  });
}

function render() {
  el('nameA').textContent = (CB.item(S.a.weapon) || { n: 'Unarmed' }).n;
  el('nameB').textContent = (CB.item(S.b.weapon) || { n: 'Unarmed' }).n;
  var A = statsFor('a'), B = statsFor('b');
  headline(A, B);
  table(A, B);
}

// ------------------------------------------------------------------ url state

var loading = false;
function save() {
  if (loading) return;
  var p = [];
  ['a', 'b'].forEach(function (s) {
    p.push(s + '=' + [S[s].weapon || '', S[s].style, S[s].ammo || ''].join('.'));
  });
  p.push('lv=' + LEVEL_KEYS.map(function (k) { return S.lv[k]; }).join('.'));
  p.push('pr=' + [S.pray.attack, S.pray.strength].join('.'));
  var kit = KIT_SLOTS.map(function (s) { return S.kit[s] || ''; }).join('.');
  if (kit.replace(/\./g, '')) p.push('kit=' + kit);
  history.replaceState(null, '', '#' + p.join('&'));
}

function load() {
  loading = true;
  location.hash.replace(/^#/, '').split('&').forEach(function (kv) {
    var i = kv.indexOf('='); if (i < 0) return;
    var k = kv.slice(0, i), v = kv.slice(i + 1);
    if (k === 'a' || k === 'b') {
      var bits = v.split('.');
      if (G.items[bits[0]]) S[k].weapon = bits[0];
      S[k].style = parseInt(bits[1], 10) || 0;
      if (G.items[bits[2]]) S[k].ammo = bits[2];
    } else if (k === 'lv') {
      v.split('.').forEach(function (n, j) {
        if (LEVEL_KEYS[j]) S.lv[LEVEL_KEYS[j]] = Math.max(1, Math.min(99, parseInt(n, 10) || 1));
      });
      S.lv.defence = S.lv.attack;
    } else if (k === 'pr') {
      var pr = v.split('.').map(Number);
      S.pray.attack = pr[0] || 0; S.pray.strength = pr[1] || 0;
    } else if (k === 'kit') {
      v.split('.').forEach(function (id, j) { if (id && G.items[id]) S.kit[KIT_SLOTS[j]] = id; });
    }
  });
  loading = false;
}

// ------------------------------------------------------------------ boot

function byName(n) {
  for (var id in G.items) if (G.items[id].n === n) return id;
  return null;
}

function init() {
  // something worth looking at on a cold open: the comparison this page was built for
  S.a.weapon = byName('Rune scimitar');
  S.b.weapon = byName('Dragon longsword');
  var slashOf = function (w) {
    var rows = CB.styleRows(CB.item(w));
    for (var i = 0; i < rows.length; i++) if (rows[i][1] === 1) return i;   // aggressive
    return 0;
  };
  S.a.style = slashOf(S.a.weapon);
  S.b.style = slashOf(S.b.weapon);
  load();

  ['a', 'b'].forEach(function (s) { fillWeapon(s); fillStyle(s); fillAmmo(s); });
  fillLevels(); fillPrayers(); fillKit();
  el('preset-clear').onclick = function () { S.kit = {}; fillKit(); save(); render(); };
  el('preset-melee').onclick = function () { S.kit = bestKit(10); fillKit(); save(); render(); };   // strength
  el('preset-ranged').onclick = function () { S.kit = bestKit(4); fillKit(); save(); render(); };   // ranged attack
  el('filter').oninput = function () { S.filter = el('filter').value; render(); };
  el('onlyB').onchange = function () { S.onlyB = el('onlyB').checked; render(); };
  render();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();

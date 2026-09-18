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
  // every dragonfire proc asks `inv_total(worn, antidragonbreathshield) > 0`
  var shield = String(S.eq[SLOT_SHIELD]) === String(G.antifireShield);
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

function fight(mon, ps) {
  var rows = mon.atk.map(function (e) {
    var a = npcAttack(mon, e, ps);
    a.weight = e.w;
    return a;
  });
  var perAttack = 0, cycle = 0;
  rows.forEach(function (r) { perAttack += r.weight * r.damage; cycle += r.weight * r.delay; });
  var taken = cycle > 0 ? perAttack / (cycle * TICK) : 0;

  var dealtHit = hitChance(ps.attackRoll, npcDefenceRoll(mon, ps.damagetype));
  var capped = Math.min(ps.maxhit, mon.md);
  var dealt = dealtHit * (capped / 2) / (ps.rate * TICK);
  return {
    rows: rows, taken: taken, dealt: dealt, dealtHit: dealtHit, capped: capped,
    ttk: dealt > 0 ? mon.st[5] / dealt : Infinity,
    survive: taken > 0 ? S.lv.hitpoints / taken : Infinity
  };
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
  el('bonuses').innerHTML = table('Attack bonus', atk) + table('Defence bonus', def) + table('Other', other);
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
    if (!ammo) warn = '<p class="warn small">No ammunition in the quiver &ndash; a bow without arrows will not fire.</p>';
    else if (!ammoFits(weapon, ammo)) warn = '<p class="warn small">' + esc(ammo.n) + ' does not fit a ' +
      esc(weapon.n) + ', so its ranged strength is not counted and the shot will not fire.</p>';
    else if ((ammo.lr || 0) > (weapon.lr || 0)) warn = '<p class="warn small">' + esc(weapon.n) +
      ' is not powerful enough for ' + esc(ammo.n) + ', so it will not fire &ndash; the ranged strength below ' +
      'still counts, because <code>~equip_get_bonuses</code> only checks that the ammo is the right kind.</p>';
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
      if (k === 'quest') { missing.push(it.req.quest); continue; }
      if ((S.lv[k] || 1) < it.req[k]) missing.push(k + ' ' + it.req[k]);
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
      box(time(f.survive), 'until you drop', S.lv.hitpoints + ' hitpoints, no food') +
    '</div>' +
    '<h3>What it throws at you</h3>' +
    '<table class="data"><thead><tr><th>Attack</th><th>Share</th><th>Max hit</th><th>Lands</th>' +
      '<th>Every</th><th>Average damage</th><th></th></tr></thead><tbody>' + rows + '</tbody></table>' +
    (mon.ap ? '<p class="small">Its combat script branches on something other than a die roll, so the shares ' +
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
  var out = [];
  for (var id in G.items) {
    var it = G.items[id];
    if (it.s !== slot) continue;
    if (S.f2p && it.m) continue;
    out.push([id, it]);
  }
  out.sort(function (a, b) { return a[1].n.localeCompare(b[1].n); });
  return out;
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
      for (var k in it.req) bits.push(k === 'quest' ? it.req.quest : k + ' ' + it.req[k]);
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

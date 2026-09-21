/* A player, as a thing the pages can build and read.
 *
 * Everything that describes one player -- what they wear, their levels, which
 * prayers are up, which attack style they are on -- lives in a plain state
 * object, and PlayerPanel() renders the controls for one of those into any
 * element: the equipment grid and its picker, the style tabs, the level inputs
 * with a hitpoints estimate and combat level, the prayers with what they cost
 * to keep up, and one-click loadout and level presets.  The equipment builder
 * uses one; the player-vs-player page uses two.  The arithmetic those pages
 * then do is in assets/combat.js (CB); this file is the person, not the fight.
 *
 * The hitpoints estimate is the one thing here that is a model rather than a
 * lookup.  [proc,give_combat_experience] gives four xp a point of damage to the
 * skill being trained and one-and-a-third to hitpoints, so an account's
 * hitpoints xp is a third of its attack + strength + defence + ranged xp on top
 * of the 1154 (level 10) everyone starts with.  Magic is left out on purpose:
 * its damage xp carries hitpoints the same way, but most magic xp is the base
 * a spell gives for being cast, which carries none, and the two cannot be told
 * apart from a level.
 */
'use strict';

var PP = (function () {
  var G = window.GEAR;
  var TICK = CB.TICK;
  var SLOT_WEAPON = CB.SLOT_WEAPON, SLOT_SHIELD = CB.SLOT_SHIELD, SLOT_AMMO = CB.SLOT_AMMO;
  var item = CB.item;

  var OFF = -1;                                       // no prayer chosen for a slot
  // the hash writes these by position, so new ones go on the end and old links still read
  var LEVEL_KEYS = ['attack', 'strength', 'defence', 'ranged', 'magic', 'hitpoints', 'prayer'];
  var PRAY_KINDS = ['attack', 'strength', 'defence', 'protect'];
  var LAYOUT = [[null, 0, null], [1, 2, 13], [3, 4, 5], [null, 7, null], [9, 10, 12]];

  // ---------------------------------------------------------------- helpers

  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function signed(n) { return (n > 0 ? '+' : '') + n; }
  function num(n, d) { return (Math.round(n * Math.pow(10, d)) / Math.pow(10, d)).toFixed(d); }
  function time(s) {
    if (!isFinite(s)) return 'never';
    if (s < 90) return num(s, 1) + 's';
    var m = Math.floor(s / 60), r = Math.round(s - m * 60);
    if (m < 60) return m + 'm ' + r + 's';
    var h = Math.floor(m / 60);
    return h + 'h ' + (m - h * 60) + 'm';
  }
  function clampLevel(v) { v = parseInt(v, 10); return isNaN(v) ? 1 : Math.max(1, Math.min(99, v)); }

  function newState() {
    return {
      eq: {},                                          // slot -> item id
      lv: { attack: 70, strength: 70, defence: 70, ranged: 70, magic: 50, hitpoints: 70, prayer: 43 },
      // Thick Skin lives in %prayer0, so "off" cannot be 0 as well.  Every read
      // compares the varp *and* the kind, so a varp meant for another slot is
      // ignored rather than switching something on.
      pray: { attack: OFF, strength: OFF, defence: OFF, protect: OFF },
      f2p: false,
      style: 0
    };
  }

  // ---------------------------------------------------------------- levels

  function xpFor(level) { return G.xp[clampLevel(level) - 1]; }
  function levelFor(xp) {
    var l = 1;
    for (var i = 0; i < G.xp.length; i++) if (xp >= G.xp[i]) l = i + 1;
    return l;
  }

  /* What the combat levels alone would have made of hitpoints.  Every point of
   * melee or ranged damage was G.combatXp.skill to the skill and
   * G.combatXp.hitpoints to hitpoints, and hitpoints began at level 10. */
  function estimateHitpoints(lv) {
    var trained = xpFor(lv.attack) + xpFor(lv.strength) + xpFor(lv.defence) + xpFor(lv.ranged);
    return levelFor(xpFor(10) + trained * (G.combatXp.hitpoints / G.combatXp.skill));
  }

  /* [proc,player_combat_level], integer arithmetic and all */
  function combatLevel(lv) {
    var base = 10 * (lv.defence + lv.hitpoints + Math.floor(lv.prayer / 2));
    var melee = 13 * (lv.attack + lv.strength);
    var ranged = 13 * Math.floor(lv.ranged * 3 / 2);
    var magic = 13 * Math.floor(lv.magic * 3 / 2);
    return Math.floor((base + Math.max(melee, ranged, magic)) / 40);
  }

  // ---------------------------------------------------------------- prayers

  // the protection slot holds protect_melee / protect_ranged / protect_magic
  function matchesKind(p, kind) {
    return kind === 'protect' ? p[3].indexOf('protect_') === 0 : p[3] === kind;
  }
  function prayerRow(state, kind) {
    var varp = state.pray[kind];
    if (varp === OFF) return null;
    for (var i = 0; i < G.prayers.length; i++) {
      if (G.prayers[i][0] === varp && matchesKind(G.prayers[i], kind)) return G.prayers[i];
    }
    return null;
  }
  function prayerMultiplier(state, kind) {
    var p = prayerRow(state, kind);
    return p ? p[4] : 100;
  }
  function protectedStyle(state) {
    var p = prayerRow(state, 'protect');
    return p ? p[3].replace('protect_', '') : null;        // 'melee' | 'ranged' | 'magic'
  }
  function protecting(state, style) { return protectedStyle(state) === style; }

  /* What the prayers you have on cost, from [timer,prayer_drain]: the counter
   * climbs by the drain effect every tick and sheds a point for every whole
   * `resistance` it reaches, so over a fight it settles at effect / resistance
   * a tick.  Resistance is 60 + twice the equipment prayer bonus. */
  function prayerDrainEffect(state) {
    var total = 0;
    PRAY_KINDS.forEach(function (kind) { var p = prayerRow(state, kind); if (p) total += p[5] || 0; });
    return total;
  }
  function prayerPerSecond(state, prayerBonus) {
    var effect = prayerDrainEffect(state);
    if (!effect) return 0;
    var d = G.prayerDrain;
    var resist = Math.max(d.base + (prayerBonus || 0) * d.perBonus, d.base);
    return effect / resist / TICK;
  }
  function unmetPrayers(state) {
    var out = [];
    PRAY_KINDS.forEach(function (kind) {
      var p = prayerRow(state, kind);
      if (p && state.lv.prayer < p[2]) out.push(p[1] + ' needs prayer ' + p[2]);
    });
    return out;
  }

  // ---------------------------------------------------------------- items

  function wearable(state, it) {
    var req = it.req || {};
    for (var k in req) if (k !== 'quest' && (state.lv[k] || 1) < req[k]) return false;
    return !(state.f2p && it.m);
  }
  function unmetRequirements(state) {
    var out = [];
    for (var slot in state.eq) {
      var it = item(state.eq[slot]);
      if (!it || !it.req) continue;
      var missing = [];
      for (var k in it.req) {
        // quests are left to the reader; only the level gates are checked
        if (k !== 'quest' && (state.lv[k] || 1) < it.req[k]) missing.push(k + ' ' + it.req[k]);
      }
      if (missing.length) out.push(it.n + ' needs ' + missing.join(' + '));
    }
    return out;
  }
  function slotItems(state, slot) {
    return CB.slotItems(slot).filter(function (r) { return !(state.f2p && r[1].m); });
  }
  function bonusSummary(it) {
    var parts = [];
    for (var i = 0; i < 13; i++) if (it.b[i]) parts.push(G.bonusKeys[i].replace('attack', ' att').replace('defence', ' def') + ' ' + signed(it.b[i]));
    if (it.s === SLOT_WEAPON) parts.unshift(it.r + 't');
    return parts.slice(0, 4).join(', ');
  }

  /* Everything [proc,player_combat_stat] works out for this player. */
  function stats(state) {
    return CB.playerStats(state.eq, state.lv, state.style, {
      attack: prayerMultiplier(state, 'attack'),
      strength: prayerMultiplier(state, 'strength'),
      defence: prayerMultiplier(state, 'defence')
    });
  }

  /* Put an item on, with what that displaces: a two-handed weapon covers the
   * shield slot, a shield pushes one off, a new weapon starts on its first style. */
  function equip(state, slot, id) {
    if (!id) { delete state.eq[slot]; return; }
    state.eq[slot] = id;
    var it = item(id);
    if (it.c && it.c.indexOf(SLOT_SHIELD) >= 0) delete state.eq[SLOT_SHIELD];
    if (slot === SLOT_SHIELD) {
      var w = item(state.eq[SLOT_WEAPON]);
      if (w && w.c && w.c.indexOf(SLOT_SHIELD) >= 0) delete state.eq[SLOT_WEAPON];
    }
    if (slot === SLOT_WEAPON) state.style = 0;
  }
  function applyLoadout(state, loadout) {
    state.eq = {};
    for (var slot in loadout.eq) state.eq[slot] = String(loadout.eq[slot]);
    state.style = 0;
  }
  function applyLevels(state, preset) {
    for (var k in preset.lv) state.lv[k] = preset.lv[k];
    state.lv.hitpoints = estimateHitpoints(state.lv);
  }

  // ---------------------------------------------------------------- the URL

  /* The keys the equipment builder has always written, so its links keep working. */
  function toHash(state) {
    var parts = [];
    var eq = G.slots.map(function (s) { return state.eq[s[0]] || ''; }).join('.');
    if (eq.replace(/\./g, '')) parts.push('eq=' + eq);
    parts.push('lv=' + LEVEL_KEYS.map(function (k) { return state.lv[k]; }).join('.'));
    parts.push('pr2=' + PRAY_KINDS.map(function (k) { return state.pray[k]; }).join('.'));
    if (state.style) parts.push('st=' + state.style);
    if (state.f2p) parts.push('f2p=1');
    return parts.join('&');
  }
  function fromHash(state, h) {
    var seen = false;
    String(h || '').split('&').forEach(function (kv) {
      var i = kv.indexOf('='); if (i < 0) return;
      var k = kv.slice(0, i), v = kv.slice(i + 1);
      if (k === 'eq') { seen = true; v.split('.').forEach(function (id, n) {
        if (id && G.items[id] && G.slots[n]) state.eq[G.slots[n][0]] = id;
      }); }
      else if (k === 'lv') { seen = true; v.split('.').forEach(function (n, i2) {
        if (LEVEL_KEYS[i2]) state.lv[LEVEL_KEYS[i2]] = clampLevel(n);
      }); }
      // `pr` was the old key, written when "off" was 0 -- Thick Skin's own varp,
      // so an old link cannot be read back without inventing a prayer
      else if (k === 'pr2') { seen = true; var p = v.split('.').map(Number);
        PRAY_KINDS.forEach(function (kind, i3) { state.pray[kind] = isNaN(p[i3]) ? OFF : p[i3]; }); }
      else if (k === 'st') { seen = true; state.style = parseInt(v, 10) || 0; }
      else if (k === 'f2p') { seen = true; state.f2p = v === '1'; }
    });
    return seen;
  }

  // ---------------------------------------------------------------- the picker

  // One modal for every panel on the page; it remembers who opened it.
  var pick = { panel: null, slot: null };

  function pickerDom() {
    var p = document.getElementById('pp-picker');
    if (p) return p;
    p = document.createElement('div');
    p.id = 'pp-picker';
    p.innerHTML = '<div class="pp-pickbox"><div><b class="pp-picktitle">Choose an item</b>' +
      '<button class="pp-pickclose" style="float:right">Close</button></div>' +
      '<input type="text" class="pp-pickfind" placeholder="Filter..." autocomplete="off">' +
      '<div class="pp-picklist"></div></div>';
    document.body.appendChild(p);
    p.querySelector('.pp-pickclose').onclick = closePicker;
    p.onclick = function (e) { if (e.target === p) closePicker(); };
    p.querySelector('.pp-pickfind').oninput = function () { fillPicker(this.value); };
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closePicker(); });
    return p;
  }
  function openPicker(panel, slot) {
    pick.panel = panel; pick.slot = slot;
    var p = pickerDom();
    var labels = {};
    G.slots.forEach(function (s) { labels[s[0]] = s[2]; });
    p.querySelector('.pp-picktitle').textContent = panel.label + ': choose ' + (labels[slot] || 'item').toLowerCase();
    p.querySelector('.pp-pickfind').value = '';
    p.classList.add('on');
    fillPicker('');
    p.querySelector('.pp-pickfind').focus();
  }
  function closePicker() {
    var p = document.getElementById('pp-picker');
    if (p) p.classList.remove('on');
    pick.panel = null; pick.slot = null;
  }
  function fillPicker(q) {
    if (!pick.panel) return;
    q = q.toLowerCase();
    var state = pick.panel.state;
    var list = slotItems(state, pick.slot).filter(function (r) { return !q || r[1].n.toLowerCase().indexOf(q) >= 0; });
    var html = '<div class="row" data-id=""><span class="nm">(nothing)</span></div>';
    html += list.slice(0, 400).map(function (r) {
      var id = r[0], it = r[1];
      var req = '';
      if (it.req) {
        var bits = [];
        for (var k in it.req) if (k !== 'quest') bits.push(k + ' ' + it.req[k]);
        req = ' <span class="req">(' + esc(bits.join(', ')) + ')</span>';
      }
      return '<div class="row' + (wearable(state, it) ? '' : ' locked') + '" data-id="' + id + '">' +
        (it.ic ? '<img src="icons/items/' + id + '.png" alt="">' : '') +
        '<span>' + esc(it.n) + (it.m ? ' <span class="tag members">mem</span>' : '') + req + '</span>' +
        '<span class="b">' + esc(bonusSummary(it)) + '</span></div>';
    }).join('');
    var list_el = pickerDom().querySelector('.pp-picklist');
    list_el.innerHTML = html;
    Array.prototype.forEach.call(list_el.querySelectorAll('.row'), function (d) {
      d.onclick = function () {
        var panel = pick.panel, slot = pick.slot;
        equip(panel.state, slot, d.dataset.id);
        closePicker();
        panel.render();
        panel.changed();
      };
    });
  }

  // ---------------------------------------------------------------- the panel

  var STYLE = [
    '.pp { display: grid; grid-template-columns: 300px 1fr; gap: 18px; align-items: start; }',
    '.pp.stacked { grid-template-columns: 1fr; }',
    '.pp-box { background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 12px; }',
    '.pp-box h3 { margin: 0 0 8px; } .pp-box h3 + h3, .pp-box .pp-sub { margin-top: 14px; }',
    '.pp .slotgrid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; justify-items: center; }',
    '.pp .slot { width: 100%; min-height: 52px; background: var(--panel2); border: 1px solid var(--border); border-radius: 6px;' +
    ' cursor: pointer; padding: 3px; text-align: center; color: var(--muted); font-size: 10.5px; line-height: 1.2;' +
    ' display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px; }',
    '.pp .slot:hover { border-color: var(--accent); color: var(--text); }',
    '.pp .slot.filled { color: var(--text); background: #343026; }',
    '.pp .slot.unmet { border-color: #a04030; }',
    '.pp .slot img { width: 28px; height: 28px; image-rendering: pixelated; }',
    '.pp .slot .nm { display: block; max-width: 84px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }',
    '.pp .slot.empty-cell { visibility: hidden; cursor: default; }',
    '.pp .lv { display: grid; grid-template-columns: repeat(auto-fit, minmax(96px, 1fr)); gap: 6px; }',
    '.pp .lv label { display: flex; align-items: center; gap: 5px; font-size: 12.5px; color: var(--muted); }',
    '.pp .lv input { width: 52px; padding: 4px 6px; background: var(--panel2); color: var(--text); border: 1px solid var(--border); border-radius: 4px; }',
    '.pp .prayrow { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 6px; }',
    '.pp .prayrow label { font-size: 12.5px; color: var(--muted); display: block; }',
    '.pp select, .pp button, #pp-picker button { background: var(--panel2); color: var(--text); border: 1px solid var(--border);' +
    ' border-radius: 4px; padding: 4px 9px; cursor: pointer; font: inherit; }',
    '.pp select { width: 100%; }',
    '.pp .presets button, .pp .styletabs button { margin: 0 4px 4px 0; font-size: 12.5px; }',
    '.pp .styletabs button.on { background: var(--accent); color: #000; border-color: var(--accent); }',
    '.pp .presets { margin-top: 8px; }',
    '.pp .pp-est { font-size: 12.5px; color: var(--muted); margin-top: 6px; }',
    '.pp .pp-est b { color: var(--text); }',
    '.pp .warn { color: #ffb0a0; }',
    '.pp .sub2, .pp .small { font-size: 11.5px; color: var(--muted); }',
    '#pp-picker { position: fixed; inset: 0; background: rgba(0,0,0,.66); z-index: 40; display: none; }',
    '#pp-picker.on { display: block; }',
    '#pp-picker .pp-pickbox { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); width: min(560px, 94vw);' +
    ' max-height: 80vh; background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 12px;' +
    ' display: flex; flex-direction: column; gap: 8px; }',
    '#pp-picker input { width: 100%; padding: 7px 9px; background: var(--panel2); color: var(--text); border: 1px solid var(--border); border-radius: 4px; font: inherit; }',
    '#pp-picker .pp-picklist { overflow: auto; border: 1px solid var(--border); border-radius: 4px; }',
    '#pp-picker .row { display: flex; align-items: center; gap: 8px; padding: 4px 8px; cursor: pointer; border-bottom: 1px solid #333; }',
    '#pp-picker .row:hover { background: var(--panel2); }',
    '#pp-picker .row.locked { opacity: .55; }',
    '#pp-picker .row img { width: 24px; height: 24px; image-rendering: pixelated; }',
    '#pp-picker .b { margin-left: auto; font-size: 11.5px; color: var(--muted); white-space: nowrap; }',
    '#pp-picker .req { color: #ffb0a0; }',
    '@media (max-width: 800px) { .pp { grid-template-columns: 1fr; } }'
  ].join('\n');

  function ensureStyle() {
    if (document.getElementById('pp-style')) return;
    var s = document.createElement('style');
    s.id = 'pp-style';
    s.textContent = STYLE;
    document.head.appendChild(s);
  }

  /* opts: { root, state, onChange, label, stacked }.  The panel draws into root
   * and edits state in place; onChange fires after every edit the panel made. */
  function Panel(opts) {
    ensureStyle();
    this.root = opts.root;
    this.state = opts.state;
    this.label = opts.label || 'Player';
    this.onChange = opts.onChange || function () {};
    this.stacked = !!opts.stacked;
    this.root.innerHTML =
      '<div class="pp' + (this.stacked ? ' stacked' : '') + '">' +
        '<div class="pp-box pp-gear"><h3>' + esc(this.label) + ': equipment</h3><div class="slotgrid"></div>' +
          '<div class="presets"><button class="pp-clear">Clear all</button> ' +
            G.loadouts.map(function (l, i) { return '<button class="pp-loadout" data-i="' + i + '" title="' + esc(l.note) + '">' + esc(l.n) + '</button>'; }).join('') +
          '</div>' +
          '<div class="pp-sub"><div class="styletabs"></div></div>' +
          '<label class="small" style="display:block;margin-top:8px"><input type="checkbox" class="pp-f2p"> Free-to-play items only</label>' +
        '</div>' +
        '<div class="pp-box pp-levels"><h3>Levels</h3><div class="lv"></div>' +
          '<div class="presets">' +
            G.levelPresets.map(function (p, i) { return '<button class="pp-levels-preset" data-i="' + i + '">' + esc(p.n) + '</button>'; }).join('') +
          '</div>' +
          '<div class="pp-est"></div>' +
          '<h3>Prayers</h3><div class="prayrow"></div><div class="pp-drain small"></div>' +
        '</div>' +
      '</div>';
    var self = this;
    this.q = function (sel) { return self.root.querySelector(sel); };
    this.q('.pp-clear').onclick = function () { self.state.eq = {}; self.state.style = 0; self.render(); self.changed(); };
    Array.prototype.forEach.call(this.root.querySelectorAll('.pp-loadout'), function (b) {
      b.onclick = function () { applyLoadout(self.state, G.loadouts[+b.dataset.i]); self.render(); self.changed(); };
    });
    Array.prototype.forEach.call(this.root.querySelectorAll('.pp-levels-preset'), function (b) {
      b.onclick = function () { applyLevels(self.state, G.levelPresets[+b.dataset.i]); self.render(); self.changed(); };
    });
    this.q('.pp-f2p').onchange = function () {
      self.state.f2p = this.checked;
      if (self.state.f2p) for (var slot in self.state.eq) { var it = item(self.state.eq[slot]); if (it && it.m) delete self.state.eq[slot]; }
      self.render(); self.changed();
    };
    this.render();
  }

  Panel.prototype.changed = function () { this.onChange(this); };
  Panel.prototype.stats = function () { return stats(this.state); };

  Panel.prototype.render = function () {
    this.renderSlots(); this.renderStyles(); this.renderLevels(); this.renderPrayers();
    this.q('.pp-f2p').checked = !!this.state.f2p;
  };

  Panel.prototype.renderSlots = function () {
    var self = this, state = this.state;
    var labels = {};
    G.slots.forEach(function (s) { labels[s[0]] = s[2]; });
    var html = '';
    LAYOUT.forEach(function (row) {
      row.forEach(function (slot) {
        if (slot === null) { html += '<div class="slot empty-cell"></div>'; return; }
        var id = state.eq[slot], it = item(id);
        html += '<div class="slot' + (it ? ' filled' : '') + (it && !wearable(state, it) ? ' unmet' : '') +
          '" data-slot="' + slot + '" title="' + esc(labels[slot] || '') + '">' +
          (it ? (it.ic ? '<img src="icons/items/' + id + '.png" alt="">' : '') + '<span class="nm">' + esc(it.n) + '</span>'
              : '<span class="nm">' + esc(labels[slot] || '') + '</span>') + '</div>';
      });
    });
    var grid = this.q('.slotgrid');
    grid.innerHTML = html;
    Array.prototype.forEach.call(grid.querySelectorAll('.slot[data-slot]'), function (d) {
      d.onclick = function () { openPicker(self, +d.dataset.slot); };
    });
  };

  Panel.prototype.renderStyles = function () {
    var self = this, state = this.state;
    var weapon = item(state.eq[SLOT_WEAPON]);
    var rows = CB.styleRows(weapon);
    if (state.style >= rows.length) state.style = 0;
    var box = this.q('.styletabs');
    box.innerHTML = rows.map(function (r, i) {
      return '<button data-style="' + i + '" class="' + (i === state.style ? 'on' : '') + '">' +
        esc(r[0]) + ' <span class="small">(' + esc(G.damagetypes[r[2]]) + ')</span></button>';
    }).join('') + ' <span class="small">' + (weapon ? esc(weapon.cat || 'no category') : 'unarmed') + '</span>';
    Array.prototype.forEach.call(box.querySelectorAll('button'), function (btn) {
      btn.onclick = function () { state.style = +btn.dataset.style; self.renderStyles(); self.changed(); };
    });
  };

  Panel.prototype.renderLevels = function () {
    var self = this, state = this.state;
    var box = this.q('.lv');
    box.innerHTML = LEVEL_KEYS.map(function (k) {
      return '<label>' + k.charAt(0).toUpperCase() + k.slice(1) +
        '<input type="number" min="1" max="99" data-lv="' + k + '" value="' + state.lv[k] + '"></label>';
    }).join('');
    Array.prototype.forEach.call(box.querySelectorAll('input'), function (i) {
      i.onchange = i.oninput = function () {
        state.lv[i.dataset.lv] = clampLevel(i.value);
        self.renderEstimate(); self.renderSlots(); self.renderPrayers();
        self.changed();
      };
    });
    this.renderEstimate();
  };

  Panel.prototype.renderEstimate = function () {
    var self = this, lv = this.state.lv;
    var est = estimateHitpoints(lv);
    var box = this.q('.pp-est');
    box.innerHTML = 'Combat level <b>' + combatLevel(lv) + '</b>. ' +
      'The combat xp behind attack ' + lv.attack + ', strength ' + lv.strength + ', defence ' + lv.defence +
      ' and ranged ' + lv.ranged + ' would have brought hitpoints to <b>' + est + '</b>' +
      (est === lv.hitpoints ? ', which it is.' : ' &mdash; <button class="pp-est-apply">set hitpoints to ' + est + '</button>') +
      ' <span class="sub2">(' + G.combatXp.hitpoints + ' hitpoints xp per ' + G.combatXp.skill +
      ' to the skill, from level 10; magic left out, since casting xp carries none)</span>';
    var b = box.querySelector('.pp-est-apply');
    if (b) b.onclick = function () {
      self.state.lv.hitpoints = est;
      self.renderLevels(); self.changed();
    };
  };

  Panel.prototype.renderPrayers = function () {
    var self = this, state = this.state;
    var kinds = [['attack', 'Attack'], ['strength', 'Strength'], ['defence', 'Defence']];
    var html = kinds.map(function (k) {
      var opts = ['<option value="' + OFF + '">None</option>'];
      G.prayers.filter(function (p) { return p[3] === k[0]; }).forEach(function (p) {
        opts.push('<option value="' + p[0] + '"' + (state.pray[k[0]] === p[0] ? ' selected' : '') + '>' +
          esc(p[1]) + ' (+' + (p[4] - 100) + '%, level ' + p[2] + ')</option>');
      });
      return '<label>' + k[1] + '<select data-pray="' + k[0] + '">' + opts.join('') + '</select></label>';
    });
    var opts = ['<option value="' + OFF + '">None</option>'];
    G.prayers.filter(function (p) { return p[3].indexOf('protect_') === 0; }).forEach(function (p) {
      opts.push('<option value="' + p[0] + '"' + (state.pray.protect === p[0] ? ' selected' : '') + '>' +
        esc(p[1]) + ' (level ' + p[2] + ')</option>');
    });
    html.push('<label>Protection<select data-pray="protect">' + opts.join('') + '</select></label>');
    var box = this.q('.prayrow');
    box.innerHTML = html.join('');
    Array.prototype.forEach.call(box.querySelectorAll('select'), function (s) {
      s.onchange = function () { state.pray[s.dataset.pray] = +s.value; self.renderPrayers(); self.changed(); };
    });
    // what they cost while they are on
    var effect = prayerDrainEffect(state), note = this.q('.pp-drain');
    var unmet = unmetPrayers(state);
    if (!effect) note.innerHTML = 'Nothing on, nothing draining.';
    else {
      var bonus = CB.bonuses(state.eq)[11];
      var rate = prayerPerSecond(state, bonus);
      var d = G.prayerDrain;
      note.innerHTML = 'Draining <b>' + num(rate, 2) + '</b> prayer points a second &mdash; a bar of ' + state.lv.prayer +
        ' lasts ' + time(state.lv.prayer / rate) + '. <span class="sub2">Drain effect ' + effect + ' over a resistance of ' +
        (d.base + bonus * d.perBonus) + ' (' + d.base + ' + ' + d.perBonus + ' &times; ' + bonus + ' prayer bonus)' +
        (bonus > 0 ? '' : ' &mdash; prayer bonus on your gear would stretch it') + '.</span>';
    }
    if (unmet.length) note.innerHTML += '<div class="warn">Not yet: ' + esc(unmet.join('; ')) + '.</div>';
  };

  return {
    OFF: OFF, LEVEL_KEYS: LEVEL_KEYS, PRAY_KINDS: PRAY_KINDS,
    esc: esc, signed: signed, num: num, time: time,
    newState: newState, Panel: Panel,
    xpFor: xpFor, levelFor: levelFor, estimateHitpoints: estimateHitpoints, combatLevel: combatLevel,
    prayerMultiplier: prayerMultiplier, protectedStyle: protectedStyle, protecting: protecting,
    prayerDrainEffect: prayerDrainEffect, prayerPerSecond: prayerPerSecond, unmetPrayers: unmetPrayers,
    wearable: wearable, unmetRequirements: unmetRequirements, slotItems: slotItems, bonusSummary: bonusSummary,
    stats: stats, equip: equip, applyLoadout: applyLoadout, applyLevels: applyLevels,
    toHash: toHash, fromHash: fromHash, closePicker: closePicker
  };
})();

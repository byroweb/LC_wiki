// Chunk picker. Unlock 64x64 map squares; work out what a player who starts with
// nothing could actually do with that set. Data: data/chunks.js (window.CHUNKDATA).
(function () {
  var D = window.CHUNKDATA;
  if (!D) {
    document.getElementById('report').innerHTML = '<h1>Chunk picker</h1><p class="blocked">data/chunks.js is missing. ' +
      'Rebuild the site without --no-tables: <code>python build/build.py --content source/content</code></p>';
    return;
  }
  var RESKIND = ['tree', 'rock', 'fishing_spot'];
  var SKILL_ORDER = ['woodcutting', 'mining', 'fishing', 'firemaking', 'cooking', 'smithing', 'crafting', 'fletching'];
  var SLOTS = [['hat', 'Head'], ['back', 'Cape'], ['front', 'Amulet'], ['righthand', 'Weapon'], ['torso', 'Body'],
               ['lefthand', 'Shield'], ['legs', 'Legs'], ['hands', 'Hands'], ['feet', 'Feet'], ['ring', 'Ring'], ['quiver', 'Ammo']];
  var STYLES = [['melee', 'Melee'], ['ranged', 'Ranged'], ['magic', 'Magic']];
  // e = [stabA,slashA,crushA,magicA,rangeA, stabD,slashD,crushD,magicD,rangeD, strength, rangeBonus, prayer]
  function offence(e, style) {
    if (style === 'melee') return Math.max(e[0], e[1], e[2]) + e[10];
    if (style === 'ranged') return e[4] + e[11];
    return e[3];
  }
  function defence(e) { return e[5] + e[6] + e[7] + e[8] + e[9]; }
  function bisScore(e, style) { return offence(e, style) * 100 + defence(e) + e[12]; }
  var START = '50_50';                     // the chunk you spawn in (Lumbridge, 3221/3218)

  var canvas = document.getElementById('map'), ctx = canvas.getContext('2d');
  var report = document.getElementById('report'), coordsEl = document.getElementById('coords');
  var state = { cx: 3222, cz: 3218, zoom: 0.9, level: 0, under: false, unlocked: new Set([START]), style: 'melee', bisTicked: false, f2p: false };
  var TICK_KEY = 'ls_chunk_ticked', ticked = {}, lastHave = {};
  try { ticked = JSON.parse(localStorage.getItem(TICK_KEY) || '{}') || {}; } catch (e) { ticked = {}; }
  function saveTicks() { try { localStorage.setItem(TICK_KEY, JSON.stringify(ticked)); } catch (e) {} }

  // ---- the run in progress, kept in this browser
  //
  // The unlocked set already lives in the URL so a run can be shared, but the
  // hash only survives a reload -- follow a link to an item page and come back
  // and it is gone.  So the run is also written here on every change and read
  // back when the page opens without one in the URL.  A shared link therefore
  // still wins over your own run, and only overwrites it once you actually
  // change something, at which point the link has become the run you are on.
  var RUN_KEY = 'ls_chunk_run', RUN_VERSION = 1;
  function saveRun() {
    try {
      localStorage.setItem(RUN_KEY, JSON.stringify({
        v: RUN_VERSION, c: Array.from(state.unlocked).sort(),
        x: Math.round(state.cx), z: Math.round(state.cz), zoom: +state.zoom.toFixed(2),
        level: state.level, under: state.under
      }));
    } catch (e) {}
  }
  function loadRun() {
    var r;
    try { r = JSON.parse(localStorage.getItem(RUN_KEY) || 'null'); } catch (e) { r = null; }
    if (!r || r.v !== RUN_VERSION || !Array.isArray(r.c)) return false;
    // a chunk key can disappear when the content is rebuilt, so check each one
    state.unlocked = new Set(r.c.filter(function (ck) { return ck in D.chunks; }));
    if (typeof r.x === 'number') state.cx = r.x;
    if (typeof r.z === 'number') state.cz = r.z;
    if (typeof r.zoom === 'number') state.zoom = Math.max(0.25, Math.min(8, r.zoom));
    if (typeof r.level === 'number') state.level = r.level;
    state.under = !!r.under;
    return true;
  }
  var lastFight = [], lastTasks = [];
  function tickCount() {
    return Object.keys(lastHave).filter(function (i) { return ticked[i]; }).length +
           lastFight.filter(function (n) { return ticked['npc:' + n]; }).length +
           lastTasks.filter(function (k) { return ticked[k]; }).length;
  }
  var images = {};

  function partsOf(ck) { var c = D.chunks[ck]; return (c && c.p) || []; }
  function hasContent(ck) { return partsOf(ck).length > 0; }

  // ---- reverse indexes for the search box
  var chunkOfNpc = {}, chunkOfItem = {}, placeIndex = [];
  Object.keys(D.chunks).forEach(function (ck) {
    var c = D.chunks[ck];
    partsOf(ck).forEach(function (p) {
      (p.n || []).forEach(function (x) { (chunkOfNpc[x[0]] = chunkOfNpc[x[0]] || []).push(ck); });
      (p.o || []).forEach(function (x) { (chunkOfItem[x[0]] = chunkOfItem[x[0]] || []).push(ck); });
      (p.r || []).forEach(function (x) { (chunkOfItem[x[1]] = chunkOfItem[x[1]] || []).push(ck); });
    });
    if (c.a) placeIndex.push({ t: c.a, ck: ck });
    (c.a2 || []).forEach(function (n) { placeIndex.push({ t: n, ck: ck }); });
  });

  function chunkXZ(ck) { var p = ck.split('_'); return [+p[0], +p[1]]; }
  function chunkAt(x, z) { return (x >> 6) + '_' + (z >> 6); }
  function pxPerTile() { return state.zoom; }
  function toScreen(x, z) { var s = pxPerTile(); return [canvas.width / 2 + (x - state.cx) * s, canvas.height / 2 - (z - state.cz) * s]; }
  function toWorld(px, py) { var s = pxPerTile(); return [state.cx + (px - canvas.width / 2) / s, state.cz - (py - canvas.height / 2) / s]; }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function itemLink(id) { var i = D.items[id]; return i ? '<a href="' + i.u + '">' + esc(i.n) + '</a>' : esc(id); }
  function itemName(id) { var i = D.items[id]; return i ? i.n : id; }
  function npcLink(id) { var n = D.npcs[id]; return n ? '<a href="' + n.u + '">' + esc(n.n) + '</a>' : esc(id); }
  function questName(q) {                      // "Hero's Quest", not "the Hero's Quest quest"
    var n = (D.quests[q] || {}).n || q;
    return /quest/i.test(n) ? n : n + ' quest';
  }
  function article(n) { return (/^[aeiou]/i.test(n) ? 'an ' : 'a ') + n; }
  function chunkName(ck) { var c = D.chunks[ck]; return c && c.a ? c.a + ' (' + ck + ')' : ck; }

  function getImage(mx, mz) {
    var key = state.level + '/' + mx + '_' + mz;
    if (images[key]) return images[key];
    var rec = { img: new Image(), ok: false, missing: false };
    rec.img.onload = function () { rec.ok = true; draw(); };
    rec.img.onerror = function () { rec.missing = true; };
    rec.img.src = 'tiles/' + key + '.png';
    images[key] = rec;
    return rec;
  }

  function resize() {
    var w = canvas.parentNode;
    canvas.width = Math.max(1, w.clientWidth); canvas.height = Math.max(1, w.clientHeight);
    draw();
  }
  window.addEventListener('resize', resize);
  if (window.ResizeObserver) new ResizeObserver(resize).observe(canvas.parentNode);

  function draw() {
    var W = canvas.width, H = canvas.height, s = pxPerTile();
    ctx.fillStyle = '#0b0b0a'; ctx.fillRect(0, 0, W, H);
    ctx.imageSmoothingEnabled = false;
    var tl = toWorld(0, 0), br = toWorld(W, H);
    var mx0 = Math.floor(tl[0] / 64) - 1, mx1 = Math.floor(br[0] / 64) + 1;
    var mz0 = Math.floor(br[1] / 64) - 1, mz1 = Math.floor(tl[1] / 64) + 1;
    for (var mx = mx0; mx <= mx1; mx++) for (var mz = mz0; mz <= mz1; mz++) {
      var ck = mx + '_' + mz;
      if (!(ck in D.chunks)) continue;
      var p = toScreen(mx * 64, mz * 64 + 64), w = 64 * s;
      var rec = getImage(mx, mz + (state.under ? 100 : 0));
      if (rec.ok) ctx.drawImage(rec.img, p[0], p[1], w, w);
      else { ctx.fillStyle = '#151513'; ctx.fillRect(p[0], p[1], w, w); }
      var on = state.unlocked.has(ck);
      if (!on) { ctx.fillStyle = 'rgba(0,0,0,.66)'; ctx.fillRect(p[0], p[1], w, w); }
      ctx.strokeStyle = on ? 'rgba(125,220,125,.95)' : 'rgba(255,255,255,.10)';
      ctx.lineWidth = on ? 2 : 1;
      ctx.strokeRect(p[0] + 0.5, p[1] + 0.5, w - 1, w - 1);
      if (w > 74) {
        var c = D.chunks[ck];
        ctx.textAlign = 'center'; ctx.textBaseline = 'top';
        ctx.font = 'bold 11px Segoe UI'; ctx.lineWidth = 3; ctx.strokeStyle = 'rgba(0,0,0,.9)';
        var label = (c && c.a) ? c.a : ck;
        ctx.strokeText(label, p[0] + w / 2, p[1] + 3);
        ctx.fillStyle = on ? '#bdf5bd' : 'rgba(232,226,210,.75)';
        ctx.fillText(label, p[0] + w / 2, p[1] + 3);
        if (c && c.a && w > 120) {
          ctx.font = '10px Segoe UI'; ctx.strokeText(ck, p[0] + w / 2, p[1] + 17);
          ctx.fillStyle = 'rgba(232,226,210,.55)'; ctx.fillText(ck, p[0] + w / 2, p[1] + 17);
        }
      }
    }
  }

  // ---------------------------------------------------------------- the solver
  function solve() {
    var npcIn = {}, objIn = {}, standIn = {}, stations = {}, shopIn = [], questFolders = {}, locIn = {};
    var pending = [];                         // parts behind a barrier, waiting for the key
    function absorb(p) {
      (p.l || []).forEach(function (l) { locIn[l] = 1; });
      (p.n || []).forEach(function (x) { npcIn[x[0]] = (npcIn[x[0]] || 0) + x[1]; });
      (p.o || []).forEach(function (x) { objIn[x[0]] = (objIn[x[0]] || 0) + x[1]; });
      (p.r || []).forEach(function (x) { var k = RESKIND[x[0]] + ':' + x[1]; standIn[k] = (standIn[k] || 0) + x[2]; });
      if (p.st) Object.keys(p.st).forEach(function (k) { stations[k] = (stations[k] || 0) + p.st[k]; });
      (p.sh || []).forEach(function (sh) { if (shopIn.indexOf(sh) < 0) shopIn.push(sh); });
      (p.q || []).forEach(function (q) { questFolders[q] = 1; });
    }
    state.unlocked.forEach(function (ck) {
      partsOf(ck).forEach(function (p) {
        if (p.g) pending.push({ ck: ck, p: p });
        else absorb(p);
      });
    });

    var trainable = {};
    var have = {};
    function allowed(i) { return !state.f2p || !(D.items[i] && D.items[i].m); }
    function give(item, via, ref) {
      if (have[item] || !allowed(item)) return false;
      have[item] = { via: via, ref: ref };
      return true;
    }
    Object.keys(objIn).forEach(function (i) { give(i, 'spawn', objIn[i]); });
    Object.keys(npcIn).forEach(function (n) { (D.npcs[n] && D.npcs[n].d || []).forEach(function (i) { give(i, 'drop', n); }); });

    var canFire = false;
    function stationOk(r) {
      if (!r.st) return true;
      if (r.st === 'range_or_fire') return !!(stations.range || stations.fire || canFire);
      if (r.st === 'range') return !!stations.range;              // ovens only: a camp fire will not do
      if (r.st === 'fire') return !!(stations.fire || canFire);
      if (r.st === 'neither') return false;                       // needs wrapping/other preparation
      if (r.st.indexOf('loc:') === 0) return !!locIn[r.st.slice(4)];
      if (r.st.indexOf('npc:') === 0) return !!npcIn[r.st.slice(4)];
      if (r.st === 'tree' || r.st === 'rock' || r.st === 'fishing_spot') return !!standIn[r.st + ':' + r.p];
      return !!stations[r.st];
    }
    function missingOf(r) {
      var m = { inputs: [], tools: [] };
      r.in.forEach(function (i) { if (!have[i[0]]) m.inputs.push(i[0]); });
      r.t.forEach(function (g) { if (!g.some(function (t) { return have[t]; })) m.tools.push(g); });
      return m;
    }

    // You start every skill at level 1. A skill only opens up if something in your
    // chunks can be done at level 1 and gives xp; then you can grind it to any level.
    function levelOk(r) { return r.lv <= 1 || trainable[r.s]; }
    // a quest step only counts if every chunk the quest's npcs live in is unlocked
    function questOk(r) {
      if (!r.q) return true;
      var q = D.quests[r.q];
      if (!q) return true;              // quest with no placed npcs: nothing to require
      return q.c.every(function (ck) { return state.unlocked.has(ck); });
    }

    // a barrier opens as soon as the closure can supply any one of its alternatives
    function questDone(q) {
      var d = D.quests[q];
      return !!d && d.c.every(function (ck) { return state.unlocked.has(ck); });
    }
    // quest points you could earn: every quest whose npcs all live in unlocked chunks
    var pointsHere = 0;
    Object.keys(D.questpoints || {}).forEach(function (q) { if (questDone(q)) pointsHere += D.questpoints[q]; });
    function gateOpen(g) {
      return g.some(function (alt) {
        return (alt.i || []).every(function (i) { return have[i]; }) &&
               (alt.q || []).every(questDone) &&
               // a level door opens if the skill can be trained from 1 inside these chunks
               (alt.s || []).every(function (sl) { return sl[1] <= 1 || trainable[sl[0]]; }) &&
               (!alt.qp || pointsHere >= alt.qp);
      });
    }
    function openGates() {
      var opened = false;
      pending = pending.filter(function (x) {
        if (!gateOpen(x.p.g)) return true;
        absorb(x.p);
        Object.keys(x.p.o || {}).length;
        (x.p.o || []).forEach(function (o) { give(o[0], 'spawn', o[1]); });
        (x.p.n || []).forEach(function (n) { (D.npcs[n[0]] && D.npcs[n[0]].d || []).forEach(function (i) { give(i, 'drop', n[0]); }); });
        opened = true;
        return false;
      });
      return opened;
    }

    var done = {}, changed = true, guard = 0;
    while (changed && guard++ < 60) {
      changed = false;
      if (openGates()) changed = true;
      if (have[D.coins]) shopIn.forEach(function (inv) {
        (D.shops[inv].i || []).forEach(function (i) { if (give(i, 'shop', inv)) changed = true; });
      });
      D.recipes.forEach(function (r, idx) {
        if (done[idx] || !stationOk(r)) return;
        var m = missingOf(r);
        if (m.inputs.length || m.tools.length) return;
        if (!levelOk(r) || !questOk(r)) return;
        if (r.p && !allowed(r.p)) return;
        done[idx] = true; changed = true;
        if (r.lv <= 1 && r.xp > 0 && !trainable[r.s]) trainable[r.s] = true;
        if (r.fire) canFire = true;
        if (r.p) give(r.p, 'craft', r.s);
      });
    }

    var doable = [], blocked = [], seenBlocked = {};
    D.recipes.forEach(function (r, idx) {
      if (done[idx]) { doable.push(r); return; }
      var m = missingOf(r), stOk = stationOk(r);
      var key = r.s + '|' + (r.p || 'fire');
      if (seenBlocked[key]) return;
      var res = (r.st === 'tree' || r.st === 'rock' || r.st === 'fishing_spot') ? (standIn[r.st + ':' + r.p] || 0) : 0;
      if (stOk && !m.inputs.length && !m.tools.length && !questOk(r)) {
        seenBlocked[key] = 1;                          // everything but the quest
        blocked.push({ r: r, missing: m, res: res, quest: true });
        return;
      }
      if (stOk && !m.inputs.length && !m.tools.length && !levelOk(r)) {
        seenBlocked[key] = 1;                          // everything but the level, and no way to train it here
        blocked.push({ r: r, missing: m, res: res, level: true });
        return;
      }
      if (!r.st || !stOk) return;                      // otherwise only things physically in your chunks
      seenBlocked[key] = 1;
      blocked.push({ r: r, missing: m, res: res, level: false });
    });
    // a resource sitting in your chunks that you cannot harvest is the most useful thing to say
    blocked.sort(function (a, b) { return b.res - a.res || a.r.lv - b.r.lv; });

    return { npcIn: npcIn, objIn: objIn, standIn: standIn, stations: stations, shopIn: shopIn,
             questFolders: questFolders, have: have, doable: doable, blocked: blocked, canFire: canFire,
             trainable: trainable, locked: pending, points: pointsHere };
  }

  // ---------------------------------------------------------------- the report
  function render() {
    var u = Array.from(state.unlocked).sort();
    if (!u.length) {
      report.innerHTML = '<h1>Chunk picker</h1><p class="muted">Click squares on the map to unlock them. ' +
        'You start with nothing: every tool, weapon and ingredient has to come from a chunk you have unlocked.</p>' +
        '<p><button id="start-lumb">Start at Lumbridge</button></p>';
      document.getElementById('start-lumb').onclick = function () { state.unlocked.add(START); sync(); };
      draw();
      return;
    }
    var R = solve();
    var h = [];
    var places = u.map(chunkName);
    h.push('<h1>Chunk picker</h1>');
    lastHave = R.have;
    h.push('<div class="stat"><div><b>' + u.length + '</b>chunk' + (u.length > 1 ? 's' : '') + '</div>' +
           '<div><b id="tickn">' + tickCount() + '</b>ticked off</div>' +
           '<div><b>' + Object.keys(R.have).length + '</b>items</div>' +
           '<div><b>' + R.doable.length + '</b>things to make</div>' +
           '<div><b>' + (R.stations.bank || 0) + '</b>bank booths</div></div>');
    h.push('<div><label class="small"><input type="checkbox" id="f2ponly"' + (state.f2p ? ' checked' : '') +
           '> Free-to-play items only</label></div>');
    h.push('<div>' + u.map(function (ck) {
      return '<span class="pill"><b>' + esc(D.chunks[ck] && D.chunks[ck].a ? D.chunks[ck].a : ck) + '</b> ' + ck +
             '<button data-lock="' + ck + '" title="Lock this chunk">&times;</button></span>';
    }).join('') + '</div>');

    var iBis = h.length;
    // ---- best in slot for each combat style
    var wearable = Object.keys(R.have).filter(function (i) { return D.items[i] && D.items[i].e; });
    h.push('<h2>Best in slot</h2><div>' + STYLES.map(function (st) {
      return '<button class="styletab' + (state.style === st[0] ? ' on' : '') + '" data-style="' + st[0] + '">' + st[1] + '</button>';
    }).join('') + '</div>');
    h.push('<label class="small"><input type="checkbox" id="bisticked"' + (state.bisTicked ? ' checked' : '') +
           '> Only show items I have ticked off</label>');
    var pool = wearable.filter(function (i) { return !state.bisTicked || ticked[i]; });
    if (!pool.length) {
      h.push('<p class="muted small">Nothing wearable ' + (state.bisTicked ? 'ticked off yet' : 'in reach yet') + '.</p>');
    } else {
      var twoHanded = null;
      h.push('<table class="bis">');
      SLOTS.forEach(function (sl) {
        var best = null;
        pool.forEach(function (i) {
          var d = D.items[i];
          if (d.w !== sl[0]) return;
          if (!best || bisScore(d.e, state.style) > bisScore(D.items[best].e, state.style)) best = i;
        });
        if (sl[0] === 'righthand' && best && D.items[best].w2 === 'lefthand') twoHanded = best;
        var cell;
        if (!best) cell = '<span class="muted">None</span>';
        else {
          var d = D.items[best], off = offence(d.e, state.style), def = defence(d.e);
          cell = '<img class="icon" src="icons/items/' + d.id + '.png" alt="">' + itemLink(best) +
                 ' <span class="muted">' + (off >= 0 ? '+' : '') + off + ' att, ' + (def >= 0 ? '+' : '') + def + ' def' +
                 (d.lr ? ', needs ' + d.lr : '') + (d.m ? ', members' : '') + '</span>';
          if (sl[0] === 'lefthand' && twoHanded) cell += ' <span class="blocked">(not with ' + esc(D.items[twoHanded].n) + ')</span>';
        }
        h.push('<tr><td class="slot">' + sl[1] + '</td><td>' + cell + '</td></tr>');
      });
      h.push('</table>');
      h.push('<p class="small muted">Ranked on ' + (state.style === 'melee' ? 'best melee attack bonus plus strength' :
             state.style === 'ranged' ? 'ranged attack plus ranged strength' : 'magic attack') +
             ', then total defence. Bonuses come from the item configs.</p>');
      if (!Object.keys(R.npcIn).some(function (n) { return D.npcs[n] && D.npcs[n].a; })) {
        h.push('<p class="small blocked">Nothing here to fight, so no combat levels to wear any of it.</p>');
      }
    }

    var iDo = h.length;
    // ---- what you can do, by skill
    var bySkill = {};
    R.doable.forEach(function (r) { (bySkill[r.s] = bySkill[r.s] || []).push(r); });
    lastTasks = [];
    var skills = Object.keys(bySkill).sort(function (a, b) {
      var ia = SKILL_ORDER.indexOf(a), ib = SKILL_ORDER.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
    h.push('<h2>What you can do</h2>');
    if (!skills.length) h.push('<p class="muted">Nothing yet: no tool in these chunks can be obtained from scratch.</p>');
    skills.forEach(function (sk) {
      // merge rows that make the same thing at the same level (several tree/rock tables share a product)
      var merged = {}, order = [];
      bySkill[sk].forEach(function (r) {
        var k = (r.p || 'fire') + '|' + r.lv;
        if (!merged[k]) { merged[k] = { r: r, xp: [r.xp], count: 0 }; order.push(k); }
        else if (merged[k].xp.indexOf(r.xp) < 0) merged[k].xp.push(r.xp);
        if (r.st === 'tree' || r.st === 'rock' || r.st === 'fishing_spot') merged[k].count += R.standIn[r.st + ':' + r.p] || 0;
      });
      var rows = order.map(function (k) { return merged[k]; })
        .sort(function (a, b) { return a.r.lv - b.r.lv || (a.r.p || '').localeCompare(b.r.p || ''); });
      h.push('<h3>' + esc(sk) + ' <span class="muted">(' + rows.length + ')</span></h3><ul class="tight">');
      rows.forEach(function (m) {
        var r = m.r, where = '';
        if (r.st === 'tree' || r.st === 'rock' || r.st === 'fishing_spot') where = ' &middot; ' + m.count + ' ' + (r.st === 'fishing_spot' ? 'spots' : r.st + 's');
        else if (r.st && r.st.indexOf('loc:') === 0) where = ' &middot; at the ' + esc(r.st.slice(4).replace(/_/g, ' '));
        else if (r.st && r.st.indexOf('npc:') === 0) where = ' &middot; from ' + esc((D.npcs[r.st.slice(4)] || {}).n || r.st.slice(4));
        else if (r.st) where = ' &middot; ' + esc(r.st.replace(/_/g, ' '));
        var xp = m.xp.filter(function (x) { return x; }).sort(function (a, b) { return a - b; });
        var k = 'do:' + sk + ':' + (r.p || 'fire');
        lastTasks.push(k);
        h.push('<li class="' + (ticked[k] ? 'done' : '') + '"><input type="checkbox" class="tick" data-i="' + esc(k) + '"' +
               (ticked[k] ? ' checked' : '') + '>' + (r.p ? itemLink(r.p) : 'light a fire') + (r.n > 1 ? ' &times;' + r.n : '') +
               ' <span class="muted">lvl ' + r.lv + (xp.length ? ', ' + (xp.length > 1 ? xp[0] + '-' + xp[xp.length - 1] : xp[0]) + ' xp' : '') + where + '</span></li>');
      });
      h.push('</ul>');
    });

    var iBlocked = h.length;
    // ---- blocked: it is here, but you cannot use it yet
    if (R.blocked.length) {
      var needQuest = R.blocked.filter(function (b) { return b.quest; });
      var needLevel = R.blocked.filter(function (b) { return b.level; });
      var needTool = R.blocked.filter(function (b) { return !b.level && !b.quest && b.missing.tools.length; });
      var needInput = R.blocked.filter(function (b) { return !b.level && !b.quest && !b.missing.tools.length; });
      function line(b) {
        var need = [];
        if (b.quest) {
          var q = D.quests[b.r.q] || { n: b.r.q, c: [] };
          var miss = q.c.filter(function (ck) { return !state.unlocked.has(ck); });
          need.push('the ' + esc(questName(b.r.q)) + ' &mdash; unlock ' + miss.map(function (ck) {
            return '<a href="#" data-add="' + ck + '">' + esc(chunkName(ck)) + '</a>';
          }).join(', '));
        }
        if (b.level) need.push(esc(b.r.s) + ' level ' + b.r.lv + ', and nothing here trains ' + esc(b.r.s));
        b.missing.tools.forEach(function (g) {
          var names = g.map(itemName);          // already cheapest-first
          need.push(names.length > 1 ? 'one of ' + names.join(', ') : article(names[0]));
        });
        b.missing.inputs.forEach(function (i) { need.push(itemName(i)); });
        var where = '';
        if (b.r.st === 'tree' || b.r.st === 'rock' || b.r.st === 'fishing_spot') where = ' <span class="muted">(' + (R.standIn[b.r.st + ':' + b.r.p] || 0) + ' ' + (b.r.st === 'fishing_spot' ? 'spots' : b.r.st + 's') + ' here)</span>';
        return '<li class="blocked">' + (b.r.p ? itemLink(b.r.p) : 'fire') + where + ' <span class="muted">' + esc(b.r.s) +
               ' lvl ' + b.r.lv + '</span> &mdash; needs ' + esc(need.join(', ')) + '</li>';
      }
      h.push('<h2>Here but not usable yet</h2>');
      if (needTool.length) {
        h.push('<p class="small">Missing a tool &mdash; unlock a chunk that sells or drops one:</p><ul class="tight">' +
               needTool.map(line).join('') + '</ul>');
      }
      if (needQuest.length) {
        h.push('<p class="small">Waiting on a quest, and the quest needs chunks you have not unlocked:</p><ul class="tight">' +
               needQuest.map(line).join('') + '</ul>');
      }
      if (needLevel.length) {
        h.push('<details><summary>' + needLevel.length + ' need a level you cannot train to here</summary><ul class="tight">' +
               needLevel.map(line).join('') + '</ul></details>');
      }
      if (needInput.length) {
        h.push('<details><summary>' + needInput.length + ' need an ingredient you cannot get yet</summary><ul class="tight">' +
               needInput.map(line).join('') + '</ul></details>');
      }
    }

    var iBarrier = h.length;
    // ---- barriers still shut
    if (R.locked.length) {
      h.push('<h2>Behind a barrier</h2><ul class="tight">');
      R.locked.forEach(function (x) {
        var holds = [];
        if (x.p.sh) holds.push(x.p.sh.length + ' shop' + (x.p.sh.length > 1 ? 's' : ''));
        if (x.p.n) holds.push(x.p.n.length + ' npc types');
        if (x.p.r) holds.push(x.p.r.length + ' resources');
        if (x.p.st) holds.push(Object.keys(x.p.st).join(', '));
        var keys = x.p.g.map(function (alt) {
          var bits = (alt.i || []).map(itemName);
          (alt.s || []).forEach(function (sl) { bits.push(sl[0] + ' level ' + sl[1] + ', trained from scratch here'); });
          if (alt.qp) bits.push(alt.qp + ' quest points (your chunks can earn ' + R.points + ')');
          (alt.q || []).forEach(function (q) {
            var d = D.quests[q];
            var miss = d ? d.c.filter(function (ck) { return !state.unlocked.has(ck); }) : [];
            bits.push('the ' + esc(questName(q)) + (miss.length ? ' (unlock ' + miss.map(function (ck) {
              return '<a href="#" data-add="' + ck + '">' + esc(chunkName(ck)) + '</a>';
            }).join(', ') + ')' : ''));
          });
          return bits.join(' + ');
        }).join(' or ');
        h.push('<li class="blocked">' + esc(chunkName(x.ck)) + ' &mdash; needs ' + keys +
               (holds.length ? ' <span class="muted">(holds ' + esc(holds.join(', ')) + ')</span>' : '') + '</li>');
      });
      h.push('</ul><p class="small muted">Satisfy what it asks for and it opens by itself.</p>');
    }

    var iItems = h.length;
    // ---- items
    var byVia = { spawn: [], drop: [], shop: [], craft: [] };
    Object.keys(R.have).forEach(function (i) { byVia[R.have[i].via].push(i); });
    h.push('<h2>Items you can get <span class="muted">(' + Object.keys(R.have).length + ')</span>' +
           '<button class="clearticks" id="cleart">clear ticks</button></h2>' +
           '<p class="small muted">Tick items and monsters off as you go; ticks are remembered in this browser.</p>');
    [['spawn', 'off the ground'], ['drop', 'from kills'], ['shop', 'from shops'], ['craft', 'by making them']].forEach(function (p) {
      var list = byVia[p[0]].sort(function (a, b) { return itemName(a).localeCompare(itemName(b)); });
      if (!list.length) return;
      h.push('<details><summary>' + list.length + ' ' + p[1] + '</summary><ul class="tight">' +
             list.map(function (i) {
               var v = R.have[i], via = '';
               if (v.via === 'drop') via = ' <span class="muted">&larr; ' + esc(D.npcs[v.ref] ? D.npcs[v.ref].n : v.ref) + '</span>';
               if (v.via === 'shop') via = ' <span class="muted">&larr; ' + esc(D.shops[v.ref].t) + '</span>';
               if (v.via === 'spawn') via = ' <span class="muted">&times;' + v.ref + '</span>';
               return '<li class="' + (ticked[i] ? 'done' : '') + '"><input type="checkbox" class="tick" data-i="' + esc(i) + '"' +
                      (ticked[i] ? ' checked' : '') + '>' + itemLink(i) + via + '</li>';
             }).join('') + '</ul></details>');
    });
    if (!R.have[D.coins]) h.push('<p class="small blocked">No source of coins, so the shops in your chunks are useless.</p>');

    var iChunks = h.length;
    // ---- what is in the chunks
    h.push('<h2>In your chunks</h2>');
    var stationBits = Object.keys(R.stations).sort().map(function (k) { return R.stations[k] + ' ' + k.replace(/_/g, ' '); });
    h.push('<p class="small">' + (stationBits.length ? esc(stationBits.join(', ')) : '<span class="muted">no stations</span>') + '</p>');
    if (R.shopIn.length) h.push('<details><summary>' + R.shopIn.length + ' shops</summary><ul class="tight">' +
      R.shopIn.map(function (inv) { return '<li>' + esc(D.shops[inv].t) + ' <span class="muted">&mdash; ' + esc(D.npcs[D.shops[inv].o] ? D.npcs[D.shops[inv].o].n : D.shops[inv].o) + '</span></li>'; }).join('') + '</ul></details>');
    var fight = Object.keys(R.npcIn).filter(function (n) { return D.npcs[n] && D.npcs[n].a; })
      .sort(function (a, b) { return (D.npcs[a].l || 0) - (D.npcs[b].l || 0); });
    lastFight = fight;
    if (fight.length) h.push('<details><summary>' + fight.length + ' things to fight</summary><ul class="tight">' +
      fight.map(function (n) {
        var k = 'npc:' + n;
        return '<li class="' + (ticked[k] ? 'done' : '') + '"><input type="checkbox" class="tick" data-i="' + esc(k) + '"' +
               (ticked[k] ? ' checked' : '') + '>' + npcLink(n) + ' <span class="muted">lvl ' + (D.npcs[n].l || '?') + ' &times;' + R.npcIn[n] + '</span></li>';
      }).join('') + '</ul></details>');
    var qs = Object.keys(R.questFolders).filter(function (q) {
      return D.quests[q] && D.quests[q].c.every(function (ck) { return state.unlocked.has(ck); });
    });
    if (qs.length) h.push('<p class="small">Quests fully inside your set: ' + qs.map(function (q) { return esc(D.quests[q].n); }).join(', ') + '</p>');

    var iTail = h.length;
    // ---- doors out
    var exits = [];
    u.forEach(function (ck) {
      ((D.chunks[ck] || {}).e || []).forEach(function (e) {
        if (!state.unlocked.has(e[2])) exits.push([ck, e[0], e[1], e[2]]);
      });
    });
    if (exits.length) {
      h.push('<details><summary>' + exits.length + ' ways out into locked chunks</summary><ul class="tight">' +
        exits.map(function (e) {
          return '<li>' + esc(e[2]) + ' <span class="muted">(' + esc(e[1]) + ')</span> &rarr; <a href="#" data-add="' + e[3] + '">' + esc(chunkName(e[3])) + '</a></li>';
        }).join('') + '</ul></details>');
    }
    h.push('<p class="small muted">Chunk = one 64&times;64 map square. Every floor counts, and the dungeon under a square comes with it.</p>');

    report.innerHTML = h.slice(0, iBis)
      .concat(h.slice(iDo, iBlocked),        // what you can do
              h.slice(iBarrier, iItems),     // behind a barrier
              h.slice(iItems, iChunks),      // items you can get
              h.slice(iChunks, iTail),       // in your chunks
              h.slice(iBis, iDo),            // best in slot
              h.slice(iBlocked, iBarrier),   // here but not usable yet
              h.slice(iTail))                // ways out, footnote
      .join('');
    report.querySelectorAll('input.tick').forEach(function (cb) {
      cb.onchange = function () {
        var k = cb.getAttribute('data-i');
        if (cb.checked) ticked[k] = 1; else delete ticked[k];
        saveTicks();
        cb.parentNode.className = cb.checked ? 'done' : '';
        var n = document.getElementById('tickn'); if (n) n.textContent = tickCount();
      };
    });
    var f2 = document.getElementById('f2ponly');
    if (f2) f2.onchange = function () { state.f2p = f2.checked; render(); };
    report.querySelectorAll('button.styletab').forEach(function (b) {
      b.onclick = function () { state.style = b.getAttribute('data-style'); render(); };
    });
    var bt = document.getElementById('bisticked');
    if (bt) bt.onchange = function () { state.bisTicked = bt.checked; render(); };
    var ct = document.getElementById('cleart');
    if (ct) ct.onclick = function () { ticked = {}; saveTicks(); render(); };
    report.querySelectorAll('[data-lock]').forEach(function (b) {
      b.onclick = function () { state.unlocked.delete(b.getAttribute('data-lock')); sync(); };
    });
    report.querySelectorAll('[data-add]').forEach(function (a) {
      a.onclick = function (ev) { ev.preventDefault(); state.unlocked.add(a.getAttribute('data-add')); sync(); };
    });
    draw();
  }

  function sync() { render(); updateHash(); }

  // ---------------------------------------------------------------- interaction
  var drag = null, moved = false;
  canvas.addEventListener('mousedown', function (ev) { drag = { x: ev.clientX, y: ev.clientY, cx: state.cx, cz: state.cz }; moved = false; });
  window.addEventListener('mousemove', function (ev) {
    if (drag) {
      var s = pxPerTile(), dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) moved = true;
      state.cx = drag.cx - dx / s; state.cz = drag.cz + dy / s;
      draw();
    } else if (ev.target === canvas) {
      var r = canvas.getBoundingClientRect(), w = toWorld(ev.clientX - r.left, ev.clientY - r.top);
      var ck = chunkAt(Math.floor(w[0]), Math.floor(w[1]));
      coordsEl.textContent = ck + (D.chunks[ck] && D.chunks[ck].a ? ' - ' + D.chunks[ck].a : '') + (ck in D.chunks ? '' : ' (empty)');
    }
  });
  window.addEventListener('mouseup', function (ev) {
    if (drag && !moved && ev.target === canvas) {
      var r = canvas.getBoundingClientRect(), w = toWorld(ev.clientX - r.left, ev.clientY - r.top);
      var ck = chunkAt(Math.floor(w[0]), Math.floor(w[1]));
      if (ck in D.chunks) { state.unlocked.has(ck) ? state.unlocked.delete(ck) : state.unlocked.add(ck); sync(); }
    }
    drag = null;
  });
  canvas.addEventListener('wheel', function (ev) {
    ev.preventDefault();
    var r = canvas.getBoundingClientRect(), before = toWorld(ev.clientX - r.left, ev.clientY - r.top);
    state.zoom = Math.max(0.25, Math.min(8, state.zoom * (ev.deltaY < 0 ? 1.25 : 0.8)));
    var after = toWorld(ev.clientX - r.left, ev.clientY - r.top);
    state.cx += before[0] - after[0]; state.cz += before[1] - after[1];
    draw(); updateHash();
  }, { passive: false });

  var levelsEl = document.getElementById('levels');
  function renderLevels() {
    levelsEl.innerHTML = '';
    for (var i = 0; i < 4; i++) (function (i) {
      var b = document.createElement('button'); b.textContent = i; if (i === state.level && !state.under) b.className = 'on';
      b.onclick = function () { state.level = i; state.under = false; renderLevels(); draw(); };
      levelsEl.appendChild(b);
    })(i);
    var d = document.createElement('button'); d.textContent = 'Dungeon'; if (state.under) d.className = 'on';
    d.onclick = function () { state.under = !state.under; state.level = 0; renderLevels(); draw(); };
    levelsEl.appendChild(d);
  }
  renderLevels();
  document.getElementById('zin').onclick = function () { state.zoom = Math.min(8, state.zoom * 1.5); draw(); };
  document.getElementById('zout').onclick = function () { state.zoom = Math.max(0.25, state.zoom / 1.5); draw(); };
  /* Reset and Random both start a run over; they differ only in what you start
   * it with.  Neither adds to the run in progress -- rolling a chunk is asking
   * where to begin, and a roll that kept the last one would be a list of two.
   * Clicking squares is still how a run grows.
   *
   * The ticks go with the chunks because they are the run's progress and not any
   * one chunk's: they key on items, npcs and tasks, so carrying them over would
   * open the new chunk with half its checklist already ticked off.  "Clear
   * ticks" still clears those on their own. */
  function startRun(chunks, centre) {
    state.unlocked = new Set(chunks);
    ticked = {};
    saveTicks();
    // go() syncs, which saves the run: forgetting it instead would let the next
    // load fall back to the default and put the spawn chunk straight back.
    go(centre);
  }
  document.getElementById('reset').onclick = function () {
    startRun([], START);        // the spawn chunk goes too, so reset leaves nothing
  };
  document.getElementById('rand').onclick = function () {
    // the pool leaves out what is already unlocked, so a roll always moves you
    var pool = Object.keys(D.chunks).filter(function (ck) {
      return !state.unlocked.has(ck) && hasContent(ck);
    });
    if (!pool.length) return;
    var ck = pool[Math.floor(Math.random() * pool.length)];
    startRun([ck], ck);
  };
  function go(ck) {
    var p = chunkXZ(ck);
    state.cx = p[0] * 64 + 32; state.cz = p[1] * 64 + 32;
    if (state.zoom < 1) state.zoom = 1.6;
    sync(); draw();
  }

  // ---- search
  var find = document.getElementById('find'), sugg = document.getElementById('sugg');
  find.addEventListener('input', function () {
    var q = find.value.trim().toLowerCase();
    if (!q) { sugg.style.display = 'none'; return; }
    var hits = [];
    placeIndex.forEach(function (p) { if (p.t.toLowerCase().indexOf(q) >= 0) hits.push({ t: p.t, ck: p.ck, k: 'place' }); });
    Object.keys(chunkOfNpc).forEach(function (n) {
      var d = D.npcs[n]; if (d && d.n.toLowerCase().indexOf(q) >= 0) hits.push({ t: d.n + (d.l ? ' (lvl ' + d.l + ')' : ''), ck: chunkOfNpc[n][0], k: 'npc', more: chunkOfNpc[n].length });
    });
    Object.keys(chunkOfItem).forEach(function (i) {
      var d = D.items[i]; if (d && d.n.toLowerCase().indexOf(q) >= 0) hits.push({ t: d.n, ck: chunkOfItem[i][0], k: 'item', more: chunkOfItem[i].length });
    });
    hits = hits.slice(0, 30);
    sugg.innerHTML = hits.map(function (x, i) {
      return '<div data-i="' + i + '">' + esc(x.t) + ' <span class="muted">' + x.k + ' &middot; ' + chunkName(x.ck) + (x.more > 1 ? ' +' + (x.more - 1) : '') + '</span></div>';
    }).join('') || '<div class="muted">No matches</div>';
    sugg.style.display = 'block';
    sugg._hits = hits;
  });
  sugg.addEventListener('click', function (ev) {
    var d = ev.target.closest('div[data-i]'); if (!d) return;
    var x = sugg._hits[+d.getAttribute('data-i')];
    sugg.style.display = 'none'; find.value = '';
    state.cx = chunkXZ(x.ck)[0] * 64 + 32; state.cz = chunkXZ(x.ck)[1] * 64 + 32;
    if (state.zoom < 1) state.zoom = 1.6;
    draw();
  });

  // ---- hash: #c=50_50,49_50&x=..&z=..&zoom=..
  function writeHash() {
    history.replaceState(null, '', '#c=' + Array.from(state.unlocked).sort().join(',') +
      '&x=' + Math.round(state.cx) + '&z=' + Math.round(state.cz) + '&zoom=' + state.zoom.toFixed(2));
  }
  function updateHash() { saveRun(); writeHash(); }
  function readHash() {
    var h = {};
    location.hash.replace(/^#/, '').split('&').forEach(function (kv) { var p = kv.split('='); if (p[0]) h[p[0]] = decodeURIComponent(p[1] || ''); });
    if (h.c !== undefined) state.unlocked = new Set(h.c ? h.c.split(',').filter(function (ck) { return ck in D.chunks; }) : []);
    if (h.x) state.cx = +h.x;
    if (h.z) state.cz = +h.z;
    if (h.zoom) state.zoom = Math.max(0.25, Math.min(8, +h.zoom));
  }

  window.addEventListener('hashchange', function () { readHash(); render(); draw(); });
  // a shared link (or a reload) carries the set; without one, resume where you left off
  var hadHash = /(^|&)c=/.test(location.hash.replace(/^#/, ''));
  readHash();
  if (!hadHash) loadRun();
  resize();
  render();
  // write the hash so the run is shareable straight away, but do not save on the
  // way in: opening someone else's link should not overwrite your own run until
  // you change something, which is when it becomes the run you are on
  hadHash ? writeHash() : updateHash();
})();

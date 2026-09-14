// Minimap-style world explorer. Data comes from data/map_data.js (window.MAPDATA).
(function () {
  var D = window.MAPDATA;
  var TILE = 4;                       // rendered pixels per game tile in the PNGs
  var canvas = document.getElementById('map');
  var ctx = canvas.getContext('2d');
  var popup = document.getElementById('popup');
  var popupBody = document.getElementById('popup-body');
  var tip = document.getElementById('tip');
  var coordsEl = document.getElementById('coords');
  var statusEl = document.getElementById('status');

  var state = { cx: 3222, cz: 3218, zoom: 2, level: 0, layers: { npc: true, obj: true, fn: true, portal: true, rc: true, stand: true, lab: true }, sel: null, selEnt: null, hi: null, pulse: 0, back: null, anim: null };
  var images = {};                     // key -> {img, ok}
  var fnIcons = {};
  var spawnsByLevel = [[], [], [], []];   // entries: {x,z,kind,id,count,name}
  var byTile = {};

  // ---- index spawns
  D.npc_spawns.forEach(function (s) {
    var n = D.npcs[s[3]]; if (!n) return;
    spawnsByLevel[s[0]].push({ x: s[1], z: s[2], kind: 'npc', id: s[3], name: n.n });
  });
  D.obj_spawns.forEach(function (s) {
    var o = D.objs[s[3]]; if (!o) return;
    spawnsByLevel[s[0]].push({ x: s[1], z: s[2], kind: 'obj', id: s[3], count: s[4], name: o.n });
  });
  D.functions.forEach(function (s) {
    var extra = s[5] || {};
    spawnsByLevel[s[0]].push({ x: s[1], z: s[2], kind: 'fn', id: s[3], name: s[4], dests: extra.dests || null, ores: extra.ores || null });
  });
  function destDir(e, d) {           // 'up', 'down' or 'across' relative to the entrance
    if (d[2] >= e.z + 1000 || d[0] < e.level) return 'down';
    if (d[2] <= e.z - 1000 || d[0] > e.level) return 'up';
    return 'across';
  }
  (D.stands || []).forEach(function (s, i) {
    // [level,x,z,kind,resource,itemId,count,radius,levelReq,xp,tool,bank,bankDist,bbox]
    spawnsByLevel[s[0]].push({ x: s[1], z: s[2], level: s[0], kind: 'stand', id: i, name: s[4] + ' (' + s[3].replace('_', ' ') + ' x' + s[6] + ')', skind: s[3], res: s[4], itemId: s[5], count: s[6], radius: s[7], lvl: s[8], xp: s[9], tool: s[10], bank: s[11], bankDist: s[12], bbox: s[13] });
  });
  (D.rc || []).forEach(function (s, i) {
    // [level, x, z, kind, rune, runeItemId, talismanItemId, levelReq, xp, members, dests]
    spawnsByLevel[s[0]].push({ x: s[1], z: s[2], level: s[0], kind: 'rc', id: i, name: s[4] + (s[3] === 'altar' ? ' altar' : ' ruins'), rc: s[3], rune: s[4], runeId: s[5], talismanId: s[6], lvl: s[7], xp: s[8], members: s[9], rcdests: s[10] });
  });
  (D.portals || []).forEach(function (s, i) {
    var e = { x: s[1], z: s[2], level: s[0], kind: 'portal', id: i, name: s[3], op: s[4], dests: s[5] };
    e.up = null; e.down = null;
    e.dests.forEach(function (d) { var dir = destDir(e, d); if (dir === 'up' && !e.up) e.up = d; if (dir === 'down' && !e.down) e.down = d; });
    e.twoWay = !!(e.up && e.down);
    spawnsByLevel[s[0]].push(e);
  });
  for (var lv = 0; lv < 4; lv++) spawnsByLevel[lv].forEach(function (e) {
    var k = lv + ':' + e.x + ':' + e.z; (byTile[k] = byTile[k] || []).push(e);
  });

  // ---- search index
  var searchIndex = [];
  Object.keys(D.npcs).forEach(function (id) { var n = D.npcs[id]; searchIndex.push({ t: n.n + (n.l ? ' (lvl ' + n.l + ')' : ''), kind: 'npc', id: +id }); });
  Object.keys(D.objs).forEach(function (id) { var o = D.objs[id]; searchIndex.push({ t: o.n, kind: 'obj', id: +id }); });
  D.labels.forEach(function (l, i) { searchIndex.push({ t: l[0], kind: 'area', id: i }); });

  function resize() { canvas.width = window.innerWidth; canvas.height = window.innerHeight; draw(); }
  window.addEventListener('resize', resize);

  function scale() { return TILE * state.zoom / 4; }        // screen px per tile? no: px per tile = zoom
  function pxPerTile() { return state.zoom; }
  function toScreen(x, z) {
    var s = pxPerTile();
    return [canvas.width / 2 + (x - state.cx) * s, canvas.height / 2 - (z - state.cz) * s];
  }
  function toWorld(px, py) {
    var s = pxPerTile();
    return [state.cx + (px - canvas.width / 2) / s, state.cz - (py - canvas.height / 2) / s];
  }

  function getImage(level, mx, mz) {
    var key = level + '/' + mx + '_' + mz;
    if (images[key]) return images[key];
    var rec = { img: null, ok: false, missing: false };
    images[key] = rec;
    if (D.tiles[level].indexOf(mx + '_' + mz) === -1) { rec.missing = true; return rec; }
    var img = new Image();
    img.onload = function () { rec.ok = true; draw(); };
    img.onerror = function () { rec.missing = true; };
    img.src = 'tiles/' + key + '.png';
    rec.img = img;
    return rec;
  }

  function draw() {
    var W = canvas.width, H = canvas.height, s = pxPerTile();
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, W, H);
    ctx.imageSmoothingEnabled = false;
    var tl = toWorld(0, 0), br = toWorld(W, H);
    var mx0 = Math.floor(tl[0] / 64), mx1 = Math.floor(br[0] / 64);
    var mz0 = Math.floor(br[1] / 64), mz1 = Math.floor(tl[1] / 64);
    for (var mx = mx0; mx <= mx1; mx++) for (var mz = mz0; mz <= mz1; mz++) {
      if (mx < D.bounds.mx0 || mx > D.bounds.mx1 || mz < D.bounds.mz0 || mz > D.bounds.mz1) continue;
      var rec = getImage(state.level, mx, mz);
      var p = toScreen(mx * 64, mz * 64 + 64);
      if (rec.ok) ctx.drawImage(rec.img, p[0], p[1], 64 * s, 64 * s);
      else if (!rec.missing) { ctx.fillStyle = '#111'; ctx.fillRect(p[0], p[1], 64 * s, 64 * s); }
    }
    // spawns
    var list = spawnsByLevel[state.level];
    var r = Math.max(2, s * 0.55);
    var minX = tl[0] - 2, maxX = br[0] + 2, minZ = br[1] - 2, maxZ = tl[1] + 2;
    // map function icons first
    if (state.layers.fn && s >= 1.5) list.forEach(function (e) {
      if (e.kind !== 'fn' || e.x < minX || e.x > maxX || e.z < minZ || e.z > maxZ) return;
      var p = toScreen(e.x + 0.5, e.z + 0.5);
      var ic = fnIcon(e.id);
      var size = Math.max(15, Math.min(30, s * 4));
      if (ic.ok) ctx.drawImage(ic.img, p[0] - size / 2, p[1] - size / 2, size, size);
    });
    var showIcons = s >= 4;                       // close enough: draw the item icon inside the red dot
    if (state.layers.portal) list.forEach(function (e) {
      if (e.kind !== 'portal' || e.x < minX || e.x > maxX || e.z < minZ || e.z > maxZ) return;
      var p = toScreen(e.x + 0.5, e.z + 0.5);
      var half = Math.max(3, Math.min(9, s * 0.6));
      ctx.strokeStyle = 'rgba(0,0,0,.85)'; ctx.lineWidth = 1;
      ctx.font = 'bold ' + Math.round(half * 1.6) + 'px Segoe UI'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      if (e.twoWay) {                                     // two stacked buttons: up on top, down below
        ctx.fillStyle = '#3ee6ff';
        ctx.fillRect(p[0] - half, p[1] - half * 2, half * 2, half * 2);
        ctx.strokeRect(p[0] - half, p[1] - half * 2, half * 2, half * 2);
        ctx.fillStyle = '#ffb347';
        ctx.fillRect(p[0] - half, p[1], half * 2, half * 2);
        ctx.strokeRect(p[0] - half, p[1], half * 2, half * 2);
        if (half >= 6) { ctx.fillStyle = '#003'; ctx.fillText('↑', p[0], p[1] - half + 1); ctx.fillText('↓', p[0], p[1] + half + 1); }
      } else {
        var dir = destDir(e, e.dests[0]);
        ctx.fillStyle = dir === 'down' ? '#3ee6ff' : dir === 'up' ? '#ffb347' : '#b8ff5c';
        ctx.fillRect(p[0] - half, p[1] - half, half * 2, half * 2);
        ctx.strokeRect(p[0] - half, p[1] - half, half * 2, half * 2);
        if (half >= 6) { ctx.fillStyle = '#003'; ctx.fillText(dir === 'down' ? '↓' : dir === 'up' ? '↑' : '→', p[0], p[1] + 1); }
      }
    });
    // NPC wander / max range for the selected NPC spawn
    if (state.selEnt && state.selEnt.kind === 'npc' && state.selEnt.level === state.level) {
      var se = state.selEnt, nd0 = D.npcs[se.id];
      if (nd0) {
        if (nd0.ch) {
          var a = toScreen(se.x - nd0.mr, se.z + nd0.mr + 1), b = toScreen(se.x + nd0.mr + 1, se.z - nd0.mr);
          ctx.setLineDash([6, 4]); ctx.strokeStyle = 'rgba(255,160,60,.9)'; ctx.lineWidth = 1.5;
          ctx.strokeRect(a[0], a[1], b[0] - a[0], b[1] - a[1]); ctx.setLineDash([]);
        }
        if (nd0.mv !== 'nomove') {
          var wa = toScreen(se.x - nd0.wr, se.z + nd0.wr + 1), wb = toScreen(se.x + nd0.wr + 1, se.z - nd0.wr);
          ctx.fillStyle = 'rgba(255,212,0,.18)'; ctx.fillRect(wa[0], wa[1], wb[0] - wa[0], wb[1] - wa[1]);
          ctx.strokeStyle = 'rgba(255,212,0,.9)'; ctx.lineWidth = 1.5; ctx.strokeRect(wa[0], wa[1], wb[0] - wa[0], wb[1] - wa[1]);
        }
      }
    }
    if (state.layers.stand && s >= 1.5) list.forEach(function (e) {
      if (e.kind !== 'stand' || e.x < minX - 20 || e.x > maxX + 20 || e.z < minZ - 20 || e.z > maxZ + 20) return;
      var p = toScreen(e.x + 0.5, e.z + 0.5);
      var half = Math.max(7, Math.min(16, s * 1.1));
      var col = e.skind === 'tree' ? '#7ddc7d' : e.skind === 'rock' ? '#c9c9c9' : '#7fc8ff';
      if (state.selEnt && state.selEnt.kind === 'stand' && state.selEnt.id === e.id) {   // footprint of the selected stand
        var a = toScreen(e.bbox[0], e.bbox[3] + 1), b = toScreen(e.bbox[2] + 1, e.bbox[1]);
        ctx.fillStyle = 'rgba(125,220,125,.15)'; ctx.fillRect(a[0], a[1], b[0] - a[0], b[1] - a[1]);
        ctx.strokeStyle = col; ctx.lineWidth = 1.5; ctx.strokeRect(a[0], a[1], b[0] - a[0], b[1] - a[1]);
      }
      ctx.fillStyle = col;
      ctx.beginPath(); ctx.roundRect ? ctx.roundRect(p[0] - half, p[1] - half, half * 2, half * 2, 3) : ctx.rect(p[0] - half, p[1] - half, half * 2, half * 2); ctx.fill();
      ctx.strokeStyle = 'rgba(0,0,0,.8)'; ctx.lineWidth = 1; ctx.stroke();
      var ic = e.itemId != null ? itemIcon(e.itemId) : null;
      if (ic && ic.ok) { var sz = half * 1.7; ctx.drawImage(ic.img, p[0] - sz / 2, p[1] - sz / 2, sz, sz); }
      if (e.count > 1 && half >= 9) {
        ctx.font = 'bold 10px Segoe UI'; ctx.textAlign = 'right'; ctx.textBaseline = 'bottom';
        ctx.lineWidth = 3; ctx.strokeStyle = 'rgba(0,0,0,.9)'; ctx.strokeText(e.count, p[0] + half + 2, p[1] + half + 2);
        ctx.fillStyle = '#fff'; ctx.fillText(e.count, p[0] + half + 2, p[1] + half + 2);
      }
    });
    if (state.layers.rc) list.forEach(function (e) {
      if (e.kind !== 'rc' || e.x < minX || e.x > maxX || e.z < minZ || e.z > maxZ) return;
      var p = toScreen(e.x + 0.5, e.z + 0.5);
      var rr = Math.max(7, Math.min(18, s * 1.2));
      ctx.fillStyle = e.rc === 'altar' ? '#c08cff' : 'rgba(192,140,255,.35)';
      ctx.beginPath(); ctx.arc(p[0], p[1], rr, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = '#c08cff'; ctx.lineWidth = e.rc === 'altar' ? 1 : 2; ctx.stroke();
      var ic = e.runeId != null ? itemIcon(e.runeId) : null;
      if (ic && ic.ok) { var sz = rr * 1.7; ctx.drawImage(ic.img, p[0] - sz / 2, p[1] - sz / 2, sz, sz); }
    });
    if (state.layers.obj) list.forEach(function (e) {
      if (e.kind !== 'obj' || e.x < minX || e.x > maxX || e.z < minZ || e.z > maxZ) return;
      var o = D.objs[e.id];
      if (showIcons && o && o.ic) {
        var ic = itemIcon(e.id);
        var rr = Math.max(9, Math.min(20, s * 1.3));
        var p = toScreen(e.x + 0.5, e.z + 0.5);
        ctx.fillStyle = '#ff3b3b';
        ctx.beginPath(); ctx.arc(p[0], p[1], rr, 0, Math.PI * 2); ctx.fill();
        ctx.strokeStyle = 'rgba(0,0,0,.8)'; ctx.lineWidth = 1; ctx.stroke();
        if (ic.ok) { var size = rr * 1.6; ctx.drawImage(ic.img, p[0] - size / 2, p[1] - size / 2, size, size); }
      } else dot(e, r, '#ff3b3b');
    });
    if (state.layers.npc) list.forEach(function (e) {
      if (e.kind !== 'npc' || e.x < minX || e.x > maxX || e.z < minZ || e.z > maxZ) return;
      var nd = D.npcs[e.id];
      if (showIcons && nd && nd.ic) {
        var ic = npcIcon(e.id);
        var rr = Math.max(9, Math.min(20, s * 1.3));
        var p = toScreen(e.x + 0.5, e.z + 0.5);
        ctx.fillStyle = '#ffd400';
        ctx.beginPath(); ctx.arc(p[0], p[1], rr, 0, Math.PI * 2); ctx.fill();
        ctx.strokeStyle = 'rgba(0,0,0,.8)'; ctx.lineWidth = 1; ctx.stroke();
        if (ic.ok) {
          var size = rr * 1.9;
          ctx.save(); ctx.beginPath(); ctx.arc(p[0], p[1], rr - 0.5, 0, Math.PI * 2); ctx.clip();
          ctx.drawImage(ic.img, p[0] - size / 2, p[1] - size / 2, size, size);
          ctx.restore();
        }
      } else dot(e, r, '#ffd400');
    });
    // highlighted entity spawns (from search / deep link)
    if (state.hi) list.forEach(function (e) {
      if (e.kind !== state.hi.kind || e.id !== state.hi.id) return;
      var p = toScreen(e.x + 0.5, e.z + 0.5);
      ctx.strokeStyle = '#7dff7d'; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(p[0], p[1], r + 4 + Math.sin(state.pulse / 6) * 2, 0, Math.PI * 2); ctx.stroke();
    });
    // labels
    if (state.layers.lab) {
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      D.labels.forEach(function (l) {
        var size = l[3];
        if (size === 2 && s > 6) return;
        if (size === 0 && s < 1.2) return;
        if (l[1] < minX - 40 || l[1] > maxX + 40 || l[2] < minZ - 10 || l[2] > maxZ + 10) return;
        var p = toScreen(l[1], l[2]);
        var fs = size === 2 ? 20 : size === 1 ? 15 : 12;
        ctx.font = (size === 2 ? 'bold ' : '') + fs + 'px Segoe UI, sans-serif';
        ctx.lineWidth = 3; ctx.strokeStyle = 'rgba(0,0,0,.9)';
        ctx.strokeText(l[0], p[0], p[1]);
        ctx.fillStyle = size === 2 ? '#ffe680' : size === 1 ? '#ffffff' : '#e8e2d2';
        ctx.fillText(l[0], p[0], p[1]);
      });
    }
    // selection expansion ring
    if (state.sel) {
      var p = toScreen(state.sel.x + 0.5, state.sel.z + 0.5);
      var t = Math.min(1, (performance.now() - state.sel.t0) / 350);
      var rr = r + 3 + t * 10;
      ctx.strokeStyle = 'rgba(255,255,255,' + (1 - t * 0.6) + ')'; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(p[0], p[1], rr, 0, Math.PI * 2); ctx.stroke();
      if (t < 1) requestAnimationFrame(draw);
    }
    if (state.hi) { state.pulse++; if (!state._pulseTimer) state._pulseTimer = setTimeout(function () { state._pulseTimer = null; draw(); }, 60); }
  }

  function dot(e, r, colour) {
    var p = toScreen(e.x + 0.5, e.z + 0.5);
    ctx.fillStyle = colour;
    ctx.beginPath(); ctx.arc(p[0], p[1], r, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = 'rgba(0,0,0,.8)'; ctx.lineWidth = 1; ctx.stroke();
  }

  var npcIcons = {};
  function npcIcon(id) {
    if (npcIcons[id]) return npcIcons[id];
    var rec = { img: new Image(), ok: false };
    rec.img.onload = function () { rec.ok = true; draw(); };
    rec.img.src = 'icons/npcs/' + id + '.png';
    npcIcons[id] = rec;
    return rec;
  }
  var itemIcons = {};
  function itemIcon(id) {
    if (itemIcons[id]) return itemIcons[id];
    var rec = { img: new Image(), ok: false };
    rec.img.onload = function () { rec.ok = true; draw(); };
    rec.img.src = 'icons/items/' + id + '.png';
    itemIcons[id] = rec;
    return rec;
  }
  function fnIcon(id) {
    if (fnIcons[id]) return fnIcons[id];
    var rec = { img: new Image(), ok: false };
    rec.img.onload = function () { rec.ok = true; draw(); };
    rec.img.src = 'icons/mapfunction_' + id + '.png';
    fnIcons[id] = rec;
    return rec;
  }

  // ---- interaction
  var drag = null, moved = false;
  canvas.addEventListener('mousedown', function (ev) { drag = { x: ev.clientX, y: ev.clientY, cx: state.cx, cz: state.cz }; moved = false; canvas.classList.add('dragging'); });
  window.addEventListener('mousemove', function (ev) {
    if (drag) {
      var s = pxPerTile();
      var dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
      state.cx = drag.cx - dx / s; state.cz = drag.cz + dy / s;
      draw(); updateHash();
    } else hover(ev);
    var w = toWorld(ev.clientX, ev.clientY);
    coordsEl.textContent = 'x ' + Math.floor(w[0]) + ', z ' + Math.floor(w[1]) + ', level ' + state.level + ' | zoom ' + state.zoom.toFixed(1);
  });
  window.addEventListener('mouseup', function (ev) {
    if (drag && !moved && ev.target === canvas) click(ev);
    drag = null; canvas.classList.remove('dragging');
  });
  canvas.addEventListener('wheel', function (ev) {
    ev.preventDefault();
    var before = toWorld(ev.clientX, ev.clientY);
    var f = ev.deltaY < 0 ? 1.25 : 0.8;
    state.zoom = Math.max(0.5, Math.min(24, state.zoom * f));
    var after = toWorld(ev.clientX, ev.clientY);
    state.cx += before[0] - after[0]; state.cz += before[1] - after[1];
    draw(); updateHash();
  }, { passive: false });
  // touch
  var touch = null;
  canvas.addEventListener('touchstart', function (ev) { if (ev.touches.length === 1) { var t = ev.touches[0]; touch = { x: t.clientX, y: t.clientY, cx: state.cx, cz: state.cz }; } }, { passive: true });
  canvas.addEventListener('touchmove', function (ev) { if (touch && ev.touches.length === 1) { var t = ev.touches[0], s = pxPerTile(); state.cx = touch.cx - (t.clientX - touch.x) / s; state.cz = touch.cz + (t.clientY - touch.y) / s; draw(); } }, { passive: true });
  canvas.addEventListener('touchend', function () { touch = null; });

  function hitTest(px, py) {
    var s = pxPerTile(), best = null, bestD = Math.max(8, s * 0.8) + 2;
    var list = spawnsByLevel[state.level];
    for (var i = 0; i < list.length; i++) {
      var e = list[i];
      if (e.kind === 'npc' && !state.layers.npc) continue;
      if (e.kind === 'obj' && !state.layers.obj) continue;
      if (e.kind === 'fn' && !state.layers.fn) continue;
      if (e.kind === 'portal' && !state.layers.portal) continue;
      if (e.kind === 'rc' && !state.layers.rc) continue;
      if (e.kind === 'stand' && (!state.layers.stand || s < 1.5)) continue;
      var p = toScreen(e.x + 0.5, e.z + 0.5);
      var d = Math.hypot(p[0] - px, p[1] - py);
      if (e.twoWay) { var hh = Math.max(3, Math.min(9, s * 0.6)); d = Math.hypot(p[0] - px, Math.max(0, Math.abs(p[1] - py) - hh)); }
      if (d < bestD) { bestD = d; best = e; }
    }
    if (best && best.twoWay) best = Object.assign({}, best, { pick: py < toScreen(best.x + 0.5, best.z + 0.5)[1] ? 'up' : 'down' });
    return best;
  }
  function hover(ev) {
    var e = hitTest(ev.clientX, ev.clientY);
    if (!e) { tip.style.display = 'none'; canvas.style.cursor = 'grab'; return; }
    var group = byTile[state.level + ':' + e.x + ':' + e.z] || [e];
    var names = {};
    group.forEach(function (g) {
      var label = g.name;
      if (g.kind === 'npc' && D.npcs[g.id].l) label += ' (lvl ' + D.npcs[g.id].l + ')';
      if (g.kind === 'rc' || g.kind === 'stand') label += ' (level ' + g.lvl + ')';
      if (g.kind === 'npc' && D.npcs[g.id].fs) label += ': ' + D.npcs[g.id].fs.map(function (f) { return f[0].replace(/^Raw /, '') + ' (' + f[1] + ')'; }).join(', ');
      if (g.twoWay) label += (e.pick === 'up' ? ' ↑ up to ' + destName(g.up) : ' ↓ down to ' + destName(g.down));
      else if (g.dests) label += ' → ' + destName(g.dests[0]);
      if (g.ores) label += g.ores.length ? ': ' + g.ores.map(function (o) { return o[0] + ' (' + o[3] + ')'; }).join(', ') : ' (no rocks found nearby)';
      names[label] = 1;
    });
    tip.textContent = Object.keys(names).join(', ');
    tip.style.display = 'block';
    var tw = tip.offsetWidth, th = tip.offsetHeight;
    var left = Math.max(4, Math.min(canvas.width - tw - 4, ev.clientX - tw / 2));
    var top = ev.clientY - th - 12;
    if (top < 4) top = ev.clientY + 16;   // no room above: fall back to below
    tip.style.left = left + 'px'; tip.style.top = top + 'px';
    canvas.style.cursor = 'pointer';
  }
  function click(ev) {
    var e = hitTest(ev.clientX, ev.clientY);
    if (!e) { closePopup(); return; }
    if (e.twoWay) { travel({ name: e.name, op: e.pick === 'up' ? 'Climb-up' : 'Climb-down', x: e.x, z: e.z, dests: [e.pick === 'up' ? e.up : e.down] }); return; }
    if (e.dests && e.dests.length) { travel(e); return; }
    select(e, ev.clientX, ev.clientY);
  }

  // ---- following entrances (ladders, caves, trapdoors, dungeon icons)
  function destName(d) {
    var best = null, bd = 1e9;
    D.labels.forEach(function (l) { if (l[3] === 2) return; var dd = Math.hypot(l[1] - d[1], l[2] - d[2]); if (dd < bd) { bd = dd; best = l; } });
    var where = (bd < 90 && best) ? best[0] : (d[2] >= 6400 ? 'Underground' : 'unlabelled area');
    if (d[2] >= 6400 && best && bd < 90) where += ' (underground)';
    return where + ', level ' + d[0];
  }
  function travel(e) {
    var d = e.dests[0];
    // prefer a destination that actually changes floor/depth
    for (var i = 0; i < e.dests.length; i++) { var c = e.dests[i]; if (c[0] !== state.level || Math.abs(c[2] - e.z) >= 1000) { d = c; break; } }
    state.back = { cx: state.cx, cz: state.cz, level: state.level, zoom: state.zoom, name: e.name };
    document.getElementById('backrow').style.display = '';
    closePopup();
    var from = { cx: state.cx, cz: state.cz }, t0 = performance.now(), dur = 650;
    state.level = d[0]; renderLevels();
    if (state.zoom < 4) state.zoom = 5;
    function step() {
      var t = Math.min(1, (performance.now() - t0) / dur), k = t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
      state.cx = from.cx + (d[1] + 0.5 - from.cx) * k; state.cz = from.cz + (d[2] + 0.5 - from.cz) * k;
      draw();
      if (t < 1) requestAnimationFrame(step);
      else {
        updateHash();
        state.sel = { x: d[1], z: d[2], t0: performance.now() };
        var others = e.dests.filter(function (c) { return c !== d; });
        popupBody.innerHTML = '<div class="ent"><h3>' + esc(e.name) + (e.op ? ' <span class="small">' + esc(e.op) + '</span>' : '') + '</h3>' +
          '<div class="small">You followed it to <b>' + esc(destName(d)) + '</b> (' + d[1] + ', ' + d[2] + ').</div>' +
          (others.length ? '<div class="small">Also leads to: ' + others.map(function (c, i) { return '<a href="#" class="go" data-i="' + e.dests.indexOf(c) + '">' + esc(destName(c)) + '</a>'; }).join(', ') + '</div>' : '') +
          '<div><a href="#" id="popup-back">&larr; Back to ' + esc(state.back.name) + '</a></div></div>';
        popup.style.display = 'block';
        var p = toScreen(d[1] + 0.5, d[2] + 0.5);
        popup.style.left = Math.max(8, Math.min(canvas.width - 338, p[0] + 18)) + 'px';
        popup.style.top = Math.max(8, p[1] - 20) + 'px';
        popupBody.querySelector('#popup-back').onclick = function (ev) { ev.preventDefault(); goBack(); };
        popupBody.querySelectorAll('a.go').forEach(function (a) { a.onclick = function (ev) { ev.preventDefault(); travel({ name: e.name, op: e.op, x: e.x, z: e.z, dests: [e.dests[+a.getAttribute('data-i')]] }); }; });
        draw();
        setTimeout(draw, 300); setTimeout(draw, 1200);   // make sure freshly loaded tiles get painted
      }
    }
    step();
  }
  function goBack() {
    var b = state.back; if (!b) return;
    state.back = null; document.getElementById('backrow').style.display = 'none';
    closePopup();
    state.level = b.level; state.zoom = b.zoom; renderLevels();
    var from = { cx: state.cx, cz: state.cz }, t0 = performance.now(), dur = 500;
    (function step() {
      var t = Math.min(1, (performance.now() - t0) / dur), k = t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
      state.cx = from.cx + (b.cx - from.cx) * k; state.cz = from.cz + (b.cz - from.cz) * k;
      draw(); if (t < 1) requestAnimationFrame(step); else updateHash();
    })();
  }
  document.getElementById('back').onclick = goBack;
  function select(e, sx, sy) {
    state.sel = { x: e.x, z: e.z, t0: performance.now() };
    state.selEnt = Object.assign({ level: state.level }, e);
    // gather everything within 1 tile of the clicked dot on this level
    var group = [];
    var list = spawnsByLevel[state.level];
    list.forEach(function (g) { if (Math.abs(g.x - e.x) <= 1 && Math.abs(g.z - e.z) <= 1) group.push(g); });
    group.sort(function (a, b) { return (a.kind === e.kind && a.id === e.id ? -1 : 0) - (b.kind === e.kind && b.id === e.id ? -1 : 0); });
    // dedupe by kind+id, counting
    var seen = {}, items = [];
    group.forEach(function (g) { var k = g.kind + ':' + g.id; if (seen[k]) { seen[k].n++; return; } seen[k] = { e: g, n: 1 }; items.push(seen[k]); });
    popupBody.innerHTML = items.map(function (it) { return entityHtml(it.e, it.n); }).join('');
    popup.style.display = 'block';
    var p = toScreen(e.x + 0.5, e.z + 0.5);
    var left = p[0] + 18, top = p[1] - 20;
    if (left + 330 > canvas.width) left = p[0] - 338;
    if (top + popup.offsetHeight > canvas.height) top = Math.max(8, canvas.height - popup.offsetHeight - 8);
    popup.style.left = Math.max(8, left) + 'px'; popup.style.top = Math.max(8, top) + 'px';
    draw();
  }
  function entityHtml(e, n) {
    var h = '<div class="ent">';
    if (e.kind === 'npc') {
      var d = D.npcs[e.id];
      h += '<h3>' + (d.ic ? '<img class="icon" src="icons/npcs/' + e.id + '.png" alt="">' : '<span class="dot npc"></span>') + esc(d.n) + (d.l ? ' <span class="small">level ' + d.l + '</span>' : '') + (n > 1 ? ' <span class="tag">x' + n + '</span>' : '') + '</h3>';
      if (d.d) h += '<div class="small">' + esc(d.d) + '</div>';
      if (d.st) h += '<div class="small">HP ' + d.st[0] + ' &middot; Att ' + d.st[1] + ' &middot; Str ' + d.st[2] + ' &middot; Def ' + d.st[3] + '</div>';
      if (d.dr && d.dr.length) {
        h += '<table>' + d.dr.map(function (r) { return '<tr><td>' + esc(r[0]) + (r[1] && r[1] !== '1' ? ' &times;' + esc(r[1]) : '') + '</td><td>' + esc(r[2]) + (r[3] ? ' (' + r[3] + ')' : '') + '</td></tr>'; }).join('') + '</table>';
        if (d.dn > d.dr.length) h += '<div class="small">+' + (d.dn - d.dr.length) + ' more drops on the wiki page</div>';
      } else if (d.dd) h += '<div class="small">Drops: ' + esc(d.dd) + '</div>';
      if (d.sh) h += '<div class="small">Runs a shop: ' + esc(d.sh) + '</div>';
      h += '<div class="small">' + (d.mv === 'nomove' ? 'Does not move.' : 'Wanders up to <b>' + d.wr + '</b> tiles from its spawn (yellow square).') +
        (d.ch ? ' Chases up to <b>' + d.mr + '</b> tiles from spawn (orange dashes).' : d.fight ? ' Cannot chase.' : ' Cannot be fought.') + (d.mv && d.mv !== 'nomove' ? ' Movement: ' + esc(d.mv) + '.' : '') + '</div>';
      if (d.fs) h += '<table>' + d.fs.map(function (f) { return '<tr><td>' + esc(f[0]) + '</td><td>level ' + f[1] + (f[2] ? ' &middot; ' + esc(f[2]) : '') + (f[3] ? ' + ' + esc(f[3]) : '') + (f[4] ? ' &middot; ' + f[4] + ' xp' : '') + '</td></tr>'; }).join('') + '</table>';
      if (d.tp) h += '<div class="small">Can teleport you to the <b>' + esc(d.tp.label) + '</b>: <a href="#" class="go2" data-go="' + d.tp.dest.join(',') + '" data-name="' + esc(d.n) + '">go there &rarr;</a></div>';
      h += '<div><a href="' + d.u + '">Open wiki page &rarr;</a> &nbsp; <a href="#" data-hi="npc:' + e.id + '" class="hi">show all spawns</a></div>';
    } else if (e.kind === 'obj') {
      var o = D.objs[e.id];
      h += '<h3>' + (o.ic ? '<img class="icon" src="icons/items/' + e.id + '.png" alt="">' : '<span class="dot item"></span>') + esc(o.n) + (e.count > 1 ? ' &times;' + e.count : '') + (n > 1 ? ' <span class="tag">x' + n + '</span>' : '') + '</h3>';
      if (o.d) h += '<div class="small">' + esc(o.d) + '</div>';
      h += '<div class="small">Ground spawn at ' + e.x + ', ' + e.z + '</div>';
      h += '<div><a href="' + o.u + '">Open wiki page &rarr;</a> &nbsp; <a href="#" data-hi="obj:' + e.id + '" class="hi">show all spawns</a></div>';
    } else if (e.kind === 'fn' && e.ores) {
      h += '<h3><img class="icon" src="icons/mapfunction_' + e.id + '.png" alt="">' + esc(e.name) + '</h3>';
      if (!e.ores.length) h += '<div class="small">No mineable rocks found within 16 tiles of this icon.</div>';
      else h += '<table>' + e.ores.map(function (o) {
        var item = o[4] != null ? D.objs[o[4]] : null;
        var icon = item && item.ic ? '<img class="icon" src="icons/items/' + o[4] + '.png" alt="">' : '';
        var name = item ? '<a href="' + item.u + '">' + esc(item.n) + '</a>' : esc(o[0]);
        return '<tr><td>' + icon + name + '</td><td>' + o[3] + ' rock' + (o[3] > 1 ? 's' : '') + ' &middot; level ' + o[2] + '</td></tr>';
      }).join('') + '</table>';
    } else if (e.kind === 'stand') {
      var icon = e.itemId != null && D.objs[e.itemId] && D.objs[e.itemId].ic ? '<img class="icon" src="icons/items/' + e.itemId + '.png" alt="">' : '';
      var kindName = e.skind === 'tree' ? 'trees' : e.skind === 'rock' ? 'rocks' : 'fishing spots';
      h += '<h3>' + icon + esc(e.res) + ' <span class="small">' + e.count + ' ' + kindName + '</span></h3>';
      h += '<div class="small">' + (e.skind === 'tree' ? 'Woodcutting' : e.skind === 'rock' ? 'Mining' : 'Fishing') + ' level <b>' + e.lvl + '</b>' + (e.xp ? ', ' + e.xp + ' xp each' : '') + (e.tool ? ' &middot; ' + esc(e.tool) : '') + '</div>';
      h += '<div class="small">Spread over ' + (e.radius * 2 + 1) + ' tiles around ' + e.x + ', ' + e.z + (e.bank ? ' &middot; nearest bank: ' + esc(e.bank) + ' (' + e.bankDist + ' tiles)' : '') + '</div>';
      if (e.itemId != null && D.objs[e.itemId]) h += '<div><a href="' + D.objs[e.itemId].u + '">Open wiki page &rarr;</a></div>';
    } else if (e.kind === 'rc') {
      var rune = e.runeId != null ? D.objs[e.runeId] : null, tal = e.talismanId != null ? D.objs[e.talismanId] : null;
      var icon = rune && rune.ic ? '<img class="icon" src="icons/items/' + e.runeId + '.png" alt="">' : '';
      h += '<h3>' + icon + esc(e.name) + (e.members ? ' <span class="tag members">members</span>' : '') + '</h3>';
      h += '<div class="small">Runecrafting level <b>' + e.lvl + '</b>' + (e.xp ? ', ' + e.xp + ' xp per essence' : '') + '.</div>';
      if (rune) h += '<div class="small">Crafts <a href="' + rune.u + '">' + esc(rune.n) + '</a>' + (tal ? ' &middot; needs <a href="' + tal.u + '">' + esc(tal.n) + '</a>' : '') + '</div>';
      if (e.rcdests && e.rcdests.length) {
        var dd = e.rcdests[0];
        h += '<div><a href="#" class="go2" data-go="' + dd.join(',') + '" data-name="' + esc(e.name) + '">' + (e.rc === 'altar' ? 'Leave through the portal' : 'Use the talisman on the ruins') + ' &rarr; ' + esc(destName(dd)) + '</a></div>';
      }
    } else if (e.kind === 'portal') {
      h += '<h3>' + esc(e.name) + '</h3><div class="small">' + esc(e.op) + ' &rarr; ' + esc(destName(e.dests[0])) + '</div>';
    } else {
      h += '<h3>' + esc(e.name) + '</h3><div class="small">Map icon at ' + e.x + ', ' + e.z + '</div>';
    }
    return h + '</div>';
  }
  popupBody.addEventListener('click', function (ev) {
    var g = ev.target.closest('a.go2');
    if (g) {
      ev.preventDefault();
      var c = g.getAttribute('data-go').split(',').map(Number), se = state.selEnt || { x: state.cx, z: state.cz };
      travel({ name: g.getAttribute('data-name') || 'here', op: '', x: se.x, z: se.z, dests: [c] });
      return;
    }
    var a = ev.target.closest('a.hi'); if (!a) return;
    ev.preventDefault();
    var bits = a.getAttribute('data-hi').split(':');
    highlight(bits[0], +bits[1], false);
  });
  document.getElementById('popup-close').addEventListener('click', closePopup);
  function closePopup() { popup.style.display = 'none'; state.sel = null; state.selEnt = null; draw(); }

  function highlight(kind, id, jump) {
    state.hi = { kind: kind, id: id };
    var all = [];
    for (var lv = 0; lv < 4; lv++) spawnsByLevel[lv].forEach(function (e) { if (e.kind === kind && e.id === id) all.push({ e: e, lv: lv }); });
    statusEl.textContent = all.length ? all.length + ' spawn' + (all.length > 1 ? 's' : '') + ' highlighted (green rings)' : 'No spawns on the map for this entry';
    if (jump && all.length) { state.level = all[0].lv; state.cx = all[0].e.x; state.cz = all[0].e.z; if (state.zoom < 3) state.zoom = 4; renderLevels(); }
    draw(); updateHash();
  }

  // ---- panel
  var levelsEl = document.getElementById('levels');
  function renderLevels() {
    levelsEl.innerHTML = '';
    for (var i = 0; i < 4; i++) (function (i) {
      var b = document.createElement('button'); b.textContent = i; if (i === state.level) b.className = 'on';
      b.onclick = function () { state.level = i; renderLevels(); closePopup(); draw(); updateHash(); };
      levelsEl.appendChild(b);
    })(i);
  }
  renderLevels();
  ['npc', 'obj', 'fn', 'portal', 'rc', 'stand', 'lab'].forEach(function (k) {
    var cb = document.getElementById('l-' + k);
    cb.addEventListener('change', function () { state.layers[k] = cb.checked; draw(); });
  });
  document.getElementById('zin').onclick = function () { state.zoom = Math.min(24, state.zoom * 1.5); draw(); updateHash(); };
  document.getElementById('zout').onclick = function () { state.zoom = Math.max(0.5, state.zoom / 1.5); draw(); updateHash(); };
  function go(x, z, level, zoom) { state.cx = x; state.cz = z; if (level != null) state.level = level; if (zoom) state.zoom = zoom; renderLevels(); closePopup(); draw(); updateHash(); }

  var find = document.getElementById('find'), sugg = document.getElementById('sugg');
  find.addEventListener('input', function () {
    var q = find.value.trim().toLowerCase();
    if (!q) { sugg.style.display = 'none'; return; }
    var hits = searchIndex.filter(function (e) { return e.t.toLowerCase().indexOf(q) >= 0; }).slice(0, 30);
    hits.sort(function (a, b) { return a.t.toLowerCase().indexOf(q) - b.t.toLowerCase().indexOf(q) || a.t.localeCompare(b.t); });
    sugg.innerHTML = hits.map(function (e, i) { return '<div data-i="' + searchIndex.indexOf(e) + '"><span class="dot ' + (e.kind === 'obj' ? 'item' : e.kind) + '"></span>' + esc(e.t) + '</div>'; }).join('') || '<div>No matches</div>';
    sugg.style.display = 'block';
  });
  sugg.addEventListener('click', function (ev) {
    var d = ev.target.closest('div[data-i]'); if (!d) return;
    var e = searchIndex[+d.getAttribute('data-i')];
    sugg.style.display = 'none'; find.value = e.t;
    if (e.kind === 'area') { var l = D.labels[e.id]; state.hi = null; go(l[1], l[2], 0, 4); }
    else highlight(e.kind, e.id, true);
  });
  function pickFirst(ev) {
    if (ev.key !== 'Enter' || sugg.style.display === 'none') return;
    ev.preventDefault();
    var d = sugg.querySelector('div[data-i]'); if (d) d.click();
  }
  find.addEventListener('keydown', pickFirst);
  find.addEventListener('keyup', pickFirst);

  // ---- deep links: #x=..&z=..&level=..&zoom=..&npc=ID / obj=ID
  function readHash() {
    var h = {}; location.hash.replace(/^#/, '').split('&').forEach(function (kv) { var p = kv.split('='); if (p[0]) h[p[0]] = decodeURIComponent(p[1] || ''); });
    if (h.x) state.cx = +h.x; if (h.z) state.cz = +h.z;
    if (h.level) state.level = Math.max(0, Math.min(3, +h.level));
    if (h.zoom) state.zoom = Math.max(0.5, Math.min(24, +h.zoom));
    if (h.npc) highlight('npc', +h.npc, !h.x);
    else if (h.obj) highlight('obj', +h.obj, !h.x);
    renderLevels();
  }
  var hashTimer = null;
  function updateHash() {
    clearTimeout(hashTimer);
    hashTimer = setTimeout(function () {
      var parts = ['x=' + Math.round(state.cx), 'z=' + Math.round(state.cz), 'level=' + state.level, 'zoom=' + state.zoom.toFixed(1)];
      if (state.hi) parts.push(state.hi.kind + '=' + state.hi.id);
      history.replaceState(null, '', '#' + parts.join('&'));
    }, 150);
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  readHash();
  resize();
  window.__map = { state: state, images: images, travelTo: function (x, z, level) { travel({ name: 'debug', op: '', x: x, z: z, dests: [[level, x, z]] }); } };
})();

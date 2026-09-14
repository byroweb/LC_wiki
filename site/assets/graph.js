// Ego-network explorer for the knowledge graph (data/graph.js -> window.GRAPH).
(function () {
  var G = window.GRAPH;
  var COL = { npc: '#ffd400', item: '#ff3b3b', area: '#6ddf6d', quest: '#c08cff', shop: '#5aa9ff', table: '#ff9d3b', category: '#9a9a9a' };
  var canvas = document.getElementById('g'), ctx = canvas.getContext('2d');
  var nodesById = {}, adj = {};
  G.nodes.forEach(function (n) { nodesById[n.id] = n; adj[n.id] = []; });
  G.edges.forEach(function (e) { if (adj[e.s] && adj[e.t]) { adj[e.s].push(e); adj[e.t].push(e); } });

  var state = { focus: null, depth: 1, region: '', filters: { npc: true, item: true, area: true, quest: true, shop: true, table: true, category: true }, nodes: [], edges: [], pan: [0, 0], zoom: 1, drag: null, hover: null };
  function inRegion(n) { return !state.region || (n.r && n.r.indexOf(state.region) >= 0); }

  function build() {
    if (!state.focus) return;
    var keep = {}; keep[state.focus] = 0;
    var frontier = [state.focus];
    for (var d = 1; d <= state.depth; d++) {
      var next = [];
      frontier.forEach(function (id) {
        adj[id].forEach(function (e) {
          var o = e.s === id ? e.t : e.s;
          var on = nodesById[o];
          if (!state.filters[on.type] || !inRegion(on)) return;
          if (keep[o] == null) { keep[o] = d; next.push(o); }
        });
      });
      frontier = next;
      if (Object.keys(keep).length > 350) break;
    }
    var old = {}; state.nodes.forEach(function (n) { old[n.id] = n; });
    state.nodes = Object.keys(keep).map(function (id) {
      var n = nodesById[id];
      var prev = old[id];
      var ang = Math.random() * Math.PI * 2, rad = keep[id] === 0 ? 0 : 120 + keep[id] * 140 + Math.random() * 60;
      return { id: id, n: n, d: keep[id], x: prev ? prev.x : Math.cos(ang) * rad, y: prev ? prev.y : Math.sin(ang) * rad, vx: 0, vy: 0 };
    });
    var idx = {}; state.nodes.forEach(function (n) { idx[n.id] = n; });
    state.edges = [];
    var seen = {};
    state.nodes.forEach(function (n) {
      adj[n.id].forEach(function (e) {
        var k = e.s + '|' + e.t + '|' + e.type;
        if (seen[k] || !idx[e.s] || !idx[e.t]) return;
        seen[k] = 1;
        state.edges.push({ a: idx[e.s], b: idx[e.t], e: e });
      });
    });
    state.pan = [0, 0]; ticks = 0;
    renderInfo();
  }

  var ticks = 0;
  function step() {
    var ns = state.nodes, es = state.edges;
    if (!ns.length) return;
    var alpha = Math.max(0.02, 0.9 * Math.pow(0.97, ticks));
    for (var i = 0; i < ns.length; i++) {
      var a = ns[i];
      for (var j = i + 1; j < ns.length; j++) {
        var b = ns[j];
        var dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy + 0.01, d = Math.sqrt(d2);
        var f = 2600 / d2;
        if (f > 6) f = 6;
        var fx = dx / d * f, fy = dy / d * f;
        a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
      }
    }
    es.forEach(function (e) {
      var dx = e.b.x - e.a.x, dy = e.b.y - e.a.y, d = Math.sqrt(dx * dx + dy * dy) + 0.01;
      var want = 70 + 40 * Math.max(e.a.d, e.b.d);
      var f = (d - want) * 0.02;
      e.a.vx += dx / d * f; e.a.vy += dy / d * f; e.b.vx -= dx / d * f; e.b.vy -= dy / d * f;
    });
    ns.forEach(function (n) {
      if (n.d === 0) { n.x *= 0.8; n.y *= 0.8; }
      n.vx -= n.x * 0.002; n.vy -= n.y * 0.002;
      if (state.drag && state.drag.node === n) { n.vx = n.vy = 0; return; }
      n.x += n.vx * alpha; n.y += n.vy * alpha; n.vx *= 0.6; n.vy *= 0.6;
    });
    ticks++;
  }

  function draw() {
    var W = canvas.width, H = canvas.height;
    ctx.fillStyle = '#14130f'; ctx.fillRect(0, 0, W, H);
    ctx.save(); ctx.translate(W / 2 + state.pan[0], H / 2 + state.pan[1]); ctx.scale(state.zoom, state.zoom);
    ctx.lineWidth = 1 / state.zoom;
    state.edges.forEach(function (e) {
      ctx.strokeStyle = (state.hover && (e.a === state.hover || e.b === state.hover)) ? '#fff' : 'rgba(255,255,255,.18)';
      ctx.beginPath(); ctx.moveTo(e.a.x, e.a.y); ctx.lineTo(e.b.x, e.b.y); ctx.stroke();
      if (state.zoom > 1.4 || (state.hover && (e.a === state.hover || e.b === state.hover))) {
        ctx.fillStyle = 'rgba(232,226,210,.7)'; ctx.font = (10 / state.zoom) + 'px Segoe UI';
        ctx.textAlign = 'center';
        ctx.fillText(e.e.l || e.e.type, (e.a.x + e.b.x) / 2, (e.a.y + e.b.y) / 2 - 3 / state.zoom);
      }
    });
    state.nodes.forEach(function (n) {
      var r = n.d === 0 ? 14 : 7;
      ctx.fillStyle = COL[n.n.type] || '#ccc';
      ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = n === state.hover ? '#fff' : '#000'; ctx.lineWidth = (n === state.hover ? 2 : 1) / state.zoom; ctx.stroke();
      if (n.d <= 1 || state.zoom > 1.3 || n === state.hover) {
        ctx.font = (n.d === 0 ? 'bold 14px' : '11px') + ' Segoe UI';
        ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
        ctx.lineWidth = 3; ctx.strokeStyle = 'rgba(0,0,0,.9)';
        ctx.strokeText(n.n.label, n.x + r + 3, n.y);
        ctx.fillStyle = '#e8e2d2'; ctx.fillText(n.n.label, n.x + r + 3, n.y);
      }
    });
    ctx.restore();
  }
  function loop() { step(); draw(); requestAnimationFrame(loop); }

  function toGraph(px, py) { return [(px - canvas.width / 2 - state.pan[0]) / state.zoom, (py - canvas.height / 2 - state.pan[1]) / state.zoom]; }
  function nodeAt(px, py) {
    var p = toGraph(px, py), best = null, bd = 14 / state.zoom + 4;
    state.nodes.forEach(function (n) { var d = Math.hypot(n.x - p[0], n.y - p[1]); if (d < bd) { bd = d; best = n; } });
    return best;
  }
  var moved = false;
  canvas.addEventListener('mousedown', function (ev) { var n = nodeAt(ev.clientX, ev.clientY); state.drag = { x: ev.clientX, y: ev.clientY, node: n, pan: state.pan.slice() }; moved = false; });
  window.addEventListener('mousemove', function (ev) {
    if (state.drag) {
      var dx = ev.clientX - state.drag.x, dy = ev.clientY - state.drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
      if (state.drag.node) { var p = toGraph(ev.clientX, ev.clientY); state.drag.node.x = p[0]; state.drag.node.y = p[1]; }
      else state.pan = [state.drag.pan[0] + dx, state.drag.pan[1] + dy];
    } else { state.hover = nodeAt(ev.clientX, ev.clientY); canvas.style.cursor = state.hover ? 'pointer' : 'default'; }
  });
  window.addEventListener('mouseup', function (ev) {
    if (state.drag && !moved && state.drag.node) focus(state.drag.node.id);
    state.drag = null;
  });
  canvas.addEventListener('dblclick', function (ev) { var n = nodeAt(ev.clientX, ev.clientY); if (n && n.n.url) location.href = n.n.url; });
  canvas.addEventListener('wheel', function (ev) { ev.preventDefault(); state.zoom = Math.max(0.3, Math.min(4, state.zoom * (ev.deltaY < 0 ? 1.15 : 0.87))); }, { passive: false });
  window.addEventListener('resize', resize);
  function resize() { canvas.width = innerWidth; canvas.height = innerHeight; }

  // ---- panel
  var find = document.getElementById('find'), sugg = document.getElementById('sugg'), info = document.getElementById('info');
  find.addEventListener('input', function () {
    var q = find.value.trim().toLowerCase();
    if (!q) { sugg.style.display = 'none'; return; }
    var hits = G.nodes.filter(function (n) { return n.label.toLowerCase().indexOf(q) >= 0 && (inRegion(n) || n.type === 'category'); }).slice(0, 40);
    hits.sort(function (a, b) { return a.label.toLowerCase().indexOf(q) - b.label.toLowerCase().indexOf(q) || a.label.localeCompare(b.label); });
    sugg.innerHTML = hits.map(function (n) { return '<div data-id="' + n.id + '"><span class="dot ' + n.type + '"></span>' + esc(n.label) + ' <span class="small">' + n.type + '</span></div>'; }).join('') || '<div>No matches</div>';
    sugg.style.display = 'block';
  });
  sugg.addEventListener('click', function (ev) { var d = ev.target.closest('div[data-id]'); if (!d) return; sugg.style.display = 'none'; find.value = ''; focus(d.getAttribute('data-id')); });
  find.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') { var d = sugg.querySelector('div[data-id]'); if (d) d.click(); } });
  document.getElementById('d1').onclick = function () { state.depth = 1; this.className = 'on'; document.getElementById('d2').className = ''; build(); };
  document.getElementById('d2').onclick = function () { state.depth = 2; this.className = 'on'; document.getElementById('d1').className = ''; build(); };
  ['npc', 'item', 'area', 'quest', 'shop', 'table'].forEach(function (k) { var cb = document.getElementById('f-' + k); cb.onchange = function () { state.filters[k] = cb.checked; build(); }; });
  var regionSel = document.getElementById('region');
  ((G.meta && G.meta.regions) || []).forEach(function (r) { var o = document.createElement('option'); o.value = r; o.textContent = r; regionSel.appendChild(o); });
  regionSel.onchange = function () { state.region = regionSel.value; build(); };

  function focus(id) {
    if (!nodesById[id]) return;
    state.focus = id; build();
    history.replaceState(null, '', '#' + encodeURIComponent(id));
  }
  function renderInfo() {
    var n = nodesById[state.focus]; if (!n) return;
    var groups = {};
    adj[n.id].forEach(function (e) {
      var o = e.s === n.id ? e.t : e.s; var on = nodesById[o];
      if (!inRegion(on) && on.type !== 'category') return;
      var lab = (e.s === n.id ? e.l || e.type : (e.r || ('&larr; ' + (e.l || e.type))));
      (groups[lab] = groups[lab] || []).push(on);
    });
    var h = '<h3><span class="dot ' + n.type + '"></span>' + esc(n.label) + ' <span class="small">' + n.type + '</span></h3>';
    if (n.sub) h += '<div class="small">' + esc(n.sub) + '</div>';
    if (n.r && n.r.length) h += '<div class="small">Found in: ' + n.r.map(esc).join(', ') + '</div>';
    if (state.region) h += '<div class="small">Showing only connections in <b>' + esc(state.region) + '</b>.</div>';
    if (n.url) h += '<div><a href="' + n.url + '">Open wiki page &rarr;</a>' + (n.m ? ' &nbsp; <a href="map.html#' + n.m + '">Show on map &rarr;</a>' : '') + '</div>';
    Object.keys(groups).sort().forEach(function (k) {
      var list = groups[k];
      h += '<div class="small" style="margin-top:6px"><b>' + k + '</b> (' + list.length + ')</div><ul>' +
        list.slice(0, 60).map(function (o) { return '<li><a href="#" data-focus="' + o.id + '">' + esc(o.label) + '</a></li>'; }).join('') +
        (list.length > 60 ? '<li class="small">+' + (list.length - 60) + ' more</li>' : '') + '</ul>';
    });
    info.innerHTML = h;
  }
  info.addEventListener('click', function (ev) { var a = ev.target.closest('a[data-focus]'); if (!a) return; ev.preventDefault(); focus(a.getAttribute('data-focus')); });
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  resize();
  var start = decodeURIComponent(location.hash.replace(/^#/, '')) || 'npc:fat_tony';
  if (!nodesById[start]) start = G.nodes[0].id;
  focus(start);
  loop();
})();

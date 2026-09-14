// Shared wiki behaviour: quick search box and table filtering.
(function () {
  var base = document.body.getAttribute('data-base') || '';
  var input = document.getElementById('q');
  var results = document.getElementById('q-results');
  if (input && results && window.SEARCH) {
    var data = window.SEARCH;
    function render(q) {
      q = q.trim().toLowerCase();
      if (!q) { results.style.display = 'none'; results.innerHTML = ''; return; }
      var hits = [];
      for (var i = 0; i < data.length && hits.length < 40; i++) {
        var e = data[i];
        var t = e.t.toLowerCase();
        var score = t === q ? 0 : t.indexOf(q) === 0 ? 1 : t.indexOf(q) >= 0 ? 2 : (e.s && e.s.toLowerCase().indexOf(q) >= 0) ? 3 : -1;
        if (score >= 0) hits.push([score, e]);
      }
      hits.sort(function (a, b) { return a[0] - b[0] || a[1].t.localeCompare(b[1].t); });
      results.innerHTML = hits.map(function (h) {
        var e = h[1];
        return '<a href="' + base + e.u + '"><span class="dot ' + e.k + '"></span>' + esc(e.t) +
          '<span class="k">' + e.k + '</span>' + (e.s ? '<div class="small">' + esc(e.s) + '</div>' : '') + '</a>';
      }).join('') || '<a>No matches</a>';
      results.style.display = 'block';
    }
    input.addEventListener('input', function () { render(input.value); });
    input.addEventListener('focus', function () { if (input.value) render(input.value); });
    document.addEventListener('click', function (ev) {
      if (!results.contains(ev.target) && ev.target !== input) results.style.display = 'none';
    });
    input.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter') { var a = results.querySelector('a[href]'); if (a) location.href = a.href; }
      if (ev.key === 'Escape') results.style.display = 'none';
    });
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  // generic list/table filter: <input class="filter" data-target="#id">
  document.querySelectorAll('input.filter').forEach(function (f) {
    var target = document.querySelector(f.getAttribute('data-target'));
    if (!target) return;
    var rows = target.querySelectorAll(target.tagName === 'TABLE' ? 'tbody tr' : 'a, .card');
    f.addEventListener('input', function () {
      var q = f.value.trim().toLowerCase();
      rows.forEach(function (r) { r.style.display = (!q || r.textContent.toLowerCase().indexOf(q) >= 0) ? '' : 'none'; });
    });
  });
})();

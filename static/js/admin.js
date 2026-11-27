(function () {
  var $ = function (id) { return document.getElementById(id); };
  var SLOT = { strong_1: 'Strong, primary', strong_2: 'Strong, secondary', weak_1: 'Weak, primary', weak_2: 'Weak, secondary', free: 'Free fallback' };
  var pct = function (a, b) { return b ? Math.round(a / b * 100) : 0; };

  function ledger(t) {
    var cells = [
      ['Users', fmtNum(t.users), '+' + t.new_week + ' in the last 7 days'],
      ['Active today', fmtNum(t.active_today), 'Users who sent a request'],
      ['Requests today', fmtNum(t.requests_today), fmtNum(t.requests_month) + ' this month'],
      ['Tokens today', fmtNum(t.tokens_today), fmtNum(t.tokens_month) + ' this month'],
      ['Average latency', fmtMs(t.avg_latency_ms), pct(t.strong_today, t.requests_today) + '% of traffic went strong'],
      ['Failures today', fmtNum(t.errors_today), fmtNum(t.fallback_today) + ' used a fallback key']
    ];
    $('ledger').innerHTML = cells.map(function (c) {
      return '<div class="cell"><div class="k">' + c[0] + '</div><div class="v">' + c[1] + '</div><div class="s">' + c[2] + '</div></div>';
    }).join('');
  }

  function chart(series) {
    var W = 640, H = 230, pl = 36, pr = 6, pt = 10, pb = 26, iw = W - pl - pr, ih = H - pt - pb;
    var maxV = Math.max(4, Math.max.apply(null, series.map(function (d) { return d.strong + d.weak; })));
    var p = Math.pow(10, Math.floor(Math.log10(maxV))), top = Math.ceil(maxV / p) * p;
    var step = iw / series.length, bw = Math.min(30, step * 0.64);
    var y = function (v) { return pt + ih - (v / top) * ih; };
    var out = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Requests per day, split by strong and weak model">';
    [0, top / 2, top].forEach(function (v) {
      out += '<line class="grid" x1="' + pl + '" x2="' + (W - pr) + '" y1="' + y(v) + '" y2="' + y(v) + '"/><text x="' + (pl - 6) + '" y="' + (y(v) + 4) + '" text-anchor="end">' + fmtNum(v) + '</text>';
    });
    series.forEach(function (d, i) {
      var x = pl + i * step + (step - bw) / 2, hw = d.weak / top * ih, hs = d.strong / top * ih;
      var label = new Date(d.date + 'T00:00:00').toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
      out += '<g><title>' + label + ': ' + d.strong + ' strong, ' + d.weak + ' weak</title>' +
        '<rect x="' + x + '" y="' + (pt + ih - hw) + '" width="' + bw + '" height="' + hw + '" fill="#17b26a" rx="2"/>' +
        '<rect x="' + x + '" y="' + (pt + ih - hw - hs) + '" width="' + bw + '" height="' + hs + '" fill="#c2561a" rx="2"/></g>';
      if (i % 2 === (series.length - 1) % 2) out += '<text x="' + (x + bw / 2) + '" y="' + (H - 7) + '" text-anchor="middle">' + label + '</text>';
    });
    $('chart').innerHTML = out + '</svg>';
  }

  function plans(list) {
    var total = list.reduce(function (a, b) { return a + b.count; }, 0);
    $('plans').innerHTML = list.map(function (p) {
      return '<div class="plan-row"><span>' + esc(p.label) + '</span><div class="bar"><i style="width:' + pct(p.count, total) + '%"></i></div><b class="r">' + p.count + '</b></div>';
    }).join('') + '<p class="dim small">' + total + ' accounts in total</p>';
  }

  function table(id, head, rowsHtml, empty) {
    $(id).innerHTML = '<thead><tr>' + head.map(function (h, i) { return '<th' + (h[1] ? ' class="r"' : '') + '>' + h[0] + '</th>'; }).join('') +
      '</tr></thead><tbody>' + (rowsHtml.length ? rowsHtml.join('') : '<tr><td colspan="' + head.length + '" class="dim">' + empty + '</td></tr>') + '</tbody>';
  }

  function render(d) {
    $('stamp').textContent = 'Refreshed at ' + fmtTime(d.generated_at) + '. Updates every 15 seconds.';
    var c = $('clf');
    c.className = 'clf ' + (d.classifier.ready ? 'ok' : 'warn');
    c.textContent = 'Classifier: ' + d.classifier.status;
    ledger(d.totals); chart(d.series); plans(d.plans);

    table('models', [['Model'], ['Requests', 1], ['Avg latency', 1], ['Tokens', 1]],
      d.models.map(function (m) {
        return '<tr><td><span class="mono">' + esc(m.model || '-') + '</span><div class="dim small">' + esc(m.provider) + '</div></td><td class="num">' + fmtNum(m.count) +
          '</td><td class="num">' + fmtMs(m.avg_latency_ms) + '</td><td class="num">' + fmtNum(m.tokens) + '</td></tr>';
      }), 'No requests this month.');

    table('top-users', [['User'], ['Requests', 1], ['Tokens', 1]],
      d.top_users.map(function (u) {
        return '<tr><td><div class="strong-text">' + esc(u.name) + '</div><div class="dim small">' + esc(u.email) + '</div></td><td class="num">' + fmtNum(u.count) + '</td><td class="num">' + fmtNum(u.tokens) + '</td></tr>';
      }), 'Nobody has sent a request today.');

    table('slots', [['Slot'], ['Provider'], ['Model'], ['Key']],
      d.slots.map(function (s) {
        return '<tr><td>' + SLOT[s.name] + '</td><td>' + (s.configured ? esc(s.provider) : '<span class="dim">Not set</span>') +
          '</td><td>' + (s.configured ? '<span class="mono">' + esc(s.model) + '</span>' : '<span class="dim">Not set</span>') +
          '</td><td>' + (s.configured ? '<span class="tag weak">Added</span> <span class="dim mono">' + esc(s.key_hint) + '</span>' : '<span class="tag neutral dim">Missing</span>') + '</td></tr>';
      }), '');

    table('recent', [['Time'], ['User'], ['Prompt'], ['Routing'], ['Model'], ['Tokens in / out', 1], ['Latency', 1]],
      d.recent.map(function (r) {
        var failed = r.status === 'error';
        return '<tr class="' + (failed ? 'failed' : '') + '"><td class="nowrap dim">' + fmtTime(r.created_at) + '</td><td class="small">' + esc(r.user) +
          '</td><td class="clip" title="' + esc(r.prompt) + '">' + esc(r.prompt) + (failed ? '<div class="err-note">' + esc(r.error || 'Failed') + '</div>' : '') +
          '</td><td class="nowrap"><span class="tag ' + r.tier + '">' + (r.tier === 'strong' ? 'Strong' : 'Weak') + '</span> ' + gauge(r.complexity, d.threshold) + '</td><td class="small">' +
          esc(r.model || '-') + (r.fallback ? ' <span class="tag fb">fallback</span>' : '') + '</td><td class="num">' + (failed ? '-' : fmtNum(r.tokens_in) + ' / ' + fmtNum(r.tokens_out)) +
          '</td><td class="num">' + (failed ? '-' : fmtMs(r.latency_ms)) + '</td></tr>';
      }), 'No requests yet.');
  }

  async function load() {
    try {
      var r = await api('/admin/api/stats');
      if (!r.ok) throw new Error('bad status');
      render(await r.json());
    } catch (e) { $('stamp').textContent = 'Could not refresh. Retrying…'; }
  }
  load();
  setInterval(load, 15000);
})();

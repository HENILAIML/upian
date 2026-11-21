(function () {
  var $ = function (id) { return document.getElementById(id); };

  function cells(id, list) {
    $(id).innerHTML = list.map(function (c) {
      return '<div class="cell"><div class="k">' + c[0] + '</div><div class="v">' + c[1] + '</div><div class="s">' + c[2] + '</div></div>';
    }).join('');
  }

  function table(id, head, rows, empty) {
    $(id).innerHTML = '<thead><tr>' + head.map(function (h) { return '<th' + (h[1] ? ' class="r"' : '') + '>' + h[0] + '</th>'; }).join('') +
      '</tr></thead><tbody>' + (rows.length ? rows.join('') : '<tr><td colspan="' + head.length + '" class="dim">' + empty + '</td></tr>') + '</tbody>';
  }

  function bars(id, series, key, color, fmt, label) {
    var W = 640, H = 220, pl = 44, pr = 6, pt = 10, pb = 24, iw = W - pl - pr, ih = H - pt - pb;
    var maxV = Math.max(1, Math.max.apply(null, series.map(function (d) { return d[key] || 0; })));
    var step = iw / series.length, bw = Math.max(2, Math.min(24, step * 0.7));
    var y = function (v) { return pt + ih - (v / maxV) * ih; };
    var out = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="' + label + '">';
    [0, maxV / 2, maxV].forEach(function (v) {
      out += '<line class="grid" x1="' + pl + '" x2="' + (W - pr) + '" y1="' + y(v) + '" y2="' + y(v) + '"/><text x="' + (pl - 6) + '" y="' + (y(v) + 4) + '" text-anchor="end">' + fmt(Math.round(v)) + '</text>';
    });
    var every = Math.ceil(series.length / 8);
    series.forEach(function (d, i) {
      var v = d[key] || 0, x = pl + i * step + (step - bw) / 2;
      var lab = new Date(d.date + 'T00:00:00').toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
      out += '<g><title>' + lab + ': ' + fmt(v) + '</title><rect x="' + x + '" y="' + y(v) + '" width="' + bw + '" height="' + (pt + ih - y(v)) + '" fill="' + color + '"/></g>';
      if (i % every === 0) out += '<text x="' + (x + bw / 2) + '" y="' + (H - 6) + '" text-anchor="middle">' + lab + '</text>';
    });
    $(id).innerHTML = out + '</svg>';
  }

  function render(d) {
    var t = d.totals;
    $('stamp').textContent = 'Last ' + d.days + ' days, refreshed ' + fmtTime(d.generated_at);
    cells('ledger', [
      ['Total users', fmtNum(t.users), fmtNum(t.active_users) + ' used AI in this range'],
      ['Active users', fmtNum(t.dau) + ' / ' + fmtNum(t.wau) + ' / ' + fmtNum(t.mau), 'Day / week / month'],
      ['Requests', fmtNum(t.requests), t.strong_pct + '% went to the strong model'],
      ['Failures', t.error_rate + '%', fmtNum(t.errors) + ' failed, ' + fmtNum(t.fallback) + ' used fallback']
    ]);
    cells('ledger2', [
      ['Tokens used', fmtNum(t.tokens), fmtNum(t.tokens_in) + ' in, ' + fmtNum(t.tokens_out) + ' out'],
      ['Avg tokens / request', fmtNum(t.avg_tokens), 'Input plus output'],
      ['Avg latency', fmtMs(t.latency_avg), 'Median ' + fmtMs(t.latency_p50)],
      ['p95 latency', fmtMs(t.latency_p95), '95% of requests were faster']
    ]);
    bars('chart-req', d.series, 'requests', '#5f3de8', fmtNum, 'Requests per day');
    bars('chart-lat', d.series, 'avg_latency_ms', '#17b26a', fmtMs, 'Average latency per day');

    table('plans', [['Plan'], ['Users', 1], ['Active', 1], ['Requests', 1], ['Tokens', 1], ['Avg latency', 1], ['Quota used', 1]],
      d.plans.map(function (p) {
        return '<tr><td class="strong-text">' + esc(p.label) + '</td><td class="num">' + fmtNum(p.users) + '</td><td class="num">' + fmtNum(p.active) +
          '</td><td class="num">' + fmtNum(p.requests) + '</td><td class="num">' + fmtNum(p.tokens) + '</td><td class="num">' + fmtMs(p.avg_latency_ms) +
          '</td><td class="num">' + p.quota_used_pct + '%</td></tr>';
      }), 'No plans.');

    table('top', [['User'], ['Plan'], ['Requests', 1], ['Tokens', 1], ['Latency', 1]],
      d.top_users.map(function (u) {
        return '<tr><td><div class="strong-text">' + esc(u.name) + '</div><div class="dim small">' + esc(u.email) + '</div></td><td>' + esc(u.plan) +
          '</td><td class="num">' + fmtNum(u.requests) + '</td><td class="num">' + fmtNum(u.tokens) + '</td><td class="num">' + fmtMs(u.avg_latency_ms) + '</td></tr>';
      }), 'No usage in this range.');

    table('near', [['User'], ['Plan'], ['Used this month', 1]],
      d.near_limit.map(function (u) {
        return '<tr><td><div class="strong-text">' + esc(u.name) + '</div><div class="dim small">' + esc(u.email) + '</div></td><td>' + esc(u.plan) +
          '</td><td class="num">' + fmtNum(u.used) + ' of ' + fmtNum(u.limit) + '</td></tr>';
      }), 'Nobody is close to their limit.');
  }

  async function load() {
    try {
      var r = await api('/admin/api/analytics?days=' + $('range').value);
      if (!r.ok) throw new Error('bad status');
      render(await r.json());
    } catch (e) { $('stamp').textContent = 'Could not load. Retrying…'; }
  }
  $('range').addEventListener('change', load);
  load();
  setInterval(load, 30000);
})();

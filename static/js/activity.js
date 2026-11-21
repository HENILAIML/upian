(function () {
  var boot = JSON.parse(document.getElementById('boot').textContent);
  var $ = function (id) { return document.getElementById(id); };
  var rows = $('rows'), moreBtn = $('more'), summary = $('summary');
  var items = [], hasMore = false;

  function row(it) {
    var failed = it.status === 'error';
    return '<tr class="' + (failed ? 'failed' : '') + '">' +
      '<td class="nowrap dim">' + esc(fmtDateTime(it.created_at)) + '</td>' +
      '<td class="clip" title="' + esc(it.prompt) + '">' + esc(it.prompt) +
        (failed ? '<div class="err-note">Failed and not counted. ' + esc(it.error || '') + '</div>' : '') + '</td>' +
      '<td class="nowrap"><span class="tag ' + it.tier + '">' + tierLabel(it.tier) + '</span> ' + gauge(it.complexity, boot.threshold) +
        '<span class="dim small">' + Number(it.complexity || 0).toFixed(2) + '</span>' +
        (it.downgraded ? ' <span class="tag fb">Downgraded</span>' : '') + '</td>' +
      '<td><span class="mono">' + esc(it.model || '-') + '</span>' + (it.fallback ? ' <span class="tag fb">Fallback key</span>' : '') +
        '<div class="dim small">' + esc(it.provider || '') + '</div></td>' +
      '<td class="num">' + (failed ? '-' : fmtNum(it.tokens_in) + ' / ' + fmtNum(it.tokens_out)) + '</td>' +
      '<td class="num">' + (failed ? '-' : fmtMs(it.latency_ms)) +
        '<div class="dim small">+' + fmtMs(it.classify_ms) + ' routing</div></td></tr>';
  }

  function draw(s) {
    rows.innerHTML = items.length ? items.map(row).join('') : '<tr><td colspan="6" class="dim">No requests yet. Send a message in the chat and it will appear here.</td></tr>';
    moreBtn.hidden = !hasMore;
    if (s) {
      var share = s.requests ? Math.round(s.strong / s.requests * 100) + '% went to the strong model' : 'No requests yet today';
      summary.innerHTML =
        '<div class="cell"><div class="k">Requests today</div><div class="v">' + fmtNum(s.requests) + '</div><div class="s">' + share + '</div></div>' +
        '<div class="cell"><div class="k">Tokens today</div><div class="v">' + fmtNum(s.tokens) + '</div><div class="s">Input and output combined</div></div>' +
        '<div class="cell"><div class="k">Average latency</div><div class="v">' + fmtMs(s.avg_latency_ms) + '</div><div class="s">Time the model took to answer</div></div>';
    }
  }

  async function refresh() {
    try {
      var r = await api('/api/activity?limit=30');
      if (!r.ok) return;
      var d = await r.json();
      var fresh = d.items;
      if (fresh.length) {
        var minId = fresh[fresh.length - 1].id;
        var kept = items.filter(function (x) { return x.id < minId; });
        items = fresh.concat(kept);
        if (!kept.length) hasMore = d.has_more;
      } else { items = []; hasMore = false; }
      draw(d.summary);
      renderUsage(d.usage);
    } catch (e) { /* keep the last good view */ }
  }

  moreBtn.addEventListener('click', async function () {
    if (!items.length) return;
    moreBtn.disabled = true;
    try {
      var r = await api('/api/activity?limit=30&before=' + items[items.length - 1].id);
      var d = await r.json();
      items = items.concat(d.items); hasMore = d.has_more; draw();
    } finally { moreBtn.disabled = false; }
  });

  refresh();
  setInterval(refresh, 5000);
})();

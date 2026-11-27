/* Shared helpers */
const CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

function api(path, opts) {
  opts = opts || {};
  opts.headers = Object.assign({ 'X-CSRF-Token': CSRF }, opts.headers || {});
  opts.credentials = 'same-origin';
  return fetch(path, opts);
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}
function fmtNum(n) { return Number(n || 0).toLocaleString('en-IN'); }
function fmtMs(ms) {
  if (ms == null) return '-';
  return ms >= 1000 ? (ms / 1000).toFixed(2) + 's' : ms + 'ms';
}
function fmtTime(iso) {
  return iso ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '-';
}
function fmtDateTime(iso) {
  return iso ? new Date(iso).toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '-';
}
function tierLabel(t) {
  if (t === 'simple') return 'Light';
  if (t === 'medium') return 'Medium';
  if (t === 'complex') return 'Pro';
  if (t === 'image-gen') return 'Image';
  if (t === 'multimodal-vision') return 'Vision';
  return t === 'strong' ? 'Strong' : 'Light';
}

/* Small bar showing the complexity score against the routing threshold. */
function gauge(score, threshold) {
  var s = Math.max(0, Math.min(1, Number(score) || 0));
  var t = Math.max(0, Math.min(1, Number(threshold) || 0.5));
  return '<span class="gauge" title="Complexity ' + s.toFixed(2) + ' (strong model above ' + t.toFixed(2) + ')">' +
    '<b style="width:' + Math.round(s * 100) + '%"></b><i class="cut" style="left:' + Math.round(t * 100) + '%"></i></span>';
}

/* Safe markdown: code blocks, tables, headings, lists, bold, inline code. Everything is escaped first. */
function mdInline(s) {
  return s
    .replace(/`([^`\n]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    .replace(/_([^_\n]+)_/g, '<em>$1</em>')
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s\)]+|\/[^\s\)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer" class="link">$1</a>');
}
function mdSplitRow(line) {
  line = line.trim();
  if (line.charAt(0) === '|') line = line.slice(1);
  if (line.charAt(line.length - 1) === '|') line = line.slice(0, -1);
  return line.split('|').map(function (c) { return c.trim(); });
}
function mdIsSep(l) {
  return l.indexOf('|') > -1 && l.indexOf('-') > -1 && /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(l);
}
function mdAlign(c) {
  c = c.trim();
  return c.slice(-1) === ':' ? (c.charAt(0) === ':' ? 'center' : 'right') : 'left';
}
function mdTableHtml(head, al, rows) {
  function cell(tag, c, k) { return '<' + tag + ' style="text-align:' + (al[k] || 'left') + '">' + mdInline(esc(c)) + '</' + tag + '>'; }
  return '<div class="tbl-wrap"><table><thead><tr>' + head.map(function (c, k) { return cell('th', c, k); }).join('') +
    '</tr></thead><tbody>' + rows.map(function (r) {
      return '<tr>' + head.map(function (_, k) { return cell('td', r[k] || '', k); }).join('') + '</tr>';
    }).join('') + '</tbody></table></div>';
}
function mdText(p) {
  var lines = p.split('\n'), out = [], para = [], i = 0, m;
  function flush() {
    if (para.length) { out.push('<p>' + para.map(function (l) { return mdInline(esc(l)); }).join('<br>') + '</p>'); para = []; }
  }
  while (i < lines.length) {
    var ln = lines[i];
    if (!ln.trim()) { flush(); i++; continue; }
    if (ln.indexOf('|') > -1 && i + 1 < lines.length && mdIsSep(lines[i + 1])) {
      flush();
      var head = mdSplitRow(ln), al = mdSplitRow(lines[i + 1]).map(mdAlign), rows = [];
      i += 2;
      while (i < lines.length && lines[i].trim() && lines[i].indexOf('|') > -1) { rows.push(mdSplitRow(lines[i])); i++; }
      out.push(mdTableHtml(head, al, rows));
      continue;
    }
    if ((m = ln.match(/^\s{0,3}(#{1,6})\s+(.*)$/))) {
      flush();
      var lv = Math.min(m[1].length + 2, 6);
      out.push('<h' + lv + '>' + mdInline(esc(m[2])) + '</h' + lv + '>');
      i++; continue;
    }
    if (/^\s*[-*\u2022]\s+/.test(ln)) {
      flush();
      var ul = [];
      while (i < lines.length && /^\s*[-*\u2022]\s+/.test(lines[i])) { ul.push(lines[i].replace(/^\s*[-*\u2022]\s+/, '')); i++; }
      out.push('<ul>' + ul.map(function (x) { return '<li>' + mdInline(esc(x)) + '</li>'; }).join('') + '</ul>');
      continue;
    }
    if (/^\s*\d+[.)]\s+/.test(ln)) {
      flush();
      var ol = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) { ol.push(lines[i].replace(/^\s*\d+[.)]\s+/, '')); i++; }
      out.push('<ol>' + ol.map(function (x) { return '<li>' + mdInline(esc(x)) + '</li>'; }).join('') + '</ol>');
      continue;
    }
    para.push(ln); i++;
  }
  flush();
  return out.join('');
}
function renderMd(src) {
  var parts = String(src).split('```');
  return parts.map(function (p, i) {
    if (i % 2 === 1) {
      var nl = p.indexOf('\n'), code = p, lang = '';
      if (nl > -1 && /^[\w+#.\-]*$/.test(p.slice(0, nl).trim())) {
        var detected = p.slice(0, nl).trim();
        if (detected) lang = detected;
        code = p.slice(nl + 1);
      }
      var cleanCode = code.replace(/\n$/, '');
      if (!cleanCode.trim()) {
        return '';
      }
      var langDisplay = '';
      var lowLang = (lang || '').toLowerCase();
      if (lowLang && lowLang !== 'text' && lowLang !== 'code' && lowLang !== 'plain') {
        langDisplay = '<span class="code-lang">' + esc(lang) + '</span>';
      }
      return '<div class="code-block-wrap">' +
        '<div class="code-block-head">' +
          langDisplay +
          '<button type="button" class="btn-copy-code" title="Copy code to clipboard">' +
            '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg> ' +
            '<span>Copy</span>' +
          '</button>' +
        '</div>' +
        '<pre><code>' + esc(cleanCode) + '</code></pre>' +
      '</div>';
    }
    return mdText(p);
  }).join('');
}

/* Global copy button handler for fenced code blocks (Clipboard API + fallback) */
document.addEventListener('click', function (e) {
  var btn = e.target.closest('.btn-copy-code, .code-copy-btn');
  if (!btn) return;
  e.preventDefault();
  e.stopPropagation();

  var wrap = btn.closest('.code-block-wrap');
  var codeEl = wrap ? wrap.querySelector('pre code, pre') : null;
  var rawText = codeEl ? codeEl.textContent : '';

  function onCopied() {
    var span = btn.querySelector('span');
    var oldText = span ? span.textContent : 'Copy';
    btn.classList.add('copied');
    if (span) span.textContent = 'Copied!';
    setTimeout(function () {
      btn.classList.remove('copied');
      if (span) span.textContent = oldText;
    }, 2000);
  }

  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(rawText).then(onCopied).catch(function () {
      fallbackCopyCode(rawText, onCopied);
    });
  } else {
    fallbackCopyCode(rawText, onCopied);
  }
});

function fallbackCopyCode(text, cb) {
  var ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.left = '-9999px';
  ta.style.top = '0';
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  try {
    document.execCommand('copy');
    if (cb) cb();
  } catch (err) {
    console.error('Fallback copy failed', err);
  }
  document.body.removeChild(ta);
}

/* Pull every markdown table out of an answer: [{head:[...], rows:[[...]]}] */
function mdTables(src) {
  var res = [];
  String(src).split('```').forEach(function (p, idx) {
    if (idx % 2 === 1) return;
    var lines = p.split('\n'), i = 0;
    while (i < lines.length) {
      if (lines[i].indexOf('|') > -1 && i + 1 < lines.length && mdIsSep(lines[i + 1])) {
        var head = mdSplitRow(lines[i]), rows = [];
        i += 2;
        while (i < lines.length && lines[i].trim() && lines[i].indexOf('|') > -1) { rows.push(mdSplitRow(lines[i])); i++; }
        res.push({ head: head, rows: rows });
      } else i++;
    }
  });
  return res;
}

/* Table styling, injected so style.css stays untouched. */
(function () {
  var st = document.createElement('style');
  st.textContent = '.tbl-wrap{overflow-x:auto;margin:.7em 0}' +
    '.msg table{border-collapse:collapse;width:100%;font-size:.92em}' +
    '.msg th,.msg td{border:1px solid rgba(128,128,128,.35);padding:6px 10px;vertical-align:top}' +
    '.msg th{background:rgba(128,128,128,.15);font-weight:600}' +
    '.msg tbody tr:nth-child(even) td{background:rgba(128,128,128,.06)}' +
    '.msg ul,.msg ol{margin:.4em 0 .7em 1.4em;padding:0}.msg h3,.msg h4,.msg h5,.msg h6{margin:.9em 0 .3em}';
  document.head.appendChild(st);
})();

/* Usage meters in the sidebar (chat and activity pages). */
function resetText(iso) {
  var ms = new Date(iso) - Date.now();
  if (!(ms > 0)) return 'soon';
  var h = Math.floor(ms / 3600000), m = Math.round((ms % 3600000) / 60000);
  return 'in ' + (h ? h + 'h ' : '') + m + 'm';
}
function meter(label, o) {
  var pct = o.limit ? Math.min(100, Math.round(o.used / o.limit * 100)) : 0;
  var cls = pct >= 100 ? 'full' : pct >= 80 ? 'high' : '';
  return '<div class="meter ' + cls + '"><div class="meter-top"><span>' + label + '</span><b>' + fmtNum(o.used) +
    ' of ' + fmtNum(o.limit) + '</b></div><div class="bar"><i style="width:' + pct + '%"></i></div></div>';
}
function renderUsage(u) {
  var box = document.getElementById('meters');
  if (!box || !u) return;
  box.innerHTML = meter('Today', u.day) + meter('This month', u.month) + meter('Strong model, this month', u.strong) +
    (u.images ? meter('Images today', u.images) : '');
  var name = document.getElementById('plan-name'); if (name) name.textContent = u.plan_label;
  var note = document.getElementById('reset-note');
  if (note) note.textContent = 'Daily count resets ' + resetText(u.day_resets_at) + '.';

  var topPlanName = document.getElementById('top-plan-name');
  if (topPlanName) topPlanName.textContent = u.plan_label;
  var topPlanCount = document.getElementById('top-plan-count');
  if (topPlanCount && u.day) {
    topPlanCount.textContent = fmtNum(u.day.used) + ' / ' + fmtNum(u.day.limit);
  }
  var topPlanBarFill = document.getElementById('top-plan-bar-fill');
  if (topPlanBarFill && u.day && u.day.limit) {
    var pct = Math.min(100, Math.round((u.day.used / u.day.limit) * 100));
    topPlanBarFill.style.width = pct + '%';
  }
}
(function () {
  var boot = document.getElementById('boot');
  if (boot) { try { renderUsage(JSON.parse(boot.textContent).usage); } catch (e) {} }

  // Topbar Plan dropdown toggle & Global Popup Synchronization
  window.closeAllPopups = function () {
    var pDrop = document.getElementById('topbar-plan-dropdown');
    var pWidget = document.getElementById('topbar-plan-widget');
    if (pDrop) pDrop.hidden = true;
    if (pWidget) pWidget.classList.remove('open');

    if (typeof window.closeContextDropdown === 'function') {
      window.closeContextDropdown();
    } else {
      var ctxP = document.getElementById('ctx-panel');
      var ctxB = document.querySelector('.ctx');
      var ctxBtn = document.getElementById('ctx-bar-pill');
      if (ctxP) ctxP.hidden = true;
      if (ctxB) ctxB.classList.remove('open');
      if (ctxBtn) ctxBtn.setAttribute('aria-expanded', 'false');
    }
  };

  var planBtn = document.getElementById('topbar-plan-btn');
  var planDrop = document.getElementById('topbar-plan-dropdown');
  var planWidget = document.getElementById('topbar-plan-widget');
  if (planBtn && planDrop) {
    planBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      var willOpen = planDrop.hidden;
      if (willOpen) {
        // Close Context dropdown so both never show at the same time
        if (typeof window.closeContextDropdown === 'function') {
          window.closeContextDropdown();
        } else {
          var ctxP = document.getElementById('ctx-panel');
          var ctxB = document.querySelector('.ctx');
          var ctxBtn = document.getElementById('ctx-bar-pill');
          if (ctxP) ctxP.hidden = true;
          if (ctxB) ctxB.classList.remove('open');
          if (ctxBtn) ctxBtn.setAttribute('aria-expanded', 'false');
        }
      }
      planDrop.hidden = !willOpen;
      if (planWidget) planWidget.classList.toggle('open', willOpen);
    });

    document.addEventListener('click', function (e) {
      if (!planDrop.hidden && !planDrop.contains(e.target) && !planBtn.contains(e.target)) {
        planDrop.hidden = true;
        if (planWidget) planWidget.classList.remove('open');
      }
    });
  }

  // Theme management: Instant Black / White theme toggle with persistence
  (function () {
    function applyTheme(theme) {
      if (theme !== 'black' && theme !== 'white') theme = 'white';
      document.documentElement.setAttribute('data-theme', theme);
      try { localStorage.setItem('upian_theme', theme); } catch (e) {}
      document.querySelectorAll('.theme-switch-btn').forEach(function (btn) {
        if (btn.getAttribute('data-theme-val') === theme) {
          btn.classList.add('active');
          btn.setAttribute('aria-pressed', 'true');
        } else {
          btn.classList.remove('active');
          btn.setAttribute('aria-pressed', 'false');
        }
      });
    }

    var current = 'white';
    try {
      var stored = localStorage.getItem('upian_theme');
      if (stored === 'black' || stored === 'white') current = stored;
    } catch (e) {}
    applyTheme(current);
    window.setAppTheme = applyTheme;

    document.addEventListener('click', function (e) {
      var btn = e.target.closest('.theme-switch-btn');
      if (btn) {
        var val = btn.getAttribute('data-theme-val');
        if (val) {
          e.preventDefault();
          applyTheme(val);
        }
      }
    });
  })();
})();

/* Global Sidebar and Live Panel Drawer Controller */
(function () {
  var _sidebarLock = 0;
  var _liveLock = 0;

  function setSidebarCollapsed(col) {
    var shell = document.querySelector('.shell') || document.getElementById('app-shell');
    var side = document.getElementById('sidebar');
    if (!side) return;
    if (col) {
      side.classList.add('collapsed');
      if (shell) shell.classList.add('sidebar-collapsed');
    } else {
      side.classList.remove('collapsed');
      if (shell) shell.classList.remove('sidebar-collapsed');
    }
    try { localStorage.setItem('upian_sidebar_collapsed', col ? '1' : '0'); } catch (e) {}
    var btn = document.getElementById('sidebar-toggle');
    if (btn) btn.setAttribute('aria-expanded', !col);
  }

  function setLiveCollapsed(col) {
    var shell = document.querySelector('.shell') || document.getElementById('app-shell');
    var live = document.getElementById('live-panel');
    if (!live) return;
    if (col) {
      live.classList.add('collapsed');
      if (shell) shell.classList.add('live-collapsed');
    } else {
      live.classList.remove('collapsed');
      if (shell) shell.classList.remove('live-collapsed');
    }
    try { localStorage.setItem('upian_live_collapsed', col ? '1' : '0'); } catch (e) {}
    var btn = document.getElementById('live-toggle');
    if (btn) {
      btn.setAttribute('aria-expanded', !col);
      if (col) btn.classList.add('panel-off');
      else btn.classList.remove('panel-off');
    }
  }

  window.toggleLeftSidebar = function () {
    var now = Date.now();
    if (now - _sidebarLock < 250) return;
    _sidebarLock = now;
    var side = document.getElementById('sidebar');
    var shell = document.querySelector('.shell') || document.getElementById('app-shell');
    var isCol = (side && side.classList.contains('collapsed')) || (shell && shell.classList.contains('sidebar-collapsed'));
    setSidebarCollapsed(!isCol);
  };

  window.toggleRightActivity = function () {
    var now = Date.now();
    if (now - _liveLock < 250) return;
    _liveLock = now;
    var live = document.getElementById('live-panel');
    var shell = document.querySelector('.shell') || document.getElementById('app-shell');
    var isCol = (live && live.classList.contains('collapsed')) || (shell && shell.classList.contains('live-collapsed'));
    setLiveCollapsed(!isCol);
  };

  // Restore saved collapse states on boot:
  try {
    if (localStorage.getItem('upian_sidebar_collapsed') === '1') {
      setSidebarCollapsed(true);
    }
    if (localStorage.getItem('upian_live_collapsed') === '1') {
      setLiveCollapsed(true);
    }
  } catch (e) {}

  // Single global click listener using event delegation:
  document.addEventListener('click', function (e) {
    var sideBtn = e.target.closest('#sidebar-toggle, .sidebar-dock-topbar-btn');
    if (sideBtn) {
      e.preventDefault();
      e.stopPropagation();
      window.toggleLeftSidebar();
      return;
    }
    var liveBtn = e.target.closest('#live-toggle, .activity-arrow-btn, #btn-close-live-arrow, .btn-live-close-arrow');
    if (liveBtn) {
      e.preventDefault();
      e.stopPropagation();
      window.toggleRightActivity();
      return;
    }
  });
})();

/* Nickname editing in the sidebar user card */
(function () {
  var profileView = document.getElementById('who-profile-view') || document.querySelector('.who-name-wrap');
  var displayEl = document.getElementById('who-display-name');
  var editBtn = document.getElementById('btn-edit-nickname');
  var editBox = document.getElementById('nickname-edit-box');
  var inputEl = document.getElementById('nickname-input');
  var saveBtn = document.getElementById('nickname-save-btn');
  var cancelBtn = document.getElementById('nickname-cancel-btn');

  if (!editBtn || !editBox || !inputEl) return;

  function openEdit(e) {
    if (e) e.stopPropagation();
    if (profileView) profileView.hidden = true;
    editBox.hidden = false;
    inputEl.value = displayEl ? displayEl.textContent.trim() : '';
    inputEl.focus();
    inputEl.select();
  }

  function closeEdit() {
    editBox.hidden = true;
    if (profileView) profileView.hidden = false;
  }

  editBtn.addEventListener('click', openEdit);
  if (displayEl) {
    displayEl.addEventListener('click', openEdit);
  }

  if (cancelBtn) cancelBtn.addEventListener('click', function (e) {
    e.stopPropagation();
    closeEdit();
  });

  async function saveNickname() {
    var val = inputEl.value.trim();
    if (!val) {
      inputEl.focus();
      return;
    }
    var csrf = document.querySelector('meta[name="csrf-token"]');
    var headers = { 'Content-Type': 'application/json' };
    if (csrf) headers['X-CSRF-Token'] = csrf.content;

    saveBtn.disabled = true;
    try {
      var res = await fetch('/api/user/nickname', {
        method: 'POST',
        headers: headers,
        body: JSON.stringify({ name: val })
      });
      var data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed to update nickname');

      if (displayEl) displayEl.textContent = data.name;
      var avatarEl = document.querySelector('.who-avatar');
      if (avatarEl && data.name) avatarEl.textContent = data.name.charAt(0).toUpperCase();

      closeEdit();
    } catch (err) {
      alert(err.message);
    } finally {
      saveBtn.disabled = false;
    }
  }

  if (saveBtn) saveBtn.addEventListener('click', function (e) {
    e.stopPropagation();
    saveNickname();
  });

  inputEl.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      saveNickname();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      closeEdit();
    }
  });
})();

/* Plan & Payment Modal Controller */
(function () {
  var modal = document.getElementById('plan-payment-modal');
  var closeBtn = document.getElementById('plan-modal-close');
  var buyPlanBtn = document.getElementById('btn-buy-plan');
  var confirmBtn = document.getElementById('btn-confirm-payment');
  var payBtnText = document.getElementById('pay-btn-text');
  var successView = document.getElementById('plan-modal-success');
  var bodyStep = document.getElementById('plan-modal-body-step');
  var doneBtn = document.getElementById('btn-success-done');
  var copyUpiBtn = document.getElementById('btn-copy-upi');
  var upiVpa = document.getElementById('upi-vpa');
  var planCards = document.querySelectorAll('.plan-card');
  var methodTabs = document.querySelectorAll('.pay-tab-btn');
  var tabViews = {
    razorpay: document.getElementById('view-razorpay'),
    upi: document.getElementById('view-upi'),
    card: document.getElementById('view-card'),
    netbanking: document.getElementById('view-netbanking')
  };

  var selectedPlan = null;
  var selectedMethod = 'razorpay';
  var modalMode = 'presets'; // 'presets' or 'dynamic'
  var customExtraFeatures = [];

  window.updateCustomPlanSummary = function () {
    var input = document.getElementById('custom-plan-name-input');
    var name = (input && input.value.trim()) ? input.value.trim() : 'My Custom Plan';
    var pill = document.getElementById('custom-summary-name-pill');
    if (pill) pill.textContent = name;
    window.recalculateModalPrice();
  };

  window.addCustomExtraFeature = function () {
    var input = document.getElementById('custom-extra-feature-input');
    if (!input) return;
    var feat = input.value.trim();
    if (!feat) return;
    if (customExtraFeatures.indexOf(feat) === -1) {
      customExtraFeatures.push(feat);
    }
    input.value = '';
    renderCustomExtraFeatures();
    window.recalculateModalPrice();
  };

  window.removeCustomExtraFeature = function (feat) {
    customExtraFeatures = customExtraFeatures.filter(function (f) { return f !== feat; });
    renderCustomExtraFeatures();
    window.recalculateModalPrice();
  };

  function renderCustomExtraFeatures() {
    var container = document.getElementById('custom-extra-features-container');
    if (!container) return;
    container.innerHTML = customExtraFeatures.map(function (f) {
      return '<span class="chip" style="font-size:12px;">' +
             f +
             ' <button type="button" onclick="removeCustomExtraFeature(\'' + f.replace(/'/g, "\\'") + '\')" style="background:none;border:none;color:var(--text-secondary);cursor:pointer;font-weight:bold;padding:0 2px;">&times;</button>' +
             '</span>';
    }).join('');
  }

  window.currentBillingCycle = 'monthly';

  function getServerPlans() {
    if (window.SERVER_PLANS) return window.SERVER_PLANS;
    try {
      var scriptTag = document.getElementById('server-plans-data');
      if (scriptTag && scriptTag.textContent) {
        window.SERVER_PLANS = JSON.parse(scriptTag.textContent);
        return window.SERVER_PLANS;
      }
    } catch (e) {
      console.warn('Could not parse server-plans-data:', e);
    }
    return {};
  }

  function getPlanPrice(plan, cycle) {
    var plans = getServerPlans();
    var key = (plan === 'pro') ? 'plus' : ((plan === 'pro_plus') ? 'plus_pro' : plan);
    var info = plans[plan] || plans[key];
    if (info) {
      if (cycle === 'yearly') {
        var yr = parseFloat(info.price_inr_yearly || 0);
        if (yr > 0) return yr;
        var mo = parseFloat(info.price_inr || 0);
        return Math.round(mo * 10);
      }
      return parseFloat(info.price_inr || 0);
    }
    var card = document.querySelector('#card-modal-' + plan) ||
               document.querySelector('#card-modal-' + key) ||
               document.querySelector('.plan-card[data-plan="' + plan + '"]') ||
               document.querySelector('.plan-card[data-plan="' + key + '"]');
    if (card) {
      var attr = (cycle === 'yearly') ? 'data-yearly-inr' : 'data-monthly-inr';
      var val = parseFloat(card.getAttribute(attr) || '0');
      if (val > 0) return val;
    }
    return 0;
  }

  window.switchBillingCycle = function (cycle) {
    window.currentBillingCycle = cycle;
    var btns = document.querySelectorAll('.btn-cycle-opt');
    btns.forEach(function (b) {
      if (b.id === 'btn-cycle-' + cycle || b.getAttribute('data-cycle') === cycle) {
        b.classList.add('active');
      } else {
        b.classList.remove('active');
      }
    });

    // Dynamically update each plan card in the modal
    var cards = document.querySelectorAll('#modal-view-presets .plan-card');
    cards.forEach(function (card) {
      var planId = card.getAttribute('data-plan');
      if (!planId || planId === 'free') return;
      var inrPrice = (cycle === 'yearly') ? parseInt(card.getAttribute('data-yearly-inr') || '0', 10) : parseInt(card.getAttribute('data-monthly-inr') || '0', 10);
      var usdPrice = (cycle === 'yearly') ? parseInt(card.getAttribute('data-yearly-usd') || '0', 10) : parseInt(card.getAttribute('data-monthly-usd') || '0', 10);
      var label = card.getAttribute('data-label') || planId;

      var amountEl = card.querySelector('.plan-card-price .amount');
      var periodEl = card.querySelector('.plan-card-price .period');
      var usdEl = card.querySelector('.plan-card-price .usd-alt');
      var btnEl = card.querySelector('.btn-plan-select');

      if (amountEl) amountEl.textContent = inrPrice.toLocaleString();
      if (periodEl) periodEl.textContent = (cycle === 'yearly') ? '/yr' : '/mo';
      if (usdEl) usdEl.textContent = (cycle === 'yearly') ? '($' + usdPrice.toLocaleString() + ' USD/yr)' : '($' + usdPrice.toLocaleString() + ' USD)';
      if (btnEl) btnEl.textContent = 'Choose ' + label + ' (₹' + inrPrice.toLocaleString() + (cycle === 'yearly' ? '/yr' : '') + ')';
    });

    updatePayButton();
  };

  window.clearModalPlanSelection = function () {
    selectedPlan = null;
    var cards = document.querySelectorAll('#modal-view-presets .plan-card');
    cards.forEach(function (c) {
      c.classList.remove('selected');
      var r = c.querySelector('input[type="radio"]');
      if (r) r.checked = false;
    });
    if (paySection) paySection.hidden = true;
    if (confirmBtn) confirmBtn.disabled = true;
    if (payBtnText) payBtnText.textContent = 'Select a plan above to continue';
  };

  window.openPlanModal = function (preferredPlan) {
    if (!modal) return;
    window.switchModalMode('presets');
    window.clearModalPlanSelection();
    if (preferredPlan === 'custom') {
      window.switchModalMode('dynamic');
      window.selectModalPlan('custom');
    }
    // Never auto-select Pro or any other plan when opening modal.
    // Let all cards start clean and unselected so the user chooses intentionally.
    if (bodyStep) bodyStep.hidden = false;
    if (successView) successView.hidden = true;
    modal.hidden = false;
  };

  window.closePlanModal = function () {
    if (!modal) return;
    modal.hidden = true;
  };

  if (closeBtn) closeBtn.addEventListener('click', window.closePlanModal);
  if (doneBtn) doneBtn.addEventListener('click', function () {
    window.closePlanModal();
    window.location.reload();
  });

  if (modal) {
    modal.addEventListener('click', function (e) {
      if (e.target === modal) window.closePlanModal();
    });
  }

  var paySection = document.querySelector('.pay-section');
  var usageBox = document.getElementById('usage-box') || document.querySelector('.usage');

  if (usageBox && !usageBox.getAttribute('onclick')) {
    usageBox.addEventListener('click', function (e) {
      e.stopPropagation();
      window.openPlanModal();
    });
  }

  if (buyPlanBtn && buyPlanBtn.tagName === 'BUTTON') {
    buyPlanBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      window.openPlanModal();
    });
  }

  /* Mode Switcher / Selection */
  window.switchModalMode = function (mode) {
    if (mode === 'dynamic' || mode === 'custom') {
      window.selectModalPlan('custom');
    }
  };

  window.selectModalPlan = function (plan) {
    if (selectedPlan === plan) {
      // Clicking the already selected plan deselects it
      window.clearModalPlanSelection();
      return;
    }
    selectedPlan = plan;
    var cards = document.querySelectorAll('#modal-view-presets .plan-card');
    cards.forEach(function (c) {
      if (c.getAttribute('data-plan') === plan) {
        c.classList.add('selected');
        var r = c.querySelector('input[type="radio"]');
        if (r) r.checked = true;
      } else {
        c.classList.remove('selected');
        var r2 = c.querySelector('input[type="radio"]');
        if (r2) r2.checked = false;
      }
    });
    updatePayButton();
  };

  /* Jump to Dynamic Builder from any Preset card with that preset loaded */
  window.customizeFromPlan = function (plan, ev) {
    if (ev) ev.stopPropagation();
    window.switchModalMode('dynamic');
    window.loadModalPreset(plan === 'free' ? 'core' : (plan === 'pro_plus' ? 'pro_plus' : 'pro'));
  };

  /* Quick presets inside builder */
  window.loadModalPreset = function (presetKey) {
    var buttons = document.querySelectorAll('.dyn-quick-presets .btn-quick-preset');
    buttons.forEach(function (b) { b.classList.remove('active'); });

    var targetId = (presetKey === 'pro') ? 'dyn-pre-pro' : ((presetKey === 'pro_plus') ? 'dyn-pre-max' : null);
    if (targetId) {
      var tb = document.getElementById(targetId);
      if (tb) tb.classList.add('active');
    }

    var targets = {
      core: ['core'],
      pro: ['core', 'image_gen', 'reasoning'],
      pro_plus: ['core', 'image_gen', 'reasoning', 'ocr_pdf', 'vision_vqa', 'priority_sla'],
      all: ['core', 'image_gen', 'reasoning', 'ocr_pdf', 'vision_vqa', 'priority_sla', 'high_quota']
    };

    var activeList = targets[presetKey] || targets.pro;
    var allCbs = document.querySelectorAll('.modal-feat-cb');
    allCbs.forEach(function (cb) {
      if (cb.value === 'core') return; // core is always checked
      cb.checked = activeList.indexOf(cb.value) !== -1;
    });

    window.recalculateModalPrice();
  };

  /* Toggle feature by card click */
  window.toggleModalFeature = function (code, ev) {
    if (ev && ev.target && ev.target.tagName === 'INPUT') return;
    var cb = document.getElementById('mcb-' + code);
    if (cb && !cb.disabled) {
      cb.checked = !cb.checked;
      window.recalculateModalPrice();
    }
  };

  /* Dynamic price calculation */
  window.recalculateModalPrice = function () {
    var cbs = document.querySelectorAll('.modal-feat-cb:checked');
    var total = 0;
    var pills = [];

    cbs.forEach(function (cb) {
      total += parseInt(cb.getAttribute('data-price') || '0', 10);
      var name = cb.getAttribute('data-name') || cb.value;
      pills.push('<span class="dyn-feat-pill active">' + name + '</span>');
    });

    var isYearly = (window.currentBillingCycle === 'yearly');
    var displayTotal = isYearly ? Math.round(total * 10) : total;

    // Update card styling
    var allCbs = document.querySelectorAll('.modal-feat-cb');
    allCbs.forEach(function (cb) {
      var card = document.getElementById('dyn-card-' + cb.value);
      if (card) {
        if (cb.checked) card.classList.add('checked');
        else card.classList.remove('checked');
      }
    });

    var priceEl = document.getElementById('modal-dyn-price');
    if (priceEl) priceEl.textContent = displayTotal.toLocaleString();

    var customPill = document.getElementById('btn-custom-price-pill');
    if (customPill) customPill.textContent = '₹' + displayTotal.toLocaleString() + (isYearly ? '/yr' : '');

    var customCardSelectBtn = document.getElementById('btn-custom-card-select');
    if (customCardSelectBtn) {
      customCardSelectBtn.innerHTML = 'Choose Custom (<span id="btn-custom-price-pill">₹' + displayTotal.toLocaleString() + (isYearly ? '/yr' : '') + '</span>)';
    }

    var usdEl = document.getElementById('modal-dyn-usd');
    if (usdEl) {
      var usd = Math.round(displayTotal / 83);
      usdEl.textContent = '($' + usd + ' USD' + (isYearly ? '/yr)' : ')');
    }

    var upiAmount = document.getElementById('modal-upi-amount-display');
    if (upiAmount) upiAmount.textContent = '₹' + displayTotal.toLocaleString();

    var pillsBox = document.getElementById('modal-dyn-pills');
    if (pillsBox) pillsBox.innerHTML = pills.join('');

    var savingsBadge = document.getElementById('modal-bundle-savings');
    if (savingsBadge) {
      savingsBadge.style.display = (cbs.length >= 3) ? 'inline-block' : 'none';
    }

    if (modalMode === 'dynamic' || selectedPlan === 'custom') {
      if (paySection) paySection.hidden = false;
      if (confirmBtn) confirmBtn.disabled = false;
      var cNameInput = document.getElementById('custom-plan-name-input');
      var cName = (cNameInput && cNameInput.value.trim()) ? cNameInput.value.trim() : 'Custom Plan';
      var cycleLabel = isYearly ? ' (Yearly - 365 Days)' : ' (Monthly)';
      if (payBtnText) payBtnText.textContent = 'Pay ₹' + displayTotal.toLocaleString() + ' with Razorpay & Activate ' + cName + cycleLabel;
    }
  };

  window.updateModalCustomPrice = window.recalculateModalPrice;

  function updatePayButton() {
    if (!payBtnText || !confirmBtn) return;
    if (!selectedPlan) {
      if (paySection) paySection.hidden = true;
      confirmBtn.disabled = true;
      payBtnText.textContent = 'Select a plan above to continue';
      return;
    }
    var planBadgeEl = document.getElementById('btn-buy-plan');
    var isCurrentFree = !planBadgeEl || planBadgeEl.classList.contains('free');

    var upiAmount = document.getElementById('modal-upi-amount-display');
    var isYearly = (window.currentBillingCycle === 'yearly');
    var cycleLabel = isYearly ? ' (Yearly - 365 Days)' : ' (Monthly)';

    if (selectedPlan === 'free') {
      if (paySection) paySection.hidden = true;
      if (upiAmount) upiAmount.textContent = '₹0';
      if (isCurrentFree) {
        confirmBtn.disabled = true;
        payBtnText.textContent = 'You are currently on Free Plan';
      } else {
        confirmBtn.disabled = false;
        payBtnText.textContent = 'Downgrade to Free Plan';
      }
    } else {
      if (paySection) paySection.hidden = false;
      confirmBtn.disabled = false;
      var card = document.querySelector('#modal-view-presets .plan-card[data-plan="' + selectedPlan + '"]');
      var amt = getPlanPrice(selectedPlan, window.currentBillingCycle);
      if ((!amt || amt === 0) && card) {
        amt = isYearly ? parseInt(card.getAttribute('data-yearly-inr') || '0', 10) : parseInt(card.getAttribute('data-monthly-inr') || '0', 10);
      }
      var planLabel = (card && card.getAttribute('data-label')) || (serverPlans[selectedPlan] && serverPlans[selectedPlan].label) || selectedPlan;
      payBtnText.textContent = 'Pay ₹' + amt.toLocaleString() + ' with Razorpay & Upgrade to ' + planLabel + cycleLabel;
      if (upiAmount) upiAmount.textContent = '₹' + amt.toLocaleString();
    }
  }

  methodTabs.forEach(function (btn) {
    btn.addEventListener('click', function () {
      methodTabs.forEach(function (b) { b.classList.remove('active'); });
      btn.classList.add('active');
      selectedMethod = btn.getAttribute('data-method') || 'razorpay';

      Object.keys(tabViews).forEach(function (k) {
        if (tabViews[k]) tabViews[k].hidden = (k !== selectedMethod);
      });
    });
  });

  if (copyUpiBtn && upiVpa) {
    copyUpiBtn.addEventListener('click', function () {
      var text = upiVpa.textContent.trim();
      navigator.clipboard.writeText(text).then(function () {
        var prev = copyUpiBtn.textContent;
        copyUpiBtn.textContent = 'Copied!';
        setTimeout(function () { copyUpiBtn.textContent = prev; }, 1800);
      }).catch(function () {
        alert('UPI ID: ' + text);
      });
    });
  }

  async function finalizePaymentProcess(payload, headers) {
    var res = await fetch('/api/payment/process', {
      method: 'POST',
      headers: headers,
      body: JSON.stringify(payload)
    });
    var data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Payment failed');

    // Show success screen
    if (bodyStep) bodyStep.hidden = true;
    if (successView) successView.hidden = false;

    var txEl = document.getElementById('success-tx-id');
    if (txEl && data.transaction_id) {
      txEl.textContent = 'TX: ' + data.transaction_id;
    }

    var msgEl = document.getElementById('success-plan-msg');
    if (msgEl) {
      msgEl.innerHTML = 'Welcome to <b>Upian AI ' + (data.plan_label || 'Pro') + '</b>! Your plan has been saved and your quota is boosted.';
    }

    // Live update the sidebar badge!
    var badge = document.getElementById('btn-buy-plan');
    var newPlan = data.plan || selectedPlan;
    var newLabel = data.plan_label || (newPlan === 'free' ? 'Free' : (newPlan === 'pro_plus' ? 'Pro Plus' : 'Pro'));
    if (badge) {
      badge.className = 'who-plan-badge ' + newPlan;
      badge.innerHTML = '<span id="who-plan-label">' + newLabel + '</span>' + (newPlan === 'free' ? '<span class="badge-upgrade-cue">Upgrade</span>' : '');
    }
    var planNameEl = document.getElementById('plan-name');
    if (planNameEl) {
      planNameEl.textContent = newLabel;
    }

    // Close any upgrade modal if open
    var upModal = document.getElementById('upgrade-modal');
    if (upModal) upModal.hidden = true;
  }

  if (confirmBtn) {
    confirmBtn.addEventListener('click', async function () {
      if (!selectedPlan) {
        alert('Please select a plan first.');
        return;
      }
      var csrf = document.querySelector('meta[name="csrf-token"]');
      var headers = { 'Content-Type': 'application/json' };
      if (csrf) headers['X-CSRF-Token'] = csrf.content;

      confirmBtn.disabled = true;
      var prevText = payBtnText ? payBtnText.textContent : '';
      if (payBtnText) payBtnText.textContent = 'Processing Payment...';

      try {
        var isYearly = (window.currentBillingCycle === 'yearly');
        var card = document.querySelector('#modal-view-presets .plan-card[data-plan="' + selectedPlan + '"]');
        var amt = getPlanPrice(selectedPlan, window.currentBillingCycle);
        if ((!amt || amt === 0) && card) {
          amt = isYearly ? parseInt(card.getAttribute('data-yearly-inr') || '0', 10) : parseInt(card.getAttribute('data-monthly-inr') || '0', 10);
        }
        var payload = {
          plan: selectedPlan,
          billing_cycle: window.currentBillingCycle || 'monthly',
          payment_method: selectedMethod,
          amount: amt
        };

        if (selectedMethod === 'razorpay' && window.Razorpay) {
          try {
            var ordPayload = {
              plan: selectedPlan,
              billing_cycle: payload.billing_cycle,
              amount: payload.amount || amt || null,
              custom_plan_name: payload.custom_plan_name || null
            };
            var ordRes = await fetch('/api/payment/razorpay/order', {
              method: 'POST',
              headers: headers,
              body: JSON.stringify(ordPayload)
            });
            var ordData = await ordRes.json();
            if (ordData.success && ordData.razorpay_enabled && ordData.order_id && !ordData.simulated) {
              if (ordData.amount) {
                payload.amount = ordData.amount;
              }
              var rzp = new Razorpay({
                key: ordData.key_id,
                amount: Math.round(ordData.amount * 100),
                currency: ordData.currency || 'INR',
                name: 'Upian AI',
                description: 'Subscription for ' + (ordData.plan_label || 'Pro'),
                order_id: ordData.order_id,
                handler: async function (response) {
                  payload.razorpay_payment_id = response.razorpay_payment_id;
                  payload.razorpay_order_id = response.razorpay_order_id;
                  payload.razorpay_signature = response.razorpay_signature;
                  try {
                    await finalizePaymentProcess(payload, headers);
                  } catch (pErr) {
                    alert(pErr.message || 'Payment activation error');
                  }
                },
                prefill: {
                  name: document.getElementById('card-name') ? document.getElementById('card-name').value : '',
                  email: ''
                },
                theme: { color: '#ee7f35' }
              });
              rzp.on('payment.failed', function (resp) {
                alert('Payment failed: ' + (resp.error.description || 'Unknown error'));
              });
              rzp.open();
              confirmBtn.disabled = false;
              if (payBtnText) payBtnText.textContent = prevText;
              return;
            }
          } catch (ordErr) {
            console.warn('Razorpay order creation fallback:', ordErr);
          }
        }

        // Direct process fallback
        await finalizePaymentProcess(payload, headers);

      } catch (err) {
        alert(err.message || 'Payment processing error');
      } finally {
        confirmBtn.disabled = false;
        if (payBtnText) payBtnText.textContent = prevText;
      }
    });
  }
})();

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
      var nl = p.indexOf('\n'), code = p;
      if (nl > -1 && /^[\w+#.\-]*$/.test(p.slice(0, nl).trim())) code = p.slice(nl + 1);
      return '<pre><code>' + esc(code.replace(/\n$/, '')) + '</code></pre>';
    }
    return mdText(p);
  }).join('');
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
    topPlanCount.textContent = fmtNum(u.day.used) + ' of ' + fmtNum(u.day.limit);
  }
}
(function () {
  var boot = document.getElementById('boot');
  if (boot) { try { renderUsage(JSON.parse(boot.textContent).usage); } catch (e) {} }

  // Topbar Plan dropdown toggle
  var planBtn = document.getElementById('topbar-plan-btn');
  var planDrop = document.getElementById('topbar-plan-dropdown');
  var planWidget = document.getElementById('topbar-plan-widget');
  if (planBtn && planDrop) {
    planBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      var isHidden = planDrop.hidden;
      planDrop.hidden = !isHidden;
      if (planWidget) planWidget.classList.toggle('open', isHidden);
    });
    document.addEventListener('click', function (e) {
      if (!planDrop.hidden && !planDrop.contains(e.target) && e.target !== planBtn) {
        planDrop.hidden = true;
        if (planWidget) planWidget.classList.remove('open');
      }
    });
  }
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
    upi: document.getElementById('view-upi'),
    card: document.getElementById('view-card'),
    netbanking: document.getElementById('view-netbanking')
  };

  var selectedPlan = 'pro';
  var selectedMethod = 'upi';

  window.openPlanModal = function (preferredPlan) {
    if (!modal) return;
    if (preferredPlan) {
      selectedPlan = preferredPlan;
      var targetCard = document.querySelector('.plan-card[data-plan="' + preferredPlan + '"]');
      if (targetCard) selectPlan(targetCard);
    }
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
      window.openPlanModal('pro');
    });
  }

  function selectPlan(card) {
    planCards.forEach(function (c) { c.classList.remove('selected'); });
    card.classList.add('selected');
    var radio = card.querySelector('input[type="radio"]');
    if (radio) radio.checked = true;
    selectedPlan = card.getAttribute('data-plan') || 'pro';
    updatePayButton();
  }

  planCards.forEach(function (card) {
    card.addEventListener('click', function () {
      selectPlan(card);
    });
  });

  function updatePayButton() {
    if (!payBtnText || !confirmBtn) return;
    var planBadgeEl = document.getElementById('btn-buy-plan');
    var isCurrentFree = !planBadgeEl || planBadgeEl.classList.contains('free');

    if (selectedPlan === 'free') {
      if (paySection) paySection.hidden = true;
      if (isCurrentFree) {
        confirmBtn.disabled = true;
        payBtnText.textContent = 'You are currently on Free Plan';
      } else {
        confirmBtn.disabled = false;
        payBtnText.textContent = 'Downgrade to Free Plan';
      }
    } else if (selectedPlan === 'pro_plus') {
      if (paySection) paySection.hidden = false;
      confirmBtn.disabled = false;
      payBtnText.textContent = 'Pay ₹1,999 & Upgrade to Pro Plus';
    } else {
      if (paySection) paySection.hidden = false;
      confirmBtn.disabled = false;
      payBtnText.textContent = 'Pay ₹999 & Upgrade to Pro';
    }
  }

  methodTabs.forEach(function (btn) {
    btn.addEventListener('click', function () {
      methodTabs.forEach(function (b) { b.classList.remove('active'); });
      btn.classList.add('active');
      selectedMethod = btn.getAttribute('data-method') || 'upi';

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

  if (confirmBtn) {
    confirmBtn.addEventListener('click', async function () {
      var csrf = document.querySelector('meta[name="csrf-token"]');
      var headers = { 'Content-Type': 'application/json' };
      if (csrf) headers['X-CSRF-Token'] = csrf.content;

      confirmBtn.disabled = true;
      var prevText = payBtnText ? payBtnText.textContent : '';
      if (payBtnText) payBtnText.textContent = 'Processing Payment...';

      try {
        var res = await fetch('/api/payment/process', {
          method: 'POST',
          headers: headers,
          body: JSON.stringify({
            plan: selectedPlan,
            payment_method: selectedMethod
          })
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
          msgEl.innerHTML = 'Welcome to <b>Upian AI ' + (data.plan_label || 'Pro') + '</b>! Your account has been upgraded and your quota is boosted.';
        }

        // Live update the sidebar badge!
        var badge = document.getElementById('btn-buy-plan');
        var newPlan = data.plan || selectedPlan;
        var newLabel = data.plan_label || (newPlan === 'free' ? 'Free' : (newPlan === 'pro_plus' ? 'Pro Plus' : 'Pro'));
        if (badge) {
          badge.className = 'who-plan-badge ' + newPlan;
          badge.innerHTML = '<span id="who-plan-label">' + newLabel + '</span>' + (newPlan === 'free' ? '<span class="badge-upgrade-cue">⚡ Upgrade</span>' : '');
        }
        var planNameEl = document.getElementById('plan-name');
        if (planNameEl) {
          planNameEl.textContent = newLabel;
        }

        // Close any upgrade modal if open
        var upModal = document.getElementById('upgrade-modal');
        if (upModal) upModal.hidden = true;

      } catch (err) {
        alert(err.message || 'Payment processing error');
      } finally {
        confirmBtn.disabled = false;
        if (payBtnText) payBtnText.textContent = prevText;
      }
    });
  }
})();

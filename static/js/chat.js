(function () {
  var boot = JSON.parse(document.getElementById('boot').textContent);
  var $ = function (id) { return document.getElementById(id); };
  var thread = $('thread'), scroller = $('scroller'), form = $('composer'), input = $('prompt');
  var sendBtn = $('send'), liveList = $('live-list');
  var emptyHTML = $('empty').outerHTML;
  var KEY = 'upian_chat_' + boot.uid;
  /* Multi-conversation Chat History */
  var SESSIONS_KEY = 'upian_sessions_' + boot.uid;
  var ACTIVE_SESSION_KEY = 'upian_active_session_' + boot.uid;
  var sessions = [];
  try { sessions = JSON.parse(localStorage.getItem(SESSIONS_KEY) || '[]'); } catch (e) { sessions = []; }
  var currentSessionId = localStorage.getItem(ACTIVE_SESSION_KEY) || null;

  var history = [];
  try { history = JSON.parse(localStorage.getItem(KEY) || '[]'); } catch (e) { history = []; }

  // Migration from legacy single-history storage:
  if (!sessions.length && history.length) {
    var firstUser = history.find(function (m) { return m.role === 'user'; });
    var initTitle = firstUser ? firstUser.content.slice(0, 36) : 'Conversation';
    var initId = 'sess_' + Date.now();
    sessions.push({
      id: initId,
      title: initTitle,
      createdAt: Date.now(),
      updatedAt: Date.now()
    });
    try {
      localStorage.setItem('upian_chat_sess_' + initId, JSON.stringify(history));
      localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions));
      localStorage.setItem(ACTIVE_SESSION_KEY, initId);
    } catch (e) {}
    currentSessionId = initId;
  } else if (currentSessionId) {
    try {
      var sessMsgs = JSON.parse(localStorage.getItem('upian_chat_sess_' + currentSessionId) || '[]');
      if (sessMsgs && sessMsgs.length) {
        history = sessMsgs;
      }
    } catch (e) {}
  }

  function getSessionMessages(sessId) {
    if (!sessId) return [];
    try {
      return JSON.parse(localStorage.getItem('upian_chat_sess_' + sessId) || '[]');
    } catch (e) {
      return [];
    }
  }

  function saveSessionMessages(sessId, msgs) {
    if (!sessId) return;
    try {
      localStorage.setItem('upian_chat_sess_' + sessId, JSON.stringify(msgs.slice(-60)));
    } catch (e) {}
  }

  function renderChatHistory() {
    var cont = document.getElementById('side-history-list');
    if (!cont) return;
    if (!sessions.length) {
      cont.innerHTML = '<div class="history-empty">No previous chats yet.</div>';
      return;
    }
    cont.innerHTML = sessions.map(function (s) {
      var isActive = s.id === currentSessionId;
      return '<div class="history-item' + (isActive ? ' active' : '') + '" data-sess-id="' + esc(s.id) + '">' +
        '<span class="history-item-icon">💬</span>' +
        '<span class="history-item-title" title="' + esc(s.title) + '">' + esc(s.title) + '</span>' +
        '<div class="history-item-actions">' +
          '<button type="button" class="history-act-btn" data-del-sess="' + esc(s.id) + '" title="Delete chat">&times;</button>' +
        '</div>' +
      '</div>';
    }).join('');
  }

  function switchChat(sessId) {
    if (sessId === currentSessionId) return;
    if (busy) {
      if (!confirm('A response is currently generating. Stop and switch to another chat?')) {
        return;
      }
      stopCurrentPrompt();
    }
    currentSessionId = sessId;
    try { localStorage.setItem(ACTIVE_SESSION_KEY, sessId); } catch (e) {}
    history = getSessionMessages(sessId);
    thread.innerHTML = '';
    if (!history.length) {
      thread.innerHTML = emptyHTML;
    } else {
      history.forEach(function (m) { addMessage(m.role, m.content, m.meta, m.files_used, m.extra, m.versions, m.versionIndex); });
      document.querySelectorAll('.msg .meta').forEach(function (el) { el.remove(); });
    }
    uploadedFiles = [];
    renderUploadedChips();
    temp = [];
    drawTemp();
    ctxUpdate();
    toBottom();
    renderChatHistory();
    input.focus();
  }

  function startNewChat() {
    if (busy) {
      if (!confirm('A response is currently generating. Stop and start a fresh chat?')) {
        return;
      }
      stopCurrentPrompt();
    }
    currentSessionId = null;
    try { localStorage.removeItem(ACTIVE_SESSION_KEY); } catch (e) {}
    history = [];
    thread.innerHTML = emptyHTML;
    uploadedFiles = [];
    renderUploadedChips();
    temp = [];
    drawTemp();
    ctxUpdate();
    renderChatHistory();
    input.focus();
  }

  var busy = false;
  var currentAbortController = null;
  var currentStreamReader = null;
  var currentPendingMsg = null;
  var currentLiveRow = null;
  var currentRequestId = null;

  function setBusyState(isBusy) {
    busy = isBusy;
    if (isBusy) {
      sendBtn.disabled = false;
      sendBtn.classList.add('stop-mode');
      sendBtn.innerHTML = '<span class="stop-sq">■</span> <span>Stop</span>';
      sendBtn.title = 'Stop generating (Esc)';
    } else {
      resetSendBtn();
    }
  }

  function resetSendBtn() {
    busy = false;
    sendBtn.classList.remove('stop-mode');
    sendBtn.innerHTML = 'Send';
    sendBtn.title = 'Send message';
    sendBtn.disabled = false;
    currentAbortController = null;
    currentStreamReader = null;
    currentPendingMsg = null;
    currentLiveRow = null;
    currentRequestId = null;
  }

  function stopCurrentPrompt() {
    if (!busy) return;
    if (currentRequestId) {
      try {
        if (navigator.sendBeacon) {
          navigator.sendBeacon('/api/chat/stop', JSON.stringify({ request_id: currentRequestId }));
        } else {
          api('/api/chat/stop', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ request_id: currentRequestId })
          });
        }
      } catch (e) {}
    }
    if (currentAbortController) {
      try { currentAbortController.abort(); } catch (e) {}
    }
    if (currentStreamReader) {
      try { currentStreamReader.cancel(); } catch (e) {}
    }
    if (currentPendingMsg && currentPendingMsg.isConnected) {
      var bodyEl = currentPendingMsg.querySelector('.body');
      var partial = currentPendingMsg._partialText || '';
      var stoppedNotice = (partial ? renderMd(partial) : '<em>Generation stopped by user.</em>') +
        ' <span class="dim small" style="margin-left:8px;font-weight:700;color:var(--bad);">(stopped)</span>';
      if (bodyEl) {
        bodyEl.innerHTML = stoppedNotice;
        bodyEl.classList.remove('pending-text');
      }
      currentPendingMsg._text = (partial || 'Generation stopped by user.') + ' (stopped)';
      history.push({ role: 'assistant', content: currentPendingMsg._text, stopped: true });
      save();
    }
    if (currentLiveRow && currentLiveRow.stop) {
      currentLiveRow.stop();
    }
    resetSendBtn();
  }

  /* attach files picker */
  var chips = $('chips'), docs = [];
  var attachList = document.createElement('div');
  attachList.id = 'attach-list';
  attachList.className = 'attach-list-inner';

  function selectedIds() {
    return Array.from(attachList.querySelectorAll('input:checked')).map(function (c) { return Number(c.value); });
  }

  function refreshChips() {
    var ids = selectedIds();
    chips.innerHTML = docs.filter(function (d) { return ids.indexOf(d.id) >= 0; }).map(function (d) {
      return '<span class="chip">📄 ' + esc(d.filename) + ' <b data-uncheck="' + d.id + '" title="Remove">&times;</b></span>';
    }).join('');
  }

  chips.addEventListener('click', function (e) {
    var rm = e.target.closest('[data-uncheck]');
    if (rm) {
      var cb = attachList.querySelector('input[value="' + rm.dataset.uncheck + '"]');
      if (cb) { cb.checked = false; refreshChips(); }
    }
  });

  function loadDocs() {
    api('/api/docs').then(function (r) { return r.json(); }).then(function (d) {
      docs = d.items;
      attachList.innerHTML = docs.length ? docs.map(function (f) {
        return '<label class="attach-row"><input type="checkbox" value="' + f.id + '"> <span>' + esc(f.filename) + '</span></label>';
      }).join('') : '<span class="dim small" style="padding:6px 8px;display:block">No files in vault yet. <a href="/files" target="_blank">Upload in My Files</a>.</span>';
      refreshChips();
    }).catch(function () { attachList.textContent = 'Could not load files.'; });
  }
  loadDocs();
  attachList.addEventListener('change', refreshChips);

  /* save an answer as a file */
  var LOCAL = { csv: 1, json: 1, html: 1, xlsx: 1 };
  function download(blob, name) {
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
  }
  function csvCell(v) { v = String(v == null ? '' : v); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }
  function loadXlsx() {
    return new Promise(function (ok, fail) {
      if (window.XLSX) return ok();
      var s = document.createElement('script');
      s.src = '/static/js/xlsx.full.min.js';
      s.onload = function () { ok(); };
      s.onerror = function () { fail(new Error('Could not load the Excel library. Check your internet connection.')); };
      document.head.appendChild(s);
    });
  }
  function saveXlsx(tables) {
    loadXlsx().then(function () {
      var wb = XLSX.utils.book_new();
      tables.forEach(function (t, k) {
        var ws = XLSX.utils.aoa_to_sheet([t.head].concat(t.rows));
        XLSX.utils.book_append_sheet(wb, ws, 'Table ' + (k + 1));
      });
      XLSX.writeFile(wb, 'upian-answer.xlsx');
    }).catch(function (e) { alert(e.message); });
  }
  function loadXlsx() {
    return new Promise(function (ok, fail) {
      if (window.XLSX) return ok();
      var s = document.createElement('script');
      s.src = '/static/js/xlsx.full.min.js';
      s.onload = function () { ok(); };
      s.onerror = function () { fail(new Error('Could not load the Excel library.')); };
      document.head.appendChild(s);
    });
  }
  function saveXlsx(tables) {
    loadXlsx().then(function () {
      var wb = XLSX.utils.book_new();
      tables.forEach(function (t, k) {
        var ws = XLSX.utils.aoa_to_sheet([t.head].concat(t.rows));
        XLSX.utils.book_append_sheet(wb, ws, 'Table ' + (k + 1));
      });
      XLSX.writeFile(wb, 'upian-answer.xlsx');
    }).catch(function (e) { alert(e.message); });
  }
  function saveLocal(fmt, text) {
    var tables = mdTables(text);
    if (fmt === 'xlsx') { saveXlsx(tables); return; }
    if (fmt === 'xlsx') { saveXlsx(tables); return; }
    if (fmt === 'csv') {
      var rows = [];
      tables.forEach(function (t, k) {
        if (k) rows.push([]);
        rows.push(t.head); t.rows.forEach(function (r) { rows.push(r); });
      });
      var csv = rows.map(function (r) { return r.map(csvCell).join(','); }).join('\r\n');
      download(new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' }), 'upian-answer.csv');
    } else if (fmt === 'json') {
      var data = tables.length ? { text: text, tables: tables.map(function (t) {
        return t.rows.map(function (r) { var o = {}; t.head.forEach(function (h, k) { o[h] = r[k] == null ? '' : r[k]; }); return o; });
      }) } : { text: text };
      download(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }), 'upian-answer.json');
    } else {
      var html = '<!doctype html><html><head><meta charset="utf-8"><title>Upian answer</title><style>' +
        'body{font:16px/1.6 system-ui,sans-serif;max-width:800px;margin:2em auto;padding:0 1em}' +
        'table{border-collapse:collapse;width:100%}th,td{border:1px solid #bbb;padding:6px 10px}th{background:#eee}' +
        'pre{background:#f4f4f4;padding:12px;overflow:auto}</style></head><body>' + renderMd(text) + '</body></html>';
      download(new Blob([html], { type: 'text/html;charset=utf-8' }), 'upian-answer.html');
    }
  }
  thread.addEventListener('click', function (e) {
    var b = e.target.closest('[data-save]');
    if (!b) return;
    var msg = b.closest('.msg');
    if (LOCAL[b.dataset.save]) { saveLocal(b.dataset.save, msg._text); return; }
    b.disabled = true;
    api('/api/create-file', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: msg._text, format: b.dataset.save }) })
      .then(function (r) {
        if (!r.ok) return r.json().then(function (j) { throw new Error(j.error || 'Could not create the file.'); });
        return r.blob();
      })
      .then(function (blob) { download(blob, 'upian-answer.' + b.dataset.save); })
      .catch(function (err) { alert(err.message); })
      .finally(function () { b.disabled = false; });
  });

  /* single Download button with a format menu */
  (function () {
    var st = document.createElement('style');
    st.textContent = '.dl{display:block}.dl-menu{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}.dl-menu[hidden]{display:none}';
    document.head.appendChild(st);
  })();
  thread.addEventListener('click', function (e) {
    var cp = e.target.closest('.copy-msg-btn');
    if (cp) {
      var msg = cp.closest('.msg');
      if (msg && msg._text) {
        navigator.clipboard.writeText(msg._text).then(function () {
          var sp = cp.querySelector('span');
          if (sp) {
            var old = sp.textContent;
            sp.textContent = 'Copied!';
            setTimeout(function () { sp.textContent = old; }, 1800);
          }
        });
      }
      return;
    }
    var ret = e.target.closest('.retry-msg-btn');
    if (ret) {
      var msg = ret.closest('.msg');
      if (msg) {
        var textToRetry = null;
        if (msg.classList.contains('user')) {
          textToRetry = msg._text;
        } else if (msg.classList.contains('assistant')) {
          var prev = msg.previousElementSibling;
          while (prev && !prev.classList.contains('user')) {
            prev = prev.previousElementSibling;
          }
          if (prev && prev._text) textToRetry = prev._text;
        }
        if (textToRetry) {
          if (busy) stopCurrentPrompt();
          send(textToRetry);
        }
      }
      return;
    }
    var vbtn = e.target.closest('.version-btn');
    if (vbtn) {
      var msg = vbtn.closest('.msg');
      var userMsg = msg.classList.contains('user') ? msg : (msg._userMsg || msg.previousElementSibling);
      while (userMsg && !userMsg.classList.contains('user')) {
        userMsg = userMsg.previousElementSibling;
      }
      if (!userMsg || !userMsg._versions || userMsg._versions.length <= 1) return;
      var curIdx = (userMsg._versionIndex != null) ? userMsg._versionIndex : (userMsg._versions.length - 1);
      if (vbtn.classList.contains('prev')) {
        if (curIdx > 0) switchMessageVersion(userMsg, curIdx - 1);
      } else if (vbtn.classList.contains('next')) {
        if (curIdx < userMsg._versions.length - 1) switchMessageVersion(userMsg, curIdx + 1);
      }
      return;
    }
    var ed = e.target.closest('.edit-msg-btn');
    if (ed) {
      var msg = ed.closest('.msg');
      if (msg && msg._text) {
        showInlineEdit(msg);
      }
      return;
    }
    var tg = e.target.closest('.dl-toggle');
    if (tg) { var mn = tg.parentNode.querySelector('.dl-menu'); mn.hidden = !mn.hidden; return; }
    var it = e.target.closest('.dl-menu [data-save]');
    if (it) it.closest('.dl-menu').hidden = true;
  });
  /* ctxv3: dynamic-island context bar (50k tokens, estimated as characters / 4) */
  var CTX_MAX = 50000, CARRY = KEY + '_carry', ctxOpen = false, ctxAuto = false;
  (function () {
    var st = document.createElement('style');
    st.textContent =
      '.ctx{position:fixed;top:14px;left:50%;transform:translateX(-50%);z-index:999;width:172px;box-sizing:border-box;background:#0b0b0f;color:#fff;border-radius:22px;padding:8px 14px;font-size:13px;box-shadow:0 8px 26px rgba(0,0,0,.35);overflow:hidden;transition:width .4s cubic-bezier(.2,.9,.3,1.15),border-radius .4s}' +
      '.ctx.open{width:370px;border-radius:26px}' +
      '.ctx-row{display:flex;align-items:center;gap:9px}' +
      '.ctx-ring{width:18px;height:18px;border-radius:50%;flex:none;position:relative}' +
      '.ctx-ring::after{content:"";position:absolute;inset:4px;border-radius:50%;background:#0b0b0f}' +
      '.ctx.warn .ctx-ring{animation:ctxp 1.2s infinite}.ctx.full .ctx-ring{animation:ctxp .8s infinite}' +
      '@keyframes ctxp{50%{transform:scale(1.25)}}' +
      '.ctx-label{flex:1;font-weight:600;white-space:nowrap}' +
      '.ctx-arrow{background:none;border:0;color:#fff;cursor:pointer;font-size:13px;padding:2px 4px;line-height:1;transition:transform .35s}' +
      '.ctx.open .ctx-arrow{transform:rotate(180deg)}' +
      '.ctx-more{display:none;margin-top:10px}.ctx.open .ctx-more{display:block}' +
      '.ctx-txt{display:block;margin-bottom:6px;opacity:.85}' +
      '.ctx-bar{height:5px;border-radius:3px;background:rgba(255,255,255,.2);overflow:hidden}' +
      '.ctx-bar i{display:block;height:100%;width:0;background:#2fd18b;transition:width .3s}' +
      '.ctx.warn .ctx-bar i{background:#ffb020}.ctx.full .ctx-bar i{background:#ff4d4f}' +
      '.ctx-btns{display:flex;gap:8px;margin-top:10px}' +
      '.ctx-b{flex:1;background:rgba(255,255,255,.14);color:#fff;border:0;border-radius:14px;padding:7px 10px;font:inherit;font-weight:600;cursor:pointer}' +
      '.ctx-b:hover{background:rgba(255,255,255,.24)}.ctx-b:disabled{opacity:.4;cursor:not-allowed}' +
      '.ctx-msg{margin-top:10px;padding:7px 10px;border-radius:12px;background:rgba(255,176,32,.18);line-height:1.4}' +
      '.ctx.full .ctx-msg{background:rgba(255,77,79,.2)}.ctx-note{margin-top:8px;opacity:.65;font-size:12px}';
    document.head.appendChild(st);
  })();
  var ctxBox = document.createElement('div');
  ctxBox.className = 'ctx';
  ctxBox.innerHTML = '<div class="ctx-row"><span class="ctx-ring"></span><span class="ctx-label">Context 0%</span>' +
    '<button type="button" class="ctx-arrow" id="ctx-arrow" title="Show options">&#9662;</button></div>' +
    '<div class="ctx-more"><span class="ctx-txt"></span><div class="ctx-bar"><i></i></div>' +
    '<div class="ctx-btns"><button type="button" class="ctx-b" id="sum-dl">Download summary</button>' +
    '<button type="button" class="ctx-b" id="sum-save">Save summary</button></div>' +
    '<div class="ctx-msg" hidden></div><div class="ctx-note" hidden>Summary and last prompt carried over from the previous window.</div></div>';
  var ctxMount = $('ctx-mount');
  if (ctxMount) {
    ctxMount.appendChild(ctxBox);
  } else {
    document.body.appendChild(ctxBox);
  }

  function getCarry() { try { return localStorage.getItem(CARRY) || ''; } catch (e) { return ''; } }
  function carryPrior() {
    var c = getCarry();
    return c ? [{ role: 'user', content: 'Context carried over from the earlier chat:\n' + c }, { role: 'assistant', content: 'Understood. I will continue from that context.' }] : [];
  }
  function buildSummary() {
    var lines = [], last = '', i;
    history.slice(-20).forEach(function (m) {
      var s = String(m.content || '').replace(/\s+/g, ' ').trim();
      if (!s) return;
      lines.push((m.role === 'user' ? 'User asked: ' : 'Upian answered: ') + s.slice(0, m.role === 'user' ? 200 : 250));
    });
    for (i = history.length - 1; i >= 0; i--) {
      if (history[i].role === 'user') { last = String(history[i].content).replace(/\s+/g, ' ').trim().slice(0, 400); break; }
    }
    var prev = getCarry();
    return (prev ? prev + '\n\n' : '') + 'Summary of the earlier chat:\n' + lines.join('\n') + (last ? '\n\nLast prompt: ' + last : '');
  }
  function ctxUsed() {
    var n = Math.ceil(getCarry().length / 4);
    history.forEach(function (m) { n += Math.ceil(String(m.content || '').length / 4) + 4; });
    return n;
  }
  function ctxUpdate() {
    var used = ctxUsed(), pct = Math.min(100, Math.floor(used / CTX_MAX * 100));
    var full = used >= CTX_MAX, warn = !full && pct >= 90;
    if ((warn || full) && !ctxAuto) { ctxAuto = true; ctxOpen = true; }
    if (!warn && !full) ctxAuto = false;
    var color = full ? '#ff4d4f' : warn ? '#ffb020' : '#2fd18b';
    ctxBox.className = 'ctx' + (ctxOpen ? ' open' : '') + (full ? ' full' : warn ? ' warn' : '');
    ctxBox.querySelector('.ctx-ring').style.background = 'conic-gradient(' + color + ' ' + pct + '%, rgba(255,255,255,.22) 0)';
    ctxBox.querySelector('.ctx-label').textContent = 'Context ' + pct + '%';
    ctxBox.querySelector('.ctx-txt').textContent = fmtNum(Math.min(used, CTX_MAX)) + ' of ' + fmtNum(CTX_MAX) + ' tokens (' + pct + '%)';
    ctxBox.querySelector('.ctx-bar i').style.width = pct + '%';
    var mg = ctxBox.querySelector('.ctx-msg');
    mg.hidden = !(warn || full);
    mg.textContent = full
      ? 'Your context window is full (100%). Downloads are off. Click Save summary to continue in a new window.'
      : 'At ' + pct + '% your context window is almost full. Download what you need, or click Save summary to start a new window.';
    ctxBox.querySelector('.ctx-note').hidden = !getCarry();
    $('sum-dl').disabled = full || !history.length;
    $('sum-save').disabled = !history.length;
    Array.prototype.forEach.call(thread.querySelectorAll('.dl-toggle,[data-save]'), function (b) { b.disabled = full; });
  }
  $('ctx-arrow').addEventListener('click', function () { ctxOpen = !ctxOpen; ctxBox.classList.toggle('open', ctxOpen); });
  $('sum-dl').addEventListener('click', function () {
    var a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([buildSummary()], { type: 'text/plain;charset=utf-8' }));
    a.download = 'upian-summary.txt';
    document.body.appendChild(a); a.click(); a.remove();
  });
  $('sum-save').addEventListener('click', function () {
    try { localStorage.setItem(CARRY, buildSummary()); } catch (e) { alert('Could not save the summary.'); return; }
    history = []; thread.innerHTML = emptyHTML; thread.insertAdjacentHTML('afterbegin', '<div class="newwin">New context window started. Your summary and last prompt are carried over.</div>'); save(); input.focus();
  });
  $('new-chat').addEventListener('click', function () { try { localStorage.removeItem(CARRY); } catch (e) {} });
  setTimeout(ctxUpdate, 0);
  /* plusv1: "+" menu, temporary attachments, drag and drop, black user bubble */
  var temp = [];
  var TEXT_EXT = /\.(txt|md|csv|tsv|json|html?|xml|log|py|js|css|sql|ya?ml|ini|java|c|cpp|cs|go|php|rb|sh)$/i;
  (function () {
    var st = document.createElement('style');
    st.textContent =
      '.msg.user{background:transparent !important;border:0 !important;box-shadow:none !important;padding:0 !important;align-self:flex-end}.msg.user .body{background:#16161d !important;color:#fff !important;border:0 !important;box-shadow:none !important;border-radius:20px 20px 4px 20px !important;padding:10px 16px !important;width:fit-content;max-width:75%;margin-left:auto}' +
      '.msg.user .who{display:none !important}.msg.user .body p{color:#fff !important;margin:0}' +
      '.tmp-chips{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 6px}' +
      '.tmp-chip{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;background:#16161d;color:#fff;font-size:.82em}' +
      '.tmp-chip b{cursor:pointer;opacity:.8}' +
      '.thumb-chip.uploading{opacity:.75;background:rgba(255,255,255,.1);animation:pulse-chip 1.2s infinite}' +
      '@keyframes pulse-chip{50%{opacity:.4}}' +
      '.msg-attachments{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:8px}' +
      '.msg-att-img{display:inline-block;width:52px;height:52px;border-radius:8px;overflow:hidden;border:1px solid rgba(255,255,255,.2);background:#000}' +
      '.msg-att-img img{width:100%;height:100%;object-fit:cover;display:block}' +
      '.msg-att-file{display:inline-flex;align-items:center;gap:5px;padding:4px 8px;border-radius:6px;background:rgba(255,255,255,.15);font-size:12px;color:#fff}' +
      '.drop-ov{position:fixed;inset:0;z-index:90;display:none;align-items:center;justify-content:center;background:rgba(22,22,29,.55);color:#fff;font-size:22px;font-weight:700;border:4px dashed #fff;box-sizing:border-box}' +
      '.drop-ov.on{display:flex}.newwin{text-align:center;margin:10px 0;font-size:.85em;opacity:.7}';
    document.head.appendChild(st);
  })();

  var plus = $('plus-btn');
  if (!plus) {
    plus = document.createElement('button');
    plus.type = 'button'; plus.className = 'plus-btn'; plus.id = 'plus-btn'; plus.title = 'Add files'; plus.innerHTML = '+';
    input.parentNode.insertBefore(plus, input);
  }

  var menu = document.createElement('div');
  menu.className = 'plus-menu'; menu.hidden = true;
  menu.innerHTML = '<div class="plus-menu-head">Attach Content</div>' +
    '<button type="button" class="plus-item" id="plus-up"><span style="font-size:18px">💻</span> <div><b>Upload from computer</b><div style="font-size:11px;color:var(--mute);font-weight:normal">Screenshots, Photos, PDFs, Word, Excel, CSV, TXT</div></div></button>' +
    '<div class="plus-h"><span>Attach from My Files</span><a href="/files" target="_blank">Manage vault &rarr;</a></div>' +
    '<div id="plus-files"></div>';
  document.body.appendChild(menu);
  $('plus-files').appendChild(attachList);

  var pick = $('direct-upload');
  if (!pick) {
    pick = document.createElement('input');
    pick.type = 'file'; pick.multiple = true; pick.hidden = true;
    document.body.appendChild(pick);
  }
  pick.accept = 'image/*,.png,.jpg,.jpeg,.webp,.bmp,.jfif,.pdf,.docx,.xlsx,.txt,.csv';

  var tchips = document.createElement('div');
  tchips.className = 'tmp-chips';
  chips.parentNode.insertBefore(tchips, chips.nextSibling);
  function drawTemp() {
    tchips.innerHTML = temp.map(function (f, i) {
      return '<span class="tmp-chip">' + esc(f.name) + ' <b data-rm="' + i + '" title="Remove">&times;</b></span>';
    }).join('');
  }
  tchips.addEventListener('click', function (e) {
    var b = e.target.closest('[data-rm]');
    if (b) { temp.splice(Number(b.dataset.rm), 1); drawTemp(); }
  });

  function placeMenu() {
    var r = plus.getBoundingClientRect();
    menu.style.left = Math.max(8, Math.min(r.left, window.innerWidth - 320)) + 'px';
    menu.style.bottom = (window.innerHeight - r.top + 10) + 'px';
  }
  plus.addEventListener('click', function (e) {
    e.stopPropagation();
    if (menu.hidden) {
      loadDocs();
      placeMenu();
      menu.hidden = false;
    } else {
      menu.hidden = true;
    }
  });
  document.addEventListener('click', function (e) {
    if (!menu.hidden && !menu.contains(e.target) && e.target !== plus) menu.hidden = true;
  });
  window.addEventListener('resize', function () {
    if (!menu.hidden) placeMenu();
  });
  $('plus-up').addEventListener('click', function (e) {
    e.stopPropagation();
    menu.hidden = true;
    pick.click();
  });

  var uploadedFiles = [];
  var pendingUploads = [];
  var activeUploadPromises = [];

  function renderUploadedChips() {
    var upCont = $('uploaded-chips');
    if (!upCont) return;
    var html = uploadedFiles.map(function (f, i) {
      if (f.kind === 'image') {
        var src = f.thumbnail_url || f.url;
        return '<span class="thumb-chip"><img src="' + esc(src) + '"> ' + esc(f.original_filename) + ' <span class="rm" data-rm-up="' + i + '" title="Remove">&times;</span></span>';
      }
      return '<span class="thumb-chip">📄 ' + esc(f.original_filename) + ' <span class="rm" data-rm-up="' + i + '" title="Remove">&times;</span></span>';
    }).join('');

    if (pendingUploads.length > 0) {
      html += pendingUploads.map(function (p) {
        return '<span class="thumb-chip uploading"><span class="spin">⏳</span> ' + esc(p.name) + '</span>';
      }).join('');
    }
    upCont.innerHTML = html;
  }
  var upChipsCont = $('uploaded-chips');
  if (upChipsCont) {
    upChipsCont.addEventListener('click', function (e) {
      var rm = e.target.closest('[data-rm-up]');
      if (rm) {
        uploadedFiles.splice(Number(rm.dataset.rmUp), 1);
        renderUploadedChips();
      }
    });
  }

  async function uploadFileToServer(file) {
    var pItem = { id: Math.random(), name: file.name || 'image.png' };
    pendingUploads.push(pItem);
    renderUploadedChips();

    var fd = new FormData();
    fd.append('file', file, file.name || 'screenshot.png');
    var csrf = document.querySelector('meta[name="csrf-token"]');
    var headers = csrf ? { 'X-CSRF-Token': csrf.content } : {};
    try {
      var res = await fetch('/api/upload', { method: 'POST', body: fd, headers: headers });
      var j = await res.json();
      if (!res.ok || j.error) {
        alert('Upload failed: ' + (j.error || 'Server error'));
        return null;
      }
      uploadedFiles.push(j.file);
      return j.file;
    } catch (err) {
      alert('Upload failed: ' + err.message);
      return null;
    } finally {
      pendingUploads = pendingUploads.filter(function (p) { return p.id !== pItem.id; });
      renderUploadedChips();
    }
  }

  function addFiles(list) {
    Array.prototype.forEach.call(list, function (f) {
      var prom = uploadFileToServer(f);
      activeUploadPromises.push(prom);
      prom.finally(function () {
        activeUploadPromises = activeUploadPromises.filter(function (p) { return p !== prom; });
      });
    });
  }
  pick.addEventListener('change', function () { addFiles(pick.files); pick.value = ''; });
  var dirUp = $('direct-upload');
  if (dirUp) {
    dirUp.addEventListener('change', function () { addFiles(this.files); this.value = ''; });
  }

  /* Clipboard paste listener for screenshots (Snipping Tool, Win+Shift+S, PrtScn) */
  document.addEventListener('paste', function (e) {
    var cd = e.clipboardData || window.clipboardData;
    if (!cd) return;
    var items = cd.items;
    if (!items || !items.length) return;

    var pastedImages = [];
    for (var i = 0; i < items.length; i++) {
      var item = items[i];
      if (item.type && item.type.indexOf('image/') === 0) {
        var blob = item.getAsFile();
        if (blob) {
          var ext = (item.type.split('/')[1] || 'png').toLowerCase();
          if (ext === 'jpeg') ext = 'jpg';
          var stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
          var fname = 'screenshot_' + stamp + (items.length > 1 ? '_' + (i + 1) : '') + '.' + ext;
          var fileObj;
          try {
            fileObj = new File([blob], fname, { type: item.type });
          } catch (err) {
            blob.name = fname;
            fileObj = blob;
          }
          pastedImages.push(fileObj);
        }
      }
    }

    if (pastedImages.length > 0) {
      e.preventDefault();
      addFiles(pastedImages);
    }
  });

  function takeTemp() {
    if (!temp.length) return '';
    var out = temp.map(function (f) { return '\n\n--- Attached file: ' + f.name + ' ---\n' + f.text.slice(0, 40000); }).join('');
    temp = []; drawTemp();
    return out.slice(0, 80000);
  }

  /* drag and drop anywhere on the page */
  var ov = $('drop-overlay');
  if (!ov) {
    ov = document.createElement('div');
    ov.className = 'drop-ov'; ov.textContent = 'Drop files or images to attach';
    document.body.appendChild(ov);
  }
  var dragN = 0;
  function hasFiles(e) { return e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types || [], 'Files') >= 0; }
  document.addEventListener('dragenter', function (e) { if (!hasFiles(e)) return; e.preventDefault(); dragN++; ov.classList.add('on', 'active'); });
  document.addEventListener('dragover', function (e) { if (hasFiles(e)) e.preventDefault(); });
  document.addEventListener('dragleave', function (e) { if (!hasFiles(e)) return; dragN = Math.max(0, dragN - 1); if (!dragN) ov.classList.remove('on', 'active'); });
  document.addEventListener('drop', function (e) {
    if (!hasFiles(e)) return;
    e.preventDefault(); dragN = 0; ov.classList.remove('on', 'active'); addFiles(e.dataTransfer.files);
  });
  function save() {
    if (currentSessionId) {
      saveSessionMessages(currentSessionId, history);
      var s = sessions.find(function (x) { return x.id === currentSessionId; });
      if (s) {
        s.updatedAt = Date.now();
        try { localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions)); } catch (e) {}
      }
    }
    try { localStorage.setItem(KEY, JSON.stringify(history.slice(-60))); } catch (e) {}
    ctxUpdate();
    renderChatHistory();
  }
  function toBottom() { scroller.scrollTop = scroller.scrollHeight; }
  function dropEmpty() { var e = $('empty'); if (e) e.remove(); }

  function metaLine(m) {
    return '<div class="meta"><span class="tag ' + m.tier + '">' + tierLabel(m.tier) + '</span>' +
      (m.fallback ? '<span class="tag fb">Fallback key</span>' : '') +
      (m.downgraded ? '<span class="tag fb">Strong quota used up</span>' : '') +
      '<span class="mono">' + esc(m.model) + '</span>' +
      '<span>' + fmtNum(m.tokens_in) + ' tokens in, ' + fmtNum(m.tokens_out) + ' out</span>' +
      '<span>' + fmtMs(m.latency_ms) + '</span></div>';
  }

  var CREATE = ['txt', 'md', 'docx', 'pdf', 'html', 'json'];
  function actionsBlock(role, filesUsed, content, versions, verIdx) {
    var verNav = '';
    if (versions && versions.length > 1) {
      var curI = (verIdx != null) ? verIdx : (versions.length - 1);
      var prevDis = curI <= 0 ? ' disabled' : '';
      var nextDis = curI >= versions.length - 1 ? ' disabled' : '';
      verNav = '<span class="version-nav">' +
        '<button type="button" class="version-btn prev"' + prevDis + ' title="Previous version">&lt;</button> ' +
        '<span class="ver-text">' + (curI + 1) + '/' + versions.length + '</span> ' +
        '<button type="button" class="version-btn next"' + nextDis + ' title="Next version">&gt;</button>' +
      '</span>';
    }
    if (role === 'user') {
      return '<div class="actions user-actions">' +
        verNav +
        '<button type="button" class="btn sm copy-msg-btn" title="Copy prompt to clipboard">' +
          '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg> ' +
          '<span>Copy</span>' +
        '</button>' +
        '<button type="button" class="btn sm retry-msg-btn" title="Re-run this prompt">' +
          '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg> ' +
          '<span>Retry</span>' +
        '</button>' +
        '<button type="button" class="btn sm edit-msg-btn" title="Edit this prompt">' +
          '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg> ' +
          '<span>Edit</span>' +
        '</button>' +
      '</div>';
    }
    if (role !== 'assistant') return '';
    var used = filesUsed && filesUsed.length ? '<div class="used">Used files: ' + filesUsed.map(esc).join(', ') + '</div>' : '';
    var fmts = ['txt', 'md'];
    if (mdTables(content).length) fmts.push('csv');
    fmts.push('xlsx', 'docx', 'pdf', 'html', 'json');
    return used + '<div class="actions dl">' +
      verNav +
      '<button type="button" class="btn sm copy-msg-btn" title="Copy answer to clipboard">' +
        '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg> ' +
        '<span>Copy</span>' +
      '</button>' +
      '<button type="button" class="btn sm retry-msg-btn" title="Regenerate this answer">' +
        '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg> ' +
        '<span>Retry</span>' +
      '</button>' +
      '<button type="button" class="btn sm dl-toggle" title="Export as document or data file">' +
        '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg> ' +
        '<span>Download</span> &#9662;' +
      '</button>' +
      '<div class="dl-menu" hidden>' + fmts.map(function (x) {
        return '<button type="button" class="btn sm" data-save="' + x + '">' + (x === 'xlsx' ? 'Excel' : '.' + x) + '</button>';
      }).join('') + '</div></div>';
  }

  function showInlineEdit(userMsg) {
    if (busy) return;
    var bodyEl = userMsg.querySelector('.body');
    if (!bodyEl) return;
    if (userMsg.querySelector('.edit-msg-form')) return;
    var origText = userMsg._text || '';

    var editWrap = document.createElement('div');
    editWrap.className = 'edit-msg-form';
    editWrap.style.cssText = 'margin-top:8px;display:flex;flex-direction:column;gap:8px;width:100%;';
    editWrap.innerHTML = '<textarea class="edit-msg-textarea" rows="3" style="width:100%;box-sizing:border-box;border-radius:10px;padding:8px 12px;font:inherit;background:#fff;color:var(--ink);border:2px solid var(--ink);resize:vertical;"></textarea>' +
      '<div style="display:flex;justify-content:flex-end;gap:8px;">' +
        '<button type="button" class="btn sm cancel-edit-btn">Cancel</button>' +
        '<button type="button" class="btn sm primary submit-edit-btn">Save & Submit</button>' +
      '</div>';
    var ta = editWrap.querySelector('textarea');
    ta.value = origText;
    bodyEl.style.display = 'none';
    userMsg.insertBefore(editWrap, bodyEl.nextSibling);
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);

    editWrap.querySelector('.cancel-edit-btn').addEventListener('click', function () {
      editWrap.remove();
      bodyEl.style.display = '';
    });

    editWrap.querySelector('.submit-edit-btn').addEventListener('click', function () {
      var newPrompt = ta.value.trim();
      if (!newPrompt) return;
      editWrap.remove();
      bodyEl.style.display = '';
      if (newPrompt === origText) return;
      resubmitEditedPrompt(userMsg, newPrompt);
    });
  }

  async function resubmitEditedPrompt(userMsg, newPrompt) {
    if (busy) return;
    var origText = userMsg._text || '';

    if (!userMsg._versions || !userMsg._versions.length) {
      var nextAst = userMsg.nextElementSibling;
      while (nextAst && !nextAst.classList.contains('assistant') && !nextAst.classList.contains('user')) nextAst = nextAst.nextElementSibling;
      userMsg._versions = [{
        prompt: origText,
        reply: nextAst ? nextAst._text : '',
        meta: nextAst ? nextAst._meta : null,
        extra: nextAst ? nextAst._extra : null,
        files_used: nextAst ? nextAst._filesUsed : null
      }];
      userMsg._versionIndex = 0;
    }

    userMsg._versions.push({
      prompt: newPrompt,
      reply: null,
      meta: null,
      extra: null,
      files_used: null
    });
    userMsg._versionIndex = userMsg._versions.length - 1;
    userMsg._text = newPrompt;
    userMsg.querySelector('.body').innerHTML = '<p>' + esc(newPrompt).replace(/\n/g, '<br>') + '</p>';

    var oldActions = userMsg.querySelector('.actions.user-actions');
    if (oldActions) {
      oldActions.outerHTML = actionsBlock('user', null, newPrompt, userMsg._versions, userMsg._versionIndex);
    }

    var nextAst = userMsg.nextElementSibling;
    while (nextAst && !nextAst.classList.contains('assistant') && !nextAst.classList.contains('user')) nextAst = nextAst.nextElementSibling;
    if (!nextAst || !nextAst.classList.contains('assistant')) {
      nextAst = addPending();
      userMsg.parentNode.insertBefore(nextAst, userMsg.nextSibling);
    } else {
      nextAst.innerHTML = '<div class="who assistant-who"><span class="bot-badge"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg> Upian AI</span></div>' +
        '<div class="body pending-text"><span class="dots"><i></i><i></i><i></i></span><span class="ptext">Choosing a model for edited prompt</span></div>';
    }
    nextAst._userMsg = userMsg;
    currentPendingMsg = nextAst;

    var prevNodes = [];
    var sib = userMsg.previousElementSibling;
    while (sib) {
      if (sib.classList.contains('msg') && (sib.classList.contains('user') || sib.classList.contains('assistant'))) {
        prevNodes.unshift(sib);
      }
      sib = sib.previousElementSibling;
    }
    var prior = prevNodes.map(function (m) {
      return { role: m.classList.contains('user') ? 'user' : 'assistant', content: m._text || '' };
    });
    prior = carryPrior().concat(prior.slice(-8));

    currentRequestId = 'req_' + Date.now().toString(36) + '_' + Math.random().toString(36).substr(2, 8);
    currentAbortController = new AbortController();
    setBusyState(true);
    var row = addLivePending(newPrompt);
    currentLiveRow = row;

    try {
      var res = await api('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: currentAbortController.signal,
        body: JSON.stringify({
          prompt: newPrompt,
          history: prior,
          request_id: currentRequestId
        })
      });
      if (!res.ok) {
        var j = await res.json().catch(function () { return {}; });
        if (j.usage) renderUsage(j.usage);
        throw new Error(j.error || 'Request failed (' + res.status + ')');
      }
      var reader = res.body.getReader(), dec = new TextDecoder(), buf = '', finished = false;
      currentStreamReader = reader;
      for (;;) {
        var chunk = await reader.read();
        if (chunk.done) break;
        buf += dec.decode(chunk.value, { stream: true });
        var i;
        while ((i = buf.indexOf('\n')) >= 0) {
          var line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
          if (!line) continue;
          var ev = JSON.parse(line);
          if (ev.event === 'route') onRoute(ev, nextAst, row);
          else if (ev.event === 'done') {
            finished = true;
            var extra = { image: ev.image, vision: ev.vision, data: ev.data };
            userMsg._versions[userMsg._versionIndex].reply = ev.reply;
            userMsg._versions[userMsg._versionIndex].log = ev.log;
            userMsg._versions[userMsg._versionIndex].extra = extra;
            userMsg._versions[userMsg._versionIndex].files_used = ev.files_used;

            nextAst.className = 'msg assistant';
            nextAst._text = ev.reply;
            nextAst._meta = ev.log;
            nextAst._extra = extra;
            nextAst._filesUsed = ev.files_used;
            nextAst._userMsg = userMsg;
            nextAst.innerHTML = '<div class="who assistant-who"><span class="bot-badge"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg> Upian AI</span></div>' +
              '<div class="body">' + renderMd(ev.reply) + '</div>' + actionsBlock('assistant', ev.files_used, ev.reply, userMsg._versions, userMsg._versionIndex);

            // Sync with history
            for (var hi = 0; hi < history.length; hi++) {
              if (history[hi].role === 'user' && history[hi].versions && history[hi].versions.length === userMsg._versions.length) {
                history[hi].content = newPrompt;
                history[hi].versions = userMsg._versions;
                history[hi].versionIndex = userMsg._versionIndex;
                if (history[hi + 1] && history[hi + 1].role === 'assistant') {
                  history[hi + 1].content = ev.reply;
                  history[hi + 1].meta = ev.log;
                  history[hi + 1].extra = extra;
                  history[hi + 1].files_used = ev.files_used;
                }
                break;
              }
            }
            save();
            replaceLive(row, ev.log);
            renderUsage(ev.usage);
          } else if (ev.event === 'error') {
            finished = true;
            onError(ev, nextAst, row);
          }
        }
      }
    } catch (e) {
      if (currentAbortController && currentAbortController.signal.aborted) return;
      onError({ error: e.message }, nextAst, row);
    } finally {
      row.stop();
      resetSendBtn();
    }
  }

  function switchMessageVersion(userMsg, newIdx) {
    if (!userMsg || !userMsg._versions || newIdx < 0 || newIdx >= userMsg._versions.length) return;
    userMsg._versionIndex = newIdx;
    var v = userMsg._versions[newIdx];
    userMsg._text = v.prompt;
    var userBody = userMsg.querySelector('.body');
    if (userBody) userBody.innerHTML = '<p>' + esc(v.prompt).replace(/\n/g, '<br>') + '</p>';
    var userActs = userMsg.querySelector('.actions.user-actions');
    if (userActs) {
      userActs.outerHTML = actionsBlock('user', null, v.prompt, userMsg._versions, newIdx);
    }

    var nextAst = userMsg.nextElementSibling;
    while (nextAst && !nextAst.classList.contains('assistant') && !nextAst.classList.contains('user')) nextAst = nextAst.nextElementSibling;
    if (nextAst && nextAst.classList.contains('assistant')) {
      nextAst._text = v.reply || '';
      nextAst._meta = v.log;
      nextAst._extra = v.extra;
      nextAst._filesUsed = v.files_used;
      nextAst._userMsg = userMsg;
      var replyHtml = v.reply ? renderMd(v.reply) : '<em>(No response recorded for this version)</em>';
      nextAst.innerHTML = '<div class="who assistant-who"><span class="bot-badge"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg> Upian AI</span></div>' +
        '<div class="body">' + replyHtml + '</div>' + actionsBlock('assistant', v.files_used, v.reply, userMsg._versions, newIdx);
    }

    for (var i = 0; i < history.length; i++) {
      if (history[i].role === 'user' && history[i].versions && history[i].versions.length === userMsg._versions.length) {
        history[i].content = v.prompt;
        history[i].versionIndex = newIdx;
        if (history[i + 1] && history[i + 1].role === 'assistant') {
          history[i + 1].content = v.reply || '';
          history[i + 1].meta = v.log;
          history[i + 1].extra = v.extra;
        }
        break;
      }
    }
    save();
  }

  function imageCard(img) {
    if (!img) return '';
    return '<div class="card-image">' +
      '<img src="' + esc(img.url) + '" alt="' + esc(img.prompt) + '" onclick="window.open(\'' + esc(img.url) + '\',\'_blank\')">' +
      '<div class="card-image-foot"><span>' + esc(img.model) + ' (' + (img.width || 1024) + '&times;' + (img.height || 1024) + ')</span>' +
      '<a href="' + esc(img.url) + '?download=1" class="btn sm" download>Download Image</a></div></div>';
  }

  function visionCard(vis) {
    if (!vis) return '';
    var bars = (vis.labels || []).slice(0, 5).map(function (l) {
      var pct = Math.round((l.score || 0) * 100);
      return '<div class="card-vision-bar"><span>' + esc(l.label) + '</span><div class="bar"><i style="width:' + pct + '%"></i></div><span>' + pct + '%</span></div>';
    }).join('');
    var uncert = vis.uncertainty ? '<div class="dim small" style="margin-top:6px">Uncertainty: <b>' + esc(vis.uncertainty.uncertainty) + '</b> (Margin: ' + vis.uncertainty.margin + ', Entropy: ' + vis.uncertainty.entropy + ')</div>' : '';
    return '<div class="card-vision">' +
      '<div class="card-vision-head"><b>' + esc(vis.model || 'Vision Intelligence') + '</b>' +
      (vis.confidence != null ? '<span class="tag ' + (vis.confidence > 0.5 ? 'weak' : 'fb') + '">' + Math.round(vis.confidence * 100) + '% confidence</span>' : '') +
      '</div>' + (bars ? '<div class="card-vision-bars">' + bars + '</div>' : '') + uncert + '</div>';
  }

  function addMessage(role, content, meta, filesUsed, extra, versions, verIdx) {
    dropEmpty();
    var el = document.createElement('article');
    el.className = 'msg ' + role;
    var body = role === 'user' ? '<p>' + esc(content).replace(/\n/g, '<br>') + '</p>' : renderMd(content);
    if (extra && extra.attachments && extra.attachments.length) {
      var attsHtml = '<div class="msg-attachments">' + extra.attachments.map(function (att) {
        if (att.kind === 'image') {
          var src = att.thumbnail_url || att.url;
          return '<span class="msg-att-img" title="' + esc(att.original_filename) + '"><img src="' + esc(src) + '" alt="' + esc(att.original_filename) + '"></span>';
        }
        return '<span class="msg-att-file">📄 ' + esc(att.original_filename) + '</span>';
      }).join('') + '</div>';
      body = attsHtml + body;
    }
    if (extra && extra.image) body += imageCard(extra.image);
    if (extra && extra.vision) body += visionCard(extra.vision);
    var whoHtml = role === 'user' ? '<div class="who user-who">You</div>' :
      role === 'error' ? '<div class="who error-who">Error</div>' :
      '<div class="who assistant-who"><span class="bot-badge"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg> Upian AI</span></div>';
    el.innerHTML = whoHtml +
      '<div class="body">' + body + '</div>' + actionsBlock(role, filesUsed, content, versions, verIdx);
    el._text = content;
    el._meta = meta;
    el._extra = extra;
    el._filesUsed = filesUsed;
    if (versions) {
      el._versions = versions;
      el._versionIndex = (verIdx != null) ? verIdx : (versions.length - 1);
    }
    thread.appendChild(el);
    toBottom();
    return el;
  }

  function addPending() {
    var el = document.createElement('article');
    el.className = 'msg assistant';
    el.innerHTML = '<div class="who">Upian</div><div class="body pending-text"><span class="dots"><i></i><i></i><i></i></span><span class="ptext">Choosing a model</span></div>';
    thread.appendChild(el);
    toBottom();
    return el;
  }

  function liveRow(it) {
    var failed = it.status === 'error';
    return '<li class="live-row ' + (failed ? 'err' : '') + '" data-id="' + it.id + '">' +
      '<div class="l1"><span class="tag ' + (failed ? 'err' : it.tier) + '">' + (failed ? 'Failed' : tierLabel(it.tier)) + '</span>' +
      '<span class="mono model">' + esc(it.model || '') + '</span>' +
      (it.fallback ? '<span class="tag fb">Fallback key</span>' : '') +
      (it.downgraded ? '<span class="tag fb">Downgraded</span>' : '') + '</div>' +
      '<div class="l2">' + esc(it.prompt) + '</div>' +
      '<div class="l3">' + gauge(it.complexity, boot.threshold) +
      '<span>' + (failed ? 'No tokens counted' : fmtNum(it.tokens_in) + ' in, ' + fmtNum(it.tokens_out) + ' out') + '</span>' +
      '<span>' + (failed ? '' : fmtMs(it.latency_ms)) + '</span><span>' + fmtTime(it.created_at) + '</span></div></li>';
  }

  function clearPlaceholder() { var p = liveList.querySelector('.live-empty'); if (p) p.remove(); }

  function addLivePending(prompt) {
    clearPlaceholder();
    var li = document.createElement('li');
    li.className = 'live-row pending';
    li.innerHTML = '<div class="l1"><span class="tag neutral">Routing</span><span class="mono model">classifying prompt</span></div>' +
      '<div class="l2">' + esc(prompt.slice(0, 120)) + '</div><div class="l3"><span class="t">0.0s</span></div>';
    liveList.prepend(li);
    var t0 = performance.now(), t = li.querySelector('.t');
    var iv = setInterval(function () { t.textContent = ((performance.now() - t0) / 1000).toFixed(1) + 's'; }, 100);
    return { el: li, stop: function () { clearInterval(iv); } };
  }

  function replaceLive(row, log) {
    if (log) { row.el.outerHTML = liveRow(log); } else { row.el.remove(); }
  }

  function onRoute(ev, pend, row) {
    var tag = row.el.querySelector('.tag');
    tag.className = 'tag ' + ev.tier;
    tag.textContent = tierLabel(ev.tier);
    row.el.querySelector('.model').textContent = ev.model;
    var txt = 'Routed to ' + tierLabel(ev.tier) + ' (' + ev.model + ')';
    if (ev.capabilities && ev.capabilities.length) txt += ' [' + ev.capabilities.join(', ') + ']';
    if (ev.downgraded) txt += ' · Quota fallback';
    pend.querySelector('.ptext').textContent = txt;
  }

  function onDone(ev, pend, row) {
    pend.remove();
    var extra = { image: ev.image, vision: ev.vision, data: ev.data };
    var lastUserMsgEl = thread.querySelector('.msg.user:last-of-type');
    if (lastUserMsgEl && lastUserMsgEl._versions) {
      var vIdx = (lastUserMsgEl._versionIndex != null) ? lastUserMsgEl._versionIndex : (lastUserMsgEl._versions.length - 1);
      if (lastUserMsgEl._versions[vIdx]) {
        lastUserMsgEl._versions[vIdx].reply = ev.reply;
        lastUserMsgEl._versions[vIdx].log = ev.log;
        lastUserMsgEl._versions[vIdx].extra = extra;
        lastUserMsgEl._versions[vIdx].files_used = ev.files_used;
      }
    }
    var astEl = addMessage('assistant', ev.reply, ev.log, ev.files_used, extra, lastUserMsgEl ? lastUserMsgEl._versions : null, lastUserMsgEl ? lastUserMsgEl._versionIndex : null);
    if (lastUserMsgEl) astEl._userMsg = lastUserMsgEl;
    history.push({ role: 'assistant', content: ev.reply, meta: ev.log, files_used: ev.files_used, extra: extra });
    save();
    replaceLive(row, ev.log);
    renderUsage(ev.usage);
    uploadedFiles = [];
    renderUploadedChips();
    if (attachList) {
      attachList.querySelectorAll('input:checked').forEach(function (c) { c.checked = false; });
      refreshChips();
    }
  }

  function onError(ev, pend, row) {
    if (pend.isConnected) pend.remove();
    if (history.length && history[history.length - 1].role === 'user') { history.pop(); save(); }
    addMessage('error', ev.error || 'Something went wrong.');
    if (row.el.isConnected) replaceLive(row, ev.log);
    if (ev.usage) renderUsage(ev.usage);
  }

  /* Upgrade modal logic for Free plan image generation requests */
  var upgradeModal = $('upgrade-modal');
  var upgradeClose = $('upgrade-modal-close');
  var upgradeCancel = $('upgrade-cancel-btn');

  function showUpgradeModal(info) {
    if (!upgradeModal) return;
    info = info || {};
    var titleEl = $('upgrade-modal-title');
    var descEl = $('upgrade-modal-desc');
    var badgeEl = $('upgrade-plan-badge');
    if (titleEl && info.title) titleEl.textContent = info.title;
    if (descEl && info.message) descEl.innerHTML = esc(info.message).replace(/\b(Free plan|Pro|Pro Plus)\b/g, '<b>$1</b>');
    if (badgeEl && info.current_plan) badgeEl.textContent = 'Current: ' + (info.current_plan === 'free' ? 'Free Plan' : info.current_plan);
    upgradeModal.hidden = false;
  }

  function hideUpgradeModal() {
    if (upgradeModal) upgradeModal.hidden = true;
  }

  if (upgradeClose) upgradeClose.addEventListener('click', hideUpgradeModal);
  if (upgradeCancel) upgradeCancel.addEventListener('click', hideUpgradeModal);
  if (upgradeModal) {
    upgradeModal.addEventListener('click', function (e) {
      if (e.target === upgradeModal) hideUpgradeModal();
    });
  }
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && upgradeModal && !upgradeModal.hidden) {
      hideUpgradeModal();
    }
  });

  function isClientImageRequest(p) {
    var s = p.trim().toLowerCase();
    var patterns = [
      /^(?:please\s+)?(?:can you\s+)?(?:generate|genrate|gerante|generte|create|creat|draw|paint|render|make)\s+(?:me\s+)?(?:a\s+)?(?:new\s+)?(?:an?\s+)?(?:image|img|picture|pic|photo|illustration|art)\s+(?:of\s+)?(.+)$/i,
      /^(?:please\s+)?(?:can you\s+)?(?:generate|genrate|gerante|generte|create|creat|draw|paint|render|make)\s+(?:me\s+)?(?:a\s+)?(.+?)\s+(?:image|img|picture|pic|photo|illustration|art)$/i,
      /^(?:please\s+)?(?:can you\s+)?(?:draw|paint)\s+(?:me\s+)?(?:an?\s+)?(.+)$/i,
      /^(?:image|img|picture|pic|photo)\s+of\s+(.+)$/i,
      /^(?:generate|genrate|gerante|generte|create|creat|render)\s+(?:an?\s+)?(?:image|img|picture|pic|photo)$/i
    ];
    for (var i = 0; i < patterns.length; i++) {
      var m = s.match(patterns[i]);
      if (m) {
        var subj = m[1] ? m[1].trim() : 'image';
        if (!/\b(flowchart|diagram|architecture|er diagram|code|class diagram|table|sql|html)\b/i.test(subj)) {
          return true;
        }
      }
    }
    return false;
  }

  async function send(prompt) {
    if (busy || ctxUsed() >= CTX_MAX) return;
    
    // Check if image generation is disallowed for the current account
    if (boot && boot.can_image_gen === false && isClientImageRequest(prompt)) {
      addMessage('user', prompt);
      history.push({ role: 'user', content: prompt }); save();
      addMessage('assistant', '⚠️ **AI Image Generation is a Pro Feature**\n\nYour current plan does not include AI image generation. Please **Upgrade to Pro** (includes **20 AI images per day**) or **Pro Plus** (includes **30 AI images per day**) to unlock image generation.\n\n<button type="button" class="btn sm primary" onclick="if(window.openPlanModal)window.openPlanModal()">⚡ Upgrade Plan (Choose Pro / Pro Plus)</button>');
      showUpgradeModal({
        title: 'AI Image Generation Requires Upgrade',
        current_plan: boot.plan,
        message: 'Your current subscription plan does not include AI image generation capabilities. Upgrade to Pro (20 AI images/day) or Pro Plus (30 AI images/day).'
      });
      return;
    }

    // Await any in-flight uploads so screenshots/photos are never missed
    if (activeUploadPromises.length > 0) {
      var origSendText = sendBtn.textContent;
      sendBtn.disabled = true;
      sendBtn.textContent = 'Uploading...';
      try {
        await Promise.all(activeUploadPromises);
      } catch (e) {}
      sendBtn.textContent = origSendText;
      sendBtn.disabled = false;
    }

    if (!currentSessionId) {
      currentSessionId = 'sess_' + Date.now();
      try { localStorage.setItem(ACTIVE_SESSION_KEY, currentSessionId); } catch (e) {}
      var sessTitle = prompt.trim().replace(/\s+/g, ' ');
      if (sessTitle.length > 36) sessTitle = sessTitle.slice(0, 34) + '...';
      sessions.unshift({
        id: currentSessionId,
        title: sessTitle || 'New chat',
        createdAt: Date.now(),
        updatedAt: Date.now()
      });
      try { localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions)); } catch (e) {}
      renderChatHistory();
    }

    currentRequestId = 'req_' + Date.now().toString(36) + '_' + Math.random().toString(36).substr(2, 8);
    currentAbortController = new AbortController();
    setBusyState(true);
    var prior = history.slice(-8).map(function (m) { return { role: m.role, content: m.content }; });
    prior = carryPrior().concat(prior);
    var sendText = prompt + takeTemp();
    var imgItems = uploadedFiles.filter(function (u) { return u.kind === 'image'; });
    var imgTokens = imgItems.map(function (u) { return u.token; });
    var docTokens = uploadedFiles.filter(function (u) { return u.kind !== 'image'; }).map(function (u) { return u.token; });
    var allTokens = uploadedFiles.map(function (u) { return u.token; });
    var currentAttachments = uploadedFiles.slice();
    
    // Clear chatbar attachment chips immediately so screenshots/files are removed from the composer bar
    uploadedFiles = [];
    renderUploadedChips();

    var userExtra = currentAttachments.length ? { attachments: currentAttachments } : null;
    var userVersions = [{ prompt: prompt, reply: null, meta: null, extra: null, files_used: null }];
    history.push({ role: 'user', content: prompt, extra: userExtra, versions: userVersions, versionIndex: 0 }); save();
    var userMsgEl = addMessage('user', prompt, null, null, userExtra, userVersions, 0);
    var pend = addPending(), row = addLivePending(prompt), finished = false;
    currentPendingMsg = pend;
    currentLiveRow = row;

    try {
      var res = await api('/api/chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        signal: currentAbortController.signal,
        body: JSON.stringify({
          prompt: sendText,
          history: prior,
          doc_ids: selectedIds(),
          image_token: imgTokens[0] || null,
          image_tokens: imgTokens,
          uploaded_tokens: allTokens,
          request_id: currentRequestId
        })
      });
      if (!res.ok) {
        var j = await res.json().catch(function () { return {}; });
        if (j.usage) renderUsage(j.usage);
        throw new Error(j.error || 'Request failed (' + res.status + ')');
      }
      var reader = res.body.getReader(), dec = new TextDecoder(), buf = '';
      currentStreamReader = reader;
      for (;;) {
        var chunk = await reader.read();
        if (chunk.done) break;
        buf += dec.decode(chunk.value, { stream: true });
        var i;
        while ((i = buf.indexOf('\n')) >= 0) {
          var line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
          if (!line) continue;
          var ev = JSON.parse(line);
          if (ev.event === 'route') onRoute(ev, pend, row);
          else if (ev.event === 'done') { finished = true; onDone(ev, pend, row); }
          else if (ev.event === 'error') { finished = true; onError(ev, pend, row); }
          else if (ev.event === 'upgrade_required') {
            finished = true;
            if (pend.isConnected) pend.remove();
            addMessage('assistant', '⚠️ **AI Image Generation is a Pro Feature**\n\nFree users cannot generate AI images. Please **Upgrade to Pro** (includes **20 AI images per day**) or **Pro Plus** (includes **30 AI images per day**) to unlock image generation.\n\n<button type="button" class="btn sm primary" onclick="if(window.openPlanModal)window.openPlanModal()">⚡ Upgrade Plan (Choose Pro / Pro Plus)</button>');
            if (row.el.isConnected) replaceLive(row, ev.log);
            if (ev.usage) renderUsage(ev.usage);
            showUpgradeModal(ev);
          }
        }
      }
      if (!finished && !(currentAbortController && currentAbortController.signal.aborted)) {
        throw new Error('The connection closed before an answer arrived.');
      }
    } catch (e) {
      if (currentAbortController && currentAbortController.signal.aborted) {
        return;
      }
      if (!finished) onError({ error: e.message }, pend, row);
    } finally {
      row.stop();
      resetSendBtn();
      input.focus();
    }
  }

  /* wiring */
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    if (busy) {
      // ONLY stop if the user explicitly clicked the Send/Stop button (in stop-mode)
      var submitter = e.submitter;
      if (submitter && (submitter.classList.contains('stop-mode') || submitter === sendBtn)) {
        stopCurrentPrompt();
      }
      return;
    }
    var p = input.value.trim();
    if (!p && uploadedFiles.length > 0) {
      p = "Describe this image in detail and list all observed objects and text.";
    }
    if (!p) return;
    input.value = ''; input.style.height = 'auto';
    send(p);
  });
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      if (busy) {
        // Do NOT cancel generation when hitting Enter in input
        return;
      }
      form.requestSubmit();
    }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && busy) {
      stopCurrentPrompt();
    }
  });
  input.addEventListener('input', function () {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 200) + 'px';
  });
  thread.addEventListener('click', function (e) {
    var b = e.target.closest('.starter');
    if (b) {
      if (busy) return;
      input.value = b.dataset.p; input.focus();
    }
  });
  $('new-chat').addEventListener('click', function (e) {
    e.preventDefault();
    startNewChat();
  });
  var sideNewBtn = $('side-new-chat-btn');
  if (sideNewBtn) {
    sideNewBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      startNewChat();
    });
  }

  // Intercept click on active nav links or accidental unloads while streaming
  document.querySelectorAll('.nav a').forEach(function (link) {
    link.addEventListener('click', function (e) {
      var href = link.getAttribute('href');
      if (href === '/' || link.classList.contains('on')) {
        e.preventDefault();
        return;
      }
      if (busy) {
        if (!confirm('A prompt is currently generating. Leave page and cancel generation?')) {
          e.preventDefault();
        }
      }
    });
  });

  window.addEventListener('beforeunload', function (e) {
    if (busy) {
      e.preventDefault();
      e.returnValue = '';
    }
  });

  /* Chat history item click and delete handling */
  document.addEventListener('click', function (e) {
    var delBtn = e.target.closest('[data-del-sess]');
    if (delBtn) {
      e.stopPropagation();
      var idToDel = delBtn.dataset.delSess;
      sessions = sessions.filter(function (s) { return s.id !== idToDel; });
      try {
        localStorage.removeItem('upian_chat_sess_' + idToDel);
        localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions));
      } catch (err) {}
      if (currentSessionId === idToDel) {
        if (sessions.length) {
          switchChat(sessions[0].id);
        } else {
          startNewChat();
        }
      } else {
        renderChatHistory();
      }
      return;
    }
    var item = e.target.closest('.history-item');
    if (item && item.dataset.sessId) {
      switchChat(item.dataset.sessId);
      return;
    }
  });

  /* Sidebar & Live Panel Collapsing (Left & Right Buttons) */
  var shell = document.querySelector('.shell') || document.getElementById('app-shell');
  var sidebarToggle = document.getElementById('sidebar-toggle');
  var liveToggle = document.getElementById('live-toggle');
  var newChatBtn = document.getElementById('new-chat-btn');

  function setSidebarCollapsed(col) {
    if (!shell) return;
    if (col) {
      shell.classList.add('sidebar-collapsed');
    } else {
      shell.classList.remove('sidebar-collapsed');
    }
    try { localStorage.setItem('upian_sidebar_collapsed', col ? '1' : '0'); } catch(e){}
    if (sidebarToggle) sidebarToggle.setAttribute('aria-expanded', !col);
  }

  function setLiveCollapsed(col) {
    if (!shell) return;
    if (col) {
      shell.classList.add('live-collapsed');
      if (liveToggle) liveToggle.classList.add('panel-off');
    } else {
      shell.classList.remove('live-collapsed');
      if (liveToggle) liveToggle.classList.remove('panel-off');
    }
    try { localStorage.setItem('upian_live_collapsed', col ? '1' : '0'); } catch(e){}
    if (liveToggle) liveToggle.setAttribute('aria-expanded', !col);
  }

  if (sidebarToggle) {
    sidebarToggle.addEventListener('click', function (e) {
      e.stopPropagation();
      setSidebarCollapsed(!shell.classList.contains('sidebar-collapsed'));
    });
  }

  if (liveToggle) {
    liveToggle.addEventListener('click', function (e) {
      e.stopPropagation();
      setLiveCollapsed(!shell.classList.contains('live-collapsed'));
    });
  }

  if (newChatBtn) {
    newChatBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      startNewChat();
    });
  }

  /* Restore saved collapse states */
  try {
    if (localStorage.getItem('upian_sidebar_collapsed') === '1') {
      setSidebarCollapsed(true);
    }
    if (localStorage.getItem('upian_live_collapsed') === '1') {
      setLiveCollapsed(true);
    }
  } catch(e){}

  /* Connect Upgrade modal button to the Payment modal */
  var upgradeConfirm = document.getElementById('upgrade-confirm-btn');
  if (upgradeConfirm) {
    upgradeConfirm.addEventListener('click', function () {
      var upModal = document.getElementById('upgrade-modal');
      if (upModal) upModal.hidden = true;
      if (window.openPlanModal) window.openPlanModal();
    });
  }

  renderChatHistory();

  history.forEach(function (m) { addMessage(m.role, m.content, m.meta, m.files_used, m.extra, m.versions, m.versionIndex); });
  document.querySelectorAll('.msg .meta').forEach(function (el) { el.remove(); });
  scroller.scrollTop = scroller.scrollHeight;

  api('/api/activity?limit=10').then(function (r) { return r.json(); }).then(function (d) {
    liveList.innerHTML = d.items.length ? d.items.map(liveRow).join('') : '<li class="live-empty">Your requests will show up here as they run.</li>';
  }).catch(function () {
    liveList.innerHTML = '<li class="live-empty">Could not load recent activity.</li>';
  });
})();

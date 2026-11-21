(function () {
  var boot = JSON.parse(document.getElementById('boot').textContent);
  var drop = document.getElementById('drop'), input = document.getElementById('file-input');
  var queue = document.getElementById('queue'), tbody = document.getElementById('docs');

  function fmtSize(b) {
    if (b > 1048576) return (b / 1048576).toFixed(1) + ' MB';
    return Math.max(1, Math.round(b / 1024)) + ' KB';
  }

  function getBadge(fn) {
    var ext = (fn.split('.').pop() || '').toLowerCase();
    if (ext === 'pdf') return { label: 'PDF', cls: 'badge-pdf' };
    if (ext === 'docx' || ext === 'doc') return { label: 'DOC', cls: 'badge-doc' };
    if (ext === 'xlsx' || ext === 'xls' || ext === 'csv') return { label: 'SHEET', cls: 'badge-sheet' };
    if (ext === 'pptx' || ext === 'ppt') return { label: 'PPT', cls: 'badge-slide' };
    if (ext === 'json' || ext === 'html' || ext === 'py' || ext === 'js' || ext === 'css') return { label: 'CODE', cls: 'badge-code' };
    return { label: 'TXT', cls: 'badge-txt' };
  }

  function load() {
    api('/api/docs').then(function (r) { return r.json(); }).then(function (d) {
      if (!d.items || !d.items.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="empty-docs-cell">' +
          '<div class="empty-docs-state">' +
          '<div class="empty-docs-icon">📂</div>' +
          '<div class="empty-docs-title">No documents uploaded yet</div>' +
          '<p class="empty-docs-sub">Drag and drop documents or datasets above to index them for deep AI analysis and chat referencing.</p>' +
          '</div></td></tr>';
        return;
      }
      tbody.innerHTML = d.items.map(function (f) {
        var badge = getBadge(f.filename);
        var charsFormatted = f.chars ? Number(f.chars).toLocaleString() : '—';
        return '<tr data-id="' + f.id + '">' +
          '<td><div class="file-cell">' +
          '<span class="file-tag ' + badge.cls + '">' + badge.label + '</span>' +
          '<span class="file-name" title="' + esc(f.filename) + '">' + esc(f.filename) + '</span>' +
          '</div></td>' +
          '<td class="num font-num">' + fmtSize(f.size) + '</td>' +
          '<td class="num font-num">' + (f.pages || '—') + '</td>' +
          '<td class="num font-num">' + charsFormatted + '</td>' +
          '<td class="dim date-cell">' + fmtDateTime(f.created_at) + '</td>' +
          '<td class="r nowrap actions-cell">' +
          '<a class="btn sm btn-action" href="/api/docs/' + f.id + '/download" title="Download file">' +
          '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> Download</a> ' +
          '<button class="btn sm danger btn-action" data-del="' + f.id + '" title="Delete file">' +
          '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg> Delete</button>' +
          '</td></tr>';
      }).join('');
    }).catch(function () {
      tbody.innerHTML = '<tr><td colspan="6" class="dim empty-docs-cell">Could not load your files. Please refresh the page.</td></tr>';
    });
  }

  function upload(file) {
    var li = document.createElement('li');
    li.innerHTML = '<span class="q-name">' + esc(file.name) + '</span><span class="q-state">Waiting…</span><div class="bar"><i style="width:0"></i></div>';
    queue.appendChild(li);
    var state = li.querySelector('.q-state'), bar = li.querySelector('.bar i');
    var ext = '.' + file.name.split('.').pop().toLowerCase();
    if (boot.allowed.indexOf(ext) < 0) { fail('Unsupported file type'); return Promise.resolve(); }
    if (file.size > boot.max_mb * 1048576) { fail('Larger than ' + boot.max_mb + ' MB'); return Promise.resolve(); }

    function fail(msg) { li.classList.add('err'); state.textContent = msg; }

    return new Promise(function (resolve) {
      var xhr = new XMLHttpRequest(), fd = new FormData();
      fd.append('file', file);
      xhr.open('POST', '/api/docs');
      xhr.setRequestHeader('X-CSRF-Token', CSRF);
      xhr.upload.onprogress = function (e) {
        if (!e.lengthComputable) return;
        var p = Math.round(e.loaded / e.total * 100);
        bar.style.width = p + '%';
        state.textContent = p < 100 ? 'Uploading ' + p + '%' : 'Processing… (reading and indexing)';
      };
      xhr.onload = function () {
        var j = {}; try { j = JSON.parse(xhr.responseText); } catch (e) {}
        if (xhr.status === 200) { state.textContent = 'Ready'; li.classList.add('ok'); bar.style.width = '100%'; setTimeout(function () { li.remove(); }, 2500); }
        else fail(j.error || 'Upload failed (' + xhr.status + ')');
        resolve();
      };
      xhr.onerror = function () { fail('Network error'); resolve(); };
      xhr.send(fd);
    });
  }

  async function addFiles(list) {
    for (var i = 0; i < list.length; i++) { await upload(list[i]); load(); }  // one at a time
  }

  document.getElementById('pick').addEventListener('click', function (e) {
    e.stopPropagation();
    input.click();
  });
  input.addEventListener('change', function () { addFiles(Array.from(input.files)); input.value = ''; });
  ['dragenter', 'dragover'].forEach(function (n) { drop.addEventListener(n, function (e) { e.preventDefault(); drop.classList.add('over'); }); });
  ['dragleave', 'drop'].forEach(function (n) { drop.addEventListener(n, function (e) { e.preventDefault(); drop.classList.remove('over'); }); });
  drop.addEventListener('drop', function (e) { addFiles(Array.from(e.dataTransfer.files)); });

  tbody.addEventListener('click', function (e) {
    var b = e.target.closest('[data-del]');
    if (!b || !confirm('Delete this file and its search data?')) return;
    b.disabled = true;
    api('/api/docs/' + b.dataset.del, { method: 'DELETE' }).then(load);
  });
  load();
})();

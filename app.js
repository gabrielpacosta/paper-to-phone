/* Paper to Phone: a library of reflowed papers that lives in the reader's own OneDrive folder.
   Layout of that folder (shared with the paper2phone.py drop-folder service):
     Inbox/              PDFs and link files waiting to be converted by a computer
     Library/            reader files (.html) and EPUBs; .library.json holds titles and covers
     Originals/          PDFs that were converted
     Could not convert/  items that failed, each with a note
     .paper2phone/state.json   reading positions, recently opened and reader settings, for every device
   The app has no server: it talks to Microsoft Graph with the reader's own sign-in. */
(function () {
  'use strict';
  var D = document, W = window, CFG = W.P2P_CONFIG || {};
  function $(id) { return D.getElementById(id); }
  var SCOPES = ['Files.ReadWrite', 'User.Read'];
  var GRAPH = 'https://graph.microsoft.com/v1.0';
  var DEMO = /[?&]demo\b/.test(location.search);
  var PAPER_EXT = /\.(html?)$/i, WAIT_EXT = /\.(pdf|txt|url|webloc)$/i;
  var NOTE_RE = / \(what went wrong\)\.txt$/i;

  /* ---------- small helpers ---------- */
  function ls(k, d) { try { var v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } }
  function lsSet(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  function lsDel(k) { try { localStorage.removeItem(k); } catch (e) {} }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function mb(n) { return n > 1e6 ? (n / 1e6).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1e3)) + ' KB'; }
  function stem(name) { return name.replace(/\.[^.]+$/, ''); }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  var toastTimer = null;
  function toast(msg, action, fn, ms) {
    $('toast-text').textContent = msg;
    var b = $('toast-btn');
    b.hidden = !action; b.textContent = action || '';
    b.onclick = function () { $('toast').classList.remove('on'); if (fn) fn(); };
    $('toast').classList.add('on');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { $('toast').classList.remove('on'); }, ms || (action ? 6500 : 4500));
  }
  function fail(e, what) {
    console.error(e);
    var msg = (e && e.friendly) || (what ? what + ' did not work.' : 'Something did not work.');
    if (!navigator.onLine) msg = 'You are offline. Saved papers still open.';
    toast(msg);
  }

  /* ---------- sign-in (MSAL) ---------- */
  var redirectUri = location.origin + location.pathname.replace(/index\.html$/, '');
  function clientId() { return (CFG.clientId || ls('p2p:clientId', '') || '').trim(); }
  var pca = null, account = null;
  function authInit() {
    pca = new msal.PublicClientApplication({
      auth: { clientId: clientId(), authority: 'https://login.microsoftonline.com/common', redirectUri: redirectUri,
              postLogoutRedirectUri: redirectUri, navigateToLoginRequestUrl: false },
      cache: { cacheLocation: 'localStorage' }
    });
    return pca.initialize().then(function () {
      return pca.handleRedirectPromise().catch(function (e) {
        var m = String(e && (e.errorMessage || e.message) || '');
        if (/AADSTS65001|consent/i.test(m)) toast('Your organization needs to approve this app before you can sign in with that account.', null, null, 9000);
        else if (/AADSTS700016|AADSTS50011|unauthorized_client/i.test(m)) toast('This app ID is not set up for this address yet. Check the README steps.', null, null, 9000);
        else if (!/user_cancelled|interaction_in_progress/i.test(m)) toast('Sign-in did not finish. Try again.');
        return null;
      });
    }).then(function (r) {
      account = (r && r.account) || pca.getActiveAccount() || pca.getAllAccounts()[0] || null;
      if (account) pca.setActiveAccount(account);
    });
  }
  var redirecting = false;
  function token(force) {
    return pca.acquireTokenSilent({ scopes: SCOPES, account: account, forceRefresh: !!force }).then(function (r) { return r.accessToken; })
      .catch(function (e) {
        if (!navigator.onLine) throw e;
        if (!redirecting) { redirecting = true; pca.acquireTokenRedirect({ scopes: SCOPES, account: account }); }
        return new Promise(function () {});          // the page is leaving for the sign-in screen
      });
  }

  /* ---------- OneDrive through Microsoft Graph ---------- */
  function enc(name) { return name.split('/').map(encodeURIComponent).join('/'); }
  function norm(x) {
    return { id: x.id, name: x.name, folder: !!x.folder, size: x.size || 0, modified: x.lastModifiedDateTime, etag: x.eTag, ctag: x.cTag,
             download: x['@microsoft.graph.downloadUrl'], parentId: x.parentReference && x.parentReference.id };
  }
  function httpError(r, body) {
    var e = new Error('HTTP ' + r.status + ' ' + (body || '').slice(0, 200));
    e.status = r.status;
    if (r.status === 403) e.friendly = 'OneDrive refused that. The account may not allow this app.';
    if (r.status === 507) e.friendly = 'Your OneDrive is full.';
    return e;
  }
  function g(path, opts, retried) {
    opts = opts || {};
    return token(retried).then(function (t) {
      var h = Object.assign({ Authorization: 'Bearer ' + t }, opts.headers || {});
      return fetch(path.indexOf('http') === 0 ? path : GRAPH + path, Object.assign({}, opts, { headers: h }));
    }).then(function (r) {
      if (r.status === 401 && !retried) return g(path, opts, true);
      if (r.status === 429 || r.status === 503) {
        var wait = (parseInt(r.headers.get('Retry-After') || '2', 10) || 2) * 1000;
        return sleep(Math.min(wait, 10000)).then(function () { return g(path, opts, retried); });
      }
      return r;
    });
  }
  function gj(path, opts) {
    return g(path, opts).then(function (r) {
      if (!r.ok) return r.text().then(function (b) { throw httpError(r, b); });
      return r.status === 204 ? null : r.json();
    });
  }
  var Graph = {
    me: function () {
      return gj('/me?$select=displayName,userPrincipalName,mail').then(function (m) {
        return { name: m.displayName || m.userPrincipalName, email: m.mail || m.userPrincipalName };
      });
    },
    root: function () { return gj('/me/drive/root').then(function (r) { return { id: r.id, name: 'OneDrive' }; }); },
    children: function (id) {
      var out = [];
      function page(url) {
        return gj(url).then(function (r) {
          out = out.concat(r.value.map(norm));
          return r['@odata.nextLink'] ? page(r['@odata.nextLink']) : out;
        });
      }
      return page('/me/drive/items/' + id + '/children?$top=200');
    },
    item: function (id) { return gj('/me/drive/items/' + id).then(norm); },
    child: function (parentId, name) {
      return g('/me/drive/items/' + parentId + ':/' + enc(name)).then(function (r) {
        if (r.status === 404) return null;
        if (!r.ok) return r.text().then(function (b) { throw httpError(r, b); });
        return r.json().then(norm);
      });
    },
    mkdir: function (parentId, name) {
      var self = this;
      return g('/me/drive/items/' + parentId + '/children', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name, folder: {}, '@microsoft.graph.conflictBehavior': 'fail' })
      }).then(function (r) {
        if (r.status === 409) return self.child(parentId, name);
        if (!r.ok) return r.text().then(function (b) { throw httpError(r, b); });
        return r.json().then(norm);
      });
    },
    readText: function (it) {
      var self = this;
      function get(u) { return fetch(u, { cache: 'no-store' }); }
      var start = it.download ? Promise.resolve(it) : self.item(it.id);
      return start.then(function (x) { return get(x.download); }).then(function (r) {
        if (r.ok) return r.text();
        return self.item(it.id).then(function (x) { return get(x.download); }).then(function (r2) {
          if (!r2.ok) throw httpError(r2, '');
          return r2.text();
        });
      });
    },
    put: function (parentId, name, body, type, etag) {
      var h = { 'Content-Type': type || 'application/octet-stream' };
      if (etag) h['If-Match'] = etag;
      var behave = etag === false ? 'rename' : 'replace';
      return g('/me/drive/items/' + parentId + ':/' + enc(name) + ':/content?@microsoft.graph.conflictBehavior=' + behave,
        { method: 'PUT', headers: h, body: body }).then(function (r) {
        if (r.status === 412) { var e = new Error('changed elsewhere'); e.conflict = true; throw e; }
        if (!r.ok) return r.text().then(function (b) { throw httpError(r, b); });
        return r.json().then(norm);
      });
    },
    upload: function (parentId, name, blob, onProgress) {
      if (blob.size < 3500000) return this.put(parentId, name, blob, blob.type || 'application/octet-stream', false);
      return gj('/me/drive/items/' + parentId + ':/' + enc(name) + ':/createUploadSession', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item: { '@microsoft.graph.conflictBehavior': 'rename' } })
      }).then(function (s) {
        var CH = 327680 * 10, off = 0, done = null;
        function next() {
          if (off >= blob.size) return done;
          var end = Math.min(off + CH, blob.size);
          return fetch(s.uploadUrl, { method: 'PUT', headers: { 'Content-Range': 'bytes ' + off + '-' + (end - 1) + '/' + blob.size }, body: blob.slice(off, end) })
            .then(function (r) {
              if (!(r.ok || r.status === 202)) throw httpError(r, '');
              off = end;
              if (onProgress) onProgress(off / blob.size);
              if (r.status === 200 || r.status === 201) return r.json().then(function (j) { done = norm(j); return next(); });
              return next();
            });
        }
        return next();
      });
    },
    remove: function (id) {
      return g('/me/drive/items/' + id, { method: 'DELETE' }).then(function (r) {
        if (!(r.ok || r.status === 404)) return r.text().then(function (b) { throw httpError(r, b); });
      });
    },
    move: function (id, parentId) {
      return gj('/me/drive/items/' + id + '?@microsoft.graph.conflictBehavior=rename', {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ parentReference: { id: parentId } })
      });
    },
    appGet: function () {          // the app's own small folder: remembers which folder is the library, on every device
      var self = this;
      return g('/me/drive/special/approot:/config.json').then(function (r) {
        if (!r.ok) return null;
        return r.json().then(function (x) { return self.readText(norm(x)); }).then(JSON.parse).catch(function () { return null; });
      }).catch(function () { return null; });
    },
    appPut: function (obj) {
      return g('/me/drive/special/approot:/config.json:/content', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(obj)
      }).catch(function () {});
    }
  };

  /* ---------- a pretend OneDrive for trying the app without signing in ---------- */
  function DemoDrive() {
    var store = {}, n = 0;
    function add(parentId, name, folder, content, extra) {
      var id = 'd' + (++n);
      store[id] = Object.assign({ id: id, name: name, folder: folder, parentId: parentId, content: content || null,
                                  size: content ? (content.size || content.length || 0) : 0, etag: 'e' + n, modified: new Date().toISOString() }, extra || {});
      return store[id];
    }
    var root = add(null, 'OneDrive', true);
    var home = add(root.id, 'Paper to Phone', true);
    add(root.id, 'Documents', true); add(root.id, 'Pictures', true);
    var lib = add(home.id, 'Library', true), inbox = add(home.id, 'Inbox', true), bad = add(home.id, 'Could not convert', true);
    add(home.id, 'Originals', true);
    add(lib.id, 'McCarthy 2026 - The impact of cannabidiol (CBD) in hyperglycemic zebrafish (Danio rerio).html', false, null,
        { url: 'demo/sample.html', size: 1340000 });
    add(lib.id, '.library.json', false, JSON.stringify([{ title: 'The impact of cannabidiol (CBD) in hyperglycemic zebrafish (Danio rerio)',
        authors: 'Elizabeth McCarthy, Leehy Gertner, Justin Ciocirlan, Victoria P. Connaughton', venue: 'PLOS One', year: '2026',
        html: 'McCarthy 2026 - The impact of cannabidiol (CBD) in hyperglycemic zebrafish (Danio rerio).html', epub: '', thumb: W.P2P_DEMO_THUMB || '' }]));
    add(inbox.id, 'Smith 2026 cannabis and pain.pdf', false, null, { size: 2400000 });
    add(bad.id, 'Link 2026-10-18.txt', false, 'https://www.example.org/paywalled-article');
    add(bad.id, 'Link 2026-10-18 (what went wrong).txt', false,
        'https://www.example.org/paywalled-article\n  Could not get a PDF from that link. If the paper needs a library login, download the PDF in your browser and add the file instead.');
    function copy(x) { return Object.assign({}, x); }
    return {
      demo: true,
      home: home,
      me: function () { return Promise.resolve({ name: 'Sample reader', email: 'Demo, nothing is saved online' }); },
      root: function () { return Promise.resolve(copy(root)); },
      children: function (id) { return Promise.resolve(Object.keys(store).map(function (k) { return store[k]; }).filter(function (x) { return x.parentId === id; }).map(copy)); },
      item: function (id) { return store[id] ? Promise.resolve(copy(store[id])) : Promise.reject(Object.assign(new Error('gone'), { status: 404 })); },
      child: function (pid, name) { var hit = Object.keys(store).map(function (k) { return store[k]; }).filter(function (x) { return x.parentId === pid && x.name === name; })[0]; return Promise.resolve(hit ? copy(hit) : null); },
      mkdir: function (pid, name) { var self = this; return self.child(pid, name).then(function (x) { return x || copy(add(pid, name, true)); }); },
      readText: function (it) {
        var x = store[it.id];
        if (x.url) return fetch(x.url).then(function (r) { return r.text(); });
        if (x.content && x.content.text) return x.content.text();
        return Promise.resolve(String(x.content || ''));
      },
      put: function (pid, name, body, type, etag) {
        var hit = Object.keys(store).map(function (k) { return store[k]; }).filter(function (x) { return x.parentId === pid && x.name === name; })[0];
        if (hit && etag && etag !== hit.etag) return Promise.reject(Object.assign(new Error('changed'), { conflict: true }));
        if (hit && etag === false) { name = stem(name) + ' 1' + (name.match(/\.[^.]+$/) || [''])[0]; hit = null; }
        if (hit) { hit.content = body; hit.etag = 'e' + (++n); hit.size = body.size || body.length || 0; hit.modified = new Date().toISOString(); return Promise.resolve(copy(hit)); }
        return Promise.resolve(copy(add(pid, name, false, body)));
      },
      upload: function (pid, name, blob) { return this.put(pid, name, blob, blob.type, false); },
      remove: function (id) { delete store[id]; return Promise.resolve(); },
      move: function (id, pid) { store[id].parentId = pid; return Promise.resolve(copy(store[id])); },
      appGet: function () { return Promise.resolve(null); },
      appPut: function () { return Promise.resolve(); }
    };
  }

  /* ---------- papers cached on this device for offline reading ---------- */
  var cdb = null;
  function cacheOpen() {
    return new Promise(function (res) {
      try {
        var r = indexedDB.open('paper-to-phone-cache', 1);
        r.onupgradeneeded = function () { r.result.createObjectStore('html', { keyPath: 'id' }); };
        r.onsuccess = function () { cdb = r.result; res(); };
        r.onerror = r.onblocked = function () { res(); };
      } catch (e) { res(); }
    });
  }
  function cacheGet(id) {
    return new Promise(function (res) {
      if (!cdb) return res(null);
      try { var q = cdb.transaction('html').objectStore('html').get(id); q.onsuccess = function () { res(q.result || null); }; q.onerror = function () { res(null); }; }
      catch (e) { res(null); }
    });
  }
  function cachePut(rec) {
    if (!cdb) return;
    try { cdb.transaction('html', 'readwrite').objectStore('html').put(rec); } catch (e) {}
  }

  /* ---------- state shared by every device ---------- */
  var drive = null, me = null, folder = null, sub = {}, libjson = {}, libjsonTag = null;
  var state = ls('p2p:state', { papers: {}, meta: {}, prefs: null, prefsT: 0 });
  var stateTag = ls('p2p:stateTag', null), lastSync = 0, syncing = false;
  var view = ls('p2p:snap', { lib: [], wait: [], bad: [] });

  function mergeState(remote) {
    if (!remote || typeof remote !== 'object') return;
    var papers = remote.papers || {};
    Object.keys(papers).forEach(function (k) {
      var r = papers[k], l = state.papers[k];
      if (!l || (r.t || 0) > (l.t || 0)) state.papers[k] = Object.assign({}, l || {}, r);
      else if (r.opened && (!l.opened || r.opened > l.opened)) l.opened = r.opened;
      var pos = ls('p2p:pos:' + k, null);           // the reader resumes from this key
      if (r.t && (!pos || (pos.t || 0) < r.t)) lsSet('p2p:pos:' + k, { p: r.p, h: r.h, t: r.t });
    });
    Object.assign(state.meta, remote.meta || {});
    if (remote.prefs && (remote.prefsT || 0) > (state.prefsT || 0)) {
      state.prefs = remote.prefs; state.prefsT = remote.prefsT;
      lsSet('p2p:prefs', remote.prefs); lsSet('p2p:prefsSeen', JSON.stringify(remote.prefs));
    }
    lsSet('p2p:state', state);
  }
  function collectLocal() {               // fold what the reader saved on this device into the shared state
    var changed = false;
    Object.keys(state.meta).forEach(function (name) {
      var k = state.meta[name].key;
      if (!k) return;
      var pos = ls('p2p:pos:' + k, null), cur = state.papers[k];
      if (pos && pos.t && (!cur || (cur.t || 0) < pos.t)) { state.papers[k] = Object.assign({}, cur || {}, { p: pos.p, h: pos.h, t: pos.t }); changed = true; }
    });
    var prefs = ls('p2p:prefs', null);
    if (prefs && JSON.stringify(prefs) !== ls('p2p:prefsSeen', '')) {
      state.prefs = prefs; state.prefsT = Date.now(); lsSet('p2p:prefsSeen', JSON.stringify(prefs)); changed = true;
    }
    if (changed) lsSet('p2p:state', state);
    return changed;
  }
  var pushTimer = null;
  function schedulePush(force) {
    clearTimeout(pushTimer);
    pushTimer = setTimeout(function () { pushState(force); }, 900);
  }
  function pushState(force) {
    if (!drive || !folder) return Promise.resolve();
    if (!collectLocal() && !force) return Promise.resolve();
    var dir = sub.dot ? Promise.resolve(sub.dot) : drive.mkdir(folder.id, '.paper2phone').then(function (d) { sub.dot = d; return d; });
    var tries = 0;
    function attempt(d) {
      return drive.put(d.id, 'state.json', JSON.stringify(state), 'application/json', stateTag || undefined).then(function (it) {
        stateTag = it.etag; lsSet('p2p:stateTag', stateTag);
      }).catch(function (e) {
        if (e.conflict && tries++ < 3) {          // another device saved first: take its changes, then save ours on top
          return drive.child(d.id, 'state.json').then(function (it) {
            return drive.readText(it).then(function (t) { mergeState(JSON.parse(t)); collectLocal(); stateTag = it.etag; return attempt(d); });
          });
        }
        throw e;
      });
    }
    return dir.then(attempt).catch(function (e) { console.warn('state not saved', e); });
  }

  /* ---------- reading the folder ---------- */
  function setStatus(kind, text) { $('dot').className = 'dot' + (kind ? ' ' + kind : ''); $('status-text').textContent = text; }
  function syncedLabel() {
    if (drive && drive.demo) return setStatus('', 'Sample library. Sign in to use your OneDrive.');
    if (!navigator.onLine) return setStatus('off', 'Offline. Saved papers still open.');
    if (!lastSync) return;
    var s = Math.round((Date.now() - lastSync) / 1000);
    setStatus('', 'Synced with OneDrive ' + (s < 50 ? 'just now' : s < 3600 ? Math.round(s / 60) + ' min ago' : 'over an hour ago'));
  }
  setInterval(syncedLabel, 30000);

  function refresh(quiet) {
    if (!drive || !folder || syncing) return Promise.resolve();
    if (!navigator.onLine) { syncedLabel(); return Promise.resolve(); }
    syncing = true;
    if (!quiet) setStatus('busy', 'Syncing with OneDrive');
    return drive.children(folder.id).then(function (kids) {
      function find(n) { return kids.filter(function (k) { return k.folder && k.name.toLowerCase() === n.toLowerCase(); })[0] || null; }
      sub.dot = find('.paper2phone'); sub.bad = find('Could not convert'); sub.orig = find('Originals');
      var need = [find('Library') ? Promise.resolve(find('Library')) : drive.mkdir(folder.id, 'Library'),
                  find('Inbox') ? Promise.resolve(find('Inbox')) : drive.mkdir(folder.id, 'Inbox')];
      return Promise.all(need);
    }).then(function (both) {
      sub.lib = both[0]; sub.inbox = both[1];
      return Promise.all([drive.children(sub.lib.id), drive.children(sub.inbox.id), sub.bad ? drive.children(sub.bad.id) : [],
                          sub.dot ? drive.child(sub.dot.id, 'state.json') : null]);
    }).then(function (r) {
      var lib = r[0], inbox = r[1], bad = r[2], st = r[3], jobs = [];
      var lj = lib.filter(function (x) { return x.name === '.library.json'; })[0];
      if (lj && lj.etag !== libjsonTag) {
        jobs.push(drive.readText(lj).then(function (t) {
          libjson = {}; (JSON.parse(t) || []).forEach(function (e) { if (e.html) libjson[e.html] = e; });
          libjsonTag = lj.etag; lsSet('p2p:libjson', libjson);
        }).catch(function () {}));
      }
      if (st && st.etag !== stateTag) {
        jobs.push(drive.readText(st).then(function (t) { mergeState(JSON.parse(t)); stateTag = st.etag; lsSet('p2p:stateTag', stateTag); }).catch(function () {}));
      }
      return Promise.all(jobs).then(function () { return [lib, inbox, bad]; });
    }).then(function (r) {
      var lib = r[0], inbox = r[1], bad = r[2];
      var epubs = {};
      lib.forEach(function (x) { if (/\.epub$/i.test(x.name)) epubs[stem(x.name)] = x; });
      view.lib = lib.filter(function (x) { return !x.folder && PAPER_EXT.test(x.name) && x.name !== 'index.html'; }).map(function (x) {
        var e = epubs[stem(x.name)];
        return { item: x, epub: e ? { id: e.id, name: e.name } : null };
      });
      view.wait = inbox.filter(function (x) { return !x.folder && WAIT_EXT.test(x.name); });
      var notes = {};
      bad.forEach(function (x) { if (NOTE_RE.test(x.name)) notes[x.name.replace(NOTE_RE, '')] = x; });
      var pdfStems = {};
      bad.forEach(function (x) { if (/\.pdf$/i.test(x.name)) pdfStems[stem(x.name)] = 1; });
      view.bad = bad.filter(function (x) {
        return !x.folder && WAIT_EXT.test(x.name) && !NOTE_RE.test(x.name) && !(/\.txt$/i.test(x.name) && pdfStems[stem(x.name)]);
      }).map(function (x) {
        var note = notes[stem(x.name)] || bad.filter(function (y) { return y.name === stem(x.name) + '.txt' && /\.pdf$/i.test(x.name); })[0];
        return Object.assign({}, x, { note: note ? { id: note.id, name: note.name } : null });
      });
      lsSet('p2p:snap', { lib: view.lib.map(function (p) { return { item: Object.assign({}, p.item, { download: null }), epub: p.epub }; }), wait: view.wait, bad: view.bad });
      lastSync = Date.now();
      render();
      syncedLabel();
      return pushState();
    }).catch(function (e) {
      setStatus('bad', 'Could not reach OneDrive. Showing saved papers.');
      if (e && e.status === 404) { toast('That folder is gone. Choose your folder again.'); folder = null; lsDel('p2p:folder'); openFolderPicker(); }
      console.error(e);
    }).then(function () { syncing = false; });
  }

  /* ---------- the shelf ---------- */
  function metaFor(p) {
    var name = p.item.name, m = state.meta[name] || {}, j = libjson[name] || {};
    var parts = stem(name).split(' - '), guess = parts.length > 1 ? parts.slice(1).join(' - ') : parts[0];
    var by = parts.length > 1 ? parts[0] : '';
    return {
      title: m.title || j.title || guess, venue: [m.venue || j.venue, m.venue ? '' : j.year].filter(Boolean).join(', ') || by,
      key: m.key, minutes: m.minutes || 0, thumb: j.thumb || ls('p2p:thumb:' + name, '')
    };
  }
  function cover(thumb, label) {
    return thumb ? '<span class="cover img" style="background-image:url(&quot;' + thumb + '&quot;)"></span>' : '<span class="cover">' + esc(label || '') + '</span>';
  }
  var editing = false;
  function render() {
    var lib = $('lib'), wait = $('wait'), bad = $('bad');
    var papers = view.lib.slice().map(function (p) { return { p: p, m: metaFor(p) }; });
    papers.sort(function (a, b) {
      var oa = (a.m.key && state.papers[a.m.key] && state.papers[a.m.key].opened) || 0, ob = (b.m.key && state.papers[b.m.key] && state.papers[b.m.key].opened) || 0;
      return ob - oa || String(b.p.item.modified).localeCompare(String(a.p.item.modified));
    });
    lib.innerHTML = '';
    papers.forEach(function (x) { lib.appendChild(paperRow(x.p, x.m)); });
    if (!papers.length) {
      lib.innerHTML = '<li class="empty"><b>No papers yet</b>Tap Add a paper to choose a PDF or paste a link. Reader files saved to the Library folder from any device appear here too.</li>';
    }
    wait.innerHTML = '';
    view.wait.forEach(function (x) { wait.appendChild(waitRow(x)); });
    $('g-wait').hidden = !view.wait.length;
    bad.innerHTML = '';
    view.bad.forEach(function (x) { bad.appendChild(badRow(x)); });
    $('g-bad').hidden = !view.bad.length;
    $('edit').hidden = !papers.length;
  }
  function paperRow(p, m) {
    var st = m.key ? state.papers[m.key] : null, pos = m.key ? ls('p2p:pos:' + m.key, null) : null;
    var prog = Math.max((st && st.p) || 0, (pos && pos.p) || 0), pct = Math.round(prog * 100);
    var left = m.minutes ? Math.max(1, Math.ceil(m.minutes * (1 - prog))) : 0;
    var status = prog > 0.985 ? 'Finished' : prog > 0.02 ? pct + '% read' + (left ? ', ' + left + ' min left' : '') : (m.minutes ? m.minutes + ' min read' : 'Not opened yet');
    var year = (m.venue.match(/(19|20)\d\d/) || [''])[0];
    var li = D.createElement('li');
    li.className = 'row';
    li.innerHTML = '<button class="open" type="button">' + cover(m.thumb, year) + '<span class="meta"><span class="t">' + esc(m.title) +
      '</span><span class="v">' + esc(m.venue) + '</span><span class="p"><span class="bar"><i style="width:' + pct + '%"></i></span>' + esc(status) +
      '</span></span></button>' + (p.epub ? '<button class="rowbtn epub" type="button">Books</button>' : '') +
      '<button class="rowbtn del" type="button">Delete</button>';
    li.querySelector('.open').addEventListener('click', function () { if (!editing) openPaper(p); });
    var eb = li.querySelector('.epub');
    if (eb) eb.addEventListener('click', function () { openEpub(p); });
    var del = li.querySelector('.del'), armed = null;
    del.addEventListener('click', function () {
      if (!armed) { del.textContent = 'Confirm'; del.style.color = 'var(--warn)'; armed = setTimeout(function () { armed = null; del.textContent = 'Delete'; del.style.color = ''; }, 3500); return; }
      clearTimeout(armed);
      deletePaper(p);
    });
    return li;
  }
  function waitRow(x) {
    var li = D.createElement('li'), isLink = !/\.pdf$/i.test(x.name);
    li.className = 'row';
    li.innerHTML = '<div class="open" style="cursor:default"><span class="cover pending"><svg viewBox="0 0 24 24"><path d="M12 6v6l4 2"/><circle cx="12" cy="12" r="8.5"/></svg></span>' +
      '<span class="meta"><span class="t">' + esc(stem(x.name)) + '</span><span class="v">' + (isLink ? 'Link, waiting to download and convert' : 'PDF, ' + mb(x.size) + ', waiting to convert') +
      '</span><span class="tag">Waiting for your computer</span></span></div>';
    return li;
  }
  function badRow(x) {
    var li = D.createElement('li');
    li.className = 'row';
    li.innerHTML = '<button class="open" type="button"><span class="cover failed"><svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M12 7v6M12 16.5v.5"/><circle cx="12" cy="12" r="8.5"/></svg></span>' +
      '<span class="meta"><span class="t">' + esc(stem(x.name)) + '</span><span class="v">Tap to see what went wrong</span><span class="tag warn">Not converted</span></span></button>';
    li.querySelector('.open').addEventListener('click', function () { showNote(x); });
    return li;
  }
  $('edit').addEventListener('click', function () {
    editing = !editing;
    $('edit').setAttribute('aria-pressed', String(editing));
    $('edit').textContent = editing ? 'Done' : 'Edit';
    $('app').classList.toggle('editing', editing);
  });
  function deletePaper(p) {
    var jobs = [drive.remove(p.item.id)];
    if (p.epub) jobs.push(drive.remove(p.epub.id));
    Promise.all(jobs).then(function () {
      view.lib = view.lib.filter(function (x) { return x !== p; });
      render();
      toast(drive.demo ? 'Deleted from the sample.' : 'Moved to the OneDrive recycle bin.');
    }).catch(function (e) { fail(e, 'Deleting'); });
  }
  function openEpub(p) {
    var win = W.open('', '_blank');
    drive.item(p.epub.id).then(function (it) {
      if (win && it.download) win.location = it.download; else if (it.download) location.href = it.download;
    }).catch(function (e) { if (win) win.close(); fail(e, 'Opening the EPUB'); });
  }
  function showNote(x) {
    $('note-text').textContent = 'Loading…';
    showSheet('s-note');
    (x.note ? drive.readText(x.note) : Promise.resolve('No note was left for this item.')).then(function (t) {
      $('note-text').textContent = t.trim();
    }).catch(function () { $('note-text').textContent = 'The note could not be read.'; });
    $('note-retry').onclick = function () {
      hideSheet();
      var jobs = [drive.move(x.id, sub.inbox.id)];
      if (x.note) jobs.push(drive.remove(x.note.id));
      Promise.all(jobs).then(function () { toast('Back in the Inbox for another try.'); refresh(); }).catch(function (e) { fail(e, 'Moving it'); });
    };
    $('note-remove').onclick = function () {
      hideSheet();
      var jobs = [drive.remove(x.id)];
      if (x.note) jobs.push(drive.remove(x.note.id));
      Promise.all(jobs).then(function () { toast('Removed.'); refresh(); }).catch(function (e) { fail(e, 'Removing it'); });
    };
  }

  /* ---------- reading a paper ---------- */
  var INJECT = '<script>window.P2P_SHELF=1;document.addEventListener("click",function(e){var a=e.target.closest&&e.target.closest("a[href^=\\"http\\"]");if(a){a.target="_blank";a.rel="noopener";}},true);<\/script>';
  var readyTimer = null, reading = null, readPoll = null;
  function parseMeta(html) {
    var doc = new DOMParser().parseFromString(html, 'text/html'), paper = doc.getElementById('paper');
    if (!paper) return null;
    function tx(sel) { var e = doc.querySelector(sel); return e ? e.textContent.replace(/\s+/g, ' ').trim() : ''; }
    var img = doc.querySelector('.fig img');
    return { key: paper.getAttribute('data-key'), title: tx('h1.title') || doc.title, venue: tx('.venue'), authors: tx('.authors'),
             minutes: parseInt(paper.getAttribute('data-minutes') || '0', 10), img: img && img.getAttribute('src') };
  }
  function thumbFrom(src, name) {
    if (!src || ls('p2p:thumb:' + name, '')) return;
    var im = new Image();
    im.onload = function () {
      try {
        var c = D.createElement('canvas'), w = 116, h = 156; c.width = w; c.height = h;
        var x = c.getContext('2d'); x.fillStyle = '#fff'; x.fillRect(0, 0, w, h);
        var k = Math.max(w / im.naturalWidth, h / im.naturalHeight);
        x.drawImage(im, (w - im.naturalWidth * k) / 2, (h - im.naturalHeight * k) / 2, im.naturalWidth * k, im.naturalHeight * k);
        lsSet('p2p:thumb:' + name, c.toDataURL('image/jpeg', 0.78));
      } catch (e) {}
    };
    im.src = src;
  }
  function showReader(text) {
    $('reader').hidden = false;
    D.documentElement.classList.add('locked');
    $('loading').hidden = !text;
    $('loading-text').textContent = text || '';
    $('back').hidden = !text;
  }
  function openPaper(p) {
    reading = p;
    showReader('Opening');
    cacheGet(p.item.id).then(function (c) {
      if (c && (c.etag === p.item.etag || !navigator.onLine)) return c.html;
      $('loading-text').textContent = 'Downloading ' + mb(p.item.size || 0);
      return drive.readText(p.item).then(function (html) { cachePut({ id: p.item.id, etag: p.item.etag, html: html }); return html; })
        .catch(function (e) { if (c) return c.html; throw e; });
    }).then(function (html) {
      if (reading !== p) return;
      var m = parseMeta(html);
      if (m && m.key) {
        state.meta[p.item.name] = { key: m.key, title: m.title, venue: m.venue, authors: m.authors, minutes: m.minutes };
        var sp = state.papers[m.key], pos = ls('p2p:pos:' + m.key, null);
        if (sp && sp.t && (!pos || (pos.t || 0) < sp.t)) lsSet('p2p:pos:' + m.key, { p: sp.p, h: sp.h, t: sp.t });
        state.papers[m.key] = Object.assign({}, sp || {}, { opened: Date.now() });
        lsSet('p2p:state', state);
        thumbFrom(m.img, p.item.name);
        schedulePush(true);
      }
      $('frame').srcdoc = /<head[^>]*>/i.test(html) ? html.replace(/<head[^>]*>/i, function (x) { return x + INJECT; }) : INJECT + html;
      clearTimeout(readyTimer);
      readyTimer = setTimeout(function () { $('back').hidden = false; $('loading').hidden = true; }, 3000);
      clearInterval(readPoll);
      readPoll = setInterval(function () { schedulePush(); }, 60000);   // keep other devices current during long reads
    }).catch(function (e) { closeReader(); fail(e, 'Opening the paper'); });
  }
  function closeReader() {
    reading = null;
    clearInterval(readPoll);
    $('reader').hidden = true;
    D.documentElement.classList.remove('locked');
    $('frame').removeAttribute('srcdoc');
    $('frame').src = 'about:blank';
    render();
    schedulePush();
  }
  $('back').addEventListener('click', closeReader);
  W.addEventListener('message', function (e) {
    if (e.source !== $('frame').contentWindow || !e.data || e.data.type !== 'p2p') return;
    if (e.data.ready) { clearTimeout(readyTimer); $('back').hidden = true; $('loading').hidden = true; }
    if (e.data.close) closeReader();
  });

  /* ---------- adding papers ---------- */
  function isRef(t) { return /^\s*(https?:\/\/\S+|10\.\d{4,9}\/\S+|doi:\s*10\.\S+|arxiv:\s*\S+|PMC\d{5,9})\s*$/i.test(t); }
  function stamp() { var d = new Date(); return d.toISOString().slice(0, 10) + ' ' + String(d.getHours()).padStart(2, '0') + '.' + String(d.getMinutes()).padStart(2, '0') + '.' + String(d.getSeconds()).padStart(2, '0'); }
  function addFiles(files) {
    files = Array.prototype.slice.call(files || []);
    if (!files.length || !sub.inbox) return Promise.resolve();
    hideSheet();
    var n = 0;
    return files.reduce(function (chain, f) {
      return chain.then(function () {
        if (/\.pdf$/i.test(f.name) || f.type === 'application/pdf') {
          setStatus('busy', 'Uploading ' + f.name);
          return drive.upload(sub.inbox.id, /\.pdf$/i.test(f.name) ? f.name : 'Pasted ' + stamp() + '.pdf', f, function (p) { setStatus('busy', 'Uploading ' + Math.round(p * 100) + '%'); })
            .then(function () { n++; toast('Added to your Inbox. Your computer converts it next time it syncs.'); });
        }
        if (PAPER_EXT.test(f.name) || f.type === 'text/html') {
          return f.text().then(function (html) {
            var m = parseMeta(html);
            if (!m || !m.key) { toast('“' + f.name + '” is not a Paper to Phone reader file.'); return; }
            return drive.upload(sub.lib.id, f.name, new Blob([html], { type: 'text/html' })).then(function (it) {
              cachePut({ id: it.id, etag: it.etag, html: html });
              state.meta[it.name] = { key: m.key, title: m.title, venue: m.venue, authors: m.authors, minutes: m.minutes };
              thumbFrom(m.img, it.name);
              n++; toast('Added to your library.');
            });
          });
        }
        toast('Add a PDF, a reader file (.html), or a link.');
      });
    }, Promise.resolve()).then(function () { if (n) return refresh(); }).catch(function (e) { fail(e, 'Uploading'); refresh(true); });
  }
  function addLink(text) {
    text = text.trim();
    if (!isRef(text)) { toast('That does not look like a link, DOI or arXiv id.'); return Promise.resolve(); }
    hideSheet();
    return drive.put(sub.inbox.id, 'Link ' + stamp() + '.txt', text + '\n', 'text/plain', false).then(function () {
      $('link').value = ''; $('add-link').disabled = true;
      toast('Link added. Your computer downloads and converts it.');
      return refresh();
    }).catch(function (e) { fail(e, 'Adding the link'); });
  }
  $('add').addEventListener('click', function () { showSheet('s-add'); });
  $('choose-file').addEventListener('click', function () { $('pick').click(); });
  $('pick').addEventListener('change', function () { var f = $('pick').files; addFiles(f).then(function () { $('pick').value = ''; }); });
  $('link').addEventListener('input', function () { $('add-link').disabled = !isRef($('link').value); });
  $('link').addEventListener('keydown', function (e) { if (e.key === 'Enter' && !$('add-link').disabled) addLink($('link').value); });
  $('add-link').addEventListener('click', function () { addLink($('link').value); });
  $('paste').addEventListener('click', function () {
    if (!navigator.clipboard || !navigator.clipboard.readText) { $('link').focus(); toast('Long-press the field and choose Paste.'); return; }
    navigator.clipboard.readText().then(function (t) { $('link').value = (t || '').trim(); $('add-link').disabled = !isRef($('link').value); if (!t) toast('The clipboard is empty, or holds a file. To add a copied PDF, paste it on this screen.'); },
      function () { $('link').focus(); toast('Long-press the field and choose Paste.'); });
  });
  var PASTE_HINT = 'Long-press here and choose Paste';
  $('pastebox').addEventListener('input', function () { $('pastebox').textContent = PASTE_HINT; });   // never keep typed text
  $('pastebox').addEventListener('blur', function () { $('pastebox').textContent = PASTE_HINT; });
  D.addEventListener('paste', function (e) {
    if (!$('reader').hidden || !drive || !folder) return;
    var cd = e.clipboardData;
    if (!cd) return;
    var files = Array.prototype.slice.call(cd.files || []);
    if (!files.length && cd.items) {
      Array.prototype.forEach.call(cd.items, function (it) { if (it.kind === 'file') { var f = it.getAsFile(); if (f) files.push(f); } });
    }
    if (files.length) { e.preventDefault(); addFiles(files); return; }
    if (D.activeElement === $('pastebox')) {
      e.preventDefault();
      var t0 = (cd.getData('text') || '').trim();
      if (isRef(t0)) { addLink(t0); return; }
      toast('The clipboard has no PDF. Copy the PDF first (Share, then Copy), or add it with Choose a file.');
      return;
    }
    var t = (cd.getData('text') || '').trim();
    if (D.activeElement === $('link')) return;
    if (isRef(t)) { e.preventDefault(); $('link').value = t; $('add-link').disabled = false; showSheet('s-add'); }
  });
  var depth = 0;
  D.addEventListener('dragenter', function (e) { if (folder && e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types, 'Files') >= 0) { depth++; $('drop').hidden = false; } });
  D.addEventListener('dragleave', function () { depth = Math.max(0, depth - 1); if (!depth) $('drop').hidden = true; });
  D.addEventListener('dragover', function (e) { e.preventDefault(); });
  D.addEventListener('drop', function (e) { e.preventDefault(); depth = 0; $('drop').hidden = true; if (folder && e.dataTransfer) addFiles(e.dataTransfer.files); });

  /* ---------- choosing the folder ---------- */
  var trail = [];
  function openFolderPicker() {
    showSheet('s-folder');
    $('folders').innerHTML = '<li class="none">Loading your OneDrive…</li>';
    drive.root().then(function (r) { trail = [r]; return showFolder(); }).catch(function (e) { fail(e, 'Opening OneDrive'); });
  }
  function showFolder() {
    var cur = trail[trail.length - 1];
    var crumbs = $('crumbs');
    crumbs.innerHTML = '';
    trail.forEach(function (f, i) {
      if (i) crumbs.appendChild(D.createTextNode(' › '));
      var b = D.createElement('button'); b.type = 'button'; b.textContent = f.name;
      b.addEventListener('click', function () { trail = trail.slice(0, i + 1); showFolder(); });
      crumbs.appendChild(b);
    });
    $('folders').innerHTML = '<li class="none">Loading…</li>';
    return drive.children(cur.id).then(function (kids) {
      var dirs = kids.filter(function (k) { return k.folder && k.name[0] !== '.'; }).sort(function (a, b) { return a.name.localeCompare(b.name); });
      var looks = kids.some(function (k) { return k.folder && k.name === 'Library'; }) && kids.some(function (k) { return k.folder && k.name === 'Inbox'; });
      $('use-folder').textContent = looks ? 'Use this Paper to Phone folder' : 'Use this folder';
      $('new-folder').hidden = looks || kids.some(function (k) { return k.folder && k.name === 'Paper to Phone'; });
      var ul = $('folders');
      ul.innerHTML = dirs.length ? '' : '<li class="none">No folders here.</li>';
      dirs.forEach(function (d) {
        var li = D.createElement('li'), b = D.createElement('button');
        b.type = 'button';
        b.innerHTML = '<svg viewBox="0 0 24 24"><path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/></svg>' + esc(d.name);
        b.addEventListener('click', function () { trail.push(d); showFolder(); });
        li.appendChild(b); ul.appendChild(li);
      });
    });
  }
  function chooseFolder(f) {
    var path = trail.slice(1).map(function (x) { return x.name; }).concat(trail[trail.length - 1] === f ? [] : [f.name]).join('/');
    folder = { id: f.id, name: f.name, path: path || f.name };
    lsSet('p2p:folder', folder);
    drive.appPut({ folder: folder, t: Date.now() });
    hideSheet();
    view = { lib: [], wait: [], bad: [] }; stateTag = null; libjsonTag = null; libjson = {};
    render();
    refresh();
  }
  $('use-folder').addEventListener('click', function () {
    var cur = trail[trail.length - 1];
    if (trail.length === 1) { toast('Pick or create a folder inside OneDrive rather than the top level.'); return; }
    chooseFolder(cur);
  });
  $('new-folder').addEventListener('click', function () {
    var cur = trail[trail.length - 1];
    drive.mkdir(cur.id, 'Paper to Phone').then(function (f) {
      return Promise.all([drive.mkdir(f.id, 'Inbox'), drive.mkdir(f.id, 'Library')]).then(function () { trail.push(f); chooseFolder(f); });
    }).catch(function (e) { fail(e, 'Creating the folder'); });
  });

  /* ---------- account ---------- */
  $('who').addEventListener('click', function () {
    $('acct-name').textContent = me ? me.name + (me.email && me.email !== me.name ? ' (' + me.email + ')' : '') : '';
    $('acct-folder').textContent = folder ? 'OneDrive › ' + folder.path.replace(/\//g, ' › ') : 'Not chosen';
    $('signout').textContent = drive && drive.demo ? 'Leave the sample' : 'Sign out';
    showSheet('s-account');
  });
  $('change-folder').addEventListener('click', function () { hideSheet(); setTimeout(openFolderPicker, 250); });
  $('how').addEventListener('click', function () {
    var p = folder ? folder.path : 'Paper to Phone';
    $('cmd').textContent = 'python3 paper2phone.py --install-service "$HOME/Library/CloudStorage/OneDrive-Personal/' + p + '"\n\n' +
      'On Windows:\npy paper2phone.py --install-service "%USERPROFILE%\\OneDrive\\' + p.replace(/\//g, '\\') + '"';
    hideSheet(); setTimeout(function () { showSheet('s-how'); }, 250);
  });
  $('copy-cmd').addEventListener('click', function () {
    var t = $('cmd').textContent.split('\n')[0];
    (navigator.clipboard ? navigator.clipboard.writeText(t) : Promise.reject()).then(function () { toast('Copied. Paste it into Terminal on your Mac.'); },
      function () { toast('Select the command and copy it.'); });
  });
  $('signout').addEventListener('click', function () {
    ['p2p:folder', 'p2p:state', 'p2p:stateTag', 'p2p:snap', 'p2p:libjson'].forEach(lsDel);
    try { indexedDB.deleteDatabase('paper-to-phone-cache'); } catch (e) {}
    if (drive && drive.demo) { location.href = redirectUri; return; }
    pca.logoutRedirect({ account: account, postLogoutRedirectUri: redirectUri });
  });

  /* ---------- sheets ---------- */
  var openSheet = null;
  function showSheet(id) {
    if (openSheet) hideSheet();
    openSheet = $(id);
    openSheet.classList.add('on');
    $('scrim').classList.add('on');
    D.documentElement.classList.add('locked');
  }
  function hideSheet() {
    if (!openSheet) return;
    if (openSheet.contains(D.activeElement)) D.activeElement.blur();
    openSheet.classList.remove('on');
    openSheet = null;
    $('scrim').classList.remove('on');
    if ($('reader').hidden) D.documentElement.classList.remove('locked');
  }
  $('scrim').addEventListener('click', function () { if (folder || (drive && drive.demo)) hideSheet(); });
  Array.prototype.forEach.call(D.querySelectorAll('.sheet .close'), function (b) {
    b.addEventListener('click', function () { if (openSheet && openSheet.id === 's-folder' && !folder) return; hideSheet(); });
  });
  D.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    if (!$('reader').hidden) closeReader(); else if (openSheet && !(openSheet.id === 's-folder' && !folder)) hideSheet();
  });

  /* ---------- start ---------- */
  function startApp() {
    $('welcome').hidden = true;
    $('app').hidden = false;
    libjson = ls('p2p:libjson', {});
    render();
    syncedLabel();
    return cacheOpen().then(function () { return drive.me(); }).then(function (m) {
      me = m;
      $('who').textContent = (m.name || '?').split(/\s+/).map(function (w) { return w[0]; }).slice(0, 2).join('').toUpperCase();
      if (drive.demo) { trail = [{ id: 'd1', name: 'OneDrive' }, drive.home]; folder = { id: drive.home.id, name: drive.home.name, path: 'Paper to Phone' }; return refresh(); }
      folder = ls('p2p:folder', null);
      if (folder) return refresh();
      return drive.appGet().then(function (cfg) {
        if (cfg && cfg.folder) { folder = cfg.folder; lsSet('p2p:folder', folder); return refresh(); }
        openFolderPicker();
      });
    }).catch(function (e) {
      if (!navigator.onLine && ls('p2p:folder', null)) { folder = ls('p2p:folder', null); syncedLabel(); return; }
      fail(e, 'Connecting to OneDrive');
    });
  }
  function showWelcome() {
    $('app').hidden = true;
    $('welcome').hidden = false;
    var needId = !clientId();
    $('signin-box').hidden = needId;
    $('setup-box').hidden = !needId;
  }
  $('signin').addEventListener('click', function () {
    if (!pca) return;
    pca.loginRedirect({ scopes: SCOPES, prompt: 'select_account' });
  });
  function goDemo() { location.href = redirectUri + '?demo'; }
  $('demo').addEventListener('click', goDemo);
  $('demo2').addEventListener('click', goDemo);
  $('save-id').addEventListener('click', function () {
    var v = $('clientid').value.trim();
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v)) { toast('That is not an app ID. It looks like 1a2b3c4d-…'); return; }
    lsSet('p2p:clientId', v);
    location.reload();
  });
  D.addEventListener('visibilitychange', function () {
    if (D.visibilityState === 'visible' && Date.now() - lastSync > 15000 && $('reader').hidden) refresh(true);
    if (D.visibilityState === 'hidden') pushState();
  });
  setInterval(function () { if (D.visibilityState === 'visible' && $('reader').hidden) refresh(true); }, 45000);
  W.addEventListener('online', function () { refresh(); });
  W.addEventListener('offline', syncedLabel);
  if ('serviceWorker' in navigator && location.protocol === 'https:') navigator.serviceWorker.register('sw.js').catch(function () {});

  if (DEMO) {
    var ds = D.createElement('script');
    ds.src = 'demo/thumb.js';
    ds.onload = ds.onerror = function () { drive = DemoDrive(); startApp(); };
    D.head.appendChild(ds);
    return;
  }
  if (!clientId() || !W.msal) { showWelcome(); return; }
  authInit().then(function () {
    if (!account) { showWelcome(); return; }
    drive = Graph;
    return startApp();
  }).catch(function (e) { console.error(e); showWelcome(); toast('Sign-in could not start. Check your connection.'); });
})();

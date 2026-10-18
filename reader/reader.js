/* p2p-reader 2 */
(function () {
  'use strict';
  var D = document, R = D.documentElement, W = window;
  function $(s, c) { return (c || D).querySelector(s); }
  function $$(s, c) { return Array.prototype.slice.call((c || D).querySelectorAll(s)); }
  var reduce = W.matchMedia && W.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var paper = $('#paper');
  var KEY = 'p2p:pos:' + (paper.getAttribute('data-key') || location.pathname);
  var TOTAL = parseInt(paper.getAttribute('data-minutes') || '0', 10);
  function load(k, d) { try { var v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } }
  function save(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  function esc(s) { return s.replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function scrollTo(y) { W.scrollTo({ top: Math.max(0, y), behavior: reduce ? 'auto' : 'smooth' }); }
  function maxScroll() { return Math.max(1, R.scrollHeight - W.innerHeight); }

  /* ---------- preferences ---------- */
  var prefs = load('p2p:prefs', {}) || {};
  function fsNow() { return parseFloat(getComputedStyle(R).getPropertyValue('--fs')) || 18; }
  function anchor() {                       // keep the reader's place while the layout changes
    var el = D.elementFromPoint(W.innerWidth / 2, Math.min(140, W.innerHeight / 3));
    el = el && el.closest('p, h1, h2, h3, figure, li, .tbl, .box');
    if (!el || !paper.contains(el)) return function () {};
    var top = el.getBoundingClientRect().top;
    return function () { W.scrollTo(0, W.scrollY + el.getBoundingClientRect().top - top); };
  }
  function applyPrefs(keep) {
    var restore = keep ? anchor() : null;
    if (prefs.theme && prefs.theme !== 'auto') R.setAttribute('data-theme', prefs.theme); else R.removeAttribute('data-theme');
    if (prefs.font === 'sans') R.setAttribute('data-font', 'sans'); else R.removeAttribute('data-font');
    if (prefs.fs) R.style.setProperty('--fs', prefs.fs + 'px'); else R.style.removeProperty('--fs');
    var meta = $('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', getComputedStyle(D.body).backgroundColor);
    $$('.themes button').forEach(function (b) { b.setAttribute('aria-pressed', String((prefs.theme || 'auto') === b.dataset.v)); });
    $$('#set-font button').forEach(function (b) { b.setAttribute('aria-pressed', String((prefs.font || 'serif') === b.dataset.v)); });
    $('#fs-out').textContent = Math.round(fsNow());
    if (restore) { restore(); clampAll(); tables(D); }
  }
  $$('.themes button').forEach(function (b) {
    b.addEventListener('click', function () { prefs.theme = b.dataset.v; save('p2p:prefs', prefs); applyPrefs(true); });
  });
  $$('#set-font button').forEach(function (b) {
    b.addEventListener('click', function () { prefs.font = b.dataset.v; save('p2p:prefs', prefs); applyPrefs(true); });
  });
  $$('.stepper button').forEach(function (b) {
    b.addEventListener('click', function () {
      prefs.fs = Math.max(14, Math.min(28, Math.round(fsNow()) + parseInt(b.dataset.step, 10)));
      save('p2p:prefs', prefs); applyPrefs(true);
    });
  });

  /* ---------- sheets ---------- */
  var scrim = $('#scrim'), viewer = $('#viewer'), openSheet = null;
  function lock() { R.classList.toggle('locked', !!openSheet || viewer.classList.contains('on')); }
  function showSheet(id) {
    var s = D.getElementById(id);
    if (openSheet && openSheet !== s) hideSheet();
    openSheet = s;
    s.style.transform = '';
    s.classList.add('on');
    s.setAttribute('aria-hidden', 'false');
    scrim.classList.add('on');
    lock();
    dock.classList.add('hide');
  }
  function hideSheet() {
    if (!openSheet) return;
    var s = openSheet;
    openSheet = null;
    if (s.contains(D.activeElement)) D.activeElement.blur();
    s.classList.remove('on', 'drag');
    s.style.transform = '';
    s.setAttribute('aria-hidden', 'true');
    scrim.classList.remove('on');
    lock();
    dock.classList.remove('hide');
  }
  scrim.addEventListener('click', hideSheet);
  function bindSheet(s) {                          // close button, and drag the handle or header down to dismiss
    $$('.close', s).forEach(function (b) { b.addEventListener('click', hideSheet); });
    var y0 = null, t0 = 0, dy = 0;
    $$('.grab, header', s).forEach(function (h) {
      h.addEventListener('touchstart', function (e) {
        if (e.target.closest('button, input')) return;
        y0 = e.touches[0].clientY; t0 = Date.now(); dy = 0; s.classList.add('drag');
      }, { passive: true });
      h.addEventListener('touchmove', function (e) {
        if (y0 === null) return;
        dy = Math.max(0, e.touches[0].clientY - y0);
        s.style.transform = 'translateY(' + dy + 'px)';
      }, { passive: true });
      h.addEventListener('touchend', function () {
        if (y0 === null) return;
        s.classList.remove('drag');
        if (dy > 110 || dy / Math.max(1, Date.now() - t0) > 0.6) hideSheet(); else s.style.transform = '';
        y0 = null;
      });
    });
  }
  $$('.sheet').forEach(bindSheet);

  /* ---------- jumping around without losing your place ---------- */
  var back = $('#back'), backY = null, backTimer = null;
  function flash(el) {
    el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash');
    setTimeout(function () { el.classList.remove('flash'); }, 2500);
  }
  function jump(el, quiet) {
    var y0 = W.scrollY;
    var top = el.getBoundingClientRect().top + W.scrollY - Math.min(84, W.innerHeight * 0.12);
    scrollTo(top);
    flash(el);
    if (!quiet && Math.abs(top - y0) > W.innerHeight * 0.8) {
      backY = y0;
      back.classList.add('on');
      clearTimeout(backTimer);
      backTimer = setTimeout(function () { back.classList.remove('on'); }, 10000);
    }
  }
  back.addEventListener('click', function () {
    if (backY !== null) scrollTo(backY);
    backY = null;
    back.classList.remove('on');
  });

  /* ---------- figures in the viewer ---------- */
  var items = [];
  $$('.paper .fig, .paper .tbl').forEach(function (el) {
    var shot = $('.shot, .orig', el);
    if (!shot) return;
    var cap = $('.cap', el), lab = cap && $('.flabel', cap);
    var label = lab ? lab.textContent : (el.classList.contains('tbl') ? 'Table' : 'Figure');
    var imgs = $$('img', shot);
    imgs.forEach(function (im, k) {
      items.push({ el: el, img: im, label: label, part: imgs.length > 1 ? (k + 1) + '/' + imgs.length : '', cap: cap ? cap.innerHTML : '' });
    });
    shot.addEventListener('click', function () { openViewer(indexOf(el)); });
  });
  function indexOf(el) { for (var i = 0; i < items.length; i++) if (items[i].el === el) return i; return -1; }

  var stage = $('.stage', viewer), vimg = $('img', stage), vtitle = $('.vtop b', viewer), vcount = $('.vtop small', viewer);
  var vcap = $('.vcap', viewer), vcapIn = $('.vcap .in', viewer), vhint = $('.vhint', viewer);
  var vi = -1, s = 1, tx = 0, ty = 0, bw = 0, bh = 0, ox = 0, oy = 0;
  function openViewer(i) {
    if (i < 0 || !items.length) return;
    if (openSheet) hideSheet();
    viewer.classList.add('on');
    viewer.setAttribute('aria-hidden', 'false');
    lock();
    requestAnimationFrame(function () { viewer.classList.add('in'); });
    show(i);
    if (!load('p2p:hint', false)) {
      save('p2p:hint', true);
      vhint.classList.add('on');
      setTimeout(function () { vhint.classList.remove('on'); }, 2200);
    }
  }
  function closeViewer() {
    viewer.classList.remove('in');
    viewer.setAttribute('aria-hidden', 'true');
    setTimeout(function () {
      viewer.classList.remove('on', 'bare');
      viewer.style.background = '';
      vimg.removeAttribute('src');
      lock();
    }, reduce ? 0 : 250);
  }
  function show(i) {
    vi = (i + items.length) % items.length;
    var it = items[vi];
    s = 1; tx = 0; ty = 0;
    viewer.style.background = '';
    vtitle.textContent = it.label + (it.part ? ' (' + it.part + ')' : '');
    vcount.textContent = items.length > 1 ? (vi + 1) + ' of ' + items.length : '';
    vcapIn.innerHTML = it.cap;
    vcap.hidden = !it.cap;
    vcap.classList.add('clamp');
    vcap.scrollTop = 0;
    vimg.onload = layout;
    vimg.src = it.img.src;
    vimg.alt = it.img.alt;
    if (vimg.complete) layout();
  }
  function layout() {
    var Wd = stage.clientWidth, Ht = stage.clientHeight;
    var nw = vimg.naturalWidth || 1, nh = vimg.naturalHeight || 1;
    var top = 64, bottom = vcap.hidden ? 24 : Math.min(150, Ht * 0.24);
    var k = Math.min((Wd - 16) / nw, (Ht - top - bottom) / nh, 3);
    bw = nw * k; bh = nh * k;
    vimg.style.width = bw + 'px';
    vimg.style.height = bh + 'px';
    ox = (Wd - bw) / 2;
    oy = top + (Ht - top - bottom - bh) / 2;
    apply(false);
  }
  function apply(anim) {
    vimg.classList.toggle('anim', !!anim);
    vimg.style.transform = 'translate(' + (ox + tx) + 'px,' + (oy + ty) + 'px) scale(' + s + ')';
  }
  function bound() {
    var Wd = stage.clientWidth, Ht = stage.clientHeight, w = bw * s, h = bh * s;
    if (w <= Wd) tx = (Wd - w) / 2 - ox; else tx = Math.min(0, Math.max(Wd - w, ox + tx)) - ox;
    if (s <= 1.001) ty = 0;
    else if (h <= Ht) ty = (Ht - h) / 2 - oy;
    else ty = Math.min(0, Math.max(Ht - h, oy + ty)) - oy;
  }
  function zoomAt(px, py, ns, anim) {
    ns = Math.max(1, Math.min(8, ns));
    var u = (px - ox - tx) / s, v = (py - oy - ty) / s;
    s = ns; tx = px - ox - u * s; ty = py - oy - v * s;
    if (s <= 1.001) { s = 1; tx = 0; ty = 0; }
    bound(); apply(anim);
  }
  function reset(anim) { s = 1; tx = 0; ty = 0; apply(anim); }
  function step(dir) {
    if (items.length < 2) { reset(true); return; }
    vimg.style.opacity = '0';
    setTimeout(function () { show(vi + dir); vimg.style.opacity = ''; }, reduce ? 0 : 120);
  }
  function dist(a, b) { return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY); }
  var pinch = null, pan = null, swipe = null, moved = false, lastTap = 0, lastX = 0, lastY = 0, tapTimer = null;
  stage.addEventListener('touchstart', function (e) {
    if (e.target.closest('.vcap, .vtop')) return;
    e.preventDefault();
    var t = e.touches;
    if (t.length === 2) {
      pan = null; swipe = null; moved = true;
      pinch = { d: dist(t[0], t[1]) || 1, s: s, mx: (t[0].clientX + t[1].clientX) / 2, my: (t[0].clientY + t[1].clientY) / 2, tx: tx, ty: ty };
    } else if (t.length === 1 && !pinch) {
      moved = false;
      pan = { x: t[0].clientX, y: t[0].clientY, tx: tx, ty: ty, t: Date.now() };
    }
  }, { passive: false });
  stage.addEventListener('touchmove', function (e) {
    if (e.target.closest('.vcap, .vtop')) return;
    e.preventDefault();
    var t = e.touches;
    if (pinch && t.length === 2) {
      var mx = (t[0].clientX + t[1].clientX) / 2, my = (t[0].clientY + t[1].clientY) / 2;
      var ns = Math.max(0.7, Math.min(8, pinch.s * dist(t[0], t[1]) / pinch.d));
      var u = (pinch.mx - ox - pinch.tx) / pinch.s, v = (pinch.my - oy - pinch.ty) / pinch.s;
      s = ns; tx = mx - ox - u * s; ty = my - oy - v * s;
      apply(false);
      return;
    }
    if (!pan || t.length !== 1) return;
    var dx = t[0].clientX - pan.x, dy = t[0].clientY - pan.y;
    if (!moved && Math.abs(dx) + Math.abs(dy) > 8) moved = true;
    if (!moved) return;
    if (s > 1.001) { tx = pan.tx + dx; ty = pan.ty + dy; apply(false); return; }
    if (!swipe) swipe = Math.abs(dy) > Math.abs(dx) ? 'v' : 'h';
    if (swipe === 'v') {
      ty = dy;
      viewer.style.background = 'rgba(0,0,0,' + Math.max(0.25, 1 - Math.abs(dy) / 420) + ')';
    } else { tx = dx * (items.length > 1 ? 1 : 0.3); }
    apply(false);
  }, { passive: false });
  stage.addEventListener('touchend', function (e) {
    if (e.target.closest('.vcap, .vtop')) return;
    e.preventDefault();
    if (pinch) {
      if (e.touches.length < 2) {
        pinch = null;
        if (s < 1.02) reset(true); else { bound(); apply(true); }
        pan = null;
      }
      return;
    }
    if (!pan) return;
    var c = e.changedTouches[0], dx = c.clientX - pan.x, dy = c.clientY - pan.y, dt = Math.max(1, Date.now() - pan.t);
    pan = null;
    if (!moved) { tap(c.clientX, c.clientY); return; }
    if (s > 1.001) { bound(); apply(true); }
    else if (swipe === 'v') {
      if (Math.abs(dy) > 110 || Math.abs(dy) / dt > 0.65) closeViewer();
      else { viewer.style.background = ''; reset(true); }
    } else if (swipe === 'h') {
      if (items.length > 1 && (Math.abs(dx) > 70 || Math.abs(dx) / dt > 0.5)) step(dx < 0 ? 1 : -1); else reset(true);
    }
    swipe = null;
  }, { passive: false });
  function tap(x, y) {
    var now = Date.now();
    if (now - lastTap < 300 && Math.hypot(x - lastX, y - lastY) < 30) {
      clearTimeout(tapTimer); lastTap = 0;
      if (s > 1.05) { s = 1; bound(); reset(true); } else zoomAt(x, y, 2.6, true);
      return;
    }
    lastTap = now; lastX = x; lastY = y;
    tapTimer = setTimeout(function () { viewer.classList.toggle('bare'); }, 300);
  }
  stage.addEventListener('wheel', function (e) {
    e.preventDefault();
    zoomAt(e.clientX, e.clientY, s * Math.exp(-e.deltaY * (e.ctrlKey ? 0.012 : 0.0025)), false);
  }, { passive: false });
  var drag = null;
  stage.addEventListener('mousedown', function (e) {
    if (e.target.closest('.vcap, .vtop')) return;
    drag = { x: e.clientX, y: e.clientY, tx: tx, ty: ty, m: false };
  });
  W.addEventListener('mousemove', function (e) {
    if (!drag) return;
    var dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 4) drag.m = true;
    if (s > 1.001) { tx = drag.tx + dx; ty = drag.ty + dy; apply(false); }
  });
  W.addEventListener('mouseup', function () { if (drag) { if (s > 1.001) { bound(); apply(true); } drag = null; } });
  stage.addEventListener('dblclick', function (e) {
    if (e.target.closest('.vcap, .vtop')) return;
    if (s > 1.05) reset(true); else zoomAt(e.clientX, e.clientY, 2.6, true);
  });
  $('.vclose', viewer).addEventListener('click', closeViewer);
  vcap.addEventListener('click', function (e) {
    if (e.target.closest('a')) return;
    vcap.classList.toggle('clamp');
  });
  W.addEventListener('resize', function () { if (viewer.classList.contains('on')) { reset(false); layout(); } });

  /* ---------- citations, figure and table mentions ---------- */
  var peekList = $('#peek-list'), peekTbl = $('#peek-table'), peekGo = $('#peek-go'), peekTitle = $('#peek-title');
  var numbered = !!$('.paper ol.refs');
  function rangeTitle(ns) {
    if (!numbered) return ns.length > 1 ? 'References' : 'Reference';
    if (ns.length === 1) return 'Reference ' + ns[0];
    var v = ns.map(Number), run = v.every(function (n, i) { return !i || n === v[i - 1] + 1; });
    return 'References ' + (run ? v[0] + '–' + v[v.length - 1] : v.join(', '));
  }
  function peekRefs(ns, focus) {
    ns = ns.filter(function (n) { return D.getElementById('ref-' + n); });
    if (!ns.length) return;
    peekList.innerHTML = '';
    peekList.className = numbered ? 'refs' : 'refs ay';
    ns.forEach(function (n) {
      var li = D.getElementById('ref-' + n).cloneNode(true);
      li.removeAttribute('id');
      peekList.appendChild(li);
    });
    peekList.hidden = false; peekTbl.hidden = true; peekTbl.innerHTML = '';
    peekTitle.textContent = rangeTitle(ns);
    peekGo.textContent = 'Show in the reference list';
    peekGo.onclick = function () { hideSheet(); jump(D.getElementById('ref-' + (focus || ns[0]))); };
    showSheet('peek');
    var body = $('#peek .body'); body.scrollTop = 0;
    var i = ns.indexOf(String(focus));
    if (i > 0) body.scrollTop = peekList.children[i].offsetTop - 8;
  }
  function peekTable(el) {
    var c = el.cloneNode(true);
    c.removeAttribute('id');
    $$('[id]', c).forEach(function (x) { x.removeAttribute('id'); });
    $$('.more', c).forEach(function (x) { x.remove(); });
    $$('.cap', c).forEach(function (x) { x.classList.remove('clamp'); });
    peekTbl.innerHTML = '';
    peekTbl.appendChild(c);
    peekTbl.hidden = false; peekList.hidden = true;
    var lab = $('.flabel', el);
    peekTitle.textContent = lab ? lab.textContent : 'Table';
    peekGo.textContent = 'Go to the table';
    peekGo.onclick = function () { hideSheet(); jump(el); };
    $$('.orig, .shot', c).forEach(function (b) { b.addEventListener('click', function () { openViewer(indexOf(el)); }); });
    showSheet('peek');
    $('#peek .body').scrollTop = 0;
    tables(peekTbl);
  }
  D.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a');
    if (!a) return;
    if (a.classList.contains('cite')) {
      e.preventDefault();
      var g = a.closest('.cg');
      var ns = g && g.dataset.refs ? g.dataset.refs.split(' ') : [a.dataset.ref];
      if (ns.indexOf(a.dataset.ref) < 0) ns = [a.dataset.ref];
      peekRefs(ns, a.dataset.ref);
      return;
    }
    if (a.classList.contains('xref')) {
      e.preventDefault();
      var t = D.getElementById(a.dataset.x);
      if (!t) return;
      if (t.classList.contains('fig') && indexOf(t) >= 0) openViewer(indexOf(t));
      else if (t.classList.contains('tbl')) peekTable(t);
      else { if (viewer.classList.contains('on')) closeViewer(); hideSheet(); jump(t); }
      return;
    }
    var href = a.getAttribute('href') || '';
    if (href.charAt(0) === '#' && href.length > 1) {
      var target = D.getElementById(decodeURIComponent(href.slice(1)));
      if (target) { e.preventDefault(); hideSheet(); jump(target); }
    }
  });

  /* ---------- contents, search and the figure shelf ---------- */
  var heads = $$('.paper h2[id], .paper h3[id]').filter(function (h) { return !h.closest('.box, .mast'); });
  var toc = $('#toc'), shelf = $('#shelf'), results = $('#results'), q = $('#q'), built = false;
  function buildContents() {
    if (built) return;
    built = true;
    heads.forEach(function (h) {
      var li = D.createElement('li'), a = D.createElement('a');
      li.className = h.tagName === 'H2' ? 'l2' : 'l3';
      a.href = '#' + h.id;
      a.textContent = h.textContent;
      li.appendChild(a);
      toc.appendChild(li);
    });
    var seen = [];
    items.forEach(function (it, i) {
      if (seen.indexOf(it.el) >= 0) return;
      seen.push(it.el);
      var b = D.createElement('button'), im = D.createElement('i'), sp = D.createElement('span');
      b.type = 'button'; b.className = 'thumb';
      im.style.backgroundImage = 'url("' + it.img.src + '")';
      sp.textContent = it.label;
      b.appendChild(im); b.appendChild(sp);
      b.addEventListener('click', function () { openViewer(i); });
      shelf.appendChild(b);
    });
    if (!seen.length) { shelf.hidden = true; $('#shelf-lbl').hidden = true; }
    if (!heads.length) $('#toc-lbl').hidden = true;
  }
  function openContents(searching) {
    buildContents();
    renderHl();
    var cur = current();
    $$('a', toc).forEach(function (a) { a.classList.toggle('cur', !!cur && a.getAttribute('href') === '#' + cur.id); });
    showSheet('contents');
    var body = $('#contents .body');
    var on = $('a.cur', toc);
    body.scrollTop = 0;
    if (on && !searching) {
      var y = on.getBoundingClientRect().top - body.getBoundingClientRect().top;
      if (y > body.clientHeight * 0.75) body.scrollTop = y - body.clientHeight / 2;
    }
    if (searching) q.focus();
  }
  var index = null;
  function textOf(el) {                     // superscript citations get a space so words stay apart
    var out = '', w = D.createTreeWalker(el, NodeFilter.SHOW_TEXT), n;
    while ((n = w.nextNode())) { if (n.parentNode.closest('sup')) out += ' '; out += n.nodeValue; }
    return out;
  }
  function buildIndex() {
    index = [];
    var section = '';
    $$('.paper h2[id], .paper h3[id], .paper p, .paper figcaption, .paper .refs li').forEach(function (el) {
      if (el.closest('.mast')) return;
      var t = textOf(el).replace(/\s+/g, ' ').trim();
      if (!t) return;
      var kind = 'p', label = section, target = el;
      if (/^H[23]$/.test(el.tagName)) { kind = 's'; section = t; label = t; }
      else if (el.classList.contains('cap')) { kind = 'f'; target = el.closest('.fig, .tbl') || el; var l = $('.flabel', el); label = l ? l.textContent : 'Caption'; }
      else if (el.tagName === 'LI') { kind = 'r'; var n = $('.rn', el); label = n ? 'Reference ' + n.textContent : 'Reference'; }
      else if (el.closest('.tbl')) return;
      index.push({ el: target, kind: kind, label: label, t: t, l: t.toLowerCase() });
    });
  }
  function snippet(it, terms) {
    var i = Math.max(0, it.l.indexOf(terms[0])), a = Math.max(0, i - 48), b = Math.min(it.t.length, i + 120);
    var out = esc((a > 0 ? '…' : '') + it.t.slice(a, b) + (b < it.t.length ? '…' : ''));
    terms.forEach(function (t) {
      out = out.replace(new RegExp('(' + esc(t).replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'ig'), '<mark>$1</mark>');
    });
    return out;
  }
  var searchT = null;
  q.addEventListener('input', function () {
    clearTimeout(searchT);
    searchT = setTimeout(runSearch, 110);
  });
  q.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { var f = $('.hit', results); if (f) f.click(); }
  });
  function runSearch() {
    var v = q.value.trim().toLowerCase();
    var terms = v.split(/\s+/).filter(function (t) { return t.length > 0; });
    var on = terms.length && v.length >= 2;
    results.hidden = !on;
    $('#contents-main').hidden = !!on;
    if (!on) return;
    if (!index) buildIndex();
    var groups = { s: [], f: [], p: [], r: [] };
    index.forEach(function (it) { if (terms.every(function (t) { return it.l.indexOf(t) >= 0; })) groups[it.kind].push(it); });
    var names = { s: 'Sections', f: 'Figures and tables', p: 'Text', r: 'References' }, caps = { s: 12, f: 12, p: 40, r: 25 };
    results.innerHTML = '';
    var total = 0;
    ['s', 'f', 'p', 'r'].forEach(function (k) {
      var g = groups[k];
      if (!g.length) return;
      total += g.length;
      var h = D.createElement('p');
      h.className = 'lbl';
      h.textContent = names[k] + (g.length > caps[k] ? ' (' + caps[k] + ' of ' + g.length + ')' : ' (' + g.length + ')');
      results.appendChild(h);
      g.slice(0, caps[k]).forEach(function (it) {
        var b = D.createElement('button');
        b.type = 'button'; b.className = 'hit';
        b.innerHTML = (it.label ? '<b>' + esc(it.label) + '</b>' : '') + (k === 's' ? '' : snippet(it, terms));
        b.addEventListener('click', function () { hideSheet(); jump(it.el); });
        results.appendChild(b);
      });
    });
    if (!total) results.innerHTML = '<p class="none">Nothing in this paper matches “' + esc(q.value.trim()) + '”.</p>';
  }

  /* ---------- dock, progress and resume ---------- */
  var dock = $('#dock'), where = $('#where b'), sub = $('#where small'), ring = $('#ring .val');
  var C = 2 * Math.PI * 11, lastY = W.scrollY, ticking = false, saveT = null, curName = null;
  ring.style.strokeDasharray = C;
  var shortTitle = (function () { var h = $('h1.title'); var t = h ? h.textContent.trim() : D.title; return t; })();
  function current() {
    var cur = null;
    for (var i = 0; i < heads.length; i++) { if (heads[i].getBoundingClientRect().top < 150) cur = heads[i]; else break; }
    return cur;
  }
  function tick() {
    ticking = false;
    var y = W.scrollY, max = maxScroll(), p = Math.min(1, Math.max(0, y / max));
    ring.style.strokeDashoffset = C * (1 - p);
    var cur = current(), name = cur ? cur.textContent : shortTitle;
    if (name !== curName) { where.textContent = name; curName = name; }
    var left = Math.ceil(TOTAL * (1 - p));
    sub.textContent = p < 0.015 ? (TOTAL ? TOTAL + ' min read' : 'Start') : p > 0.985 ? 'Finished' : (TOTAL ? left + ' min left' : Math.round(p * 100) + '%');
    if (!openSheet && !viewer.classList.contains('on') && !(selbar && selbar.classList.contains('on'))) {
      if (y > lastY + 8 && y > 260) dock.classList.add('hide');
      else if (y < lastY - 8 || y >= max - 80) dock.classList.remove('hide');
    }
    lastY = y;
    clearTimeout(saveT);
    saveT = setTimeout(function () { save(KEY, { p: p, h: cur ? cur.textContent : '', t: Date.now() }); }, 700);
  }
  W.addEventListener('scroll', function () { if (!ticking) { ticking = true; requestAnimationFrame(tick); } }, { passive: true });
  $('#where').addEventListener('click', function () { openContents(false); });
  $('#find').addEventListener('click', function () { openContents(true); });
  $('#aa').addEventListener('click', function () { showSheet('settings'); });

  var toast = $('#toast'), last = load(KEY, null);
  if (last && last.p > 0.03 && last.p < 0.97) {
    $('small', toast).textContent = (last.h ? last.h + ', ' : '') + Math.round(last.p * 100) + '% read';
    setTimeout(function () { toast.classList.add('on'); }, 500);
    var hideToast = setTimeout(function () { toast.classList.remove('on'); }, 9000);
    $('.go', toast).addEventListener('click', function () {
      clearTimeout(hideToast); toast.classList.remove('on');
      scrollTo(last.p * maxScroll());
    });
    $('.x', toast).addEventListener('click', function () { clearTimeout(hideToast); toast.classList.remove('on'); });
  }

  /* ---------- captions, authors and tables that need a second look ---------- */
  function clampOne(el, label) {
    if (el.dataset.open) return;
    el.classList.add('clamp');
    var b = el.nextElementSibling, has = b && b.classList.contains('more');
    var over = el.scrollHeight > el.clientHeight + 2;
    if (over && !has) {
      b = D.createElement('button');
      b.type = 'button'; b.className = 'more'; b.textContent = label;
      b.addEventListener('click', function () { el.classList.remove('clamp'); el.dataset.open = '1'; b.remove(); });
      el.parentNode.insertBefore(b, el.nextSibling);
    } else if (!over) {
      el.classList.remove('clamp');
      if (has) b.remove();
    }
  }
  function clampAll() {
    $$('.paper .fig > .cap, .paper .tbl > .cap').forEach(function (c) { clampOne(c, 'Show full caption'); });
    var au = $('.paper .authors');
    if (au) clampOne(au, 'Show all authors');
  }
  function tables(root) {
    $$('.tscroll', root).forEach(function (t) {
      function upd() {
        t.classList.toggle('scrolls', t.scrollWidth > t.clientWidth + 2);
        t.classList.toggle('end', t.scrollLeft + t.clientWidth >= t.scrollWidth - 2);
        t.classList.toggle('moved', t.scrollLeft > 2);
      }
      upd();
      if (!t.dataset.bound) { t.dataset.bound = '1'; t.addEventListener('scroll', upd, { passive: true }); }
    });
  }

  /* ---------- the facts row ---------- */
  $$('[data-act]').forEach(function (b) {
    b.addEventListener('click', function () {
      var act = b.dataset.act;
      if (act === 'figures') { for (var i = 0; i < items.length; i++) if (items[i].el.classList.contains('fig')) return openViewer(i); }
      if (act === 'tables') { var t = $('.paper .tbl'); if (t) jump(t); }
      if (act === 'refs') { var r = $('.paper .refs'); var h = r && r.closest('section'); h = h && h.previousElementSibling; jump(h && /^H/.test(h.tagName) ? h : r); }
    });
  });

  /* ---------- highlights and notes ----------
     Select text: a bar offers Highlight and Note. Tap a highlight to add a note, copy or remove it.
     Each highlight is stored by block number and character offsets, plus its quote as a check,
     under p2p:hl:<paper key> as { items: {id: {s, a, e, b, q, n, c, u}}, gone: {id: time} }. */
  var HKEY = 'p2p:hl:' + (paper.getAttribute('data-key') || location.pathname);
  var BLOCK = 'p, li, figcaption, h1, h2, h3, h4, h5, h6, td, th, dt, dd, blockquote';
  var blocks = $$(BLOCK, paper).filter(function (b) { return !b.querySelector(BLOCK) && !b.closest('.refs ol ol'); });
  blocks.forEach(function (b, i) { b.__hb = i; });
  var notes = load(HKEY, null);
  if (!notes || typeof notes.items !== 'object') notes = { items: {}, gone: {} };
  if (!notes.gone) notes.gone = {};
  function saveNotes() { notes.t = Date.now(); save(HKEY, notes); }
  function norm(t) { return String(t || '').replace(/\s+/g, ' ').trim(); }
  function blockOf(n) {
    for (var e = n && (n.nodeType === 3 ? n.parentNode : n); e && e !== paper; e = e.parentNode) if (e.__hb !== undefined) return e;
    return null;
  }
  function texts(root) {
    var out = [], w = D.createTreeWalker(root, NodeFilter.SHOW_TEXT), n;
    while ((n = w.nextNode())) if (!n.parentNode.closest('button, svg, script, style')) out.push(n);
    return out;
  }
  function textIn(b) { return texts(b).map(function (t) { return t.nodeValue; }).join(''); }
  function offsetIn(b, node, off) {                 // characters of b's text before a boundary point
    var r = D.createRange(), k = 0, ts = texts(b);
    r.setStart(b, 0); r.setEnd(node, off);
    for (var i = 0; i < ts.length; i++) {
      if (ts[i] === node) return k + off;
      if (r.comparePoint(ts[i], ts[i].nodeValue.length) === 0) k += ts[i].nodeValue.length; else break;
    }
    return k;
  }
  function pointAt(b, k, end) {                     // the text node and offset k characters into b
    var ts = texts(b), acc = 0;
    for (var i = 0; i < ts.length; i++) {
      var L = ts[i].nodeValue.length;
      if (k < acc + L || (end && k === acc + L)) return [ts[i], k - acc];
      acc += L;
    }
    var last = ts[ts.length - 1];
    return last ? [last, last.nodeValue.length] : null;
  }
  function quoteOf(s, a, e, b) {
    var out = [];
    for (var i = s; i <= e; i++) { var t = textIn(blocks[i]); out.push(t.slice(i === s ? a : 0, i === e ? b : t.length)); }
    return norm(out.join(' '));
  }
  function describe(rg) {
    var sb = blockOf(rg.startContainer), eb = blockOf(rg.endContainer), a, b;
    var hit = blocks.filter(function (x) { return rg.intersectsNode(x); });
    if (!hit.length) return null;
    a = sb ? offsetIn(sb, rg.startContainer, rg.startOffset) : 0;
    b = eb ? offsetIn(eb, rg.endContainer, rg.endOffset) : textIn(hit[hit.length - 1]).length;
    sb = sb || hit[0]; eb = eb || hit[hit.length - 1];
    var s = sb.__hb, e = eb.__hb;
    if (e < s || (s === e && b <= a)) return null;
    var st = textIn(sb), et = s === e ? st : textIn(eb), W1 = /[\wÀ-ɏͰ-Ͽ-]/;
    while (a > 0 && W1.test(st.charAt(a - 1)) && W1.test(st.charAt(a))) a--;          // whole words only
    while (b < et.length && W1.test(et.charAt(b)) && W1.test(et.charAt(b - 1))) b++;
    var q = quoteOf(s, a, e, b);
    return q ? { s: s, a: a, e: e, b: b, q: q.slice(0, 4000) } : null;
  }
  function place(it) {                              // check the stored spot still holds the quote; else look for it
    if (blocks[it.s] && blocks[it.e] && quoteOf(it.s, it.a, it.e, it.b) === norm(it.q)) return true;
    for (var i = 0; i < blocks.length; i++) {
      var t = textIn(blocks[i]), k = t.indexOf(it.q);
      if (k >= 0) { it.s = it.e = i; it.a = k; it.b = k + it.q.length; return true; }
    }
    return false;
  }
  function paint(id, it) {
    it.lost = !place(it);
    if (it.lost) return;
    for (var i = it.e; i >= it.s; i--) {
      var bl = blocks[i], L = textIn(bl).length, a = i === it.s ? it.a : 0, b = i === it.e ? it.b : L;
      if (b <= a) continue;
      var p0 = pointAt(bl, a, false), p1 = pointAt(bl, b, true);
      if (!p0 || !p1) continue;
      var ts = texts(bl), i0 = ts.indexOf(p0[0]), i1 = ts.indexOf(p1[0]);
      for (var j = i1; j >= i0; j--) {              // backwards, so splitting leaves earlier nodes alone
        var t = ts[j], s0 = j === i0 ? p0[1] : 0, e0 = j === i1 ? p1[1] : t.nodeValue.length;
        if (e0 <= s0) continue;
        if (e0 < t.nodeValue.length) t.splitText(e0);
        if (s0 > 0) t = t.splitText(s0);
        var m = D.createElement('mark');
        m.className = 'hl' + (it.n ? ' noted' : '');
        m.setAttribute('data-h', id);
        t.parentNode.insertBefore(m, t);
        m.appendChild(t);
      }
    }
  }
  function marks(id) { return $$('mark.hl[data-h="' + id + '"]', paper); }
  function unpaint(id) {
    marks(id).forEach(function (m) {
      var p = m.parentNode;
      while (m.firstChild) p.insertBefore(m.firstChild, m);
      p.removeChild(m);
      p.normalize();
    });
  }
  function ordered() {
    return Object.keys(notes.items).map(function (id) { return { id: id, it: notes.items[id] }; })
      .sort(function (x, y) { return (x.it.s - y.it.s) || (x.it.a - y.it.a); });
  }
  ordered().forEach(function (x) { paint(x.id, x.it); });

  function copyText(t, btn) {
    function done() { if (!btn) return; var was = btn.textContent; btn.textContent = 'Copied'; setTimeout(function () { btn.textContent = was; }, 1600); }
    function legacy() {
      var ta = D.createElement('textarea');
      ta.value = t; ta.setAttribute('readonly', ''); ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
      D.body.appendChild(ta); ta.select();
      try { D.execCommand('copy'); done(); } catch (e) {}
      ta.remove();
    }
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(t).then(done, legacy); else legacy();
  }

  /* the bar that appears while text is selected */
  var selbar = D.createElement('div');
  selbar.className = 'selbar'; selbar.id = 'selbar';
  selbar.innerHTML = '<button type="button" data-a="hl"><i aria-hidden="true"></i>Highlight</button>' +
    '<button type="button" data-a="note"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5.5A1.5 1.5 0 0 1 6.5 4h11A1.5 1.5 0 0 1 19 5.5v9a1.5 1.5 0 0 1-1.5 1.5H10l-4 3.5V16h0a1 1 0 0 1-1-1z"/></svg>Note</button>';
  D.body.appendChild(selbar);
  var pending = null, selT = null;
  function selRange() {
    var s = W.getSelection();
    if (!s || s.isCollapsed || !s.rangeCount) return null;
    var r = s.getRangeAt(0);
    return paper.contains(r.commonAncestorContainer) && norm(r.toString()) ? r : null;
  }
  function showBar(on) {
    if (on === selbar.classList.contains('on')) return;
    selbar.classList.toggle('on', on);
    if (on) dock.classList.add('hide'); else if (!openSheet) dock.classList.remove('hide');
  }
  D.addEventListener('selectionchange', function () {
    clearTimeout(selT);
    selT = setTimeout(function () {
      var r = openSheet || viewer.classList.contains('on') ? null : selRange();
      if (r) pending = r.cloneRange();
      showBar(!!r);
    }, 160);
  });
  selbar.addEventListener('mousedown', function (e) { e.preventDefault(); });     // keep the selection on a computer
  function create() {
    var r = selRange() || pending, it = r && describe(r);
    pending = null;
    try { W.getSelection().removeAllRanges(); } catch (e) {}
    showBar(false);
    if (!it) return null;
    var id = 'h' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    it.c = it.u = Date.now();
    notes.items[id] = it;
    paint(id, it);
    saveNotes();
    return id;
  }
  selbar.addEventListener('click', function (e) {
    var b = e.target.closest('button');
    if (!b) return;
    var id = create();
    if (id && b.dataset.a === 'note') openNote(id, true);
  });

  /* the sheet for one highlight: its note, copy and remove */
  var hs = D.createElement('section');
  hs.className = 'sheet'; hs.id = 'hlsheet';
  hs.setAttribute('role', 'dialog'); hs.setAttribute('aria-modal', 'true'); hs.setAttribute('aria-label', 'Highlight'); hs.setAttribute('aria-hidden', 'true');
  hs.innerHTML = '<div class="grab"></div><header><h2>Highlight</h2>' + $('.sheet .close').outerHTML + '</header>' +
    '<div class="body"><blockquote class="hlq"></blockquote>' +
    '<textarea id="hlnote" rows="3" placeholder="Add a note" aria-label="Note"></textarea>' +
    '<div class="hlrow"><button type="button" class="warn" id="hl-del">Remove highlight</button><button type="button" id="hl-copy">Copy</button></div></div>';
  D.body.appendChild(hs);
  bindSheet(hs);
  var noteEl = $('#hlnote', hs), editing = null, noteT = null;
  function flushNote() {
    clearTimeout(noteT);
    var it = editing && notes.items[editing];
    if (!it) return;
    var v = noteEl.value.replace(/\s+$/, '');
    if (v === (it.n || '')) return;
    it.n = v; it.u = Date.now();
    marks(editing).forEach(function (m) { m.classList.toggle('noted', !!v); });
    saveNotes();
  }
  noteEl.addEventListener('input', function () { clearTimeout(noteT); noteT = setTimeout(flushNote, 500); });
  noteEl.addEventListener('blur', flushNote);
  function openNote(id, focus) {
    var it = notes.items[id];
    if (!it) return;
    flushNote();
    editing = id;
    $('.hlq', hs).textContent = it.q;
    noteEl.value = it.n || '';
    showSheet('hlsheet');
    if (focus) noteEl.focus({ preventScroll: true });
  }
  $('#hl-del', hs).addEventListener('click', function () {
    var id = editing;
    editing = null;
    if (id && notes.items[id]) { delete notes.items[id]; notes.gone[id] = Date.now(); unpaint(id); saveNotes(); }
    hideSheet();
  });
  $('#hl-copy', hs).addEventListener('click', function () {
    var it = editing && notes.items[editing];
    if (it) copyText('“' + it.q + '”' + (noteEl.value.trim() ? '\n' + noteEl.value.trim() : ''), this);
  });
  D.addEventListener('click', function (e) {
    var m = e.target.closest && e.target.closest('mark.hl');
    if (!m || !paper.contains(m) || e.target.closest('a, button')) return;
    var s = W.getSelection();
    if (s && !s.isCollapsed) return;
    openNote(m.getAttribute('data-h'), false);
  });

  /* every highlight, listed at the top of Contents */
  var hlWrap = D.createElement('div');
  hlWrap.id = 'hl-wrap'; hlWrap.hidden = true;
  hlWrap.innerHTML = '<div class="lblrow"><p class="lbl" id="hl-lbl">Highlights</p><button type="button" class="mini" id="hl-all">Copy all</button></div><div id="hl-list"></div>';
  var cm = $('#contents-main');
  if (cm) cm.insertBefore(hlWrap, cm.firstChild);
  function renderHl() {
    var list = ordered(), box = $('#hl-list', hlWrap);
    hlWrap.hidden = !list.length;
    box.innerHTML = '';
    $('#hl-lbl', hlWrap).textContent = 'Highlights (' + list.length + ')';
    list.forEach(function (x) {
      var b = D.createElement('button');
      b.type = 'button'; b.className = 'hit hlitem';
      b.innerHTML = '<span class="hq">' + esc(x.it.q) + '</span>' + (x.it.n ? '<span class="hn">' + esc(x.it.n) + '</span>' : '') +
        (x.it.lost ? '<span class="hn">Not found in this version of the paper</span>' : '');
      b.addEventListener('click', function () {
        var m = marks(x.id)[0];
        if (!m) return;
        hideSheet();
        jump(m);
      });
      box.appendChild(b);
    });
  }
  $('#hl-all', hlWrap).addEventListener('click', function () {
    var venue = $('.venue');
    var out = ['Highlights: ' + shortTitle + (venue ? '\n' + norm(venue.textContent) : '')];
    ordered().forEach(function (x) { out.push('“' + x.it.q + '”' + (x.it.n ? '\nNote: ' + x.it.n : '')); });
    copyText(out.join('\n\n'), this);
  });

  /* ---------- keyboard ---------- */
  D.addEventListener('keydown', function (e) {
    if (viewer.classList.contains('on')) {
      if (e.key === 'Escape') closeViewer();
      else if (e.key === 'ArrowRight') step(1);
      else if (e.key === 'ArrowLeft') step(-1);
      return;
    }
    if (e.key === 'Escape' && openSheet) { hideSheet(); return; }
    var typing = /INPUT|TEXTAREA/.test((D.activeElement || {}).tagName || '');
    if ((e.key === 'k' && (e.metaKey || e.ctrlKey)) || (e.key === '/' && !typing)) { e.preventDefault(); openContents(true); }
  });

  /* ---------- inside the Paper to Phone shelf ---------- */
  if (W.P2P_SHELF && W.parent !== W) {
    var lib = D.createElement('button');
    lib.type = 'button'; lib.className = 'ibtn'; lib.id = 'lib';
    lib.setAttribute('aria-label', 'Back to your papers');
    lib.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14.5 5.5L8 12l6.5 6.5"/></svg>';
    dock.insertBefore(lib, dock.firstChild);
    lib.addEventListener('click', function () { W.parent.postMessage({ type: 'p2p', close: true }, '*'); });
    W.parent.postMessage({ type: 'p2p', ready: true }, '*');
  }

  applyPrefs(false);
  clampAll();
  tables(D);
  tick();
  W.addEventListener('resize', function () { clampAll(); tables(D); });
})();

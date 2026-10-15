/**
 * Arxiu virtual — interfície de les pàgines «Arxiu» (arbre de destí, cobertura, comprovacions).
 *
 * - Arbre: plegar i desplegar amb estat persistent (localStorage), filtre de text, filtre per
 *   paper, nivell de desplegament; edició en línia de paper, destí i nota; menú ⋯ per membre
 *   (mou, treu, explorador, cobertura, edita) i per espai (fill, reanomena, estat, mou, ordena,
 *   esborra); amplades de columna compartides per totes les taules de membres, amb nanses a les
 *   capçaleres i persistència («reinicia amplades» les torna al valor inicial).
 * - Cobertura: nivell a nivell (camí de molles) amb desplegament en línia (el servidor serveix
 *   les files del nivell següent), llindar «amaga < 1 GB» persistent, ordenació, selecció
 *   múltiple a qualsevol profunditat i «Afegeix a l'arxiu» (espai, paper, destí, nota).
 * - Pendents: xips que filtren els grups (persistents), grups plegables, «canvia el paper» i
 *   «treu de l'espai» directes.
 * - Totes les pàgines: commutadors «detalls» (camps per executar) i «edició» (botons d'acció),
 *   persistents a localStorage; «Desfés» (darrera acció) i registre d'accions.
 *
 * Cada acció crida l'API JSON (/api/arxiu/…). Els canvis simples (paper, destí, nota d'un
 * membre; nom i estat d'un espai) s'apliquen en línia sense recarregar; la resta recarrega la
 * pàgina (el servidor té la memòria cau: és ràpid) conservant el scroll. Després de cada acció,
 * un tost amb «Desfés» a dins; diàleg de confirmació propi només per esborrar un espai, amb
 * l'abast real. JavaScript pur, sense dependències.
 */
(function () {
  'use strict';
  var cfgEl = document.getElementById('ax-config');
  if (!cfgEl) return;
  var cfg = JSON.parse(cfgEl.textContent || '{}');
  var API = cfg.api || '/api/arxiu';
  var URLS = cfg.urls || {};
  var SPACES = [];
  try { SPACES = JSON.parse((document.getElementById('ax-spaces-json') || {}).textContent || '[]'); } catch (e) { SPACES = []; }
  var spaceByPath = {};
  SPACES.forEach(function (s) { spaceByPath[s.path.toLowerCase()] = s; });
  var msgEl = document.getElementById('ax-msg');
  var SCROLL_KEY = 'arxiu.scroll';

  // ----------------------------------------------------------------- utilitats
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function icon(name) {
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'icon');
    svg.setAttribute('aria-hidden', 'true');
    var use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', '#' + name);
    svg.appendChild(use);
    return svg;
  }
  function qs(params) {
    return Object.keys(params).filter(function (k) { return params[k] != null && params[k] !== ''; })
      .map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]); }).join('&');
  }
  function msg(text, isError) {
    if (!msgEl) return;
    msgEl.textContent = text;
    msgEl.hidden = !text;
    msgEl.classList.toggle('ax-msg-error', !!isError);
  }
  var TOAST_KEY = 'arxiu.toast';
  function reload(toast) {
    try {
      sessionStorage.setItem(SCROLL_KEY, String(window.scrollY));
      if (toast) sessionStorage.setItem(TOAST_KEY, JSON.stringify(toast));
    } catch (e) { /* ignora */ }
    location.reload();
  }
  try {
    var y = parseInt(sessionStorage.getItem(SCROLL_KEY), 10);
    if (y) { sessionStorage.removeItem(SCROLL_KEY); window.scrollTo(0, y); }
    var pendingToast = sessionStorage.getItem(TOAST_KEY);
    if (pendingToast) { sessionStorage.removeItem(TOAST_KEY); setTimeout(function () { showToast(JSON.parse(pendingToast)); }, 50); }
  } catch (e) { /* ignora */ }

  // ----------------------------------------------------------------- tost (amb «Desfés» a dins)
  var toastEl = document.getElementById('ax-toast');
  var toastTimer = null;
  function hideToast() { if (toastEl) { toastEl.hidden = true; toastEl.textContent = ''; } clearTimeout(toastTimer); }
  /** t: {text, undo: bool, error: bool} */
  function showToast(t) {
    if (!toastEl) return;
    clearTimeout(toastTimer);
    toastEl.textContent = '';
    toastEl.classList.toggle('ax-toast-error', !!t.error);
    toastEl.appendChild(el('span', 'ax-toast-text', t.text));
    if (t.undo) {
      var u = el('button', 'btn', 'Desfés');
      u.type = 'button';
      u.addEventListener('click', function () {
        u.disabled = true;
        api('POST', '/undo').then(function (d) {
          var what = d.undone ? d.undone.summary : 'res';
          reload({ text: 'Desfet: ' + what });
        }).catch(function () {});
      });
      toastEl.appendChild(u);
    }
    var x = el('button', 'ax-toast-close', '✕');
    x.type = 'button';
    x.setAttribute('aria-label', 'Tanca');
    x.addEventListener('click', hideToast);
    toastEl.appendChild(x);
    toastEl.hidden = false;
    toastTimer = setTimeout(hideToast, t.error ? 12000 : 9000);
  }
  function setLastAction(la) {
    if (undoBtn) {
      undoBtn.disabled = !la;
      undoBtn.title = la ? 'Desfés: ' + la.summary : 'No hi ha cap acció per desfer';
    }
    var lastEl = document.getElementById('ax-last');
    if (lastEl) lastEl.textContent = la ? la.summary : '';
  }

  // ----------------------------------------------------------------- diàleg (només per esborrar)
  var dialogEl = document.getElementById('ax-dialog');
  /** opts: {title, intro, scope: [[label, value]...], list: [..], okLabel}; resol true si es confirma */
  function confirmDialog(opts) {
    return new Promise(function (resolve) {
      if (!dialogEl || typeof dialogEl.showModal !== 'function') { resolve(window.confirm(opts.title)); return; }
      document.getElementById('ax-dialog-title').textContent = opts.title;
      var body = document.getElementById('ax-dialog-body');
      body.textContent = '';
      if (opts.intro) body.appendChild(el('p', null, opts.intro));
      if (opts.scope && opts.scope.length) {
        var sc = el('div', 'ax-dialog-scope');
        opts.scope.forEach(function (pair) { var sp = el('span', null, pair[0] + ' '); sp.appendChild(el('b', null, String(pair[1]))); sc.appendChild(sp); });
        body.appendChild(sc);
      }
      if (opts.list && opts.list.length) {
        var ul = el('ul');
        opts.list.forEach(function (x) { ul.appendChild(el('li', null, x)); });
        body.appendChild(ul);
      }
      body.appendChild(el('p', 'muted', 'Es pot desfer amb «Desfés».'));
      document.getElementById('ax-dialog-ok').textContent = opts.okLabel || 'Esborra';
      dialogEl.returnValue = 'cancel';
      function onClose() { dialogEl.removeEventListener('close', onClose); resolve(dialogEl.returnValue === 'ok'); }
      dialogEl.addEventListener('close', onClose);
      dialogEl.showModal();
      document.getElementById('ax-dialog-cancel').focus();
    });
  }
  function store(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* ignora */ }
  }
  function loadStored(key, fallback) {
    try { var v = JSON.parse(localStorage.getItem(key)); return v == null ? fallback : v; } catch (e) { return fallback; }
  }

  function api(method, path, body) {
    msg('Desant…');
    return fetch(API + path, {
      method: method,
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok && !j.error, data: j }; }); })
      .then(function (res) {
        msg('');
        if (!res.ok) { showToast({ text: res.data.error || 'Error', error: true }); throw new Error(res.data.error || 'Error'); }
        return res.data;
      }, function (e) { msg(''); showToast({ text: 'No s’ha pogut parlar amb el servidor.', error: true }); throw e; });
  }
  /** Acció que recarrega la pàgina (conservant el scroll) i mostra un tost amb «Desfés». */
  function act(method, path, body) {
    return api(method, path, body).then(function (d) {
      reload({ text: d.summary || 'Fet', undo: !!d.last_action });
      return d;
    }).catch(function () { /* tost d'error ja mostrat */ });
  }
  /** Acció aplicada en línia: `apply(d)` actualitza el DOM i no es recarrega res. */
  function actInline(method, path, body, apply) {
    return api(method, path, body).then(function (d) {
      if (d.changed === false) { showToast({ text: 'Sense canvis' }); return d; }
      apply(d);
      setLastAction(d.last_action);
      showToast({ text: d.summary || 'Fet', undo: !!d.last_action });
      return d;
    }).catch(function () {});
  }

  function fillDatalist() {
    var dl = document.getElementById('ax-spaces');
    if (!dl || dl.children.length) return;
    SPACES.forEach(function (s) {
      var o = document.createElement('option');
      o.value = s.path;
      o.label = s.kind;
      dl.appendChild(o);
    });
  }
  fillDatalist();

  function resolveSpace(value) {
    var v = (value || '').trim().toLowerCase();
    if (!v) return null;
    if (spaceByPath[v]) return spaceByPath[v];
    var hits = SPACES.filter(function (s) { return s.path.toLowerCase().indexOf(v) >= 0; });
    if (hits.length === 1) return hits[0];
    var exactName = hits.filter(function (s) { return s.path.toLowerCase().split('\\').pop() === v; });
    if (exactName.length === 1) return exactName[0];
    return null;
  }

  // Selector d'espai en línia: un <input list> que substitueix un element i crida `done(space)`
  function inlineSpacePicker(anchor, placeholder, done) {
    var wrap = el('span', 'ax-inline');
    var input = el('input', 'ax-inline-ctrl');
    input.setAttribute('list', 'ax-spaces');
    input.placeholder = placeholder || 'espai de destí…';
    input.autocomplete = 'off';
    var ok = el('button', 'ax-act', 'd’acord');
    ok.type = 'button';
    var cancel = el('button', 'ax-act', 'cancel·la');
    cancel.type = 'button';
    wrap.appendChild(input); wrap.appendChild(ok); wrap.appendChild(cancel);
    anchor.appendChild(wrap);
    input.focus();
    function finish() {
      var s = resolveSpace(input.value);
      if (!s) { msg('No trobe cap espai d’arxiu que es diga així.', true); input.focus(); return; }
      wrap.remove();
      done(s);
    }
    ok.addEventListener('click', finish);
    cancel.addEventListener('click', function () { wrap.remove(); });
    input.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter') { ev.preventDefault(); finish(); }
      if (ev.key === 'Escape') { wrap.remove(); }
    });
  }

  // ----------------------------------------------------------------- menú ⋯ (mateix estil que l'explorador)
  var menuEl = document.getElementById('ax-menu');
  var menuState = { open: false, anchorBtn: null, returnFocus: null, openedAt: 0 };
  function closeMenu() {
    if (!menuState.open || !menuEl) return;
    menuState.open = false;
    menuEl.hidden = true;
    menuEl.textContent = '';
    if (menuState.anchorBtn) menuState.anchorBtn.setAttribute('aria-expanded', 'false');
    var rf = menuState.returnFocus;
    menuState.returnFocus = null;
    menuState.anchorBtn = null;
    if (rf && document.contains(rf)) rf.focus({ preventScroll: true });
  }
  function menuItems() { return Array.prototype.slice.call(menuEl.querySelectorAll('[role="menuitem"]')); }
  /** items: {label, icon?, href?, action?, danger?} o {sep: true}; anchorBtn: el botó ⋯ */
  function openMenu(items, anchorBtn, title) {
    if (!menuEl) return;
    closeMenu();
    if (title) {
      var head = el('div', 'explorer-menu-head', title);
      head.title = title;
      menuEl.appendChild(head);
    }
    items.forEach(function (it) {
      if (it.sep) { menuEl.appendChild(el('div', 'explorer-menu-sep')); return; }
      var item;
      if (it.href) { item = el('a', 'explorer-menu-item'); item.href = it.href; }
      else { item = el('button', 'explorer-menu-item'); item.type = 'button'; }
      if (it.danger) item.classList.add('ax-menu-danger');
      item.setAttribute('role', 'menuitem');
      item.setAttribute('tabindex', '-1');
      if (it.icon) item.appendChild(icon(it.icon));
      item.appendChild(el('span', null, it.label));
      item.addEventListener('click', function (e) {
        if (it.action) { e.preventDefault(); closeMenu(); it.action(); return; }
        if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
        closeMenu();
      });
      menuEl.appendChild(item);
    });
    menuState.open = true;
    menuState.openedAt = Date.now();
    menuState.returnFocus = anchorBtn;
    menuState.anchorBtn = anchorBtn;
    anchorBtn.setAttribute('aria-expanded', 'true');
    menuEl.hidden = false;
    menuEl.style.left = '0px';
    menuEl.style.top = '0px';
    var w = menuEl.offsetWidth, h = menuEl.offsetHeight;
    var r = anchorBtn.getBoundingClientRect();
    var x = r.right - w, y = r.bottom + 4;
    if (y + h > window.innerHeight - 8) y = Math.max(8, r.top - h - 4);
    x = Math.max(8, Math.min(x, window.innerWidth - w - 8));
    menuEl.style.left = x + 'px';
    menuEl.style.top = y + 'px';
    var first = menuItems()[0];
    if (first) first.focus();
  }
  if (menuEl) {
    menuEl.addEventListener('keydown', function (e) {
      var items = menuItems();
      var idx = items.indexOf(document.activeElement);
      switch (e.key) {
        case 'ArrowDown': items[(idx + 1) % items.length].focus(); break;
        case 'ArrowUp': items[(idx - 1 + items.length) % items.length].focus(); break;
        case 'Home': items[0].focus(); break;
        case 'End': items[items.length - 1].focus(); break;
        case 'Escape': closeMenu(); break;
        case 'Tab': closeMenu(); return;
        default: return;
      }
      e.preventDefault();
    });
    document.addEventListener('mousedown', function (e) {
      if (menuState.open && !menuEl.contains(e.target)) closeMenu();
    });
    window.addEventListener('resize', closeMenu);
    window.addEventListener('scroll', function () { if (menuState.open && Date.now() - menuState.openedAt > 250) closeMenu(); }, true);
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && menuState.open) { e.preventDefault(); closeMenu(); }
    });
  }

  // ----------------------------------------------------------------- commutadors «detalls» i «edició»
  var UI_KEY = 'arxiu.ui.v1';
  var ui = loadStored(UI_KEY, {});
  function isEdit() { return document.body.classList.contains('ax-edit'); }
  [['ax-sw-details', 'details', 'ax-details'], ['ax-sw-edit', 'edit', 'ax-edit']].forEach(function (p) {
    var b = document.getElementById(p[0]);
    if (!b) return;
    b.setAttribute('aria-pressed', ui[p[1]] ? 'true' : 'false');
    document.body.classList.toggle(p[2], !!ui[p[1]]);
    b.addEventListener('click', function () {
      ui[p[1]] = !ui[p[1]];
      store(UI_KEY, ui);
      b.setAttribute('aria-pressed', ui[p[1]] ? 'true' : 'false');
      document.body.classList.toggle(p[2], !!ui[p[1]]);
      closeMenu();
    });
  });

  // ----------------------------------------------------------------- desfés i registre
  var undoBtn = document.getElementById('ax-undo');
  if (undoBtn) {
    undoBtn.addEventListener('click', function () {
      api('POST', '/undo').then(function (d) { reload({ text: 'Desfet: ' + (d.undone ? d.undone.summary : 'res') }); }).catch(function () {});
    });
  }
  var logToggle = document.getElementById('ax-log-toggle');
  var logEl = document.getElementById('ax-log');
  if (logToggle && logEl) {
    logToggle.addEventListener('click', function () {
      if (!logEl.hidden) { logEl.hidden = true; return; }
      api('GET', '/log?limit=30').then(function (d) {
        logEl.innerHTML = '';
        if (!d.actions.length) logEl.appendChild(el('p', 'muted', 'Cap acció encara.'));
        d.actions.forEach(function (a) {
          var p = el('div', 'ax-log-row' + (a.undone_utc ? ' ax-log-undone' : ''));
          p.appendChild(el('span', 'muted mono', (a.utc || '').slice(0, 16).replace('T', ' ')));
          p.appendChild(el('span', '', ' ' + a.summary + (a.undone_utc ? ' (desfeta)' : '')));
          logEl.appendChild(p);
        });
        logEl.hidden = false;
        msg('');
      }).catch(function () {});
    });
  }

  // ----------------------------------------------------------------- amplades de columna compartides
  var COLS_KEY = 'arxiu.cols.v1';
  var COL_DEF = { role: 56, disc: 72, size: 88, uniq: 56, files: 80, sub: 110, note: 220, act: 34 };
  var COL_MIN = { role: 40, disc: 52, size: 70, uniq: 48, files: 56, sub: 60, note: 80, act: 34 };
  var COL_MAX = 900;
  function initColumns(root) {
    var cols = loadStored(COLS_KEY, {});
    function apply() {
      Object.keys(COL_DEF).forEach(function (k) {
        var w = cols[k] || COL_DEF[k];
        root.style.setProperty('--axc-' + k, w + 'px');
      });
    }
    apply();
    var drag = null;
    root.addEventListener('pointerdown', function (ev) {
      var h = ev.target.closest('.ax-rz');
      if (!h || ev.button !== 0) return;
      ev.preventDefault();
      var k = h.dataset.col;
      drag = { col: k, x0: ev.clientX, w0: cols[k] || COL_DEF[k], handle: h };
      h.classList.add('ax-rz-active');
      document.documentElement.classList.add('ax-resizing');
      try { h.setPointerCapture(ev.pointerId); } catch (e) { /* ignora */ }
    });
    root.addEventListener('pointermove', function (ev) {
      if (!drag) return;
      var w = Math.round(drag.w0 + ev.clientX - drag.x0);
      w = Math.max(COL_MIN[drag.col], Math.min(COL_MAX, w));
      cols[drag.col] = w;
      root.style.setProperty('--axc-' + drag.col, w + 'px');
    });
    function endDrag() {
      if (!drag) return;
      drag.handle.classList.remove('ax-rz-active');
      document.documentElement.classList.remove('ax-resizing');
      drag = null;
      store(COLS_KEY, cols);
    }
    root.addEventListener('pointerup', endDrag);
    root.addEventListener('pointercancel', endDrag);
    root.addEventListener('dblclick', function (ev) {
      var h = ev.target.closest('.ax-rz');
      if (!h) return;
      delete cols[h.dataset.col];
      store(COLS_KEY, cols);
      apply();
    });
    // la nansa no ha d'activar l'ordenació ni el plegat de la fila
    root.addEventListener('click', function (ev) { if (ev.target.closest('.ax-rz')) ev.stopPropagation(); }, true);
    var reset = document.getElementById('ax-cols-reset');
    if (reset) reset.addEventListener('click', function () { cols = {}; store(COLS_KEY, cols); apply(); });
  }

  // ----------------------------------------------------------------- arbre
  var tree = document.getElementById('ax-tree');
  if (tree) initTree();

  function initTree() {
    var KEY = 'arxiu.tree.v1';
    var state = load();
    var nodes = Array.prototype.slice.call(tree.querySelectorAll('.ax-node'));
    var filterInput = document.getElementById('ax-filter');
    var chips = document.getElementById('ax-role-chips');
    var showMembers = document.getElementById('ax-show-members');
    var countEl = document.getElementById('ax-count');
    initColumns(tree);

    function load() {
      try {
        var s = JSON.parse(localStorage.getItem(KEY) || '{}');
        return { expanded: s.expanded || null, filter: s.filter || '', roles: s.roles || [], members: s.members !== false };
      } catch (e) {
        return { expanded: null, filter: '', roles: [], members: true };
      }
    }
    function save() {
      try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* ignora */ }
    }
    function setExpanded(li, on) {
      li.setAttribute('aria-expanded', on ? 'true' : 'false');
      if (!state.expanded) state.expanded = {};
      if (on) state.expanded[li.dataset.space] = 1; else delete state.expanded[li.dataset.space];
    }

    // Estat inicial: el desat o, en un subarbre (?arrel=), el que ve del servidor
    var subtree = /[?&]arrel=\d+/.test(location.search);
    nodes.forEach(function (li) {
      var on;
      if (state.expanded && !subtree) on = !!state.expanded[li.dataset.space];
      else on = parseInt(li.dataset.depth, 10) < 2;
      li.setAttribute('aria-expanded', on ? 'true' : 'false');
    });
    if (!state.expanded) {
      state.expanded = {};
      nodes.forEach(function (li) { if (li.getAttribute('aria-expanded') === 'true') state.expanded[li.dataset.space] = 1; });
    }

    tree.addEventListener('click', function (ev) {
      if (ev.target.closest('.ax-rz')) return;
      var more = ev.target.closest('.ax-more');
      if (more) { ev.preventDefault(); onMore(more); return; }
      var actBtn = ev.target.closest('.ax-act');
      if (actBtn) { onAction(actBtn.dataset.act, actBtn); return; }
      var noteCell = ev.target.closest('.ax-note');
      if (noteCell && !noteCell.classList.contains('ax-editing')) {
        if (noteCell.textContent) noteCell.classList.toggle('ax-note-open');
        return;
      }
      var editCell = ev.target.closest('.ax-edit');
      if (editCell && !editCell.classList.contains('ax-editing')) { if (isEdit()) startEdit(editCell); return; }
      var btn = ev.target.closest('.ax-toggle');
      if (btn) {
        var li = btn.closest('.ax-node');
        setExpanded(li, li.getAttribute('aria-expanded') !== 'true');
        save();
        return;
      }
      var row = ev.target.closest('.ax-row');
      if (row && !ev.target.closest('a, input, select, button')) {
        var li2 = row.closest('.ax-node');
        setExpanded(li2, li2.getAttribute('aria-expanded') !== 'true');
        save();
      }
    });
    tree.addEventListener('dblclick', function (ev) {
      var noteCell = ev.target.closest('.ax-note');
      if (noteCell && !noteCell.classList.contains('ax-editing') && isEdit()) {
        ev.preventDefault();
        noteCell.classList.remove('ax-note-open');
        startEdit(noteCell);
      }
    });

    document.querySelectorAll('.ax-levels [data-level]').forEach(function (b) {
      b.addEventListener('click', function () {
        var level = parseInt(b.dataset.level, 10);
        nodes.forEach(function (li) { setExpanded(li, parseInt(li.dataset.depth, 10) < level); });
        save();
      });
    });

    // Filtres: text (nom d'espai, disc, carpeta, nota) i paper dels membres
    function applyFilters() {
      var q = (state.filter || '').trim().toLowerCase();
      var roles = state.roles || [];
      tree.classList.toggle('ax-hide-members', !state.members);
      var shownMembers = 0, totalMembers = 0;
      var memberHit = {};
      tree.querySelectorAll('.ax-member').forEach(function (tr) {
        totalMembers++;
        var ok = true;
        if (roles.length && roles.indexOf(tr.dataset.role) < 0) ok = false;
        if (ok && q && tr.dataset.text.indexOf(q) < 0) {
          var li = tr.closest('.ax-node');
          if (li.dataset.name.indexOf(q) < 0) ok = false;
        }
        tr.hidden = !ok;
        if (ok) { shownMembers++; memberHit[tr.closest('.ax-node').dataset.space] = true; }
      });
      var visible = {};
      for (var i = nodes.length - 1; i >= 0; i--) {
        var li = nodes[i];
        var selfHit = !q || li.dataset.name.indexOf(q) >= 0;
        if (roles.length) selfHit = selfHit && (memberHit[li.dataset.space] || false);
        var childHit = false;
        li.querySelectorAll(':scope > .ax-children > ul > .ax-node').forEach(function (c) { if (visible[c.dataset.space]) childHit = true; });
        var show = selfHit || memberHit[li.dataset.space] || childHit;
        if (!q && !roles.length) show = true;
        visible[li.dataset.space] = show;
        li.hidden = !show;
        li.classList.toggle('ax-match', !!(q && li.dataset.name.indexOf(q) >= 0));
      }
      if (q || roles.length) {
        nodes.forEach(function (li) { if (visible[li.dataset.space]) li.setAttribute('aria-expanded', 'true'); });
      } else if (!subtree) {
        nodes.forEach(function (li) { li.setAttribute('aria-expanded', state.expanded[li.dataset.space] ? 'true' : 'false'); });
      }
      if (countEl) countEl.textContent = (q || roles.length) ? (shownMembers + ' de ' + totalMembers + ' membres') : (totalMembers + ' membres');
    }

    if (filterInput) {
      filterInput.value = state.filter || '';
      var t;
      filterInput.addEventListener('input', function () {
        clearTimeout(t);
        t = setTimeout(function () { state.filter = filterInput.value; save(); applyFilters(); }, 120);
      });
    }
    if (chips) {
      chips.querySelectorAll('.ax-chip').forEach(function (c) {
        c.setAttribute('aria-pressed', state.roles.indexOf(c.dataset.role) >= 0 ? 'true' : 'false');
        c.addEventListener('click', function () {
          var on = c.getAttribute('aria-pressed') !== 'true';
          c.setAttribute('aria-pressed', on ? 'true' : 'false');
          state.roles = Array.prototype.map.call(chips.querySelectorAll('.ax-chip[aria-pressed="true"]'), function (x) { return x.dataset.role; });
          save();
          applyFilters();
        });
      });
    }
    if (showMembers) {
      showMembers.checked = state.members;
      showMembers.addEventListener('change', function () { state.members = showMembers.checked; save(); applyFilters(); });
    }
    applyFilters();

    // ?espai=ID: desplega fins a l'espai, el marca i hi va
    var m = /[?&]espai=(\d+)/.exec(location.search);
    if (m) {
      var target = tree.querySelector('.ax-node[data-space="' + m[1] + '"]');
      if (target) {
        var p = target;
        while (p) { setExpanded(p, true); p = p.parentElement ? p.parentElement.closest('.ax-node') : null; }
        save();
        target.classList.add('ax-current');
        target.scrollIntoView({ block: 'start' });
        target.focus({ preventScroll: true });
      }
    }

    tree.addEventListener('keydown', function (ev) {
      if (ev.target.closest('input, select, textarea')) return;
      var li = ev.target.closest('.ax-node');
      if (!li) return;
      if (ev.key === 'ArrowRight') { setExpanded(li, true); save(); ev.preventDefault(); }
      else if (ev.key === 'ArrowLeft') { setExpanded(li, false); save(); ev.preventDefault(); }
    });
    nodes.forEach(function (li) { li.tabIndex = 0; });

    // ------------------------------------------------------------- edició en línia dels membres
    function startEdit(td) {
      var tr = td.closest('.ax-member');
      var field = td.dataset.field;
      var spaceId = tr.dataset.space, itemId = tr.dataset.item;
      var old = field === 'role' ? (tr.dataset.role === 'sense-paper' ? '' : tr.dataset.role)
        : field === 'target_subpath' ? tr.dataset.sub : tr.dataset.note;
      var original = td.innerHTML;
      td.classList.add('ax-editing');
      var ctrl;
      if (field === 'role') {
        ctrl = el('select');
        var o0 = el('option', '', '?'); o0.value = ''; ctrl.appendChild(o0);
        cfg.roles.forEach(function (r) { var o = el('option', '', (cfg.roleShort[r] || r) + ' · ' + r); o.value = r; ctrl.appendChild(o); });
        ctrl.value = old;
      } else {
        ctrl = el('input');
        ctrl.type = 'text';
        ctrl.value = old;
        ctrl.placeholder = field === 'target_subpath' ? 'subcarpeta, o «.» = arrel' : 'nota';
      }
      ctrl.className = 'ax-inline-ctrl';
      td.innerHTML = '';
      td.appendChild(ctrl);
      ctrl.focus();
      if (ctrl.select) ctrl.select();
      var done = false;
      function cancel() { if (done) return; done = true; td.innerHTML = original; td.classList.remove('ax-editing'); }
      function commit() {
        if (done) return;
        var v = ctrl.value;
        if (v === old) { cancel(); return; }
        done = true;
        var body = {};
        body[field] = v || null;
        actInline('PATCH', '/members/' + spaceId + '/' + itemId, body, function () {
          td.classList.remove('ax-editing');
          if (field === 'role') {
            var rk = v || 'sense-paper';
            tr.dataset.role = rk;
            td.innerHTML = '';
            var chip = el('span', 'ax-role ax-role-' + rk, cfg.roleShort[rk] || '?');
            td.appendChild(chip);
            refreshResolution(tr.closest('.ax-node'));
          } else if (field === 'target_subpath') {
            tr.dataset.sub = v;
            td.innerHTML = '';
            if (v === '.') td.appendChild(el('span', 'muted', '(arrel)')); else td.textContent = v;
          } else {
            tr.dataset.note = v;
            td.textContent = v;
            td.title = v ? v + ' — clic per veure-la sencera; amb «edició», doble clic per editar-la' : 'Amb «edició», doble clic per afegir una nota';
          }
          tr.dataset.text = (tr.dataset.label + ' ' + (tr.dataset.note || '')).toLowerCase();
          td.classList.add('ax-flash');
          applyFilters();
        }).then(function (d) { if (!d) cancel(); });
      }
      ctrl.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter') { ev.preventDefault(); commit(); }
        if (ev.key === 'Escape') { ev.preventDefault(); cancel(); }
      });
      if (field === 'role') ctrl.addEventListener('change', commit);
      ctrl.addEventListener('blur', function () { setTimeout(function () { if (!done) commit(); }, 0); });
    }

    /** Recalcula «n pendents»/✓ i els comptadors dels xips de l'espai i dels seus avantpassats. */
    function refreshResolution(li) {
      while (li) {
        var members = li.querySelectorAll('.ax-member');
        var noRole = 0, counts = {};
        members.forEach(function (tr) { if (tr.dataset.role === 'sense-paper') noRole++; counts[tr.dataset.role] = (counts[tr.dataset.role] || 0) + 1; });
        var missing = 0;
        var projects = li.dataset.kind === 'projecte' ? [li] : [];
        li.querySelectorAll('.ax-node[data-kind="projecte"]').forEach(function (p) { projects.push(p); });
        projects.forEach(function (p) { if (!p.querySelector('.ax-member[data-role="canonica"]')) missing++; });
        var pending = missing + noRole;
        var res = li.querySelector(':scope > .ax-row .ax-res');
        if (res) {
          res.className = 'ax-res ' + (pending ? 'ax-res-pending' : 'ax-res-ok');
          res.textContent = '';
          if (pending) {
            res.textContent = pending + (pending === 1 ? ' pendent' : ' pendents');
            res.title = (missing ? missing + (missing === 1 ? ' projecte' : ' projectes') + ' sense canònica' : '') + (missing && noRole ? ' · ' : '') + (noRole ? noRole + (noRole === 1 ? ' membre' : ' membres') + ' sense paper' : '');
          } else {
            res.appendChild(icon('i-check'));
            res.title = 'Resolt';
          }
        }
        var roles = li.querySelector(':scope > .ax-row .ax-roles');
        if (roles) {
          roles.textContent = '';
          ['canonica', 'copia', 'part', 'descartar', 'quarantena', 'tercers', 'sense-paper'].forEach(function (rk) {
            if (!counts[rk]) return;
            var chip = el('span', 'ax-role ax-role-' + rk, cfg.roleShort[rk] || '?');
            chip.appendChild(el('b', null, String(counts[rk])));
            roles.appendChild(chip);
          });
        }
        li = li.parentElement ? li.parentElement.closest('.ax-node') : null;
      }
    }

    // ------------------------------------------------------------- menú ⋯ de membres i d'espais
    function onMore(btn) {
      if (menuState.open && menuState.anchorBtn === btn) { closeMenu(); return; }
      var tr = btn.closest('.ax-member');
      var li = btn.closest('.ax-node');
      if (tr) {
        var drive = tr.dataset.drive, apiPath = tr.dataset.apipath;
        openMenu([
          { label: 'Mou a un altre espai…', icon: 'i-move', action: function () { onAction('move', btn); } },
          { label: 'Treu de l’espai', icon: 'i-trash', danger: true, action: function () { onAction('remove', btn); } },
          { sep: true },
          { label: 'Canvia el paper', icon: 'i-tag', action: function () { startEdit(tr.querySelector('[data-field="role"]')); } },
          { label: 'Canvia el destí', icon: 'i-pencil', action: function () { startEdit(tr.querySelector('[data-field="target_subpath"]')); } },
          { label: 'Edita la nota', icon: 'i-pencil', action: function () { startEdit(tr.querySelector('[data-field="note"]')); } },
          { sep: true },
          { label: 'Obre a l’explorador', icon: 'i-folder-open', href: (URLS.explorador || '/explorador') + '?' + qs({ drive: drive, path: apiPath }) },
          { label: 'Cobertura del disc ' + drive, icon: 'i-drive', href: (URLS.cobertura || '/arxiu/cobertura') + '?' + qs({ disc: drive }) },
        ], btn, tr.dataset.label);
        return;
      }
      if (!li) return;
      var kind = li.dataset.kind, sid = li.dataset.space;
      var items = [
        { label: 'Crea un espai fill…', icon: 'i-plus', action: function () { onAction('child', btn); } },
        { label: 'Reanomena…', icon: 'i-pencil', action: function () { onAction('rename', btn); } },
      ];
      if (kind === 'projecte' || kind === 'serie') items.push({ label: 'Estat: ' + (li.dataset.state || 'sense') + '…', icon: 'i-check', action: function () { onAction('state', btn); } });
      items.push({ label: 'Mou sota un altre espai…', icon: 'i-move', action: function () { onAction('movespace', btn); } });
      items.push({ label: 'Puja', icon: 'i-arrow-up', action: function () { onAction('up', btn); } });
      items.push({ label: 'Baixa', icon: 'i-arrow-down', action: function () { onAction('down', btn); } });
      items.push({ sep: true });
      items.push({ label: 'Mostra només aquest subarbre', icon: 'i-subtree', href: (URLS.arbre || '/arxiu') + '?arrel=' + sid });
      items.push({ label: 'Obre l’espai a l’explorador', icon: 'i-folder-open', href: (URLS.explorador || '/explorador') + '?space=' + sid });
      items.push({ sep: true });
      items.push({ label: 'Esborra l’espai…', icon: 'i-trash', danger: true, action: function () { onAction('delspace', btn); } });
      openMenu(items, btn, li.dataset.path);
    }

    // ------------------------------------------------------------- accions de membres i espais
    function onAction(a, btn) {
      var tr = btn.closest('.ax-member');
      var li = btn.closest('.ax-node');
      if (tr) {
        var spaceId = tr.dataset.space, itemId = tr.dataset.item;
        var label = tr.dataset.label || itemId;
        if (a === 'remove') {
          act('DELETE', '/members/' + spaceId + '/' + itemId);
        } else if (a === 'move') {
          var cell = tr.querySelector('.ax-path');
          inlineSpacePicker(cell, 'espai de destí…', function (s) {
            act('POST', '/members/' + spaceId + '/' + itemId + '/move', { to_space_id: s.id });
          });
        }
        return;
      }
      if (!li) return;
      var sid = li.dataset.space, kind = li.dataset.kind;
      var row = li.querySelector(':scope > .ax-row');
      if (a === 'up' || a === 'down') {
        act('POST', '/spaces/' + sid + '/reorder', { direction: a === 'up' ? -1 : 1 });
      } else if (a === 'delspace') {
        var desc = Array.prototype.map.call(li.querySelectorAll('.ax-node'), function (d) { return d.dataset.path; });
        var mems = Array.prototype.map.call(li.querySelectorAll('.ax-member'), function (m) { return m.dataset.label; });
        confirmDialog({
          title: 'Esborrar l’espai «' + li.dataset.path + '»?',
          intro: 'S’esborra aquest ' + kind + (desc.length ? ', els ' + desc.length + ' espais que en pengen' : '') + (mems.length ? ' i les ' + mems.length + ' pertinences de membres' : '') + '. Cap fitxer dels discs es toca.',
          scope: [['Espais', desc.length + 1], ['Membres', mems.length]],
          list: desc.concat(mems.map(function (m) { return 'membre: ' + m; })).slice(0, 40).concat(desc.length + mems.length > 40 ? ['…'] : []),
          okLabel: 'Esborra l’espai',
        }).then(function (ok) { if (ok) act('DELETE', '/spaces/' + sid); });
      } else if (a === 'movespace') {
        inlineSpacePicker(row, 'nou espai pare…', function (s) {
          act('PATCH', '/spaces/' + sid, { parent_id: s.id });
        });
      } else if (a === 'rename') {
        inlineText(row, li.dataset.target, 'nom de destí', function (v) {
          actInline('PATCH', '/spaces/' + sid, { target_name: v }, function () {
            var nameEl = row.querySelector('.ax-name');
            nameEl.textContent = v;
            li.dataset.target = v;
            var parentPath = li.dataset.path.lastIndexOf('\\') >= 0 ? li.dataset.path.slice(0, li.dataset.path.lastIndexOf('\\') + 1) : '';
            li.dataset.path = parentPath + v;
            li.dataset.name = li.dataset.path.toLowerCase();
            nameEl.title = li.dataset.path + ' — mostra només aquest subarbre';
            row.classList.add('ax-flash');
          });
        });
      } else if (a === 'state') {
        var sel = el('select', 'ax-inline-ctrl');
        [['', 'sense estat'], ['obert', 'obert'], ['en-tancament', 'en tancament'], ['tancat', 'tancat']].forEach(function (p) {
          var o = el('option', '', p[1]); o.value = p[0]; sel.appendChild(o);
        });
        sel.value = li.dataset.state || '';
        var wrap = el('span', 'ax-inline');
        wrap.appendChild(sel);
        row.appendChild(wrap);
        sel.focus();
        sel.addEventListener('change', function () {
          var v = sel.value;
          actInline('PATCH', '/spaces/' + sid, { state: v || null }, function () {
            wrap.remove();
            li.dataset.state = v;
            var st = row.querySelector('.ax-state');
            if (st) st.remove();
            if (v) {
              st = el('span', 'ax-state ax-state-' + v, v);
              row.querySelector('.ax-name').after(st);
            }
            row.classList.add('ax-flash');
          });
        });
        sel.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') wrap.remove(); });
        sel.addEventListener('blur', function () { setTimeout(function () { wrap.remove(); }, 150); });
      } else if (a === 'child') {
        childForm(li, sid, kind);
      }
    }

    function inlineText(anchor, value, placeholder, done) {
      var wrap = el('span', 'ax-inline');
      var input = el('input', 'ax-inline-ctrl');
      input.value = value || '';
      input.placeholder = placeholder;
      wrap.appendChild(input);
      anchor.appendChild(wrap);
      input.focus();
      input.select();
      input.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter') { ev.preventDefault(); if (input.value.trim() && input.value.trim() !== value) done(input.value.trim()); else wrap.remove(); }
        if (ev.key === 'Escape') wrap.remove();
      });
      input.addEventListener('blur', function () { setTimeout(function () { wrap.remove(); }, 150); });
    }

    function childForm(li, parentId, parentKind) {
      var existing = li.querySelector(':scope > .ax-childform');
      if (existing) { existing.remove(); return; }
      var kinds = cfg.kinds.filter(function (k) { return (cfg.parents[k] || []).indexOf(parentKind) >= 0; });
      if (!kinds.length) { msg('Un espai «' + parentKind + '» no pot tenir fills d’arxiu.', true); return; }
      var form = el('form', 'ax-childform');
      var sel = el('select', 'ax-inline-ctrl');
      kinds.forEach(function (k) { var o = el('option', '', k); o.value = k; sel.appendChild(o); });
      var name = el('input', 'ax-inline-ctrl');
      name.placeholder = kinds[0] === 'capa' ? 'RAW, PROJ, OUT o DOC' : kinds[0] === 'projecte' ? 'AAAA_descriptor' : 'nom de destí';
      name.required = true;
      var ok = el('button', 'btn btn-sm btn-primary', 'Crea');
      ok.type = 'submit';
      var cancel = el('button', 'btn btn-sm', 'Cancel·la');
      cancel.type = 'button';
      form.appendChild(el('span', 'muted', 'Nou fill: '));
      form.appendChild(sel); form.appendChild(name); form.appendChild(ok); form.appendChild(cancel);
      li.insertBefore(form, li.querySelector(':scope > .ax-children'));
      setExpanded(li, true); save();
      name.focus();
      cancel.addEventListener('click', function () { form.remove(); });
      form.addEventListener('submit', function (ev) {
        ev.preventDefault();
        act('POST', '/spaces', { parent_id: parseInt(parentId, 10), archive_kind: sel.value, target_name: name.value.trim() });
      });
    }
  }

  // ----------------------------------------------------------------- pendents
  var pendChips = document.getElementById('ax-pend-chips');
  if (pendChips) initPendents();

  function initPendents() {
    var PEND_KEY = 'arxiu.pend.v1';
    var prefs = loadStored(PEND_KEY, {});
    if (!prefs.groups) prefs.groups = [];
    if (!prefs.open) prefs.open = {};
    var groups = Array.prototype.slice.call(document.querySelectorAll('.ax-pend-group'));
    function applyGroups() {
      var on = prefs.groups;
      pendChips.classList.toggle('ax-pend-filtered', on.length > 0);
      pendChips.querySelectorAll('.ax-pend-chip').forEach(function (c) { c.setAttribute('aria-pressed', on.indexOf(c.dataset.group) >= 0 ? 'true' : 'false'); });
      groups.forEach(function (g) {
        g.hidden = on.length > 0 && on.indexOf(g.dataset.group) < 0;
        var collapsed = prefs.open[g.dataset.group] == null ? g.hasAttribute('data-collapsed') : !prefs.open[g.dataset.group];
        if (on.length === 1 && on[0] === g.dataset.group) collapsed = false;
        g.toggleAttribute('data-collapsed', collapsed);
        g.querySelector('.ax-pend-toggle').setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      });
    }
    pendChips.addEventListener('click', function (ev) {
      var c = ev.target.closest('.ax-pend-chip');
      if (!c) return;
      var k = c.dataset.group, i = prefs.groups.indexOf(k);
      if (i >= 0) prefs.groups.splice(i, 1); else prefs.groups.push(k);
      store(PEND_KEY, prefs);
      applyGroups();
    });
    groups.forEach(function (g) {
      g.querySelector('.ax-pend-toggle').addEventListener('click', function () {
        prefs.open[g.dataset.group] = g.hasAttribute('data-collapsed');
        store(PEND_KEY, prefs);
        applyGroups();
      });
    });
    applyGroups();
    document.addEventListener('click', function (ev) {
      var b = ev.target.closest('.ax-pend-act');
      if (!b) return;
      var tr = b.closest('.ax-pend-row');
      var spaceId = tr.dataset.space, itemId = tr.dataset.item;
      if (b.dataset.act === 'remove') {
        act('DELETE', '/members/' + spaceId + '/' + itemId);
      } else if (b.dataset.act === 'role') {
        if (tr.classList.contains('ax-editing')) return;
        tr.classList.add('ax-editing');
        var sel = el('select', 'ax-inline-ctrl');
        var o0 = el('option', '', 'tria el paper…'); o0.value = ''; sel.appendChild(o0);
        cfg.roles.forEach(function (r) { var o = el('option', '', (cfg.roleShort[r] || r) + ' · ' + r); o.value = r; sel.appendChild(o); });
        var wrap = el('span', 'ax-inline');
        wrap.appendChild(sel);
        b.parentElement.appendChild(wrap);
        sel.focus();
        sel.addEventListener('change', function () { if (sel.value) act('PATCH', '/members/' + spaceId + '/' + itemId, { role: sel.value }); });
        sel.addEventListener('keydown', function (ev2) { if (ev2.key === 'Escape') { wrap.remove(); tr.classList.remove('ax-editing'); } });
        sel.addEventListener('blur', function () { setTimeout(function () { wrap.remove(); tr.classList.remove('ax-editing'); }, 150); });
      }
    });
  }

  // ----------------------------------------------------------------- cobertura navegable
  var cov = document.getElementById('ax-coverage');
  var addbar = document.getElementById('ax-addbar');
  if (cov && addbar) initCoverage();

  function initCoverage() {
    var COV_KEY = 'arxiu.cov.v1';
    var prefs = loadStored(COV_KEY, {});
    if (prefs.hideSmall == null) prefs.hideSmall = true;
    var drive = addbar.dataset.drive;
    var filesUrl = cov.dataset.filesUrl;
    var tbody = cov.querySelector('tbody');
    var all = document.getElementById('ax-sel-all');
    var countEl = document.getElementById('ax-sel-count');
    var btn = document.getElementById('ax-add-btn');
    var hideSmall = document.getElementById('ax-cov-hide-small');
    var hiddenEl = document.getElementById('ax-cov-hidden');
    var lastBox = null;
    var SMALL = 1e9;

    function rows() { return Array.prototype.slice.call(tbody.querySelectorAll('tr.ax-cov-row')); }
    function boxes() { return Array.prototype.slice.call(tbody.querySelectorAll('input.ax-sel')); }
    function selected() {
      return boxes().filter(function (b) { return b.checked; }).map(function (b) { return b.closest('tr').dataset.path; });
    }
    function update() {
      var paths = selected();
      var n = paths.length + (addbar.dataset.extra ? 1 : 0);
      if (countEl) countEl.textContent = n ? (n + (n === 1 ? ' carpeta seleccionada' : ' carpetes seleccionades')) : 'Cap carpeta seleccionada';
      if (btn) btn.disabled = !n;
      boxes().forEach(function (b) { b.closest('tr').classList.toggle('ax-selected', b.checked); });
    }
    function rowByPath(path) {
      var all = rows();
      for (var i = 0; i < all.length; i++) if (all[i].dataset.path === path) return all[i];
      return null;
    }
    function applyThreshold() {
      var hidden = 0;
      rows().forEach(function (tr) {
        var small = prefs.hideSmall && parseInt(tr.dataset.bytes, 10) < SMALL && !tr.classList.contains('ax-selected');
        var parentHidden = false;
        var p = tr.dataset.parentRow != null ? rowByPath(tr.dataset.parentRow) : null;
        if (p && (p.hidden || p.getAttribute('aria-expanded') !== 'true')) parentHidden = true;
        tr.hidden = small || parentHidden;
        if (small && !parentHidden) hidden++;
      });
      if (hiddenEl) hiddenEl.textContent = hidden ? ('(' + hidden + ' de menys d’1 GB amagad' + (hidden === 1 ? 'a' : 'es') + ')') : '';
    }
    if (hideSmall) {
      hideSmall.checked = prefs.hideSmall;
      hideSmall.addEventListener('change', function () { prefs.hideSmall = hideSmall.checked; store(COV_KEY, prefs); applyThreshold(); });
    }

    tbody.addEventListener('click', function (ev) {
      var b = ev.target.closest('input.ax-sel');
      if (b) {
        var bs = boxes();
        var i = bs.indexOf(b), j = bs.indexOf(lastBox);
        if (ev.shiftKey && j >= 0) {
          var lo = Math.min(i, j), hi = Math.max(i, j);
          for (var k = lo; k <= hi; k++) if (!bs[k].closest('tr').hidden) bs[k].checked = b.checked;
        }
        lastBox = b;
        update();
        return;
      }
      var tg = ev.target.closest('.ax-cov-toggle');
      if (tg) { ev.preventDefault(); toggleRow(tg.closest('tr')); return; }
      var more = ev.target.closest('.ax-more');
      if (more) { ev.preventDefault(); rowMenu(more); }
    });

    // desplegament en línia: demana les files del nivell següent al servidor i les insereix
    function toggleRow(tr) {
      if (tr.getAttribute('aria-expanded') === 'true') {
        tr.setAttribute('aria-expanded', 'false');
        applyThreshold();
        return;
      }
      if (tr.dataset.loaded) { tr.setAttribute('aria-expanded', 'true'); applyThreshold(); return; }
      tr.classList.add('ax-cov-loading');
      var level = parseInt(tr.dataset.level, 10) + 1;
      fetch(filesUrl + '?' + qs({ disc: drive, carpeta: tr.dataset.path, nivell: level }))
        .then(function (r) { return r.text(); })
        .then(function (html) {
          var tpl = document.createElement('template');
          tpl.innerHTML = '<table><tbody>' + html + '</tbody></table>';
          var frag = document.createDocumentFragment();
          Array.prototype.forEach.call(tpl.content.querySelectorAll('tr'), function (r) { r.dataset.parentRow = tr.dataset.path; frag.appendChild(r); });
          tr.after(frag);
          tr.dataset.loaded = '1';
          tr.setAttribute('aria-expanded', 'true');
          tr.classList.remove('ax-cov-loading');
          applyThreshold();
        })
        .catch(function () { tr.classList.remove('ax-cov-loading'); msg('No s’ha pogut carregar el nivell.', true); });
    }

    function rowMenu(btn) {
      if (menuState.open && menuState.anchorBtn === btn) { closeMenu(); return; }
      var tr = btn.closest('tr');
      var path = tr.dataset.path, isFolder = tr.dataset.kind === 'folder';
      var items = [];
      var box = tr.querySelector('input.ax-sel');
      if (box) items.push({ label: 'Afegeix a l’arxiu…', icon: 'i-plus', action: function () {
        boxes().forEach(function (x) { x.checked = false; });
        box.checked = true; update(); document.getElementById('ax-add-space').focus();
      } });
      if (isFolder) {
        items.push({ label: 'Cobertura d’aquesta carpeta', icon: 'i-subtree', href: (URLS.cobertura || '/arxiu/cobertura') + '?' + qs({ disc: drive, carpeta: path }) });
        items.push({ label: 'Obre a l’explorador', icon: 'i-folder-open', href: (URLS.explorador || '/explorador') + '?' + qs({ drive: drive, path: path }) });
      } else {
        items.push({ label: 'Obre la carpeta a l’explorador', icon: 'i-folder-open', href: (URLS.explorador || '/explorador') + '?' + qs({ drive: drive, path: path }) });
      }
      var member = tr.querySelector('.ax-cov-member');
      if (member) items.push({ label: 'Ves a l’espai de l’arxiu', icon: 'i-tag', href: member.getAttribute('href') });
      openMenu(items, btn, drive + ' › ' + path.split('/').join(' › '));
    }

    if (all) {
      all.addEventListener('change', function () {
        boxes().forEach(function (b) { var tr = b.closest('tr'); if (tr.dataset.parentRow != null) return; if (!tr.hidden) b.checked = all.checked; });
        update();
      });
    }
    // ordenació de les files del nivell (plega el que estiga desplegat)
    cov.querySelectorAll('th.ax-sortable').forEach(function (th) {
      th.addEventListener('click', function () {
        var key = th.dataset.sort;
        var asc = th.classList.contains('ax-sort-active') ? !th.classList.contains('ax-sort-asc') : (key === 'name');
        cov.querySelectorAll('th.ax-sortable').forEach(function (x) { x.classList.remove('ax-sort-active', 'ax-sort-asc'); });
        th.classList.add('ax-sort-active');
        if (asc) th.classList.add('ax-sort-asc');
        rows().forEach(function (tr) { if (tr.dataset.parentRow != null) tr.remove(); else { tr.removeAttribute('data-loaded'); tr.setAttribute('aria-expanded', 'false'); } });
        var top = rows();
        top.sort(function (a, b) {
          var va, vb;
          if (key === 'name' || key === 'year') { va = a.dataset[key] || ''; vb = b.dataset[key] || ''; return asc ? va.localeCompare(vb) : vb.localeCompare(va); }
          va = parseFloat(a.dataset[key] || 0); vb = parseFloat(b.dataset[key] || 0);
          return asc ? va - vb : vb - va;
        });
        top.forEach(function (tr) { tbody.appendChild(tr); });
        applyThreshold();
      });
    });
    var addHere = document.getElementById('ax-add-here');
    if (addHere) {
      addHere.addEventListener('click', function () {
        var path = addHere.dataset.path || '';
        addbar.dataset.extra = path;
        boxes().forEach(function (x) { x.checked = false; });
        update();
        if (countEl) countEl.textContent = 'Aquesta carpeta: ' + path;
        document.getElementById('ax-add-space').focus();
      });
    }
    // ?afegir=ruta (enllaç des de l'explorador): preselecciona la carpeta
    var m = /[?&]afegir=([^&]+)/.exec(location.search);
    if (m) {
      var path = decodeURIComponent(m[1].replace(/\+/g, ' '));
      var found = boxes().filter(function (b) { return b.closest('tr').dataset.path === path; });
      if (found.length) { found[0].checked = true; }
      else {
        // carpeta més profunda: s'afegeix com a element ocult de la selecció
        addbar.dataset.extra = path;
      }
      update();
      if (!found.length && countEl) countEl.textContent = 'Carpeta: ' + path;
      document.getElementById('ax-add-space').focus();
    }
    update();
    applyThreshold();
    addbar.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var s = resolveSpace(document.getElementById('ax-add-space').value);
      if (!s) { msg('Tria un espai d’arxiu de la llista (nom o ruta).', true); return; }
      var paths = selected();
      if (addbar.dataset.extra) paths.push(addbar.dataset.extra);
      if (!paths.length) { msg('Cap carpeta seleccionada.', true); return; }
      act('POST', '/members', {
        space_id: s.id,
        items: paths.map(function (p) { return { kind: 'folder', drive_id: drive, path: p }; }),
        role: document.getElementById('ax-add-role').value || null,
        target_subpath: document.getElementById('ax-add-sub').value || null,
        note: document.getElementById('ax-add-note').value || null,
      });
    });
  }
})();

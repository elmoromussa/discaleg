/**
 * DiscScope — Explorador simulat (arbre + llistat) sobre l'API /api/explorer/*.
 *
 * Convencions seguides (Windows Explorer / Finder / patró Tree View de WAI-ARIA):
 *  - arbre lateral amb càrrega mandrosa, xebró per plegar/desplegar, nom per obrir;
 *  - l'arbre segueix sempre la carpeta que es mostra (es desplega i s'hi selecciona);
 *  - barra d'adreça amb segments clicables, Enrere/Endavant/Amunt, ruta copiable;
 *  - l'URL (?drive=…&path=…) reflecteix la carpeta: es pot compartir i «Enrere» del navegador funciona;
 *  - teclat: fletxes a l'arbre, Retorn per obrir, Alt+↑ o Retrocés per pujar;
 *  - llistat amb carpetes primer, ordenació per columna, filtre per nom i totals recursius.
 */
(function () {
  'use strict';

  var PAGE_SIZE = 300;
  var SIDEBAR_KEY = 'discscope-explorer-sidebar';
  var API = {
    roots: '/api/explorer/roots',
    children: '/api/explorer/tree/children',
    contents: '/api/explorer/contents',
    totals: '/api/explorer/folder-totals'
  };

  var $ = function (id) { return document.getElementById(id); };
  var root = $('explorer');
  if (!root) return;

  var els = {
    tree: $('explorer-tree'),
    treeLoading: $('explorer-tree-loading'),
    treeEmpty: $('explorer-tree-empty'),
    splitter: $('explorer-splitter'),
    back: $('explorer-back'),
    forward: $('explorer-forward'),
    up: $('explorer-up'),
    address: $('explorer-address'),
    copy: $('explorer-copy'),
    filter: $('explorer-filter-input'),
    list: $('explorer-list'),
    welcome: $('explorer-welcome'),
    emptyFolder: $('explorer-empty-folder'),
    noMatch: $('explorer-no-match'),
    error: $('explorer-error'),
    loading: $('explorer-loading'),
    table: $('explorer-table'),
    tbody: $('explorer-tbody'),
    status: $('explorer-status'),
    statusPages: $('explorer-status-pages'),
    loadMore: $('explorer-load-more'),
    toast: $('explorer-toast')
  };

  var searchUrl = root.getAttribute('data-search-url') || '/search';

  var state = {
    drives: {},            // drive_id -> fitxa
    drive: null,
    path: '',
    rows: [],              // elements carregats de la carpeta actual
    totals: null,          // resposta de /folder-totals per a la carpeta actual
    sort: { key: 'name', dir: 'asc' },
    filter: '',
    offset: 0,
    totalFiles: 0,
    directBytes: 0,
    hasMore: false,
    reqId: 0,              // per descartar respostes endarrerides
    histIdx: 0,
    histMax: 0
  };

  var nodes = {};          // clau drive\0path -> node de l'arbre

  // ------------------------------------------------------------------ utilitats

  var nf = new Intl.NumberFormat('ca-ES');
  var nf1 = new Intl.NumberFormat('ca-ES', { minimumFractionDigits: 1, maximumFractionDigits: 1 });

  function fmtInt(n) { return nf.format(Number(n) || 0); }

  function fmtBytes(bytes) {
    if (bytes == null || bytes === '') return '';
    var n = Number(bytes);
    if (Math.abs(n) < 1024) return fmtInt(n) + ' B';
    var units = ['KB', 'MB', 'GB', 'TB', 'PB'];
    var i = -1;
    do { n /= 1024; i++; } while (Math.abs(n) >= 1024 && i < units.length - 1);
    return nf1.format(n) + ' ' + units[i];
  }

  function fmtDate(iso) {
    if (!iso) return '';
    return String(iso).slice(0, 16).replace('T', ' ');
  }

  function plural(n, one, many) { return fmtInt(n) + ' ' + (n === 1 ? one : many); }

  function nodeKey(drive, path) { return drive + '\u0000' + (path || ''); }

  function parentPath(path) {
    var i = path.lastIndexOf('/');
    return i < 0 ? '' : path.slice(0, i);
  }

  function baseName(path) {
    var i = path.lastIndexOf('/');
    return i < 0 ? path : path.slice(i + 1);
  }

  function fetchJson(url) {
    return fetch(url, { headers: { Accept: 'application/json' } }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) throw new Error(data.error || data.detail || r.statusText);
        return data;
      });
    });
  }

  function qs(params) {
    return Object.keys(params).map(function (k) {
      return encodeURIComponent(k) + '=' + encodeURIComponent(params[k] == null ? '' : params[k]);
    }).join('&');
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  var SVG_NS = 'http://www.w3.org/2000/svg';
  function icon(id, cls) {
    var svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('class', 'icon' + (cls ? ' ' + cls : ''));
    svg.setAttribute('aria-hidden', 'true');
    var use = document.createElementNS(SVG_NS, 'use');
    use.setAttribute('href', '#' + id);
    svg.appendChild(use);
    return svg;
  }

  var EXT_KIND = (function () {
    var map = {};
    function add(kind, list) { list.split(' ').forEach(function (e) { map[e] = kind; }); }
    add('image', 'jpg jpeg png gif bmp tif tiff webp heic heif raw cr2 cr3 nef arw dng orf rw2 psd ai svg ico');
    add('video', 'mp4 mov avi mkv wmv m4v mpg mpeg 3gp mts m2ts webm flv vob ts');
    add('audio', 'mp3 wav flac aac ogg m4a wma aif aiff opus');
    add('doc', 'pdf doc docx xls xlsx ppt pptx odt ods odp txt rtf md csv epub pages numbers key');
    add('archive', 'zip rar 7z gz tgz tar bz2 xz iso dmg cab');
    add('code', 'py js ts jsx tsx html htm css scss json xml yml yaml sh bat ps1 c cpp h hpp java cs php rb go rs sql ipynb');
    return map;
  })();

  function cleanExt(ext) {
    if (!ext || ext === '(no_ext)') return '';
    return String(ext).replace(/^\./, '').toLowerCase();
  }

  function fileIconId(ext) {
    var kind = EXT_KIND[ext];
    return kind ? 'i-' + kind : 'i-file';
  }

  var toastTimer = null;
  function toast(msg) {
    els.toast.textContent = msg;
    els.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { els.toast.hidden = true; }, 2200);
  }

  // ------------------------------------------------------------------ arbre

  function makeNode(drive, path, opts) {
    var node = {
      drive: drive,
      path: path || '',
      isDrive: !path,
      level: opts.level,
      hasChildren: opts.hasChildren !== false,
      loaded: false,
      loading: null,
      li: null, row: null, toggle: null, iconEl: null, group: null
    };
    var li = el('li', 'tree-item' + (node.isDrive ? ' tree-drive' : ''));
    li.setAttribute('role', 'treeitem');
    li.setAttribute('aria-level', String(node.level));
    li.setAttribute('aria-selected', 'false');
    li.setAttribute('tabindex', '-1');
    li.dataset.drive = drive;
    li.dataset.path = node.path;

    var row = el('div', 'tree-row');
    var toggle = el('span', 'tree-toggle');
    toggle.appendChild(icon('i-chevron', 'tree-chevron'));
    var iconEl = el('span', 'tree-icon');
    iconEl.appendChild(icon(node.isDrive ? 'i-drive' : 'i-folder'));
    var label = el('span', 'tree-label', node.isDrive ? drive : opts.name);
    row.appendChild(toggle);
    row.appendChild(iconEl);
    row.appendChild(label);
    if (node.isDrive && opts.drive) {
      var d = opts.drive;
      if (d.label) row.appendChild(el('span', 'tree-sublabel', d.label));
      if (d.total_bytes) row.appendChild(el('span', 'tree-meta', fmtBytes(d.total_bytes)));
      var tip = [d.label, d.kind, d.status !== 'active' ? d.status : '',
        d.files_count ? plural(d.files_count, 'fitxer', 'fitxers') : '',
        d.last_scan_utc ? 'escanejat ' + fmtDate(d.last_scan_utc) : '',
        d.root_path ? 'arrel ' + d.root_path : ''].filter(Boolean).join(' · ');
      if (tip) row.title = tip;
    } else {
      row.title = opts.name;
    }
    li.appendChild(row);

    node.li = li; node.row = row; node.toggle = toggle; node.iconEl = iconEl;
    li._node = node;
    setHasChildren(node, node.hasChildren);
    nodes[nodeKey(drive, node.path)] = node;

    toggle.addEventListener('click', function (e) {
      e.stopPropagation();
      if (node.hasChildren) toggleNode(node);
    });
    row.addEventListener('click', function (e) {
      if (e.target.closest('.tree-toggle')) return;
      navigate(node.drive, node.path, { source: 'tree' });
      if (node.hasChildren) toggleNode(node, true);
    });
    return node;
  }

  function setHasChildren(node, has) {
    node.hasChildren = has;
    if (has) {
      node.li.setAttribute('aria-expanded', node.li.getAttribute('aria-expanded') || 'false');
      node.toggle.classList.remove('tree-toggle-leaf');
    } else {
      node.li.removeAttribute('aria-expanded');
      node.toggle.classList.add('tree-toggle-leaf');
    }
  }

  function setExpanded(node, expanded) {
    node.li.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    node.li.classList.toggle('tree-open', expanded);
    if (node.group) node.group.hidden = !expanded;
    if (!node.isDrive) {
      node.iconEl.textContent = '';
      node.iconEl.appendChild(icon(expanded ? 'i-folder-open' : 'i-folder'));
    }
  }

  function loadChildren(node) {
    if (node.loaded) return Promise.resolve();
    if (node.loading) return node.loading;
    node.li.classList.add('tree-loading');
    node.loading = fetchJson(API.children + '?' + qs({ drive_id: node.drive, path: node.path }))
      .then(function (data) {
        var folders = data.folders || [];
        var group = el('ul', 'tree-group');
        group.setAttribute('role', 'group');
        folders.forEach(function (f) {
          var child = makeNode(node.drive, f.path, {
            name: f.name, level: node.level + 1, hasChildren: !!f.has_children
          });
          group.appendChild(child.li);
        });
        node.group = group;
        node.li.appendChild(group);
        node.loaded = true;
        if (folders.length === 0) setHasChildren(node, false);
      })
      .catch(function (err) { toast('No s’ha pogut carregar la carpeta: ' + err.message); })
      .then(function () { node.li.classList.remove('tree-loading'); node.loading = null; });
    return node.loading;
  }

  function toggleNode(node, force) {
    var expanded = node.li.getAttribute('aria-expanded') === 'true';
    var want = typeof force === 'boolean' ? force : !expanded;
    if (want === expanded && node.loaded) return Promise.resolve();
    if (!want) { setExpanded(node, false); return Promise.resolve(); }
    return loadChildren(node).then(function () {
      if (node.hasChildren) setExpanded(node, true);
    });
  }

  function selectTreeNode(node) {
    var prev = els.tree.querySelector('[aria-selected="true"]');
    if (prev && prev !== node.li) { prev.setAttribute('aria-selected', 'false'); prev.setAttribute('tabindex', '-1'); }
    node.li.setAttribute('aria-selected', 'true');
    node.li.setAttribute('tabindex', '0');
    var keepFocus = els.tree.contains(document.activeElement);
    if (keepFocus) node.li.focus({ preventScroll: true });
    node.row.scrollIntoView({ block: 'nearest' });
  }

  /** Desplega els avantpassats de (drive, path) a l'arbre i selecciona el node (si existeix). */
  function revealPath(drive, path) {
    var driveNode = nodes[nodeKey(drive, '')];
    if (!driveNode) return Promise.resolve(null);
    var segments = path ? path.split('/') : [];
    var current = driveNode;
    var acc = '';
    var chain = Promise.resolve();
    segments.forEach(function (seg) {
      chain = chain.then(function () {
        if (!current) return;
        return toggleNode(current, true).then(function () {
          acc = acc ? acc + '/' + seg : seg;
          current = nodes[nodeKey(drive, acc)] || null;
        });
      });
    });
    return chain.then(function () {
      if (current) selectTreeNode(current);
      return current;
    });
  }

  function visibleTreeItems() {
    return Array.prototype.filter.call(els.tree.querySelectorAll('[role="treeitem"]'), function (li) {
      return !li.closest('[role="group"][hidden]');
    });
  }

  function focusItem(li) {
    if (!li) return;
    var prev = els.tree.querySelector('[role="treeitem"][tabindex="0"]');
    if (prev && prev !== li) prev.setAttribute('tabindex', '-1');
    li.setAttribute('tabindex', '0');
    li.focus();
    li._node.row.scrollIntoView({ block: 'nearest' });
  }

  els.tree.addEventListener('keydown', function (e) {
    var li = e.target.closest('[role="treeitem"]');
    if (!li || !li._node) return;
    var node = li._node;
    var items, idx;
    switch (e.key) {
      case 'ArrowDown':
      case 'ArrowUp':
        items = visibleTreeItems();
        idx = items.indexOf(li) + (e.key === 'ArrowDown' ? 1 : -1);
        if (idx >= 0 && idx < items.length) focusItem(items[idx]);
        break;
      case 'ArrowRight':
        if (!node.hasChildren) return;
        if (li.getAttribute('aria-expanded') !== 'true') {
          toggleNode(node, true);
        } else if (node.group && node.group.firstElementChild) {
          focusItem(node.group.firstElementChild);
        }
        break;
      case 'ArrowLeft':
        if (node.hasChildren && li.getAttribute('aria-expanded') === 'true') {
          toggleNode(node, false);
        } else {
          var parentLi = li.parentElement && li.parentElement.closest('[role="treeitem"]');
          if (parentLi) focusItem(parentLi);
        }
        break;
      case 'Home':
        items = visibleTreeItems();
        focusItem(items[0]);
        break;
      case 'End':
        items = visibleTreeItems();
        focusItem(items[items.length - 1]);
        break;
      case 'Enter':
      case ' ':
        navigate(node.drive, node.path, { source: 'tree' });
        if (node.hasChildren) toggleNode(node, true);
        break;
      default:
        return;
    }
    e.preventDefault();
  });

  // Si es fa clic en un element de l'arbre, passa a ser el focus del roving tabindex
  els.tree.addEventListener('mousedown', function (e) {
    var li = e.target.closest('[role="treeitem"]');
    if (li) focusItem(li);
  });

  // ------------------------------------------------------------------ navegació

  function buildUrl(drive, path) {
    var params = { drive: drive };
    if (path) params.path = path;
    return location.pathname + '?' + qs(params);
  }

  function updateNavButtons() {
    els.back.disabled = state.histIdx <= 0;
    els.forward.disabled = state.histIdx >= state.histMax;
    els.up.disabled = state.drive == null || !state.path;
  }

  function navigate(drive, path, opts) {
    opts = opts || {};
    path = path || '';
    var same = state.drive === drive && state.path === path;
    state.drive = drive;
    state.path = path;
    if (opts.push !== false && !same) {
      state.histIdx += 1;
      state.histMax = state.histIdx;
      history.pushState({ drive: drive, path: path, idx: state.histIdx }, '', buildUrl(drive, path));
    } else if (opts.replace) {
      history.replaceState({ drive: drive, path: path, idx: state.histIdx }, '', buildUrl(drive, path));
    }
    document.title = (path ? baseName(path) + ' — ' : '') + drive + ' — Explorador — DiscScope';
    updateNavButtons();
    renderAddress();
    loadContents();
    if (opts.source !== 'tree') revealPath(drive, path);
    else { var n = nodes[nodeKey(drive, path)]; if (n) selectTreeNode(n); }
  }

  window.addEventListener('popstate', function (e) {
    var st = e.state;
    if (!st || !st.drive) return;
    state.histIdx = st.idx || 0;
    navigate(st.drive, st.path || '', { push: false });
  });

  els.back.addEventListener('click', function () { history.back(); });
  els.forward.addEventListener('click', function () { history.forward(); });
  els.up.addEventListener('click', goUp);

  function goUp() {
    if (state.drive == null || !state.path) return;
    navigate(state.drive, parentPath(state.path));
  }

  document.addEventListener('keydown', function (e) {
    var inField = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;
    if (e.altKey && e.key === 'ArrowUp') { e.preventDefault(); goUp(); return; }
    if (e.key === 'Backspace' && !inField && !e.altKey && !e.ctrlKey && !e.metaKey) { e.preventDefault(); goUp(); }
  });

  // ------------------------------------------------------------------ barra d'adreça

  function renderAddress() {
    els.address.textContent = '';
    if (state.drive == null) {
      els.address.appendChild(el('span', 'explorer-address-placeholder', 'Trieu un disc o una carpeta'));
      els.copy.hidden = true;
      return;
    }
    var segments = state.path ? state.path.split('/') : [];
    var crumbs = [{ label: state.drive, path: '', isDrive: true }];
    var acc = '';
    segments.forEach(function (seg) {
      acc = acc ? acc + '/' + seg : seg;
      crumbs.push({ label: seg, path: acc });
    });
    crumbs.forEach(function (c, i) {
      var last = i === crumbs.length - 1;
      if (i > 0) {
        var sep = el('span', 'explorer-address-sep');
        sep.appendChild(icon('i-chevron'));
        els.address.appendChild(sep);
      }
      var item;
      if (last) {
        item = el('span', 'explorer-address-item explorer-address-current');
        item.setAttribute('aria-current', 'page');
      } else {
        item = el('a', 'explorer-address-item');
        item.href = buildUrl(state.drive, c.path);
        item.addEventListener('click', function (e) {
          if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
          e.preventDefault();
          navigate(state.drive, c.path);
        });
      }
      if (c.isDrive) item.appendChild(icon('i-drive'));
      item.appendChild(el('span', null, c.label));
      els.address.appendChild(item);
    });
    els.copy.hidden = false;
    els.address.scrollLeft = els.address.scrollWidth;
  }

  function nativePath() {
    var d = state.drives[state.drive] || {};
    var rel = state.path ? state.path.split('/').join('\\') : '';
    var rootPath = d.root_path || '';
    if (rootPath) {
      if (!/[\\\/]$/.test(rootPath)) rootPath += '\\';
      return rootPath + rel;
    }
    return state.drive + (rel ? '\\' + rel : '');
  }

  els.copy.addEventListener('click', function () {
    var text = nativePath();
    var done = function () { toast('Ruta copiada: ' + text); };
    var fail = function () { window.prompt('Copieu la ruta:', text); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, fail);
    } else {
      fail();
    }
  });

  // ------------------------------------------------------------------ contingut

  function setListView(view) {
    els.welcome.hidden = view !== 'welcome';
    els.emptyFolder.hidden = view !== 'empty';
    els.noMatch.hidden = view !== 'nomatch';
    els.error.hidden = view !== 'error';
    els.loading.hidden = view !== 'loading';
    els.table.hidden = view !== 'table';
  }

  function loadContents() {
    var reqId = ++state.reqId;
    var drive = state.drive, path = state.path;
    state.rows = [];
    state.totals = null;
    state.offset = 0;
    state.filter = '';
    els.filter.value = '';
    els.filter.disabled = false;
    els.tbody.textContent = '';
    els.loadMore.hidden = true;
    els.statusPages.textContent = '';
    els.status.textContent = 'Carregant…';
    setListView('loading');

    fetchJson(API.contents + '?' + qs({ drive_id: drive, path: path, limit: PAGE_SIZE, offset: 0 }))
      .then(function (data) {
        if (reqId !== state.reqId) return;
        state.rows = toRows(data);
        state.totalFiles = data.total_files || 0;
        state.directBytes = data.direct_bytes || 0;
        state.hasMore = !!data.has_more;
        applyTotalsToRows();  // els totals poden haver arribat abans que el contingut
        renderRows();
        renderStatus();
        els.list.scrollTop = 0;
      })
      .catch(function (err) {
        if (reqId !== state.reqId) return;
        els.error.textContent = 'No s’ha pogut carregar el contingut: ' + err.message;
        setListView('error');
        els.status.textContent = '';
      });

    fetchJson(API.totals + '?' + qs({ drive_id: drive, path: path }))
      .then(function (data) {
        if (reqId !== state.reqId) return;
        state.totals = data;
        applyTotalsToRows();
        if (!els.table.hidden) renderRows();
        renderStatus();
      })
      .catch(function () { /* els totals són complementaris */ });
  }

  function toRows(data) {
    var rows = [];
    (data.folders || []).forEach(function (f) {
      rows.push({
        kind: 'folder', name: f.name, path: f.path, ext: '',
        size: f.total_bytes || 0, items: f.files_count || 0, subfolders: null,
        date: '', hasTotals: false
      });
    });
    (data.files || []).forEach(function (f) {
      rows.push({
        kind: 'file', name: f.name, path: '', ext: cleanExt(f.extension),
        size: f.size_bytes == null ? null : Number(f.size_bytes), items: null,
        date: f.modified_utc || '', hasTotals: true
      });
    });
    return rows;
  }

  function applyTotalsToRows() {
    if (!state.totals) return;
    var children = state.totals.children || {};
    state.rows.forEach(function (r) {
      if (r.kind !== 'folder') return;
      var t = children[r.name];
      if (t) {
        r.size = t.bytes; r.items = t.files; r.subfolders = t.folders; r.hasTotals = true;
      } else {
        r.hasTotals = true; r.subfolders = 0;
      }
    });
  }

  var collator = new Intl.Collator('ca', { numeric: true, sensitivity: 'base' });

  function compareRows(a, b) {
    if (a.kind !== b.kind) return a.kind === 'folder' ? -1 : 1;
    var k = state.sort.key, dir = state.sort.dir === 'asc' ? 1 : -1;
    var r = 0;
    if (k === 'name') r = collator.compare(a.name, b.name);
    else if (k === 'type') r = collator.compare(a.ext, b.ext) || collator.compare(a.name, b.name);
    else if (k === 'size') r = ((a.size || 0) - (b.size || 0)) || collator.compare(a.name, b.name);
    else if (k === 'items') r = ((a.items || 0) - (b.items || 0)) || collator.compare(a.name, b.name);
    else if (k === 'date') r = (a.date < b.date ? -1 : a.date > b.date ? 1 : 0) || collator.compare(a.name, b.name);
    return r * dir;
  }

  function visibleRows() {
    var f = state.filter.trim().toLowerCase();
    var rows = f ? state.rows.filter(function (r) { return r.name.toLowerCase().indexOf(f) >= 0; }) : state.rows.slice();
    rows.sort(compareRows);
    return rows;
  }

  function renderRows() {
    var rows = visibleRows();
    els.tbody.textContent = '';
    if (state.rows.length === 0) { setListView('empty'); return; }
    if (rows.length === 0) { setListView('nomatch'); return; }
    var frag = document.createDocumentFragment();
    rows.forEach(function (r) { frag.appendChild(r.kind === 'folder' ? folderRow(r) : fileRow(r)); });
    els.tbody.appendChild(frag);
    setListView('table');
    updateSortHeaders();
    renderStatus();
  }

  function folderRow(r) {
    var tr = el('tr', 'row-folder');
    tr.dataset.path = r.path;
    var tdName = el('td', 'col-name');
    var a = el('a', 'explorer-link');
    a.href = buildUrl(state.drive, r.path);
    a.appendChild(icon('i-folder', 'row-icon'));
    a.appendChild(el('span', 'row-name', r.name));
    a.title = r.name;
    a.addEventListener('click', function (e) {
      if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
      e.preventDefault();
      navigate(state.drive, r.path);
    });
    tdName.appendChild(a);
    tr.appendChild(tdName);
    tr.appendChild(el('td', 'col-type', 'Carpeta'));
    var tdItems = el('td', 'col-items num');
    if (r.hasTotals) {
      tdItems.textContent = plural(r.items, 'fitxer', 'fitxers');
      if (r.subfolders) {
        tdItems.appendChild(el('span', 'muted', ' · ' + plural(r.subfolders, 'carpeta', 'carpetes')));
      }
    } else {
      tdItems.appendChild(el('span', 'muted pending', '…'));
    }
    tr.appendChild(tdItems);
    var tdSize = el('td', 'col-size num');
    if (r.hasTotals) tdSize.textContent = r.size ? fmtBytes(r.size) : '—';
    else tdSize.appendChild(el('span', 'muted pending', '…'));
    tr.appendChild(tdSize);
    tr.appendChild(el('td', 'col-date', ''));
    tr.addEventListener('dblclick', function () { navigate(state.drive, r.path); });
    return tr;
  }

  function fileRow(r) {
    var tr = el('tr', 'row-file');
    var tdName = el('td', 'col-name');
    var wrap = el('span', 'explorer-file');
    wrap.appendChild(icon(fileIconId(r.ext), 'row-icon'));
    wrap.appendChild(el('span', 'row-name', r.name));
    wrap.title = r.name;
    tdName.appendChild(wrap);
    var search = el('a', 'row-action');
    search.href = searchUrl + '?' + qs({ q_name: r.name, submitted: 1 });
    search.title = 'Cerca aquest nom a tots els discs';
    search.setAttribute('aria-label', 'Cerca «' + r.name + '» a tots els discs');
    search.appendChild(icon('i-search'));
    tdName.appendChild(search);
    tr.appendChild(tdName);
    tr.appendChild(el('td', 'col-type', r.ext ? r.ext.toUpperCase() : '—'));
    tr.appendChild(el('td', 'col-items num', ''));
    tr.appendChild(el('td', 'col-size num', r.size == null ? '' : fmtBytes(r.size)));
    tr.appendChild(el('td', 'col-date', fmtDate(r.date)));
    return tr;
  }

  function renderStatus() {
    if (state.drive == null) { els.status.textContent = ''; return; }
    var folders = state.rows.filter(function (r) { return r.kind === 'folder'; }).length;
    var loadedFiles = state.rows.length - folders;
    var parts = [];
    var f = state.filter.trim();
    if (f) {
      var shown = visibleRows().length;
      parts.push(fmtInt(shown) + ' de ' + plural(state.rows.length, 'element carregat', 'elements carregats') + ' coincideixen amb «' + f + '»');
    } else {
      parts.push(plural(folders, 'carpeta', 'carpetes') + ' · ' + plural(state.totalFiles, 'fitxer', 'fitxers') +
        (state.directBytes ? ' (' + fmtBytes(state.directBytes) + ')' : ''));
      if (state.totals && state.totals.subtree) {
        var s = state.totals.subtree;
        parts.push('Amb subcarpetes: ' + plural(s.files, 'fitxer', 'fitxers') + ' · ' + fmtBytes(s.bytes));
      }
    }
    els.status.textContent = parts.join('  │  ');
    if (state.totalFiles > loadedFiles) {
      els.statusPages.textContent = 'Mostrant ' + fmtInt(loadedFiles) + ' de ' + fmtInt(state.totalFiles) + ' fitxers';
    } else {
      els.statusPages.textContent = '';
    }
    els.loadMore.hidden = !state.hasMore;
  }

  els.loadMore.addEventListener('click', function () {
    var reqId = state.reqId;
    var nextOffset = state.offset + PAGE_SIZE;
    els.loadMore.disabled = true;
    fetchJson(API.contents + '?' + qs({ drive_id: state.drive, path: state.path, limit: PAGE_SIZE, offset: nextOffset }))
      .then(function (data) {
        if (reqId !== state.reqId) return;
        state.offset = nextOffset;
        var more = toRows({ files: data.files });
        state.rows = state.rows.concat(more);
        state.hasMore = !!data.has_more;
        if (data.total_files) state.totalFiles = data.total_files;
        renderRows();
      })
      .catch(function (err) { toast('No s’han pogut carregar més fitxers: ' + err.message); })
      .then(function () { els.loadMore.disabled = false; });
  });

  // ordenació
  function updateSortHeaders() {
    Array.prototype.forEach.call(els.table.querySelectorAll('th'), function (th) {
      var btn = th.querySelector('.explorer-sort');
      if (!btn) return;
      var active = btn.dataset.sort === state.sort.key;
      th.classList.toggle('sorted', active);
      th.classList.toggle('sorted-desc', active && state.sort.dir === 'desc');
      if (active) th.setAttribute('aria-sort', state.sort.dir === 'asc' ? 'ascending' : 'descending');
      else th.removeAttribute('aria-sort');
    });
  }

  els.table.addEventListener('click', function (e) {
    var btn = e.target.closest('.explorer-sort');
    if (!btn) return;
    var key = btn.dataset.sort;
    if (state.sort.key === key) state.sort.dir = state.sort.dir === 'asc' ? 'desc' : 'asc';
    else state.sort = { key: key, dir: key === 'size' || key === 'items' || key === 'date' ? 'desc' : 'asc' };
    renderRows();
  });

  // filtre
  var filterTimer = null;
  els.filter.addEventListener('input', function () {
    clearTimeout(filterTimer);
    filterTimer = setTimeout(function () {
      state.filter = els.filter.value;
      renderRows();
    }, 80);
  });
  els.filter.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { els.filter.value = ''; state.filter = ''; renderRows(); }
  });

  // ------------------------------------------------------------------ separador redimensionable

  (function initSplitter() {
    var MIN = 180, MAX = 560;
    function apply(w) {
      w = Math.max(MIN, Math.min(MAX, Math.round(w)));
      root.style.setProperty('--explorer-sidebar-width', w + 'px');
      return w;
    }
    try {
      var saved = parseInt(localStorage.getItem(SIDEBAR_KEY), 10);
      if (saved) apply(saved);
    } catch (e) { /* ignora */ }
    function save(w) { try { localStorage.setItem(SIDEBAR_KEY, String(w)); } catch (e) { /* ignora */ } }
    function currentWidth() {
      return root.querySelector('.explorer-sidebar').getBoundingClientRect().width;
    }
    els.splitter.addEventListener('pointerdown', function (e) {
      if (e.button !== 0) return;
      e.preventDefault();
      var startX = e.clientX, startW = currentWidth(), w = startW;
      els.splitter.setPointerCapture(e.pointerId);
      root.classList.add('explorer-resizing');
      function move(ev) { w = apply(startW + ev.clientX - startX); }
      function stop() {
        els.splitter.removeEventListener('pointermove', move);
        els.splitter.removeEventListener('pointerup', stop);
        els.splitter.removeEventListener('pointercancel', stop);
        root.classList.remove('explorer-resizing');
        save(w);
      }
      els.splitter.addEventListener('pointermove', move);
      els.splitter.addEventListener('pointerup', stop);
      els.splitter.addEventListener('pointercancel', stop);
    });
    els.splitter.addEventListener('dblclick', function () {
      root.style.removeProperty('--explorer-sidebar-width');
      try { localStorage.removeItem(SIDEBAR_KEY); } catch (e) { /* ignora */ }
    });
    els.splitter.addEventListener('keydown', function (e) {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      save(apply(currentWidth() + (e.key === 'ArrowLeft' ? -16 : 16)));
    });
  })();

  // ------------------------------------------------------------------ arrencada

  function init() {
    fetchJson(API.roots)
      .then(function (data) {
        els.treeLoading.hidden = true;
        var drives = data.drives || [];
        if (drives.length === 0) { els.treeEmpty.hidden = false; return; }
        var frag = document.createDocumentFragment();
        drives.forEach(function (d) {
          state.drives[d.drive_id] = d;
          var node = makeNode(d.drive_id, '', { level: 1, hasChildren: true, drive: d });
          frag.appendChild(node.li);
        });
        els.tree.appendChild(frag);
        els.tree.hidden = false;
        els.tree.firstElementChild.setAttribute('tabindex', '0');

        var params = new URLSearchParams(location.search);
        var drive = params.get('drive');
        var path = (params.get('path') || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
        if (drive && state.drives[drive]) {
          history.replaceState({ drive: drive, path: path, idx: 0 }, '', buildUrl(drive, path));
          navigate(drive, path, { push: false });
        } else if (drive) {
          toast('El disc «' + drive + '» no és a la base de dades.');
        }
      })
      .catch(function (err) {
        els.treeLoading.hidden = true;
        els.treeEmpty.hidden = false;
        els.treeEmpty.textContent = 'No s’han pogut carregar els discs: ' + err.message;
      });
  }

  init();
})();

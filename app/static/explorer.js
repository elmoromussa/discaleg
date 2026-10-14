/**
 * Discàleg — Explorador simulat (arbre + llistat) sobre l'API /api/explorer/* i /api/spaces/*.
 *
 * Convencions seguides (Windows Explorer / Finder / patró Tree View de WAI-ARIA):
 *  - arbre lateral amb càrrega mandrosa, xebró per plegar/desplegar, nom per obrir;
 *  - l'arbre segueix sempre la carpeta que es mostra (es desplega i s'hi selecciona);
 *  - barra d'adreça amb segments clicables, Enrere/Endavant/Amunt, ruta copiable;
 *  - l'URL (?drive=…&path=… o ?space=…) reflecteix el que es mostra: es pot compartir i
 *    «Enrere» del navegador funciona;
 *  - teclat: fletxes a l'arbre i al llistat, Retorn per obrir, Alt+↑ o Retrocés per pujar,
 *    Maj+F10 o clic dret per al menú d'accions;
 *  - llistat amb carpetes primer, ordenació per columna, filtre per nom i totals recursius.
 *
 * Dos modes: «folder» (una carpeta d'un disc) i «space» (un espai virtual: cerca desada o
 * col·lecció, amb fitxers de qualsevol disc; columnes Disc i Carpeta, ordenació al servidor).
 */
(function () {
  'use strict';

  var PAGE_SIZE = 300;
  var SIDEBAR_KEY = 'discaleg-explorer-sidebar';
  var API = {
    roots: '/api/explorer/roots',
    children: '/api/explorer/tree/children',
    contents: '/api/explorer/contents',
    totals: '/api/explorer/folder-totals',
    spacesTree: '/api/spaces/tree',
    spaces: '/api/spaces',
    tags: '/api/tags',
    items: '/api/items'
  };

  var $ = function (id) { return document.getElementById(id); };
  var root = $('explorer');
  if (!root) return;

  var els = {
    tree: $('explorer-tree'),
    treeLoading: $('explorer-tree-loading'),
    treeEmpty: $('explorer-tree-empty'),
    spaces: $('explorer-spaces'),
    spacesEmpty: $('explorer-spaces-empty'),
    spaceNew: $('explorer-space-new'),
    spaceActions: $('explorer-space-actions'),
    spaceEdit: $('explorer-space-edit'),
    spaceRename: $('explorer-space-rename'),
    spaceMove: $('explorer-space-move'),
    spaceDelete: $('explorer-space-delete'),
    splitter: $('explorer-splitter'),
    back: $('explorer-back'),
    forward: $('explorer-forward'),
    up: $('explorer-up'),
    address: $('explorer-address'),
    copy: $('explorer-copy'),
    actions: $('explorer-actions'),
    filter: $('explorer-filter-input'),
    list: $('explorer-list'),
    welcome: $('explorer-welcome'),
    emptyFolder: $('explorer-empty-folder'),
    emptySpace: $('explorer-empty-space'),
    emptySpaceText: $('explorer-empty-space-text'),
    emptySpaceHint: $('explorer-empty-space-hint'),
    noMatch: $('explorer-no-match'),
    error: $('explorer-error'),
    loading: $('explorer-loading'),
    table: $('explorer-table'),
    tbody: $('explorer-tbody'),
    status: $('explorer-status'),
    statusPages: $('explorer-status-pages'),
    loadMore: $('explorer-load-more'),
    toast: $('explorer-toast'),
    menu: $('explorer-menu'),
    dialog: $('explorer-add-dialog'),
    dialogForm: $('explorer-add-form'),
    dialogSub: $('explorer-add-sub'),
    dialogSpaces: $('explorer-add-spaces'),
    dialogTags: $('explorer-add-tags'),
    dialogNewSpace: $('explorer-add-new-space'),
    dialogNewTag: $('explorer-add-new-tag'),
    dialogCancel: $('explorer-add-cancel')
  };

  var searchUrl = root.getAttribute('data-search-url') || '/search';
  var duplicatesUrl = root.getAttribute('data-duplicates-url') || '/duplicates';
  var exportUrl = root.getAttribute('data-export-url') || '/api/explorer/export.csv';
  var copiesUrl = root.getAttribute('data-copies-url') || '/copies';

  var state = {
    mode: 'folder',        // 'folder' | 'space' | 'tag'
    drives: {},            // drive_id -> fitxa
    drive: null,
    path: '',
    space: null,           // espai obert (mode space)
    spaceAncestors: [],
    spacesById: {},        // space_id -> espai (de l'arbre)
    spacesTree: [],
    tags: [],              // etiquetes (de /api/tags)
    tag: null,             // etiqueta oberta (mode tag)
    anchorRow: null,       // fila d'ancoratge per a Maj+clic
    rows: [],              // elements carregats
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

  var nodes = {};          // clau drive\0path -> node de l'arbre de discs
  var spaceNodes = {};     // space_id -> node de l'arbre d'espais
  var tagNodes = {};       // tag_id -> node de l'arbre d'espais (sota «Etiquetes»)
  var tagsRootNode = null;

  // ------------------------------------------------------------------ utilitats

  var nf = new Intl.NumberFormat('ca-ES');
  var nf1 = new Intl.NumberFormat('ca-ES', { minimumFractionDigits: 1, maximumFractionDigits: 1 });

  function fmtInt(n) { return nf.format(Number(n) || 0); }

  function fmtBytes(bytes) {
    if (bytes == null || bytes === '') return '';
    var n = Number(bytes);
    if (Math.abs(n) < 1000) return fmtInt(n) + ' B';
    var units = ['kB', 'MB', 'GB', 'TB', 'PB'];  // unitats decimals, com els discs
    var i = -1;
    do { n /= 1000; i++; } while (Math.abs(n) >= 1000 && i < units.length - 1);
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

  function joinPath(parent, name) { return parent ? parent + '/' + name : name; }

  function fetchJson(url, opts) {
    var init = { headers: { Accept: 'application/json' } };
    if (opts && opts.method) init.method = opts.method;
    if (opts && opts.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.body);
    }
    return fetch(url, init).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) throw new Error(data.error || data.detail || r.statusText);
        return data;
      });
    });
  }

  /** Cadena de consulta; els valors en forma de llista es repeteixen (drive_ids=A&drive_ids=B). */
  function qs(params) {
    var parts = [];
    Object.keys(params).forEach(function (k) {
      var v = params[k];
      if (v == null) v = '';
      (Array.isArray(v) ? v : [v]).forEach(function (x) {
        parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(x == null ? '' : x));
      });
    });
    return parts.join('&');
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

  function spaceIconId(space) { return space.kind === 'smart' ? 'i-space-smart' : 'i-space'; }

  var toastTimer = null;
  function toast(msg) {
    els.toast.textContent = msg;
    els.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { els.toast.hidden = true; }, 2200);
  }

  // ------------------------------------------------------------------ arbres (discs i espais)

  /** Node de l'arbre de discs (disc o carpeta). */
  function makeNode(drive, path, opts) {
    var node = {
      kind: 'folder',
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
      var tip = [d.label, d.kind_label || d.kind, d.location ? 'a ' + d.location : '',
        d.status !== 'active' ? d.status : '',
        d.files_count ? plural(d.files_count, 'fitxer', 'fitxers') : '',
        d.last_scan_utc ? 'escanejat ' + fmtDate(d.last_scan_utc) : '',
        d.root_path ? 'arrel ' + d.root_path : ''].filter(Boolean).join(' · ');
      if (d.notes) tip += '\n' + d.notes;
      if (tip) row.title = tip;
    } else {
      row.title = opts.name;
    }
    li.appendChild(row);

    node.li = li; node.row = row; node.toggle = toggle; node.iconEl = iconEl;
    li._node = node;
    setHasChildren(node, node.hasChildren);
    nodes[nodeKey(drive, node.path)] = node;
    wireNodeEvents(node);
    return node;
  }

  /** Node de l'arbre d'espais; els fills es construeixen de cop (l'arbre és petit). */
  function makeSpaceNode(space, level) {
    var node = {
      kind: 'space',
      space: space,
      level: level,
      hasChildren: space.children.length > 0,
      loaded: true,
      loading: null,
      li: null, row: null, toggle: null, iconEl: null, group: null
    };
    var li = el('li', 'tree-item tree-space');
    li.setAttribute('role', 'treeitem');
    li.setAttribute('aria-level', String(level));
    li.setAttribute('aria-selected', 'false');
    li.setAttribute('tabindex', '-1');
    li.dataset.space = String(space.space_id);
    var row = el('div', 'tree-row');
    var toggle = el('span', 'tree-toggle');
    toggle.appendChild(icon('i-chevron', 'tree-chevron'));
    var iconEl = el('span', 'tree-icon');
    iconEl.appendChild(icon(spaceIconId(space)));
    row.appendChild(toggle);
    row.appendChild(iconEl);
    row.appendChild(el('span', 'tree-label', space.name));
    row.title = space.name + (space.kind === 'smart' ? ' · cerca desada: ' + describeQuery(space.query) : ' · col·lecció');
    li.appendChild(row);
    node.li = li; node.row = row; node.toggle = toggle; node.iconEl = iconEl;
    li._node = node;
    setHasChildren(node, node.hasChildren);
    if (node.hasChildren) {
      var group = el('ul', 'tree-group');
      group.setAttribute('role', 'group');
      group.hidden = true;
      space.children.forEach(function (child) { group.appendChild(makeSpaceNode(child, level + 1).li); });
      node.group = group;
      li.appendChild(group);
    }
    spaceNodes[space.space_id] = node;
    wireNodeEvents(node);
    return node;
  }

  function tagDot(tag) {
    var dot = el('span', 'tag-dot');
    dot.style.background = tag.color || 'transparent';
    return dot;
  }

  /** Node arrel «Etiquetes» i un node per etiqueta (sense fills). */
  function makeTagsRootNode(tags) {
    var node = { kind: 'tags-root', level: 1, hasChildren: tags.length > 0, loaded: true, loading: null,
      li: null, row: null, toggle: null, iconEl: null, group: null };
    var li = el('li', 'tree-item tree-tags-root');
    li.setAttribute('role', 'treeitem');
    li.setAttribute('aria-level', '1');
    li.setAttribute('aria-selected', 'false');
    li.setAttribute('tabindex', '-1');
    var row = el('div', 'tree-row');
    var toggle = el('span', 'tree-toggle');
    toggle.appendChild(icon('i-chevron', 'tree-chevron'));
    var iconEl = el('span', 'tree-icon');
    iconEl.appendChild(icon('i-tag'));
    row.appendChild(toggle);
    row.appendChild(iconEl);
    row.appendChild(el('span', 'tree-label', 'Etiquetes'));
    row.appendChild(el('span', 'tree-meta', tags.length ? String(tags.length) : ''));
    row.title = tags.length ? plural(tags.length, 'etiqueta', 'etiquetes') : 'Cap etiqueta encara: creeu-ne des de «Afegeix a…»';
    li.appendChild(row);
    node.li = li; node.row = row; node.toggle = toggle; node.iconEl = iconEl;
    li._node = node;
    setHasChildren(node, node.hasChildren);
    if (tags.length) {
      var group = el('ul', 'tree-group');
      group.setAttribute('role', 'group');
      group.hidden = true;
      tags.forEach(function (t) { group.appendChild(makeTagNode(t).li); });
      node.group = group;
      li.appendChild(group);
    }
    wireNodeEvents(node);
    return node;
  }

  function makeTagNode(tag) {
    var node = { kind: 'tag', tag: tag, level: 2, hasChildren: false, loaded: true, loading: null,
      li: null, row: null, toggle: null, iconEl: null, group: null };
    var li = el('li', 'tree-item tree-tag');
    li.setAttribute('role', 'treeitem');
    li.setAttribute('aria-level', '2');
    li.setAttribute('aria-selected', 'false');
    li.setAttribute('tabindex', '-1');
    li.dataset.tag = String(tag.tag_id);
    var row = el('div', 'tree-row');
    var toggle = el('span', 'tree-toggle');
    toggle.appendChild(icon('i-chevron', 'tree-chevron'));
    var iconEl = el('span', 'tree-icon');
    iconEl.appendChild(tagDot(tag));
    row.appendChild(toggle);
    row.appendChild(iconEl);
    row.appendChild(el('span', 'tree-label', tag.name));
    row.appendChild(el('span', 'tree-meta', tag.items_count ? String(tag.items_count) : ''));
    row.title = tag.name + ' · ' + plural(tag.items_count || 0, 'element', 'elements');
    li.appendChild(row);
    node.li = li; node.row = row; node.toggle = toggle; node.iconEl = iconEl;
    li._node = node;
    setHasChildren(node, false);
    tagNodes[tag.tag_id] = node;
    wireNodeEvents(node);
    return node;
  }

  function wireNodeEvents(node) {
    node.toggle.addEventListener('click', function (e) {
      e.stopPropagation();
      if (node.hasChildren) toggleNode(node);
    });
    node.row.addEventListener('click', function (e) {
      if (e.target.closest('.tree-toggle')) return;
      activateNode(node);
    });
  }

  /** Obrir el que representa un node (carpeta o espai) i desplegar-lo. */
  function activateNode(node) {
    if (node.kind === 'space') openSpace(node.space.space_id, { source: 'tree' });
    else if (node.kind === 'tag') openTag(node.tag.tag_id, { source: 'tree' });
    else if (node.kind === 'tags-root') { toggleNode(node); return; }
    else navigate(node.drive, node.path, { source: 'tree' });
    if (node.hasChildren) toggleNode(node, true);
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
    if (node.kind === 'folder' && !node.isDrive) {
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

  function clearTreeSelection() {
    [els.tree, els.spaces].forEach(function (tree) {
      Array.prototype.forEach.call(tree.querySelectorAll('[aria-selected="true"]'), function (li) {
        li.setAttribute('aria-selected', 'false');
        li.setAttribute('tabindex', '-1');
      });
    });
  }

  function selectTreeNode(node) {
    clearTreeSelection();
    node.li.setAttribute('aria-selected', 'true');
    node.li.setAttribute('tabindex', '0');
    var tree = node.li.closest('[role="tree"]');
    var keepFocus = tree && tree.contains(document.activeElement);
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

  /** Desplega els avantpassats d'un espai i el selecciona. */
  function revealSpace(spaceId) {
    var node = spaceNodes[spaceId];
    if (!node) return;
    var li = node.li.parentElement && node.li.parentElement.closest('[role="treeitem"]');
    var parents = [];
    while (li) { parents.unshift(li._node); li = li.parentElement && li.parentElement.closest('[role="treeitem"]'); }
    parents.forEach(function (p) { setExpanded(p, true); });
    selectTreeNode(node);
  }

  function visibleTreeItems(tree) {
    return Array.prototype.filter.call(tree.querySelectorAll('[role="treeitem"]'), function (li) {
      return !li.closest('[role="group"][hidden]');
    });
  }

  function focusItem(li) {
    if (!li) return;
    var tree = li.closest('[role="tree"]');
    var prev = tree.querySelector('[role="treeitem"][tabindex="0"]');
    if (prev && prev !== li) prev.setAttribute('tabindex', '-1');
    li.setAttribute('tabindex', '0');
    li.focus();
    li._node.row.scrollIntoView({ block: 'nearest' });
  }

  function treeKeydown(e) {
    var tree = e.currentTarget;
    var li = e.target.closest('[role="treeitem"]');
    if (!li || !li._node) return;
    var node = li._node;
    var items, idx;
    switch (e.key) {
      case 'ArrowDown':
      case 'ArrowUp':
        items = visibleTreeItems(tree);
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
        items = visibleTreeItems(tree);
        focusItem(items[0]);
        break;
      case 'End':
        items = visibleTreeItems(tree);
        focusItem(items[items.length - 1]);
        break;
      case 'Enter':
      case ' ':
        activateNode(node);
        break;
      default:
        return;
    }
    e.preventDefault();
  }

  function treeMousedown(e) {
    var li = e.target.closest('[role="treeitem"]');
    if (li) focusItem(li);
  }

  [els.tree, els.spaces].forEach(function (tree) {
    tree.addEventListener('keydown', treeKeydown);
    tree.addEventListener('mousedown', treeMousedown);
  });

  // ------------------------------------------------------------------ espais: arbre i consultes

  function indexSpaces(tree) {
    state.spacesTree = tree;
    state.spacesById = {};
    (function walk(list, ancestors) {
      list.forEach(function (s) {
        s.ancestors = ancestors;
        state.spacesById[s.space_id] = s;
        walk(s.children || [], ancestors.concat([s]));
      });
    })(tree, []);
  }

  function renderSpacesTree() {
    var expanded = {};
    Object.keys(spaceNodes).forEach(function (id) {
      if (spaceNodes[id].li.getAttribute('aria-expanded') === 'true') expanded[id] = true;
    });
    var tagsOpen = tagsRootNode && tagsRootNode.li.getAttribute('aria-expanded') === 'true';
    spaceNodes = {};
    tagNodes = {};
    els.spaces.textContent = '';
    var frag = document.createDocumentFragment();
    state.spacesTree.forEach(function (s) { frag.appendChild(makeSpaceNode(s, 1).li); });
    tagsRootNode = makeTagsRootNode(state.tags);
    frag.appendChild(tagsRootNode.li);
    els.spaces.appendChild(frag);
    Object.keys(expanded).forEach(function (id) { if (spaceNodes[id]) setExpanded(spaceNodes[id], true); });
    if (tagsOpen && tagsRootNode.hasChildren) setExpanded(tagsRootNode, true);
    var any = state.spacesTree.length > 0 || state.tags.length > 0;
    els.spaces.hidden = false;
    els.spacesEmpty.hidden = any;
    if (!els.spaces.querySelector('[tabindex="0"]')) els.spaces.firstElementChild.setAttribute('tabindex', '0');
    if (state.mode === 'space' && state.space && spaceNodes[state.space.space_id]) revealSpace(state.space.space_id);
    if (state.mode === 'tag' && state.tag && tagNodes[state.tag.tag_id]) revealTag(state.tag.tag_id);
  }

  function revealTag(tagId) {
    var node = tagNodes[tagId];
    if (!node) return;
    setExpanded(tagsRootNode, true);
    selectTreeNode(node);
  }

  function loadSpacesTree() {
    return Promise.all([fetchJson(API.spacesTree), fetchJson(API.tags)]).then(function (res) {
      indexSpaces(res[0].spaces || []);
      state.tags = res[1].tags || [];
      if (state.tag) state.tag = state.tags.filter(function (t) { return t.tag_id === state.tag.tag_id; })[0] || state.tag;
      renderSpacesTree();
    }).catch(function (err) {
      els.spacesEmpty.hidden = false;
      els.spacesEmpty.textContent = 'No s’han pogut carregar els espais: ' + err.message;
    });
  }

  /** Resum llegible dels criteris d'una cerca desada. */
  function describeQuery(q) {
    if (!q) return '';
    var parts = [];
    if (q.q_name) parts.push('nom «' + q.q_name + '»');
    if (q.q_path) parts.push('ruta «' + q.q_path + '»');
    if (!q.q_name && !q.q_path && q.q) {
      parts.push((q.scope === 'path' ? 'ruta' : q.scope === 'both' ? 'nom o ruta' : 'nom') + ' «' + q.q + '»');
    }
    if (q.in_drive) parts.push('dins de ' + q.in_drive + (q.in_path ? ' › ' + q.in_path.split('/').join(' › ') : ''));
    else if (q.drive_ids && q.drive_ids.length) parts.push('discs ' + q.drive_ids.join(', '));
    if (q.extension) parts.push('extensió ' + q.extension);
    if (q.min_size_mb) parts.push('≥ ' + q.min_size_mb + ' MB');
    if (q.exclude) parts.push('excloent «' + q.exclude + '»');
    if (q.tag_id) {
      var tg = state.tags.filter(function (t) { return t.tag_id === q.tag_id; })[0];
      parts.push('etiqueta «' + (tg ? tg.name : q.tag_id) + '»');
    }
    if (q.only_single_copy) parts.push('només còpia única');
    if (q.only_duplicates) parts.push('només duplicats');
    if (q.include_missing) parts.push('amb desapareguts');
    return parts.join(' · ') || 'tots els fitxers';
  }

  /** Paràmetres de la pàgina Cercar equivalents a una cerca desada. */
  function queryToParams(q) {
    var p = {};
    ['q_name', 'q_path', 'q', 'scope', 'extension', 'min_size_mb', 'exclude', 'in_drive', 'in_path'].forEach(function (k) {
      if (q[k]) p[k] = q[k];
    });
    if (q.drive_ids && q.drive_ids.length) p.drive_ids = q.drive_ids;
    if (q.include_missing) p.include_missing = 1;
    if (q.tag_id) p.tag_id = q.tag_id;
    if (q.only_single_copy) p.only_single_copy = 1;
    if (q.only_duplicates) p.only_duplicates = 1;
    if (q.sort) { p.sort_f = q.sort; p.dir_f = q.dir || 'asc'; }
    p.search_type = 'files';
    p.submitted = 1;
    return p;
  }

  // ------------------------------------------------------------------ navegació

  function buildUrl(drive, path) {
    var params = { drive: drive };
    if (path) params.path = path;
    return location.pathname + '?' + qs(params);
  }

  function buildSpaceUrl(spaceId) {
    return location.pathname + '?' + qs({ space: spaceId });
  }

  function buildTagUrl(tagId) {
    return location.pathname + '?' + qs({ tag: tagId });
  }

  /** True si el que es mostra és una col·lecció manual o una etiqueta (llista resolta amb motiu). */
  function isCollectionView() {
    return state.mode === 'tag' || (state.mode === 'space' && state.space && state.space.kind === 'manual');
  }

  function pushHistory(stateObj, url, opts) {
    if (opts.push !== false) {
      state.histIdx += 1;
      state.histMax = state.histIdx;
      stateObj.idx = state.histIdx;
      history.pushState(stateObj, '', url);
    } else if (opts.replace) {
      stateObj.idx = state.histIdx;
      history.replaceState(stateObj, '', url);
    }
  }

  function updateNavButtons() {
    els.back.disabled = state.histIdx <= 0;
    els.forward.disabled = state.histIdx >= state.histMax;
    if (state.mode === 'space') {
      els.up.disabled = !(state.space && state.space.parent_id != null);
      els.actions.hidden = true;
      els.copy.hidden = true;
      els.spaceActions.hidden = false;
      els.spaceMove.hidden = false;
      els.spaceEdit.hidden = state.space.kind !== 'smart';
      els.spaceEdit.href = state.space.kind === 'smart'
        ? searchUrl + '?' + qs(queryToParams(state.space.query || {})) + '&space=' + state.space.space_id
        : '#';
      els.spaceRename.title = 'Reanomena l’espai';
      els.spaceDelete.title = 'Elimina l’espai';
    } else if (state.mode === 'tag') {
      els.up.disabled = true;
      els.actions.hidden = true;
      els.copy.hidden = true;
      els.spaceActions.hidden = false;
      els.spaceEdit.hidden = false;
      els.spaceEdit.href = searchUrl + '?' + qs({ tag_id: state.tag.tag_id, submitted: 1 });
      els.spaceEdit.title = 'Cerca dins d’aquesta etiqueta (pàgina Cercar)';
      els.spaceMove.hidden = true;
      els.spaceRename.title = 'Reanomena l’etiqueta';
      els.spaceDelete.title = 'Elimina l’etiqueta (els fitxers no es toquen)';
    } else {
      els.up.disabled = state.drive == null || !state.path;
      els.actions.hidden = state.drive == null;
      els.spaceActions.hidden = true;
    }
    root.classList.toggle('mode-space', state.mode !== 'folder');
    root.classList.toggle('mode-collection', !!isCollectionView());
    closeMenu();
  }

  function openTag(tagId, opts) {
    opts = opts || {};
    var tag = state.tags.filter(function (t) { return t.tag_id === Number(tagId); })[0];
    if (!tag) { toast('Aquesta etiqueta ja no existeix.'); return; }
    var same = state.mode === 'tag' && state.tag && state.tag.tag_id === tag.tag_id;
    state.mode = 'tag';
    state.tag = tag;
    state.space = null;
    state.drive = null;
    state.path = '';
    if (!same) pushHistory({ tag: tag.tag_id }, buildTagUrl(tag.tag_id), opts);
    document.title = tag.name + ' — Etiquetes — Explorador — Discàleg';
    updateNavButtons();
    renderAddress();
    loadSpaceContents();
    revealTag(tag.tag_id);
  }

  function navigate(drive, path, opts) {
    opts = opts || {};
    path = path || '';
    var same = state.mode === 'folder' && state.drive === drive && state.path === path;
    state.mode = 'folder';
    state.space = null;
    state.tag = null;
    state.drive = drive;
    state.path = path;
    if (!same) pushHistory({ drive: drive, path: path }, buildUrl(drive, path), opts);
    else if (opts.replace) pushHistory({ drive: drive, path: path }, buildUrl(drive, path), { push: false, replace: true });
    document.title = (path ? baseName(path) + ' — ' : '') + drive + ' — Explorador — Discàleg';
    updateNavButtons();
    renderAddress();
    loadContents();
    if (opts.source !== 'tree') revealPath(drive, path);
    else { var n = nodes[nodeKey(drive, path)]; if (n) selectTreeNode(n); }
  }

  function openSpace(spaceId, opts) {
    opts = opts || {};
    var space = state.spacesById[spaceId];
    if (!space) { toast('Aquest espai ja no existeix.'); return; }
    var same = state.mode === 'space' && state.space && state.space.space_id === spaceId;
    state.mode = 'space';
    state.space = space;
    state.tag = null;
    state.spaceAncestors = space.ancestors || [];
    state.drive = null;
    state.path = '';
    if (!same) pushHistory({ space: spaceId }, buildSpaceUrl(spaceId), opts);
    document.title = space.name + ' — Espais — Explorador — Discàleg';
    updateNavButtons();
    renderAddress();
    loadSpaceContents();
    revealSpace(spaceId);
  }

  window.addEventListener('popstate', function (e) {
    var st = e.state;
    if (!st) return;
    state.histIdx = st.idx || 0;
    if (st.space) openSpace(st.space, { push: false });
    else if (st.tag) openTag(st.tag, { push: false });
    else if (st.drive) navigate(st.drive, st.path || '', { push: false });
  });

  els.back.addEventListener('click', function () { history.back(); });
  els.forward.addEventListener('click', function () { history.forward(); });
  els.up.addEventListener('click', goUp);

  function goUp() {
    if (state.mode === 'space') {
      if (state.space && state.space.parent_id != null) openSpace(state.space.parent_id);
      return;
    }
    if (state.drive == null || !state.path) return;
    navigate(state.drive, parentPath(state.path));
  }

  document.addEventListener('keydown', function (e) {
    var inField = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;
    if (e.altKey && e.key === 'ArrowUp') { e.preventDefault(); goUp(); return; }
    if (e.key === 'Backspace' && !inField && !e.altKey && !e.ctrlKey && !e.metaKey) { e.preventDefault(); goUp(); }
  });

  // ------------------------------------------------------------------ barra d'adreça

  function addressSep() {
    var sep = el('span', 'explorer-address-sep');
    sep.appendChild(icon('i-chevron'));
    return sep;
  }

  function renderAddress() {
    els.address.textContent = '';
    if (state.mode === 'tag') {
      var tHead = el('span', 'explorer-address-item explorer-address-root');
      tHead.appendChild(icon('i-tag'));
      tHead.appendChild(el('span', null, 'Etiquetes'));
      els.address.appendChild(tHead);
      els.address.appendChild(addressSep());
      var tItem = el('span', 'explorer-address-item explorer-address-current');
      tItem.setAttribute('aria-current', 'page');
      tItem.appendChild(tagDot(state.tag));
      tItem.appendChild(el('span', null, state.tag.name));
      els.address.appendChild(tItem);
      return;
    }
    if (state.mode === 'space') {
      var head = el('span', 'explorer-address-item explorer-address-root');
      head.appendChild(icon('i-space'));
      head.appendChild(el('span', null, 'Espais'));
      els.address.appendChild(head);
      var chain = state.spaceAncestors.concat([state.space]);
      chain.forEach(function (s, i) {
        els.address.appendChild(addressSep());
        var last = i === chain.length - 1;
        var item;
        if (last) {
          item = el('span', 'explorer-address-item explorer-address-current');
          item.setAttribute('aria-current', 'page');
        } else {
          item = el('a', 'explorer-address-item');
          item.href = buildSpaceUrl(s.space_id);
          item.addEventListener('click', function (e) {
            if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
            e.preventDefault();
            openSpace(s.space_id);
          });
        }
        item.appendChild(icon(spaceIconId(s)));
        item.appendChild(el('span', null, s.name));
        els.address.appendChild(item);
      });
      els.address.scrollLeft = els.address.scrollWidth;
      return;
    }
    if (state.drive == null) {
      els.address.appendChild(el('span', 'explorer-address-placeholder', 'Trieu un disc, una carpeta o un espai'));
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
      if (i > 0) els.address.appendChild(addressSep());
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

  /** Ruta nativa (amb l'arrel de l'escaneig) d'una ruta relativa en format API. */
  function nativePathOf(path, drive) {
    drive = drive || state.drive;
    var d = state.drives[drive] || {};
    var rel = path ? path.split('/').join('\\') : '';
    var rootPath = d.root_path || '';
    if (rootPath) {
      if (!/[\\\/]$/.test(rootPath)) rootPath += '\\';
      return rootPath + rel;
    }
    return drive + (rel ? '\\' + rel : '');
  }

  function nativePath() { return nativePathOf(state.path); }

  function copyText(text) {
    var done = function () { toast('Ruta copiada: ' + text); };
    var fail = function () { window.prompt('Copieu la ruta:', text); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, fail);
    } else {
      fail();
    }
  }

  els.copy.addEventListener('click', function () { copyText(nativePath()); });

  // ------------------------------------------------------------------ menú contextual

  /** Accions d'una carpeta (fila del llistat o carpeta actual). */
  function folderMenuItems(drive, path, opts) {
    var items = [];
    if (opts && opts.openable) {
      items.push({ label: 'Obre', icon: 'i-folder-open', action: function () { navigate(drive, path); } });
      items.push({ sep: true });
    }
    items.push({ label: 'Cerca dins d’aquesta carpeta', icon: 'i-search-folder',
      href: searchUrl + '?' + qs({ in_drive: drive, in_path: path, submitted: 1 }) });
    items.push({ label: 'Exporta a CSV (només aquesta carpeta)', icon: 'i-download', download: true,
      href: exportUrl + '?' + qs({ drive_id: drive, path: path, recursive: 0 }) });
    items.push({ label: 'Exporta a CSV (amb subcarpetes)', icon: 'i-download', download: true,
      href: exportUrl + '?' + qs({ drive_id: drive, path: path, recursive: 1 }) });
    items.push({ label: 'Còpies d’aquesta carpeta en altres discs', icon: 'i-duplicates',
      href: copiesUrl + '?' + qs({ view: 'unique', drive: drive, path: path, submitted: 1 }) });
    items.push({ sep: true });
    items.push({ label: 'Afegeix a una col·lecció o etiqueta…', icon: 'i-plus',
      action: function () { openAddDialog([{ kind: 'folder', drive: drive, path: path, name: opts && opts.name ? opts.name : (baseName(path) || drive) }]); } });
    if (path) {
      items.push({ label: 'Afegeix a l’arxiu virtual…', icon: 'i-space',
        href: '/arxiu/cobertura?' + qs({ disc: drive, afegir: path }) });
    }
    items.push({ sep: true });
    items.push({ label: 'Copia la ruta', icon: 'i-copy', action: function () { copyText(nativePathOf(path, drive)); } });
    return items;
  }

  /** Objectius del diàleg «Afegeix a…» per a una fila: la selecció si la fila en forma part, si no la fila. */
  function targetsFor(r) {
    var selected = state.rows.filter(function (x) { return x.selected && x.kind !== 'space'; });
    var list = (r.selected && selected.length > 1) ? selected : [r];
    return list.map(function (x) {
      return x.kind === 'folder'
        ? { kind: 'folder', drive: x.drive, path: x.path, name: x.name }
        : { kind: 'file', drive: x.drive, path: joinPath(x.folder, x.name), name: x.name };
    });
  }

  /** Accions per treure un fitxer resolt de la col·lecció o etiqueta oberta. */
  function removalItems(r) {
    if (!isCollectionView() || !r.itemId) return [];
    var what = r.itemKind === 'folder' ? 'la carpeta «' + baseName(r.itemPath) + '»' : (r.reason === 'hash' ? 'aquest fitxer (totes les còpies)' : 'aquest fitxer');
    if (state.mode === 'tag') {
      var tag = state.tag;
      return [{ label: 'Treu l’etiqueta «' + tag.name + '» de ' + what, icon: 'i-minus-circle', action: function () {
        fetchJson(API.items + '/untag', { method: 'POST', body: { tag_ids: [tag.tag_id], items: [{ kind: r.itemKind, drive_id: r.itemDrive, path: r.itemPath }] } })
          .then(function () { toast('Etiqueta treta.'); return afterItemsChange(); })
          .catch(function (err) { toast('No s’ha pogut treure: ' + err.message); });
      } }];
    }
    var space = state.space;
    return [{ label: 'Treu ' + what + ' de la col·lecció', icon: 'i-minus-circle', action: function () {
      fetchJson(API.spaces + '/' + space.space_id + '/items/' + r.itemId, { method: 'DELETE' })
        .then(function () { toast('Element tret de la col·lecció.'); return afterItemsChange(); })
        .catch(function (err) { toast('No s’ha pogut treure: ' + err.message); });
    } }];
  }

  /** Accions d'un fitxer del llistat. */
  function fileMenuItems(r) {
    var dupHref;
    if (r.hash) {
      dupHref = duplicatesUrl + '?' + qs({ group: 'hash', hash: r.hash });
    } else {
      dupHref = duplicatesUrl + '?' + qs({ group: 'name-size', name: r.name, size: r.size == null ? '' : r.size });
    }
    var items = [];
    if (state.mode !== 'folder') {
      items.push({ label: 'Obre la carpeta (' + r.drive + ')', icon: 'i-folder-open',
        action: function () { navigate(r.drive, r.folder); } });
      items.push({ sep: true });
    }
    items.push({ label: 'Cerca aquest nom a tots els discs', icon: 'i-search',
      href: searchUrl + '?' + qs({ q_name: r.name, submitted: 1 }) });
    items.push({ label: r.hash ? 'Còpies d’aquest fitxer (mateix contingut)' : 'Còpies d’aquest fitxer (nom i mida)',
      icon: 'i-duplicates', href: dupHref });
    items.push({ sep: true });
    var targets = targetsFor(r);
    items.push({ label: targets.length > 1 ? 'Afegeix els ' + targets.length + ' seleccionats a…' : 'Afegeix a una col·lecció o etiqueta…',
      icon: 'i-plus', action: function () { openAddDialog(targets); } });
    removalItems(r).forEach(function (it) { items.push(it); });
    items.push({ sep: true });
    items.push({ label: 'Copia la ruta', icon: 'i-copy',
      action: function () { copyText(nativePathOf(joinPath(r.folder, r.name), r.drive)); } });
    return items;
  }

  // ------------------------------------------------------------------ diàleg «Afegeix a…»

  var dialogState = { targets: [], initialTags: {} };

  function manualSpacesFlat() {
    var out = [];
    (function walk(list, depth) {
      list.forEach(function (s) {
        if (s.kind === 'manual') out.push({ space: s, depth: depth });
        walk(s.children || [], depth + 1);
      });
    })(state.spacesTree, 0);
    return out;
  }

  function openAddDialog(targets) {
    if (!targets.length) return;
    dialogState.targets = targets;
    dialogState.initialTags = {};
    els.dialogSub.textContent = targets.length === 1
      ? (targets[0].kind === 'folder' ? 'Carpeta ' : 'Fitxer ') + targets[0].drive + ' › ' + (targets[0].path.split('/').join(' › ') || '(arrel)')
      : plural(targets.length, 'element seleccionat', 'elements seleccionats') + ' (' + targets.filter(function (t) { return t.kind === 'folder'; }).length + ' carpetes)';
    // col·leccions (ràdio)
    els.dialogSpaces.textContent = '';
    var none = el('label');
    var noneInput = el('input'); noneInput.type = 'radio'; noneInput.name = 'add-space'; noneInput.value = ''; noneInput.checked = true;
    none.appendChild(noneInput);
    none.appendChild(el('span', 'muted', '— cap col·lecció —'));
    els.dialogSpaces.appendChild(none);
    var spaces = manualSpacesFlat();
    spaces.forEach(function (o) {
      var lab = el('label');
      lab.style.paddingLeft = (8 + 16 * o.depth) + 'px';
      var inp = el('input'); inp.type = 'radio'; inp.name = 'add-space'; inp.value = String(o.space.space_id);
      if (state.mode === 'space' && state.space && state.space.space_id === o.space.space_id) inp.checked = true;
      lab.appendChild(inp);
      lab.appendChild(icon('i-space'));
      lab.appendChild(el('span', null, o.space.name));
      els.dialogSpaces.appendChild(lab);
    });
    if (!spaces.length) els.dialogSpaces.appendChild(el('div', 'explorer-dialog-empty', 'Encara no hi ha cap col·lecció: escriviu un nom a sota per crear-ne una.'));
    // etiquetes (caselles)
    els.dialogTags.textContent = '';
    state.tags.forEach(function (t) {
      var lab = el('label');
      var inp = el('input'); inp.type = 'checkbox'; inp.name = 'add-tag'; inp.value = String(t.tag_id);
      if (state.mode === 'tag' && state.tag && state.tag.tag_id === t.tag_id) inp.checked = true;
      lab.appendChild(inp);
      lab.appendChild(tagDot(t));
      lab.appendChild(el('span', null, t.name));
      lab.appendChild(el('span', 'muted', ' · ' + fmtInt(t.items_count || 0)));
      els.dialogTags.appendChild(lab);
    });
    if (!state.tags.length) els.dialogTags.appendChild(el('div', 'explorer-dialog-empty', 'Encara no hi ha cap etiqueta: escriviu un nom a sota per crear-ne una.'));
    els.dialogNewSpace.value = '';
    els.dialogNewTag.value = '';
    if (typeof els.dialog.showModal === 'function') els.dialog.showModal(); else els.dialog.setAttribute('open', '');
    // amb un sol element, marquem les etiquetes que ja té
    if (targets.length === 1) {
      var t0 = targets[0];
      fetchJson(API.items + '/tags?' + qs({ kind: t0.kind, drive_id: t0.drive, path: t0.path })).then(function (data) {
        (data.tag_ids || []).forEach(function (id) {
          dialogState.initialTags[id] = true;
          var inp = els.dialogTags.querySelector('input[value="' + id + '"]');
          if (inp) inp.checked = true;
        });
      }).catch(function () { /* opcional */ });
    }
  }

  function closeAddDialog() {
    if (typeof els.dialog.close === 'function' && els.dialog.open) els.dialog.close();
    else els.dialog.removeAttribute('open');
  }

  els.dialogCancel.addEventListener('click', closeAddDialog);

  els.dialogForm.addEventListener('submit', function (e) {
    e.preventDefault();
    var targets = dialogState.targets;
    var items = targets.map(function (t) { return { kind: t.kind, drive_id: t.drive, path: t.path }; });
    var spaceChoice = els.dialogSpaces.querySelector('input[name="add-space"]:checked');
    var spaceId = spaceChoice && spaceChoice.value ? Number(spaceChoice.value) : null;
    var newSpace = els.dialogNewSpace.value.trim();
    var newTag = els.dialogNewTag.value.trim();
    var checked = {};
    Array.prototype.forEach.call(els.dialogTags.querySelectorAll('input[name="add-tag"]'), function (inp) { checked[inp.value] = inp.checked; });
    var tagOn = Object.keys(checked).filter(function (id) { return checked[id] && !dialogState.initialTags[id]; }).map(Number);
    var tagOff = Object.keys(checked).filter(function (id) { return !checked[id] && dialogState.initialTags[id]; }).map(Number);
    var summary = [];
    var chain = Promise.resolve();
    if (newSpace) {
      chain = chain.then(function () {
        return fetchJson(API.spaces, { method: 'POST', body: { name: newSpace, kind: 'manual', parent_id: spaceId } })
          .then(function (data) { spaceId = data.space.space_id; summary.push('col·lecció «' + data.space.name + '» creada'); });
      });
    }
    chain = chain.then(function () {
      if (spaceId == null) return;
      return fetchJson(API.spaces + '/' + spaceId + '/items', { method: 'POST', body: { items: items } }).then(function (res) {
        summary.push(plural(res.added, 'element afegit', 'elements afegits') + (res.already ? ' (' + res.already + ' ja hi eren)' : ''));
        (res.errors || []).forEach(function (m) { summary.push('⚠ ' + m); });
      });
    });
    if (newTag) {
      chain = chain.then(function () {
        return fetchJson(API.tags, { method: 'POST', body: { name: newTag } }).then(function (data) { tagOn.push(data.tag.tag_id); summary.push('etiqueta «' + data.tag.name + '»'); });
      });
    }
    chain = chain.then(function () {
      if (!tagOn.length) return;
      return fetchJson(API.items + '/tag', { method: 'POST', body: { items: items, tag_ids: tagOn } }).then(function () {
        summary.push(plural(tagOn.length, 'etiqueta aplicada', 'etiquetes aplicades'));
      });
    }).then(function () {
      if (!tagOff.length) return;
      return fetchJson(API.items + '/untag', { method: 'POST', body: { items: items, tag_ids: tagOff } }).then(function () {
        summary.push(plural(tagOff.length, 'etiqueta treta', 'etiquetes tretes'));
      });
    });
    chain.then(function () {
      closeAddDialog();
      toast(summary.length ? summary.join(' · ') : 'Res a fer: trieu una col·lecció o una etiqueta.');
      return afterItemsChange();
    }).catch(function (err) { toast('No s’ha pogut afegir: ' + err.message); });
  });

  /** Després d'un canvi d'elements: refresca l'arbre (recomptes) i la llista si és una col·lecció o etiqueta. */
  function afterItemsChange() {
    return loadSpacesTree().then(function () {
      if (isCollectionView()) loadSpaceContents();
      else if (state.mode === 'space' && state.space) updateNavButtons();
    });
  }

  var menuState = { open: false, returnFocus: null, anchorBtn: null, openedAt: 0 };

  function closeMenu() {
    if (!menuState.open) return;
    menuState.open = false;
    els.menu.hidden = true;
    els.menu.textContent = '';
    if (menuState.anchorBtn) menuState.anchorBtn.setAttribute('aria-expanded', 'false');
    var rf = menuState.returnFocus;
    menuState.returnFocus = null;
    menuState.anchorBtn = null;
    if (rf && document.contains(rf)) rf.focus({ preventScroll: true });
  }

  function menuItems() { return Array.prototype.slice.call(els.menu.querySelectorAll('[role="menuitem"]')); }

  /**
   * Obre el menú amb `items` ({label, icon, href?, download?, action?, checked?} o {sep:true}).
   * anchor: {x, y} (punt del ratolí) o {el} (element sota el qual es desplega).
   */
  function openMenu(items, anchor, title, returnFocus) {
    closeMenu();
    els.menu.textContent = '';
    if (title) {
      var head = el('div', 'explorer-menu-head', title);
      head.title = title;
      els.menu.appendChild(head);
    }
    items.forEach(function (it) {
      if (it.sep) { els.menu.appendChild(el('div', 'explorer-menu-sep')); return; }
      var item;
      if (it.href) {
        item = el('a', 'explorer-menu-item');
        item.href = it.href;
        if (it.download) item.setAttribute('download', '');
      } else {
        item = el('button', 'explorer-menu-item');
        item.type = 'button';
      }
      item.setAttribute('role', 'menuitem');
      item.setAttribute('tabindex', '-1');
      if (it.checked) item.classList.add('explorer-menu-item-checked');
      if (it.indent) item.style.paddingLeft = (10 + 14 * it.indent) + 'px';
      if (it.icon) item.appendChild(icon(it.icon));
      item.appendChild(el('span', null, it.label));
      item.addEventListener('click', function (e) {
        if (it.action) { e.preventDefault(); closeMenu(); it.action(); return; }
        if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;  // obre en pestanya nova
        closeMenu();
      });
      els.menu.appendChild(item);
    });
    menuState.open = true;
    menuState.openedAt = Date.now();
    menuState.returnFocus = returnFocus || null;
    els.menu.hidden = false;
    els.menu.style.left = '0px';
    els.menu.style.top = '0px';
    var w = els.menu.offsetWidth, h = els.menu.offsetHeight;
    var x, y;
    if (anchor.el) {
      var r = anchor.el.getBoundingClientRect();
      x = r.left; y = r.bottom + 4;
      menuState.anchorBtn = anchor.el;
      anchor.el.setAttribute('aria-expanded', 'true');
    } else {
      x = anchor.x; y = anchor.y;
    }
    var margin = 8;
    x = Math.max(margin, Math.min(x, window.innerWidth - w - margin));
    y = Math.max(margin, Math.min(y, window.innerHeight - h - margin));
    els.menu.style.left = x + 'px';
    els.menu.style.top = y + 'px';
    var first = menuItems()[0];
    if (first) first.focus();
  }

  els.menu.addEventListener('keydown', function (e) {
    var items = menuItems();
    var idx = items.indexOf(document.activeElement);
    switch (e.key) {
      case 'ArrowDown': items[(idx + 1) % items.length].focus(); break;
      case 'ArrowUp': items[(idx - 1 + items.length) % items.length].focus(); break;
      case 'Home': items[0].focus(); break;
      case 'End': items[items.length - 1].focus(); break;
      case 'Escape': closeMenu(); break;
      case 'Tab': closeMenu(); return;
      case ' ':
        if (document.activeElement && document.activeElement.tagName === 'A') { document.activeElement.click(); break; }
        return;
      default: return;
    }
    e.preventDefault();
  });

  document.addEventListener('mousedown', function (e) {
    if (menuState.open && !els.menu.contains(e.target)) closeMenu();
  });
  window.addEventListener('resize', closeMenu);
  els.list.addEventListener('scroll', function () {
    // el scrollIntoView de la fila en enfocar-la dispara un scroll just després d'obrir: s'ignora
    if (Date.now() - menuState.openedAt > 250) closeMenu();
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && menuState.open) { e.preventDefault(); closeMenu(); }
  });

  els.actions.addEventListener('click', function () {
    if (menuState.open && menuState.anchorBtn === els.actions) { closeMenu(); return; }
    if (state.drive == null) return;
    var items = folderMenuItems(state.drive, state.path, { name: baseName(state.path) || state.drive });
    if (!state.path) items = items.filter(function (it) { return !/Afegeix/.test(it.label || ''); });  // l'arrel no entra sencera
    openMenu(items, { el: els.actions }, nativePath(), els.actions);
  });

  function openRowMenu(tr, anchor) {
    var r = tr._row;
    if (!r) return;
    var items;
    if (r.kind === 'folder') items = folderMenuItems(r.drive, r.path, { openable: true });
    else if (r.kind === 'space') items = [{ label: 'Obre', icon: 'i-folder-open', action: function () { openSpace(r.space.space_id); } }];
    else items = fileMenuItems(r);
    openMenu(items, anchor, r.name, tr);
  }

  els.tbody.addEventListener('contextmenu', function (e) {
    var tr = e.target.closest('tr');
    if (!tr || !tr._row) return;
    e.preventDefault();
    focusRow(tr);
    openRowMenu(tr, { x: e.clientX, y: e.clientY });
  });

  // ------------------------------------------------------------------ teclat al llistat

  function focusRow(tr) {
    var prev = els.tbody.querySelector('tr[tabindex="0"]');
    if (prev && prev !== tr) prev.setAttribute('tabindex', '-1');
    tr.setAttribute('tabindex', '0');
    tr.focus({ preventScroll: true });
    tr.scrollIntoView({ block: 'nearest' });
  }

  els.tbody.addEventListener('mousedown', function (e) {
    var tr = e.target.closest('tr');
    if (tr && tr._row && !e.target.closest('a, button')) focusRow(tr);
  });

  // ------------------------------------------------------------------ selecció múltiple

  function setRowSelected(r, on) {
    r.selected = !!on;
    var tr = rowElement(r);
    if (tr) tr.classList.toggle('row-selected', r.selected);
  }

  function rowElement(r) {
    var trs = els.tbody.querySelectorAll('tr');
    for (var i = 0; i < trs.length; i++) if (trs[i]._row === r) return trs[i];
    return null;
  }

  function clearSelection() {
    var any = false;
    state.rows.forEach(function (r) { if (r.selected) { any = true; setRowSelected(r, false); } });
    state.anchorRow = null;
    if (any) renderStatus();
  }

  function selectedCount() { return state.rows.filter(function (r) { return r.selected; }).length; }

  function selectRange(from, to) {
    var order = visibleRows();
    var a = order.indexOf(from), b = order.indexOf(to);
    if (a < 0 || b < 0) return;
    var lo = Math.min(a, b), hi = Math.max(a, b);
    order.forEach(function (r, i) { setRowSelected(r, i >= lo && i <= hi); });
  }

  els.tbody.addEventListener('click', function (e) {
    var tr = e.target.closest('tr');
    if (!tr || !tr._row || e.target.closest('a, button')) return;
    var r = tr._row;
    if (r.kind === 'space') return;
    if (e.shiftKey && state.anchorRow) {
      selectRange(state.anchorRow, r);
    } else if (e.ctrlKey || e.metaKey) {
      setRowSelected(r, !r.selected);
      state.anchorRow = r;
    } else {
      var only = r.selected && selectedCount() === 1;
      state.rows.forEach(function (x) { if (x.selected && x !== r) setRowSelected(x, false); });
      setRowSelected(r, !only);
      state.anchorRow = r.selected ? r : null;
    }
    if (e.shiftKey) window.getSelection && window.getSelection().removeAllRanges();
    renderStatus();
  });

  document.addEventListener('keydown', function (e) {
    var inField = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;
    if (inField || menuState.open || (els.dialog && els.dialog.open)) return;
    if ((e.ctrlKey || e.metaKey) && (e.key === 'a' || e.key === 'A') && els.list.contains(document.activeElement)) {
      e.preventDefault();
      visibleRows().forEach(function (r) { if (r.kind !== 'space') setRowSelected(r, true); });
      renderStatus();
    } else if (e.key === 'Escape' && selectedCount()) {
      clearSelection();
    }
  });

  function openRow(r) {
    if (r.kind === 'folder') navigate(r.drive, r.path);
    else if (r.kind === 'space') openSpace(r.space.space_id);
  }

  els.tbody.addEventListener('keydown', function (e) {
    var tr = e.target.closest('tr');
    if (!tr || !tr._row || e.target !== tr) return;
    var rows = Array.prototype.slice.call(els.tbody.querySelectorAll('tr'));
    var idx = rows.indexOf(tr);
    switch (e.key) {
      case 'ArrowDown': if (idx + 1 < rows.length) focusRow(rows[idx + 1]); break;
      case 'ArrowUp': if (idx > 0) focusRow(rows[idx - 1]); break;
      case 'Home': focusRow(rows[0]); break;
      case 'End': focusRow(rows[rows.length - 1]); break;
      case 'Enter': openRow(tr._row); break;
      case ' ':
        if (tr._row.kind !== 'space') { setRowSelected(tr._row, !tr._row.selected); state.anchorRow = tr._row; renderStatus(); }
        break;
      case 'F10':
        if (!e.shiftKey) return;
        openRowMenu(tr, { el: tr.querySelector('.row-menu-btn') || tr });
        break;
      case 'ContextMenu':
        openRowMenu(tr, { el: tr.querySelector('.row-menu-btn') || tr });
        break;
      default: return;
    }
    e.preventDefault();
  });

  function rowMenuButton(tr) {
    var btn = el('button', 'row-action row-menu-btn');
    btn.type = 'button';
    btn.title = 'Accions';
    btn.setAttribute('aria-label', 'Accions de «' + tr._row.name + '»');
    btn.setAttribute('aria-haspopup', 'menu');
    btn.appendChild(icon('i-more'));
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      if (menuState.open && menuState.anchorBtn === btn) { closeMenu(); return; }
      openRowMenu(tr, { el: btn });
    });
    return btn;
  }

  // ------------------------------------------------------------------ contingut

  function setListView(view) {
    els.welcome.hidden = view !== 'welcome';
    els.emptyFolder.hidden = view !== 'empty';
    els.emptySpace.hidden = view !== 'emptyspace';
    els.noMatch.hidden = view !== 'nomatch';
    els.error.hidden = view !== 'error';
    els.loading.hidden = view !== 'loading';
    els.table.hidden = view !== 'table';
  }

  function resetList() {
    state.rows = [];
    state.anchorRow = null;
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
  }

  function loadContents() {
    var reqId = ++state.reqId;
    var drive = state.drive, path = state.path;
    resetList();

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

  function spaceContentsUrl(offset) {
    var base = state.mode === 'tag'
      ? API.tags + '/' + state.tag.tag_id + '/contents?'
      : API.spaces + '/' + state.space.space_id + '/contents?';
    return base + qs({ limit: PAGE_SIZE, offset: offset, sort: state.sort.key, dir: state.sort.dir });
  }

  /** Carrega el contingut de l'espai o etiqueta oberts (mode space / tag). */
  function loadSpaceContents() {
    var reqId = ++state.reqId;
    var space = state.space;
    resetList();
    if (!(state.sort.key in { name: 1, type: 1, size: 1, date: 1, drive: 1, folder: 1 })) state.sort = { key: 'name', dir: 'asc' };

    fetchJson(spaceContentsUrl(0))
      .then(function (data) {
        if (reqId !== state.reqId) return;
        state.rows = (space ? spaceChildRows(space) : []).concat(toSpaceRows(data));
        state.totalFiles = data.total || 0;
        state.members = data.members;
        state.directBytes = 0;
        state.hasMore = !!data.has_more;
        renderRows();
        renderStatus();
        els.list.scrollTop = 0;
      })
      .catch(function (err) {
        if (reqId !== state.reqId) return;
        els.error.textContent = 'No s’ha pogut carregar l’espai: ' + err.message;
        setListView('error');
        els.status.textContent = '';
      });
  }

  function spaceChildRows(space) {
    return (space.children || []).map(function (s) {
      return { kind: 'space', space: s, name: s.name, path: '', ext: '', size: null, items: null,
        date: '', hasTotals: true, drive: '', folder: '' };
    });
  }

  function toSpaceRows(data) {
    return (data.rows || []).map(function (f) {
      return {
        kind: 'file', name: f.name, path: '', ext: cleanExt(f.extension),
        size: f.size_bytes == null ? null : Number(f.size_bytes), items: null,
        date: f.modified_utc || '', hasTotals: true, hash: f.hash_sha256 || '',
        drive: f.drive_id, folder: f.folder || '', missing: !!f.missing_since_utc,
        reason: f.reason || '', itemId: f.item_id || null, itemKind: f.item_kind || '',
        itemDrive: f.item_drive || '', itemPath: f.item_path || ''
      };
    });
  }

  function toRows(data) {
    var rows = [];
    (data.folders || []).forEach(function (f) {
      rows.push({
        kind: 'folder', name: f.name, path: f.path, ext: '',
        size: f.total_bytes || 0, items: f.files_count || 0, subfolders: null,
        date: '', hasTotals: false, drive: state.drive, folder: state.path
      });
    });
    (data.files || []).forEach(function (f) {
      rows.push({
        kind: 'file', name: f.name, path: '', ext: cleanExt(f.extension),
        size: f.size_bytes == null ? null : Number(f.size_bytes), items: null,
        date: f.modified_utc || '', hasTotals: true, hash: f.hash_sha256 || '',
        drive: state.drive, folder: state.path
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

  function kindRank(r) { return r.kind === 'space' ? 0 : r.kind === 'folder' ? 1 : 2; }

  function compareRows(a, b) {
    if (a.kind !== b.kind) return kindRank(a) - kindRank(b);
    var k = state.sort.key, dir = state.sort.dir === 'asc' ? 1 : -1;
    var r = 0;
    if (k === 'name') r = collator.compare(a.name, b.name);
    else if (k === 'type') r = collator.compare(a.ext, b.ext) || collator.compare(a.name, b.name);
    else if (k === 'size') r = ((a.size || 0) - (b.size || 0)) || collator.compare(a.name, b.name);
    else if (k === 'items') r = ((a.items || 0) - (b.items || 0)) || collator.compare(a.name, b.name);
    else if (k === 'date') r = (a.date < b.date ? -1 : a.date > b.date ? 1 : 0) || collator.compare(a.name, b.name);
    else if (k === 'drive') r = collator.compare(a.drive, b.drive) || collator.compare(a.name, b.name);
    else if (k === 'folder') r = collator.compare(a.folder, b.folder) || collator.compare(a.name, b.name);
    return r * dir;
  }

  function visibleRows() {
    var f = state.filter.trim().toLowerCase();
    var rows = f ? state.rows.filter(function (r) { return r.name.toLowerCase().indexOf(f) >= 0; }) : state.rows.slice();
    // en mode espai l'ordre ve del servidor (la llista és una pàgina d'un conjunt més gran)
    if (state.mode !== 'space') rows.sort(compareRows);
    return rows;
  }

  function renderRows() {
    var rows = visibleRows();
    els.tbody.textContent = '';
    if (state.rows.length === 0) {
      if (state.mode === 'space') renderEmptySpace(); else setListView('empty');
      return;
    }
    if (rows.length === 0) { setListView('nomatch'); return; }
    var frag = document.createDocumentFragment();
    rows.forEach(function (r, i) {
      var tr = r.kind === 'folder' ? folderRow(r) : r.kind === 'space' ? spaceRow(r) : fileRow(r);
      tr._row = r;
      if (r.selected) tr.classList.add('row-selected');
      tr.setAttribute('tabindex', i === 0 ? '0' : '-1');
      tr.querySelector('.col-name').appendChild(rowMenuButton(tr));
      frag.appendChild(tr);
    });
    els.tbody.appendChild(frag);
    setListView('table');
    updateSortHeaders();
    renderStatus();
  }

  function renderEmptySpace() {
    var s = state.space;
    if (state.mode === 'tag') {
      els.emptySpaceText.textContent = 'Cap fitxer té l’etiqueta «' + state.tag.name + '».';
      els.emptySpaceHint.textContent = 'Seleccioneu fitxers o carpetes a qualsevol disc i trieu «Afegeix a…» al menú contextual per etiquetar-los.';
    } else if (s.kind === 'smart') {
      els.emptySpaceText.textContent = 'Cap fitxer coincideix amb aquesta cerca desada.';
      els.emptySpaceHint.textContent = 'Criteris: ' + describeQuery(s.query) + '. Podeu editar-los amb el botó de la barra d’eines.';
    } else if (state.members) {
      els.emptySpaceText.textContent = 'Els ' + plural(state.members, 'element', 'elements') + ' d’aquesta col·lecció no corresponen a cap fitxer present.';
      els.emptySpaceHint.textContent = 'Potser els fitxers han desaparegut en l’últim escaneig o les carpetes ja no existeixen.';
    } else {
      els.emptySpaceText.textContent = 'Aquesta col·lecció és buida.';
      els.emptySpaceHint.textContent = 'Seleccioneu fitxers o carpetes a qualsevol disc i trieu «Afegeix a…» al menú contextual; també hi podeu desar cerques des de Cercar o crear-hi espais amb el botó +.';
    }
    setListView('emptyspace');
  }

  function reasonCell(r) {
    var td = el('td', 'col-reason');
    if (!r.reason) return td;
    var badge = el('span', 'reason-badge reason-' + r.reason);
    var text, title;
    if (r.reason === 'hash') { text = 'mateix contingut'; title = 'Còpia per contingut (hash SHA-256) de l’element afegit'; badge.appendChild(icon('i-duplicates')); }
    else if (r.reason === 'folder') { text = 'dins de ' + baseName(r.itemPath); title = 'Dins de la carpeta afegida ' + r.itemDrive + ' › ' + r.itemPath.split('/').join(' › '); badge.appendChild(icon('i-folder')); }
    else { text = 'per disc i ruta'; title = 'Referència directa (encara sense hash)'; badge.appendChild(icon('i-file')); }
    badge.appendChild(el('span', null, text));
    badge.title = title;
    td.appendChild(badge);
    return td;
  }

  function driveCell(r) {
    var td = el('td', 'col-drive');
    if (r.drive) {
      var a = el('a', 'explorer-link');
      a.href = buildUrl(r.drive, '');
      a.appendChild(icon('i-drive', 'row-icon'));
      a.appendChild(el('span', null, r.drive));
      a.title = 'Obre el disc ' + r.drive;
      a.addEventListener('click', function (e) {
        if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
        e.preventDefault();
        navigate(r.drive, '');
      });
      td.appendChild(a);
    }
    return td;
  }

  function folderCell(r) {
    var td = el('td', 'col-folder');
    if (r.drive) {
      var a = el('a', 'explorer-link');
      a.href = buildUrl(r.drive, r.folder);
      a.textContent = r.folder ? r.folder.split('/').join('\\') : '(arrel)';
      a.title = 'Obre la carpeta a l’explorador';
      a.addEventListener('click', function (e) {
        if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
        e.preventDefault();
        navigate(r.drive, r.folder);
      });
      td.appendChild(a);
    }
    return td;
  }

  function folderRow(r) {
    var tr = el('tr', 'row-folder');
    tr.dataset.path = r.path;
    var tdName = el('td', 'col-name');
    var a = el('a', 'explorer-link');
    a.href = buildUrl(r.drive, r.path);
    a.appendChild(icon('i-folder', 'row-icon'));
    a.appendChild(el('span', 'row-name', r.name));
    a.title = r.name;
    a.addEventListener('click', function (e) {
      if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
      e.preventDefault();
      navigate(r.drive, r.path);
    });
    tdName.appendChild(a);
    tr.appendChild(tdName);
    tr.appendChild(driveCell(r));
    tr.appendChild(folderCell(r));
    tr.appendChild(el('td', 'col-reason', ''));
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
    tr.addEventListener('dblclick', function () { navigate(r.drive, r.path); });
    return tr;
  }

  function spaceRow(r) {
    var tr = el('tr', 'row-space');
    var tdName = el('td', 'col-name');
    var a = el('a', 'explorer-link');
    a.href = buildSpaceUrl(r.space.space_id);
    a.appendChild(icon(spaceIconId(r.space), 'row-icon'));
    a.appendChild(el('span', 'row-name', r.name));
    a.title = r.space.kind === 'smart' ? 'Cerca desada: ' + describeQuery(r.space.query) : 'Espai';
    a.addEventListener('click', function (e) {
      if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
      e.preventDefault();
      openSpace(r.space.space_id);
    });
    tdName.appendChild(a);
    tr.appendChild(tdName);
    tr.appendChild(el('td', 'col-drive', ''));
    tr.appendChild(el('td', 'col-folder', ''));
    tr.appendChild(el('td', 'col-reason', ''));
    tr.appendChild(el('td', 'col-type', r.space.kind === 'smart' ? 'Cerca desada' : 'Col·lecció'));
    tr.appendChild(el('td', 'col-items num', ''));
    tr.appendChild(el('td', 'col-size num', ''));
    tr.appendChild(el('td', 'col-date', fmtDate(r.space.updated_utc || r.space.created_utc)));
    tr.addEventListener('dblclick', function () { openSpace(r.space.space_id); });
    return tr;
  }

  function fileRow(r) {
    var tr = el('tr', 'row-file' + (r.missing ? ' row-missing' : ''));
    var tdName = el('td', 'col-name');
    var wrap = el('span', 'explorer-file');
    wrap.appendChild(icon(fileIconId(r.ext), 'row-icon'));
    wrap.appendChild(el('span', 'row-name', r.name));
    wrap.title = r.name + (r.missing ? ' (no present a l’últim escaneig)' : '');
    tdName.appendChild(wrap);
    tr.appendChild(tdName);
    tr.appendChild(driveCell(r));
    tr.appendChild(folderCell(r));
    tr.appendChild(reasonCell(r));
    tr.appendChild(el('td', 'col-type', r.ext ? r.ext.toUpperCase() : '—'));
    tr.appendChild(el('td', 'col-items num', ''));
    tr.appendChild(el('td', 'col-size num', r.size == null ? '' : fmtBytes(r.size)));
    tr.appendChild(el('td', 'col-date', fmtDate(r.date)));
    return tr;
  }

  function selectionStatus() {
    var n = selectedCount();
    return n ? plural(n, 'seleccionat', 'seleccionats') : '';
  }

  function setStatusText(parts) {
    els.status.textContent = '';
    els.status.appendChild(document.createTextNode(parts.join('  │  ')));
    var sel = selectionStatus();
    if (sel) {
      els.status.appendChild(document.createTextNode(parts.length ? '  │  ' : ''));
      els.status.appendChild(el('span', 'explorer-selection', sel));
    }
  }

  function renderStatus() {
    if (state.mode === 'space' || state.mode === 'tag') {
      var sp = state.space;
      var loaded = state.rows.filter(function (r) { return r.kind === 'file'; }).length;
      var sParts = [];
      var sf = state.filter.trim();
      if (sf) {
        sParts.push(fmtInt(visibleRows().length) + ' de ' + plural(state.rows.length, 'element carregat', 'elements carregats') + ' coincideixen amb «' + sf + '»');
      } else if (state.mode === 'tag') {
        sParts.push(plural(state.totalFiles, 'fitxer amb l’etiqueta', 'fitxers amb l’etiqueta') + ' «' + state.tag.name + '»');
      } else if (sp.kind === 'smart') {
        sParts.push(plural(state.totalFiles, 'fitxer coincideix', 'fitxers coincideixen') + ' · ' + describeQuery(sp.query));
      } else {
        var bits = [];
        if ((sp.children || []).length) bits.push(plural(sp.children.length, 'espai', 'espais'));
        bits.push(plural(state.totalFiles, 'fitxer', 'fitxers') + (state.members ? ' de ' + plural(state.members, 'element afegit', 'elements afegits') : ''));
        sParts.push(bits.join(' · '));
      }
      setStatusText(sParts);
      els.statusPages.textContent = state.totalFiles > loaded ? 'Mostrant ' + fmtInt(loaded) + ' de ' + fmtInt(state.totalFiles) + ' fitxers' : '';
      els.loadMore.hidden = !state.hasMore;
      return;
    }
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
    setStatusText(parts);
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
    var url = state.mode === 'space'
      ? spaceContentsUrl(nextOffset)
      : API.contents + '?' + qs({ drive_id: state.drive, path: state.path, limit: PAGE_SIZE, offset: nextOffset });
    fetchJson(url)
      .then(function (data) {
        if (reqId !== state.reqId) return;
        state.offset = nextOffset;
        var more = state.mode === 'space' ? toSpaceRows(data) : toRows({ files: data.files });
        state.rows = state.rows.concat(more);
        state.hasMore = !!data.has_more;
        if (state.mode === 'space') { if (data.total != null) state.totalFiles = data.total; }
        else if (data.total_files) state.totalFiles = data.total_files;
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
    if (state.mode === 'space') loadSpaceContents();  // l'ordre el fa el servidor
    else renderRows();
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

  // ------------------------------------------------------------------ accions dels espais

  function spaceOptionsForMove(exclude) {
    // tots els espais menys l'exclòs i els seus descendents, indentats
    var out = [];
    (function walk(list, depth) {
      list.forEach(function (s) {
        if (s.space_id === exclude) return;
        out.push({ space: s, depth: depth });
        walk(s.children || [], depth + 1);
      });
    })(state.spacesTree, 0);
    return out;
  }

  function afterSpaceChange(openId) {
    return loadSpacesTree().then(function () {
      if (openId != null && state.spacesById[openId]) openSpace(openId, { push: openId !== (state.space && state.space.space_id) });
    });
  }

  els.spaceNew.addEventListener('click', function () {
    var parent = state.mode === 'space' && state.space ? state.space : null;
    var name = window.prompt(parent ? 'Nom del nou espai dins de «' + parent.name + '»:' : 'Nom del nou espai:', '');
    if (name == null || !name.trim()) return;
    fetchJson(API.spaces, { method: 'POST', body: { name: name.trim(), kind: 'manual', parent_id: parent ? parent.space_id : null } })
      .then(function (data) { toast('Espai «' + data.space.name + '» creat.'); return afterSpaceChange(data.space.space_id); })
      .catch(function (err) { toast('No s’ha pogut crear l’espai: ' + err.message); });
  });

  els.spaceRename.addEventListener('click', function () {
    if (state.mode === 'tag' && state.tag) {
      var tname = window.prompt('Nou nom de l’etiqueta:', state.tag.name);
      if (tname == null || !tname.trim() || tname.trim() === state.tag.name) return;
      var tid = state.tag.tag_id;
      fetchJson(API.tags + '/' + tid, { method: 'PATCH', body: { name: tname.trim() } })
        .then(function () { toast('Etiqueta reanomenada.'); return loadSpacesTree().then(function () { openTag(tid, { push: false }); }); })
        .catch(function (err) { toast('No s’ha pogut reanomenar: ' + err.message); });
      return;
    }
    if (!state.space) return;
    var name = window.prompt('Nou nom de l’espai:', state.space.name);
    if (name == null || !name.trim() || name.trim() === state.space.name) return;
    var id = state.space.space_id;
    fetchJson(API.spaces + '/' + id, { method: 'PATCH', body: { name: name.trim() } })
      .then(function () { toast('Espai reanomenat.'); return afterSpaceChange(id); })
      .catch(function (err) { toast('No s’ha pogut reanomenar: ' + err.message); });
  });

  els.spaceMove.addEventListener('click', function () {
    if (!state.space) return;
    if (menuState.open && menuState.anchorBtn === els.spaceMove) { closeMenu(); return; }
    var id = state.space.space_id;
    function moveTo(parentId) {
      fetchJson(API.spaces + '/' + id, { method: 'PATCH', body: { parent_id: parentId } })
        .then(function () { toast('Espai mogut.'); return afterSpaceChange(id); })
        .catch(function (err) { toast('No s’ha pogut moure: ' + err.message); });
    }
    var items = [{ label: 'Arrel dels espais', icon: 'i-space', checked: state.space.parent_id == null,
      action: function () { moveTo(null); } }];
    spaceOptionsForMove(id).forEach(function (o) {
      items.push({ label: o.space.name, icon: spaceIconId(o.space), indent: o.depth + 1,
        checked: state.space.parent_id === o.space.space_id,
        action: function () { moveTo(o.space.space_id); } });
    });
    openMenu(items, { el: els.spaceMove }, 'Mou «' + state.space.name + '» dins de…', els.spaceMove);
  });

  els.spaceDelete.addEventListener('click', function () {
    if (state.mode === 'tag' && state.tag) {
      var tg = state.tag;
      if (!window.confirm('Eliminar l’etiqueta «' + tg.name + '»? Es treu de ' + plural(tg.items_count || 0, 'element', 'elements') + '; els fitxers dels discs no es toquen.')) return;
      fetchJson(API.tags + '/' + tg.tag_id, { method: 'DELETE' })
        .then(function () { toast('Etiqueta eliminada.'); state.tag = null; return loadSpacesTree().then(function () { location.href = location.pathname; }); })
        .catch(function (err) { toast('No s’ha pogut eliminar: ' + err.message); });
      return;
    }
    if (!state.space) return;
    var s = state.space;
    var n = (s.children || []).length;
    var msg = 'Eliminar l’espai «' + s.name + '»' + (n ? ' i els ' + n + ' espais que conté' : '') + '? Els fitxers dels discs no es toquen.';
    if (!window.confirm(msg)) return;
    fetchJson(API.spaces + '/' + s.space_id, { method: 'DELETE' })
      .then(function () {
        toast('Espai eliminat.');
        var parent = s.parent_id;
        return loadSpacesTree().then(function () {
          if (parent != null && state.spacesById[parent]) openSpace(parent);
          else location.href = location.pathname;
        });
      })
      .catch(function (err) { toast('No s’ha pogut eliminar: ' + err.message); });
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
    var rootsReady = fetchJson(API.roots)
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
      })
      .catch(function (err) {
        els.treeLoading.hidden = true;
        els.treeEmpty.hidden = false;
        els.treeEmpty.textContent = 'No s’han pogut carregar els discs: ' + err.message;
      });

    Promise.all([rootsReady, loadSpacesTree()]).then(function () {
      var params = new URLSearchParams(location.search);
      var spaceParam = params.get('space');
      var tagParam = params.get('tag');
      var drive = params.get('drive');
      var path = (params.get('path') || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
      var tagKnown = tagParam && state.tags.some(function (t) { return String(t.tag_id) === tagParam; });
      if (spaceParam && state.spacesById[spaceParam]) {
        history.replaceState({ space: Number(spaceParam), idx: 0 }, '', buildSpaceUrl(spaceParam));
        openSpace(Number(spaceParam), { push: false });
      } else if (spaceParam) {
        toast('L’espai «' + spaceParam + '» no existeix.');
      } else if (tagKnown) {
        history.replaceState({ tag: Number(tagParam), idx: 0 }, '', buildTagUrl(tagParam));
        openTag(Number(tagParam), { push: false });
      } else if (tagParam) {
        toast('L’etiqueta «' + tagParam + '» no existeix.');
      } else if (drive && state.drives[drive]) {
        history.replaceState({ drive: drive, path: path, idx: 0 }, '', buildUrl(drive, path));
        navigate(drive, path, { push: false });
      } else if (drive) {
        toast('El disc «' + drive + '» no és a la base de dades.');
      }
    });
  }

  init();
})();

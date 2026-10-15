/**
 * Inventari de discs — JavaScript mínim, comú a totes les pàgines
 * - Tema clar/fosc: preferència a localStorage i classe .dark a <html>.
 * - Densitat compacta/normal: preferència a localStorage i classe .compact a <html>.
 * - Barra de filtre compartida (.filterbar amb input.js-rowfilter): amaga les files de la taula
 *   de destí (data-target, selector CSS) que no contenen el text; el text es desa per pàgina.
 */
(function () {
  var STORAGE_KEY = 'inventari-theme';
  var DENSITY_KEY = 'inventari-density';
  var DARK = 'dark';

  function getStored(key) {
    try {
      return localStorage.getItem(key);
    } catch (e) {
      return null;
    }
  }

  function setStored(key, value) {
    try {
      if (value) localStorage.setItem(key, value);
      else localStorage.removeItem(key);
    } catch (e) {}
  }

  function applyTheme(isDark) {
    document.documentElement.classList.toggle(DARK, !!isDark);
  }

  // Respectar preferència guardada; si no hi ha, opcionalment prefers-color-scheme: dark
  var stored = getStored(STORAGE_KEY);
  if (stored === 'dark' || stored === 'light') {
    applyTheme(stored === 'dark');
  } else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
    applyTheme(true);
  }

  // Exposar per al botó de toggle: window.inventariToggleTheme()
  window.inventariToggleTheme = function () {
    var isDark = document.documentElement.classList.contains(DARK);
    isDark = !isDark;
    applyTheme(isDark);
    setStored(STORAGE_KEY, isDark ? 'dark' : 'light');
  };

  // Densitat: «compact» redueix l'alçada de les files de totes les taules i llistats
  function applyDensity(compact) {
    document.documentElement.classList.toggle('compact', !!compact);
    var b = document.getElementById('density-toggle');
    if (b) {
      b.setAttribute('aria-pressed', compact ? 'true' : 'false');
      b.title = compact ? 'Densitat compacta (clic per a la normal)' : 'Densitat normal (clic per a la compacta)';
    }
  }
  applyDensity(getStored(DENSITY_KEY) === 'compact');
  window.inventariToggleDensity = function () {
    var compact = !document.documentElement.classList.contains('compact');
    applyDensity(compact);
    setStored(DENSITY_KEY, compact ? 'compact' : 'normal');
  };

  // Barra de filtre compartida
  function initRowFilters() {
    var inputs = document.querySelectorAll('input.js-rowfilter[data-target]');
    Array.prototype.forEach.call(inputs, function (input) {
      var key = 'rowfilter:' + location.pathname + ':' + (input.id || input.dataset.target);
      var countEl = input.dataset.count ? document.querySelector(input.dataset.count) : null;
      function rows() {
        var out = [];
        Array.prototype.forEach.call(document.querySelectorAll(input.dataset.target), function (t) {
          Array.prototype.push.apply(out, t.querySelectorAll('tbody > tr'));
        });
        return out;
      }
      function apply() {
        var q = input.value.trim().toLowerCase();
        var shown = 0, total = 0;
        rows().forEach(function (tr) {
          if (tr.classList.contains('rf-skip')) return;
          total++;
          var hit = !q || (tr.dataset.filterText || tr.textContent).toLowerCase().indexOf(q) >= 0;
          tr.classList.toggle('rf-hidden', !hit);
          if (hit) shown++;
        });
        input.closest('.filterbar') && input.closest('.filterbar').classList.toggle('filterbar-active', !!q);
        if (countEl) countEl.textContent = q ? (shown + ' de ' + total) : '';
      }
      var saved = getStored(key);
      if (saved) input.value = saved;
      var t;
      input.addEventListener('input', function () {
        clearTimeout(t);
        t = setTimeout(function () { setStored(key, input.value); apply(); }, 120);
      });
      input.addEventListener('keydown', function (ev) {
        if (ev.key === 'Escape') { input.value = ''; setStored(key, ''); apply(); }
      });
      apply();
      input.dataset.filterReady = '1';
      input.applyRowFilter = apply;
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initRowFilters);
  else initRowFilters();
})();

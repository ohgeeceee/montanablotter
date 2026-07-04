(function () {
  'use strict';

  function debounce(fn, delay) {
    var timer = null;
    return function () {
      var self = this;
      var args = arguments;
      clearTimeout(timer);
      timer = setTimeout(function () {
        fn.apply(self, args);
      }, delay);
    };
  }

  function getCellValue(cell, sortType) {
    var raw = cell.getAttribute('data-sort-value') || cell.textContent || '';
    raw = raw.trim();
    if (sortType === 'time') {
      return raw;
    }
    return raw;
  }

  function compareValues(a, b, sortType) {
    if (sortType === 'time') {
      return a.localeCompare(b);
    }
    return a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' });
  }

  function initSearch(wrapper, rows, liveRegion) {
    var input = wrapper.querySelector('.records-table-search');
    if (!input) return;

    input.addEventListener(
      'input',
      debounce(function () {
        var query = (input.value || '').trim().toLowerCase();
        var visible = 0;

        rows.forEach(function (row) {
          var text = row.textContent.toLowerCase();
          var show = !query || text.indexOf(query) !== -1;
          row.hidden = !show;
          if (show) visible += 1;
        });

        if (liveRegion) {
          liveRegion.textContent = visible + ' record' + (visible === 1 ? '' : 's') + ' shown';
        }
      }, 120)
    );
  }

  function initSort(wrapper, rows, tbody) {
    var headers = wrapper.querySelectorAll('.records-table th[data-sort]');
    if (!headers.length) return;

    headers.forEach(function (th) {
      var btn = th.querySelector('.records-table__sort') || th;

      btn.addEventListener('click', function () {
        var index = parseInt(th.getAttribute('data-sort'), 10);
        var sortType = th.getAttribute('data-sort-type') || 'text';
        var current = th.getAttribute('aria-sort');
        var direction = current === 'ascending' ? 'descending' : 'ascending';
        var multiplier = direction === 'ascending' ? 1 : -1;

        headers.forEach(function (h) {
          h.removeAttribute('aria-sort');
        });
        th.setAttribute('aria-sort', direction);

        var sorted = rows.slice().sort(function (a, b) {
          var aCell = a.children[index];
          var bCell = b.children[index];
          if (!aCell || !bCell) return 0;
          var aVal = getCellValue(aCell, sortType);
          var bVal = getCellValue(bCell, sortType);
          return multiplier * compareValues(aVal, bVal, sortType);
        });

        sorted.forEach(function (row) {
          tbody.appendChild(row);
        });
      });
    });
  }

  function initExpand(wrapper) {
    var buttons = wrapper.querySelectorAll('.records-table__expand');
    buttons.forEach(function (btn) {
      btn.addEventListener('click', function () {
        var cell = btn.closest('td');
        if (!cell) return;
        var truncated = cell.querySelector('.records-table__details-truncated');
        var full = cell.querySelector('.records-table__details-full');
        var expanded = btn.getAttribute('aria-expanded') === 'true';

        btn.setAttribute('aria-expanded', String(!expanded));
        btn.textContent = expanded ? 'Show more' : 'Show less';
        if (truncated) truncated.hidden = !expanded;
        if (full) full.hidden = expanded;
      });
    });
  }

  function initTable(wrapper) {
    var table = wrapper.querySelector('.records-table');
    if (!table) return;
    var tbody = table.querySelector('tbody');
    if (!tbody) return;
    var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
    var liveRegion = wrapper.querySelector('.records-table-live');

    initSearch(wrapper, rows, liveRegion);
    initSort(wrapper, rows, tbody);
    initExpand(wrapper);
  }

  document.addEventListener('DOMContentLoaded', function () {
    var wrappers = document.querySelectorAll('[data-records-table]');
    wrappers.forEach(initTable);
  });
})();

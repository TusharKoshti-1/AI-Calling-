/* ============================================================
 * calls.js — All Calls page
 * ============================================================ */
(function () {
  const table = window.CallSaraTable.createTable({
    tbId:         'calls-tbl',
    pgId:         'calls-pager',
    prInfo:       'calls-pg-info',
    prevId:       'calls-prev',
    nextId:       'calls-next',
    filterBtns:   '#calls-filters .ftab',
    hotTog:       'calls-hot-tog',
    searchId:     'calls-search',
    countLabelId: 'calls-count-label',
    pageSize:     30,
  });

  document.addEventListener('DOMContentLoaded', () => {
    table.bindControls();
    table.load();
    setInterval(table.load, 8000);
  });
})();

/* ============================================================
 * dashboard.js — the dashboard page
 * Wires stats, the hot widget, and the recent-calls table.
 * ============================================================ */
(function () {
  const { loadStats, loadHotWidget } = window.CallSaraStats;

  const table = window.CallSaraTable.createTable({
    tbId:       'db-tbl',
    pgId:       'db-pager',
    prInfo:     'db-pg-info',
    prevId:     'db-prev',
    nextId:     'db-next',
    filterBtns: '#db-filters .ftab',
    hotTog:     'db-hot-tog',
    searchId:   'db-search',
    pageSize:   20,
  });

  async function tick() {
    loadStats();
    table.load();
    loadHotWidget();
  }

  document.addEventListener('DOMContentLoaded', () => {
    table.bindControls();
    tick();
    setInterval(tick, 8000);
  });

  window.CallSaraDashboard = {
    refreshTable: () => table.load(),
  };
})();

/* ============================================================
 * hot.js — Hot Leads page
 * ============================================================ */
(function () {
  const { loadStats, loadHotTable } = window.CallSaraStats;

  async function refresh() {
    loadStats();
    loadHotTable();
  }

  document.addEventListener('DOMContentLoaded', () => {
    refresh();
    setInterval(refresh, 8000);
  });
})();

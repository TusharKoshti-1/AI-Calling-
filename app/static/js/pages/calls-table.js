/* ============================================================
 * calls-table.js — reusable calls-table renderer
 *
 * One component used by Dashboard (db-), All Calls (calls-), Hot Leads
 * (hot-). Each caller tells us which DOM ids to read/write from.
 * ============================================================ */
(function () {
  const { api } = window.CallSaraAPI;
  const L = window.CallSaraLayout;

  function rowHtml(c) {
    const bc  = L.badgeClass(c.status);
    const dur = c.duration_sec > 0 ? L.fmtDur(c.duration_sec) : '—';
    const dt  = L.fmtDate(c.started_at);
    const hot = c.hot_lead;
    const hasTx = c.transcript && c.transcript.trim().length > 0;

    const d = JSON.stringify({
      sid: c.sid, phone: c.phone || '', hot,
      dur: c.duration_sec || 0, started: c.started_at || '',
      rec: c.recording_url || '',
    }).replace(/"/g, '&quot;');

    return `<div class="trow ${hot ? 'hot-row' : ''}"
      data-sid="${L.esc(c.sid)}"
      data-hash="${hashStr(c.sid + c.status + c.duration_sec + c.hot_lead + c.recording_url)}"
      onclick="CallSaraModal.open(${d})">
      <div class="cell-phone">${hot ? '🔥 ' : ''}${L.esc(c.phone || c.sid)}</div>
      <div><span class="badge ${bc}">${c.status}</span></div>
      <div class="cell-time">${dt}</div>
      <div class="cell-dur">${dur}</div>
      <div>${hot ? '<span class="badge b-hot">Hot</span>' : '<span style="color:var(--t3);font-size:11px">—</span>'}</div>
      <div style="font-size:11px;color:var(--t3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:200px">${hasTx ? '📝 Has transcript' : '—'}</div>
      <div class="cell-acts">
        <button class="btn btn-ghost btn-sm" onclick="event.stopPropagation();CallSaraModal.open(${d})">View</button>
        ${c.recording_url ? `<a class="btn btn-outline btn-sm" href="${L.esc(c.recording_url)}" target="_blank" onclick="event.stopPropagation()">▶</a>` : ''}
      </div>
    </div>`;
  }

  function hashStr(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) { h = ((h << 5) - h) + s.charCodeAt(i); h |= 0; }
    return h;
  }

  /**
   * Create a table controller.
   *
   * @param {object} opts
   *   tbId    — id of <div> that holds row nodes
   *   pgId    — id of pager container (or null)
   *   prInfo  — id of pager info span
   *   prevId  — id of prev button
   *   nextId  — id of next button
   *   filterBtns — CSS selector for filter buttons (optional)
   *   hotTog  — id of hot-only toggle (optional)
   *   searchId — id of search input (optional)
   *   countLabelId — id of "N calls" label (optional)
   *   pageSize — default page size (20 / 30 / 100)
   *   defaults — initial filter state {filter, hot, search}
   */
  function createTable(opts) {
    const state = {
      filter: (opts.defaults && opts.defaults.filter) || 'all',
      hot:    Boolean(opts.defaults && opts.defaults.hot),
      search: (opts.defaults && opts.defaults.search) || '',
      page:   0,
      size:   opts.pageSize || 20,
      total:  0,
    };
    let searchTimer = null;

    async function load() {
      const tb = document.getElementById(opts.tbId);
      if (!tb) return;

      try {
        const params = new URLSearchParams({
          limit: state.size, offset: state.page * state.size,
          status: state.filter, hot_only: state.hot, search: state.search,
        });
        const d = await api('/api/calls?' + params);
        state.total = d.total || 0;

        const calls = d.calls || [];
        if (!calls.length) {
          tb.innerHTML = `<div class="tbl-empty">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M22 16.92v3a2 2 0 01-2.18 2 19.79 19.79 0 01-8.63-3.07A19.5 19.5 0 013.07 9.81 19.79 19.79 0 01.02 5.13 2 2 0 012 3h3a2 2 0 012 1.72c.127.96.361 1.903.7 2.81a2 2 0 01-.45 2.11L6.09 9.91a16 16 0 006 6l1.27-1.27a2 2 0 012.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0122 16.92z"/></svg>
            <p>No calls found</p></div>`;
        } else {
          // Diff-based update — replace only rows whose content hash changed.
          const existing = {};
          tb.querySelectorAll('.trow[data-sid]').forEach((r) => { existing[r.dataset.sid] = r; });
          const newSids = new Set(calls.map((c) => c.sid));
          Object.keys(existing).forEach((sid) => {
            if (!newSids.has(sid)) existing[sid].remove();
          });
          calls.forEach((c, idx) => {
            const html = rowHtml(c);
            const row = existing[c.sid];
            if (!row) {
              const tmp = document.createElement('div');
              tmp.innerHTML = html;
              const rows = tb.querySelectorAll('.trow');
              const newNode = tmp.firstChild;
              if (rows[idx]) tb.insertBefore(newNode, rows[idx]);
              else tb.appendChild(newNode);
            } else if (row.dataset.hash !== String(hashStr(c.sid + c.status + c.duration_sec + c.hot_lead + c.recording_url))) {
              const tmp = document.createElement('div');
              tmp.innerHTML = html;
              tb.replaceChild(tmp.firstChild, row);
            }
          });
        }

        if (opts.pgId) {
          const pg = document.getElementById(opts.pgId);
          if (pg) {
            if (state.total <= state.size) pg.style.display = 'none';
            else {
              pg.style.display = 'flex';
              const start = state.page * state.size + 1;
              const end = Math.min(start + state.size - 1, state.total);
              const pr = document.getElementById(opts.prInfo);
              if (pr) pr.textContent = `${start}–${end} of ${state.total}`;
              const pv = document.getElementById(opts.prevId);
              const nx = document.getElementById(opts.nextId);
              if (pv) pv.disabled = state.page === 0;
              if (nx) nx.disabled = end >= state.total;
            }
          }
        }

        if (opts.countLabelId) {
          const el = document.getElementById(opts.countLabelId);
          if (el) el.textContent = state.total + ' calls';
        }
      } catch (e) {
        console.error('calls-table load error', e);
      }
    }

    function bindControls() {
      if (opts.filterBtns) {
        document.querySelectorAll(opts.filterBtns).forEach((btn) => {
          btn.addEventListener('click', () => {
            document.querySelectorAll(opts.filterBtns).forEach((b) => b.classList.remove('on'));
            btn.classList.add('on');
            state.filter = btn.dataset.filter || 'all';
            state.page = 0;
            load();
          });
        });
      }
      if (opts.hotTog) {
        const el = document.getElementById(opts.hotTog);
        if (el) {
          el.addEventListener('click', () => {
            state.hot = !state.hot;
            el.classList.toggle('on', state.hot);
            state.page = 0;
            load();
          });
        }
      }
      if (opts.searchId) {
        const s = document.getElementById(opts.searchId);
        if (s) {
          s.addEventListener('input', () => {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => {
              state.search = s.value;
              state.page = 0;
              load();
            }, 320);
          });
        }
      }
      if (opts.prevId) {
        document.getElementById(opts.prevId)?.addEventListener('click', () => {
          if (state.page > 0) { state.page--; load(); }
        });
      }
      if (opts.nextId) {
        document.getElementById(opts.nextId)?.addEventListener('click', () => {
          state.page++; load();
        });
      }
    }

    return { load, bindControls, state };
  }

  window.CallSaraTable = { createTable, rowHtml };
})();

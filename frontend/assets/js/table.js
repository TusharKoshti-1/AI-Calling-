/**
 * frontend/assets/js/table.js
 * Diff-based table renderer — updates only changed rows (no flicker).
 * Used by Dashboard, Calls, Hot Leads pages.
 */

class CallTable {
  /**
   * @param {string} tbodyId  - ID of the tbody/container element
   * @param {string} cols     - CSS grid-template-columns value
   * @param {Function} rowFn  - (call) => HTML string for one row
   */
  constructor(tbodyId, rowFn) {
    this.tbodyId = tbodyId;
    this.rowFn   = rowFn;
  }

  render(calls) {
    const tbody = document.getElementById(this.tbodyId);
    if (!tbody) return;

    if (!calls || !calls.length) {
      tbody.innerHTML = `
        <div class="table-empty">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M22 16.92v3a2 2 0 01-2.18 2 19.79 19.79 0 01-8.63-3.07A19.5 19.5 0 013.07 9.81 19.79 19.79 0 01.02 5.13 2 2 0 012 3h3a2 2 0 012 1.72c.127.96.361 1.903.7 2.81a2 2 0 01-.45 2.11L6.09 9.91a16 16 0 006 6l1.27-1.27a2 2 0 012.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0122 16.92z"/>
          </svg>
          <p>No calls found</p>
        </div>`;
      return;
    }

    // Build map of existing rows
    const existing = {};
    tbody.querySelectorAll('.table-row[data-sid]').forEach(el => {
      existing[el.dataset.sid] = el;
    });

    const newSids = new Set(calls.map(c => c.sid));

    // Remove rows that no longer exist
    Object.keys(existing).forEach(sid => {
      if (!newSids.has(sid)) existing[sid].remove();
    });

    // Insert or update in order
    calls.forEach((call, idx) => {
      const newHtml = this.rowFn(call);
      const newHash = String(hashStr(call.sid + call.status + call.duration_sec + call.hot_lead + (call.recording_url || '')));
      let row = existing[call.sid];

      if (!row) {
        const tmp = document.createElement('div');
        tmp.innerHTML = newHtml;
        row = tmp.firstElementChild;
        const rows = tbody.querySelectorAll('.table-row');
        if (rows[idx]) tbody.insertBefore(row, rows[idx]);
        else tbody.appendChild(row);
      } else if (row.dataset.hash !== newHash) {
        const tmp = document.createElement('div');
        tmp.innerHTML = newHtml;
        const newRow = tmp.firstElementChild;
        tbody.replaceChild(newRow, row);
      }
    });
  }
}

// ── Standard row builder for call tables ──────────────────────
function buildCallRow(call, agentName = 'Sara') {
  const hot  = call.hot_lead;
  const dur  = fmtDuration(call.duration_sec);
  const dt   = fmtDate(call.started_at);
  const hash = String(hashStr(call.sid + call.status + call.duration_sec + call.hot_lead + (call.recording_url || '')));

  const data = JSON.stringify({
    sid: call.sid, phone: call.phone || '',
    hot, dur: call.duration_sec || 0,
    started: call.started_at || '',
    rec: call.recording_url || '',
  }).replace(/"/g, '&quot;');

  return `
    <div class="table-row ${hot ? 'hot' : ''}"
         data-sid="${esc(call.sid)}"
         data-hash="${hash}"
         onclick="openCallDetail(${data})">
      <div class="cell-phone" style="font-family:'DM Mono',monospace;font-size:13px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
        ${hot ? '🔥 ' : ''}${esc(call.phone || call.sid)}
      </div>
      <div>${badgeHtml(call.status)}</div>
      <div style="font-size:11px;color:var(--t3);font-family:'DM Mono',monospace">${dt}</div>
      <div style="font-family:'DM Mono',monospace;font-size:12px;color:var(--t2)">${dur}</div>
      <div>${hot ? '<span class="badge badge-hot">🔥 Hot</span>' : '<span style="color:var(--t3);font-size:11px">—</span>'}</div>
      <div style="font-size:11px;color:var(--t3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
        ${call.transcript ? '📝 Transcript available' : '—'}
      </div>
      <div style="display:flex;gap:5px;justify-content:flex-end">
        <button class="btn btn-ghost btn-sm" onclick="event.stopPropagation();openCallDetail(${data})">View</button>
        ${call.recording_url
          ? `<a class="btn btn-outline btn-sm" href="${esc(call.recording_url)}" target="_blank" onclick="event.stopPropagation()">▶</a>`
          : ''}
      </div>
    </div>`;
}

// ── Call detail modal (shared across pages) ───────────────────
async function openCallDetail(data) {
  if (typeof data === 'string') data = JSON.parse(data);
  const { sid, phone, hot, dur, started, rec } = data;

  const agentName = window._agentName || 'Sara';

  setHtml('modalPhone',
    esc(phone || sid) +
    (hot ? ' <span class="badge badge-hot" style="font-size:11px;vertical-align:middle">🔥 Hot Lead</span>' : '')
  );
  setHtml('modalMeta', `
    <div class="modal-meta-item">📅 ${fmtDate(started)}</div>
    <div class="modal-meta-item">⏱ ${fmtDuration(dur || 0)}</div>
  `);
  setHtml('modalBody', '<div style="text-align:center;padding:24px;color:var(--t3);font-family:\'DM Mono\',monospace;font-size:12px">Loading transcript...</div>');

  openModalOverlay('callModal');

  try {
    const d = await CallsAPI.messages(sid);
    const msgs = d.messages || [];
    let html = '<div class="transcript-label">Conversation Transcript</div>';

    if (!msgs.length) {
      html += '<div class="no-messages">No transcript recorded yet</div>';
    } else {
      msgs.forEach(m => {
        const isAI = m.role === 'ai';
        html += `
          <div class="msg ${isAI ? 'ai' : ''}">
            <div class="msg-avatar ${isAI ? 'ai' : 'customer'}">${isAI ? 'AI' : 'C'}</div>
            <div>
              <div class="msg-who">${isAI ? esc(agentName) + ' — AI' : 'Customer'}</div>
              <div class="msg-bubble">${esc(m.content)}</div>
            </div>
          </div>`;
      });
    }

    if (rec) {
      html += `
        <div class="recording-section">
          <div class="transcript-label">Recording</div>
          <a class="recording-link" href="${esc(rec)}" target="_blank">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polygon points="5 3 19 12 5 21 5 3"/>
            </svg>
            Play Recording
          </a>
        </div>`;
    }

    setHtml('modalBody', html);
  } catch (e) {
    setHtml('modalBody', '<div class="no-messages">Failed to load transcript</div>');
  }
}

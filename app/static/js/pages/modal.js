/* ============================================================
 * modal.js — call-detail overlay (transcript + recording)
 * Used by every page that displays a calls-table.
 * ============================================================ */
(function () {
  const { api } = window.CallSaraAPI;
  const L = window.CallSaraLayout;

  // Track which call is currently displayed so the Delete button can
  // act on it without re-reading anything from the DOM.
  let _activeCall = null;

  function open(data) {
    if (typeof data === 'string') data = JSON.parse(data);
    _activeCall = data;
    const { sid, phone, hot, dur, started, rec } = data;
    const phoneEl = document.getElementById('m-phone');
    const metaEl  = document.getElementById('m-meta');
    const bodyEl  = document.getElementById('m-body');
    const overlay = document.getElementById('detailOverlay');
    if (!overlay) return;

    if (phoneEl) {
      phoneEl.innerHTML = L.esc(phone || sid)
        + (hot ? ' <span class="badge b-hot" style="font-size:11px;vertical-align:middle">🔥 Hot Lead</span>' : '');
    }
    if (metaEl) {
      metaEl.innerHTML = `
        <div class="meta-it">📅 ${L.fmtDate(started)}</div>
        <div class="meta-it">⏱ ${L.fmtDur(dur || 0)}</div>`;
    }
    if (bodyEl) bodyEl.innerHTML = '<div class="loading-t">Loading transcript...</div>';
    overlay.classList.add('open');
    _ensureDeleteBtn();
    loadMessages(sid, rec);
  }

  /**
   * Inject a Delete button into the modal header on first open.
   * The handler reads `_activeCall` so the button always targets
   * whichever call is currently shown.
   */
  function _ensureDeleteBtn() {
    const hd = document.querySelector('#detailOverlay .modal-hd');
    if (!hd || hd.querySelector('.modal-del-btn')) return;
    const btn = document.createElement('button');
    btn.className = 'btn btn-danger btn-sm modal-del-btn';
    btn.style.marginRight = '8px';
    btn.title = 'Delete this call';
    btn.innerHTML = '🗑 Delete';
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!_activeCall) return;
      if (!window.CallSaraTable || !window.CallSaraTable.deleteRow) return;
      const ok = await window.CallSaraTable.deleteRow({
        id: _activeCall.id || '',
        sid: _activeCall.sid,
        phone: _activeCall.phone || _activeCall.sid,
      });
      if (ok) close();
    });
    const closeX = hd.querySelector('#modal-close, .close-x');
    if (closeX) hd.insertBefore(btn, closeX);
    else hd.appendChild(btn);
  }

  async function loadMessages(sid, rec) {
    const bodyEl = document.getElementById('m-body');
    if (!bodyEl) return;
    try {
      const d = await api(`/api/calls/${sid}/messages`);
      const msgs = d.messages || [];
      let html = '<div class="sec-label">Conversation Transcript</div>';

      if (!msgs.length) {
        html += '<div class="no-msgs">No transcript recorded yet</div>';
      } else {
        const agent = L.agentName || 'AI';
        msgs.forEach((m) => {
          const isAI = m.role === 'ai';
          html += `<div class="msg ${isAI ? 'ai' : ''}">
            <div class="av ${isAI ? 'a' : 'c'}">${isAI ? 'AI' : 'C'}</div>
            <div>
              <div class="msg-who">${isAI ? L.esc(agent) + ' — AI' : 'Customer'}</div>
              <div class="bubble">${L.esc(m.content)}</div>
            </div>
          </div>`;
        });
      }
      if (rec) {
        html += `<div class="rec-block">
          <div class="sec-label">Recording</div>
          <a class="rec-link" href="${L.esc(rec)}" target="_blank">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
            Play Recording
          </a>
        </div>`;
      }
      bodyEl.innerHTML = html;
    } catch (_) {
      bodyEl.innerHTML = '<div class="no-msgs">Failed to load</div>';
    }
  }

  function close(e) {
    const overlay = document.getElementById('detailOverlay');
    if (!overlay) return;
    if (!e || e.target === overlay || e.target.id === 'modal-close') {
      overlay.classList.remove('open');
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    const overlay = document.getElementById('detailOverlay');
    if (overlay) overlay.addEventListener('click', close);
    document.getElementById('modal-close')?.addEventListener('click', close);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
  });

  window.CallSaraModal = { open, close };
})();

/* ============================================================
 * app.js — CallSara dashboard
 * ============================================================
 *
 * Extracted from the legacy all-in-one index.html. The external
 * `CallSaraAPI` module (api.js) handles auth & transport.
 * ============================================================ */
(function () {
  const api = CallSaraAPI.api;

  // ── STATE ────────────────────────────────────────────────────
  let agentName = 'Sara';
  let fromNum   = '';

  const db = { filter: 'all', hot: false, search: '', page: 0, size: 20, total: 0 };
  const cl = { filter: 'all', hot: false, search: '', page: 0, size: 30, total: 0 };
  let dbTimer = null, clTimer = null;

  const pageTitles = {
    dashboard: 'Dashboard', calls: 'All Calls', hot: 'Hot Leads',
    dialer: 'New Call', settings: 'Settings', voice: 'AI Voice',
  };

  // ── INIT ─────────────────────────────────────────────────────
  async function init() {
    await loadStatus();
    await Promise.all([loadStats(), loadTableData('db'), loadHotWidget()]);
    setInterval(tick, 8000);
    document.getElementById('qPhoneInput')?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') makeCall('qPhoneInput', 'qCallBtn');
    });
    document.getElementById('sPhoneInput')?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') makeCall('sPhoneInput', 'sCallBtn');
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });
  }

  async function tick() {
    const active = document.querySelector('.page.active')?.id || 'p-dashboard';
    loadStats();
    if (active === 'p-dashboard') { loadTableData('db'); loadHotWidget(); }
    else if (active === 'p-calls') { loadTableData('cl'); }
    else if (active === 'p-hot') { loadHotPage(); loadHotWidget(); }
  }

  async function loadStatus() {
    try {
      const d = await api('/api/status');
      agentName = d.agent || 'Sara';
      fromNum = d.from_number || '';
      setText('agencyPill', d.agency || '');
      setText('agencyTag', d.agency || 'AI Dialer');
      document.title = (d.agency || 'CallSara') + ' — AI Dialer';
      setText('fromNum', d.from_number || '—');
      const dot = document.getElementById('liveDot');
      const lbl = document.getElementById('liveLabel');
      if (d.twilio_configured) {
        if (dot) dot.className = 'dot on';
        if (lbl) lbl.textContent = d.from_number || 'Live';
      } else {
        if (dot) dot.className = 'dot err';
        if (lbl) lbl.textContent = 'Token Missing';
      }
    } catch (_) {
      setText('liveLabel', 'Server Error');
    }
  }

  // ── STATS ────────────────────────────────────────────────────
  async function loadStats() {
    try {
      const s = await api('/api/stats');
      setText('st-total', s.total_calls || 0);
      setText('st-ans', s.answered || 0);
      setText('st-hot', s.hot_leads || 0);
      setText('st-fail', s.no_answer || 0);
      setText('st-today', (s.calls_today || 0) + ' today');
      setText('st-hot-today', (s.hot_leads_today || 0) + ' today');
      setText('st-ring', (s.ringing || 0) + ' ringing');
      const avg = s.avg_duration_sec || 0;
      setText('st-avg', avg > 0 ? 'avg ' + fmtDur(avg) : 'avg —');
      setText('hl-count', s.hot_leads || 0);
      setText('hl-today', s.hot_leads_today || 0);
      setText('hl-avg', avg > 0 ? fmtDur(avg) : '—');
    } catch (_) { /* keep last values */ }
  }

  // ── PAGES ────────────────────────────────────────────────────
  function goPage(name, el) {
    document.querySelectorAll('.page').forEach((p) => p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach((n) => n.classList.remove('active'));
    document.getElementById('p-' + name)?.classList.add('active');
    if (el) el.classList.add('active');
    setText('pgTitle', pageTitles[name] || name);
    if (name === 'calls') loadTableData('cl');
    if (name === 'hot') loadHotPage();
    if (name === 'settings') loadSettings();
    if (name === 'voice' && window.CallSaraVoice) window.CallSaraVoice.load();
  }

  // ── TABLE RENDERER — diff-based ─────────────────────────────
  async function loadTableData(scope) {
    const s = scope === 'db' ? db : cl;
    const tbId = scope === 'db' ? 'db-tbl' : 'calls-tbl';
    const pgId = scope === 'db' ? 'db-pager' : 'calls-pager';
    const prId = scope === 'db' ? 'db-pg-info' : 'calls-pg-info';
    const pvId = scope === 'db' ? 'db-prev' : 'calls-prev';
    const nxId = scope === 'db' ? 'db-next' : 'calls-next';

    try {
      const params = new URLSearchParams({
        limit: s.size, offset: s.page * s.size,
        status: s.filter, hot_only: s.hot, search: s.search,
      });
      const d = await api('/api/calls?' + params);
      s.total = d.total || 0;

      const calls = d.calls || [];
      const tb = document.getElementById(tbId);
      if (!tb) return;

      if (!calls.length) {
        tb.innerHTML = `<div class="tbl-empty">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M22 16.92v3a2 2 0 01-2.18 2 19.79 19.79 0 01-8.63-3.07A19.5 19.5 0 013.07 9.81 19.79 19.79 0 01.02 5.13 2 2 0 012 3h3a2 2 0 012 1.72c.127.96.361 1.903.7 2.81a2 2 0 01-.45 2.11L6.09 9.91a16 16 0 006 6l1.27-1.27a2 2 0 012.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0122 16.92z"/></svg>
          <p>No calls found</p></div>`;
      } else {
        const existingRows = {};
        tb.querySelectorAll('.trow[data-sid]').forEach((r) => { existingRows[r.dataset.sid] = r; });
        const newSids = new Set(calls.map((c) => c.sid));
        Object.keys(existingRows).forEach((sid) => {
          if (!newSids.has(sid)) existingRows[sid].remove();
        });
        calls.forEach((c, idx) => {
          const html = rowHtml(c);
          let row = existingRows[c.sid];
          if (!row) {
            const tmp = document.createElement('div');
            tmp.innerHTML = html;
            row = tmp.firstChild;
            const rows = tb.querySelectorAll('.trow');
            if (rows[idx]) tb.insertBefore(row, rows[idx]);
            else tb.appendChild(row);
          } else if (row.dataset.hash !== hashStr(html)) {
            const tmp = document.createElement('div');
            tmp.innerHTML = html;
            tb.replaceChild(tmp.firstChild, row);
          }
        });
      }

      const pg = document.getElementById(pgId);
      if (pg) {
        if (s.total <= s.size) pg.style.display = 'none';
        else {
          pg.style.display = 'flex';
          const start = s.page * s.size + 1;
          const end = Math.min(start + s.size - 1, s.total);
          setText(prId, `${start}–${end} of ${s.total}`);
          const pv = document.getElementById(pvId);
          const nx = document.getElementById(nxId);
          if (pv) pv.disabled = s.page === 0;
          if (nx) nx.disabled = end >= s.total;
        }
      }
      if (scope === 'cl') setText('calls-count-label', s.total + ' calls');
    } catch (e) {
      console.error('loadTable', scope, e);
    }
  }

  function rowHtml(c) {
    const bc = badgeClass(c.status);
    const dur = c.duration_sec > 0 ? fmtDur(c.duration_sec) : '—';
    const dt = fmtDate(c.started_at);
    const hot = c.hot_lead;
    const hasTx = c.transcript && c.transcript.trim().length > 0;

    const d = JSON.stringify({
      sid: c.sid, phone: c.phone || '', hot,
      dur: c.duration_sec || 0, started: c.started_at || '',
      rec: c.recording_url || '',
    }).replace(/"/g, '&quot;');

    return `<div class="trow ${hot ? 'hot-row' : ''}" data-sid="${esc(c.sid)}" data-hash="${hashStr(c.sid + c.status + c.duration_sec + c.hot_lead + c.recording_url)}" onclick="openModal(${d})">
      <div class="cell-phone">${hot ? '🔥 ' : ''}${esc(c.phone || c.sid)}</div>
      <div><span class="badge ${bc}">${c.status}</span></div>
      <div class="cell-time">${dt}</div>
      <div class="cell-dur">${dur}</div>
      <div>${hot ? '<span class="badge b-hot">Hot</span>' : '<span style="color:var(--t3);font-size:11px">—</span>'}</div>
      <div style="font-size:11px;color:var(--t3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:200px">${hasTx ? '📝 Has transcript' : '—'}</div>
      <div class="cell-acts">
        <button class="btn btn-ghost btn-sm" onclick="event.stopPropagation();openModal(${d})">View</button>
        ${c.recording_url ? `<a class="btn btn-outline btn-sm" href="${esc(c.recording_url)}" target="_blank" onclick="event.stopPropagation()">▶</a>` : ''}
      </div>
    </div>`;
  }

  // ── HOT LEADS ───────────────────────────────────────────────
  async function loadHotWidget() {
    try {
      const d = await api('/api/calls?limit=8&offset=0&status=all&hot_only=true&search=');
      const hot = d.calls || [];
      const el = document.getElementById('hotWidget');
      if (!el) return;
      if (!hot.length) {
        el.innerHTML = '<div class="tbl-empty" style="padding:22px 18px"><p>No hot leads yet</p></div>';
        return;
      }
      el.innerHTML = hot.map((c) => `
        <div class="hot-item" onclick="openModal(${JSON.stringify({ sid: c.sid, phone: c.phone || '', hot: true, dur: c.duration_sec || 0, started: c.started_at || '', rec: c.recording_url || '' }).replace(/"/g, '&quot;')})">
          <div>
            <div class="hot-phone">🔥 ${esc(c.phone || c.sid)}</div>
            <div class="hot-meta">${fmtDate(c.started_at)} · ${fmtDur(c.duration_sec || 0)}</div>
          </div>
          <span class="badge ${badgeClass(c.status)}">${c.status}</span>
        </div>`).join('');
    } catch (_) { /* ignore */ }
  }

  async function loadHotPage() {
    try {
      const d = await api('/api/calls?limit=100&offset=0&status=all&hot_only=true&search=');
      const calls = d.calls || [];
      const tb = document.getElementById('hot-tbl');
      if (!tb) return;
      if (!calls.length) {
        tb.innerHTML = '<div class="tbl-empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="36" height="36"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg><p>No hot leads yet</p></div>';
        return;
      }
      tb.innerHTML = calls.map(rowHtml).join('');
    } catch (_) { /* ignore */ }
  }

  // ── TABLE CONTROLS ──────────────────────────────────────────
  function dbFilter(v, el) {
    db.filter = v; db.page = 0;
    document.querySelectorAll('#db-filters .ftab').forEach((b) => b.classList.remove('on'));
    el.classList.add('on');
    loadTableData('db');
  }
  function dbToggleHot() {
    db.hot = !db.hot; db.page = 0;
    document.getElementById('db-hot-tog').classList.toggle('on', db.hot);
    loadTableData('db');
  }
  function dbSearch() {
    clearTimeout(dbTimer);
    dbTimer = setTimeout(() => {
      db.search = document.getElementById('db-search').value;
      db.page = 0;
      loadTableData('db');
    }, 320);
  }
  function dbPrev() { if (db.page > 0) { db.page--; loadTableData('db'); } }
  function dbNext() { db.page++; loadTableData('db'); }

  function callsFilter(v, el) {
    cl.filter = v; cl.page = 0;
    document.querySelectorAll('#p-calls .ftab').forEach((b) => b.classList.remove('on'));
    el.classList.add('on');
    loadTableData('cl');
  }
  function callsToggleHot() {
    cl.hot = !cl.hot; cl.page = 0;
    document.getElementById('calls-hot-tog').classList.toggle('on', cl.hot);
    loadTableData('cl');
  }
  function callsSearch() {
    clearTimeout(clTimer);
    clTimer = setTimeout(() => {
      cl.search = document.getElementById('calls-search').value;
      cl.page = 0;
      loadTableData('cl');
    }, 320);
  }
  function callsPrev() { if (cl.page > 0) { cl.page--; loadTableData('cl'); } }
  function callsNext() { cl.page++; loadTableData('cl'); }

  // ── MAKE CALL ───────────────────────────────────────────────
  async function makeCall(inputId, btnId) {
    const inp = document.getElementById(inputId);
    const btn = document.getElementById(btnId);
    const num = inp.value.trim();
    if (!num) { toast('Enter a phone number', 'err'); return; }

    btn.disabled = true;
    btn.textContent = 'Calling...';
    try {
      const d = await api('/api/call', 'POST', { phone: num });
      if (d.success) {
        toast('✓ Calling ' + num, 'ok');
        inp.value = '';
        await loadTableData('db');
      } else {
        toast('✗ ' + (d.error || 'Call failed'), 'err');
      }
    } catch (_) {
      toast('✗ Network error', 'err');
    }
    btn.disabled = false;
    btn.textContent = btnId.includes('q') ? 'Call' : 'Call Now';
  }

  async function bulkCall() {
    const raw = document.getElementById('bulkInput').value;
    const nums = raw.split('\n').map((n) => n.trim()).filter((n) => n.length > 6);
    if (!nums.length) { toast('No valid numbers', 'err'); return; }

    const prog = document.getElementById('bulkProg');
    prog.style.display = 'block';
    for (let i = 0; i < nums.length; i++) {
      prog.textContent = `📞 Calling ${i + 1} of ${nums.length}: ${nums[i]}`;
      try {
        const d = await api('/api/call', 'POST', { phone: nums[i] });
        if (!d.success) toast('✗ ' + nums[i] + ': ' + (d.error || 'failed'), 'err');
      } catch (_) {
        toast('✗ ' + nums[i] + ': network error', 'err');
      }
      if (i < nums.length - 1) await sleep(2500);
    }
    prog.textContent = `✅ Done — ${nums.length} calls placed`;
    toast(`✓ ${nums.length} calls placed`, 'ok');
    await loadTableData('db');
  }

  // ── MODAL ───────────────────────────────────────────────────
  function openModal(data) {
    if (typeof data === 'string') data = JSON.parse(data);
    const { sid, phone, hot, dur, started, rec } = data;
    const phoneEl = document.getElementById('m-phone');
    const metaEl = document.getElementById('m-meta');
    const bodyEl = document.getElementById('m-body');
    if (phoneEl) {
      phoneEl.innerHTML = esc(phone || sid) +
        (hot ? ' <span class="badge b-hot" style="font-size:11px;vertical-align:middle">🔥 Hot Lead</span>' : '');
    }
    if (metaEl) {
      metaEl.innerHTML = `
        <div class="meta-it">📅 ${fmtDate(started)}</div>
        <div class="meta-it">⏱ ${fmtDur(dur || 0)}</div>`;
    }
    if (bodyEl) bodyEl.innerHTML = '<div class="loading-t">Loading transcript...</div>';
    document.getElementById('detailOverlay')?.classList.add('open');
    loadMessages(sid, rec);
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
        msgs.forEach((m) => {
          const isAI = m.role === 'ai';
          html += `<div class="msg ${isAI ? 'ai' : ''}">
            <div class="av ${isAI ? 'a' : 'c'}">${isAI ? 'AI' : 'C'}</div>
            <div>
              <div class="msg-who">${isAI ? esc(agentName) + ' — AI' : 'Customer'}</div>
              <div class="bubble">${esc(m.content)}</div>
            </div>
          </div>`;
        });
      }
      if (rec) {
        html += `<div class="rec-block">
          <div class="sec-label">Recording</div>
          <a class="rec-link" href="${esc(rec)}" target="_blank">
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

  function closeModal(e) {
    if (!e || e.target === document.getElementById('detailOverlay')) {
      document.getElementById('detailOverlay')?.classList.remove('open');
    }
  }

  // ── SETTINGS PAGE ───────────────────────────────────────────
  async function loadSettings() {
    try {
      const s = await api('/api/settings');
      setVal('s-agent-name', s.agent_name);
      setVal('s-agency-name', s.agency_name);
      setVal('s-system-prompt', s.system_prompt);
      const provider = (s.llm_provider || 'openai').toLowerCase();
      _setLLMButtons(provider);
      setVal('s-groq-model', s.groq_model || 'llama-3.3-70b-versatile');
      setVal('s-openai-model', s.openai_model || 'gpt-4o-mini');
      const keyInput = document.getElementById('s-openai-key');
      if (keyInput) {
        keyInput.value = '';
        keyInput.placeholder = s.openai_api_key_present
          ? '(key saved — enter new key to change)'
          : 'sk-proj-...';
      }
    } catch (_) {
      toast('Failed to load settings', 'err');
    }
  }

  function _setLLMButtons(provider) {
    const isOpenAI = provider === 'openai';
    const btnG = document.getElementById('llm-btn-groq');
    const btnO = document.getElementById('llm-btn-openai');
    const secG = document.getElementById('llm-groq-section');
    const secO = document.getElementById('llm-openai-section');
    const label = document.getElementById('llm-active-label');
    if (btnG) btnG.className = isOpenAI ? 'btn btn-ghost' : 'btn btn-gold';
    if (btnO) btnO.className = isOpenAI ? 'btn btn-gold' : 'btn btn-ghost';
    if (secG) secG.style.display = isOpenAI ? 'none' : '';
    if (secO) secO.style.display = isOpenAI ? '' : 'none';
    if (label) label.textContent = isOpenAI ? '🟢 Active: OpenAI' : '🟢 Active: Groq';
  }

  function selectLLM(provider) { _setLLMButtons(provider); }

  async function saveLLMSettings() {
    const groqSection = document.getElementById('llm-groq-section');
    const isOpenAI = groqSection && groqSection.style.display === 'none';
    const provider = isOpenAI ? 'openai' : 'groq';
    const body = { llm_provider: provider };

    if (isOpenAI) {
      const key = document.getElementById('s-openai-key').value.trim();
      if (key) body.openai_api_key = key;
      body.openai_model = document.getElementById('s-openai-model').value.trim() || 'gpt-4o-mini';
    } else {
      body.groq_model = document.getElementById('s-groq-model').value.trim() || 'llama-3.3-70b-versatile';
    }

    try {
      const d = await api('/api/settings', 'POST', body);
      if (d.success) {
        flashSaved('llm-saved');
        toast(`✓ Switched to ${provider === 'openai' ? 'OpenAI' : 'Groq'}`, 'ok');
      }
    } catch (_) { toast('✗ Save failed', 'err'); }
  }

  async function saveIdentity() {
    const body = {
      agent_name: document.getElementById('s-agent-name').value.trim(),
      agency_name: document.getElementById('s-agency-name').value.trim(),
    };
    try {
      const d = await api('/api/settings', 'POST', body);
      if (d.success) {
        flashSaved('identity-saved');
        await loadStatus();
        toast('✓ Identity saved', 'ok');
      }
    } catch (_) { toast('✗ Save failed', 'err'); }
  }

  async function savePrompt() {
    const body = { system_prompt: document.getElementById('s-system-prompt').value.trim() };
    try {
      const d = await api('/api/settings', 'POST', body);
      if (d.success) { flashSaved('prompt-saved'); toast('✓ Prompt saved', 'ok'); }
    } catch (_) { toast('✗ Save failed', 'err'); }
  }

  async function resetPrompt() {
    if (!confirm('Reset to default prompt?')) return;
    try {
      await api('/api/settings', 'POST', { system_prompt: 'default' });
      await loadSettings();
      toast('✓ Reset to default', 'info');
    } catch (_) { toast('✗ Failed', 'err'); }
  }

  function flashSaved(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.add('show');
    setTimeout(() => el.classList.remove('show'), 3000);
  }

  // ── UTILITIES ───────────────────────────────────────────────
  function badgeClass(s) {
    return ({
      ringing: 'b-ring', answered: 'b-ans', completed: 'b-done',
      'no-answer': 'b-fail', busy: 'b-fail', failed: 'b-fail',
    })[s] || 'b-done';
  }

  function fmtDur(sec) {
    if (!sec || sec <= 0) return '—';
    const m = Math.floor(sec / 60), s = sec % 60;
    return m > 0 ? `${m}m ${s}s` : `${s}s`;
  }

  function fmtDate(iso) {
    if (!iso) return '—';
    try {
      const d = new Date(iso);
      return d.toLocaleDateString('en-AE', { day: '2-digit', month: 'short' }) + ' ' +
        d.toLocaleTimeString('en-AE', { hour: '2-digit', minute: '2-digit', hour12: false });
    } catch (_) { return iso; }
  }

  function setText(id, v) { const e = document.getElementById(id); if (e) e.textContent = v; }
  function setVal(id, v) { const e = document.getElementById(id); if (e) e.value = v || ''; }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#x27;');
  }
  function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
  function hashStr(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) { h = ((h << 5) - h) + s.charCodeAt(i); h |= 0; }
    return h;
  }

  let _tt;
  function toast(msg, type = 'info') {
    const el = document.getElementById('toast');
    if (!el) return;
    const ic = { ok: '✓', err: '✗', info: 'ℹ' };
    el.innerHTML = `<span>${ic[type] || 'ℹ'}</span><span>${msg}</span>`;
    el.className = 'show ' + (type === 'err' ? 'err' : type === 'ok' ? 'ok' : 'info');
    clearTimeout(_tt);
    _tt = setTimeout(() => { el.className = ''; }, 4000);
  }

  // ── EXPORT (globals required by inline onclick handlers) ────
  window.goPage = goPage;
  window.dbFilter = dbFilter;
  window.dbToggleHot = dbToggleHot;
  window.dbSearch = dbSearch;
  window.dbPrev = dbPrev;
  window.dbNext = dbNext;
  window.callsFilter = callsFilter;
  window.callsToggleHot = callsToggleHot;
  window.callsSearch = callsSearch;
  window.callsPrev = callsPrev;
  window.callsNext = callsNext;
  window.makeCall = makeCall;
  window.bulkCall = bulkCall;
  window.openModal = openModal;
  window.closeModal = closeModal;
  window.loadSettings = loadSettings;
  window.selectLLM = selectLLM;
  window.saveLLMSettings = saveLLMSettings;
  window.saveIdentity = saveIdentity;
  window.savePrompt = savePrompt;
  window.resetPrompt = resetPrompt;

  // Shared helpers used by voice.js
  window.CallSaraUI = {
    toast, esc, fmtDur, fmtDate,
    getAgentName: () => agentName,
    refreshStatus: loadStatus,
  };

  document.addEventListener('DOMContentLoaded', init);
})();

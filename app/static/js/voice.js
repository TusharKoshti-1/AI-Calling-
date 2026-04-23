/* ============================================================
 * voice.js — AI Voice preview page
 *
 * Depends on:
 *   window.CallSaraAPI  — api.js
 *   window.CallSaraUI   — app.js (toast, esc)
 * ============================================================ */
(function () {
  const { api, rawFetch } = window.CallSaraAPI;
  const { toast, esc } = window.CallSaraUI;

  const DEFAULT_VOICE = '95d51f79-c397-46f9-b49a-23763d3eaa2d';

  const VOICES = {
    indian: [
      { id: '95d51f79-c397-46f9-b49a-23763d3eaa2d', name: 'Priya', lang: 'Hindi / Hinglish · Female', avCls: 'av-in', note: 'Warm, natural Indian accent. Best for Hinglish calls to India.' },
    ],
    arabic: [
      { id: '002622d8-19d0-4567-a16a-f99c7397c062', name: 'Huda',   lang: 'Gulf Arabic · Female', avCls: 'av-ar', note: 'Warm professional Gulf Arabic, great for female persona.' },
      { id: 'fc923f89-1de5-4ddf-b93c-6da2ba63428a', name: 'Nour',   lang: 'Gulf Arabic · Female', avCls: 'av-ar', note: 'Soft and friendly, clear Gulf dialect.' },
      { id: 'f1cdfb4a-bf7d-4e83-916e-8f0802278315', name: 'Walid',  lang: 'Gulf Arabic · Male',   avCls: 'av-ar', note: 'Confident and authoritative, Gulf accent.' },
      { id: '664aec8a-64a4-4437-8a0b-a61aa4f51fe6', name: 'Hassan', lang: 'Gulf Arabic · Male',   avCls: 'av-ar', note: 'Deep, reassuring Gulf Arabic voice.' },
      { id: 'b0aa4612-81d2-4df3-9730-3fc064754b1f', name: 'Khalid', lang: 'Gulf Arabic · Male',   avCls: 'av-ar', note: 'Clear and professional Gulf tone.' },
    ],
  };

  let activeVoiceId = '';
  let curAudio = null;
  let curBtnId = null;
  const wfTimers = {};
  const allVoices = () => [...VOICES.indian, ...VOICES.arabic];

  async function load() {
    try {
      const s = await api('/api/settings');
      activeVoiceId = s.voice_id || DEFAULT_VOICE;
    } catch (_) { /* use default */ }
    renderGrid('indian-voices', VOICES.indian);
    renderGrid('arabic-voices', VOICES.arabic);
    updateActiveLabel();
  }

  function renderGrid(containerId, voices) {
    const el = document.getElementById(containerId);
    if (el) el.innerHTML = voices.map(vcHtml).join('');
  }

  function vcHtml(v) {
    const flag = v.avCls === 'av-in' ? '🇮🇳' : '🇦🇪';
    const sid = v.id.replace(/-/g, '').slice(0, 8);
    const isActive = v.id === activeVoiceId;
    const pbId = 'pb-' + sid;
    const vj = JSON.stringify(v).replace(/"/g, '&quot;');

    return `<div class="vc ${isActive ? 'vc-active' : ''}" id="vc-${sid}">
      <div class="vc-head">
        <div class="vc-info">
          <div class="vc-av ${v.avCls}">${flag}</div>
          <div>
            <div class="vc-name">${esc(v.name)} ${isActive ? '<span style="font-size:9px;color:var(--gold);background:var(--gold3);padding:1px 7px;border-radius:20px;vertical-align:middle;letter-spacing:1px">ACTIVE</span>' : ''}</div>
            <div class="vc-lang">${esc(v.lang)}</div>
          </div>
        </div>
      </div>
      <div class="vc-note">${esc(v.note)}</div>
      <div class="wf" id="wf-${sid}">${Array(18).fill(0).map((_, i) => `<div class="wb" id="wb-${sid}-${i}" style="height:${3 + Math.random() * 8}px"></div>`).join('')}</div>
      <div class="vc-foot">
        <button class="prev-btn" id="${pbId}" onclick="CallSaraVoice.preview(${vj},'${pbId}')">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg> Preview
        </button>
        <button class="use-btn ${isActive ? 'ub-active' : ''}" id="ub-${sid}" onclick="CallSaraVoice.setActive(${vj})">
          ${isActive ? '✓ Active' : 'Use This Voice'}
        </button>
      </div>
    </div>`;
  }

  async function preview(voice, btnId) {
    if (curAudio) { curAudio.pause(); curAudio = null; }
    if (curBtnId && curBtnId !== btnId) resetPBtn(curBtnId);
    if (curBtnId === btnId) {
      const sid = voice.id.replace(/-/g, '').slice(0, 8);
      stopWf(sid);
      curBtnId = null;
      return;
    }

    const btn = document.getElementById(btnId);
    if (!btn) return;
    btn.className = 'prev-btn pb-load';
    btn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg> Loading...';
    curBtnId = btnId;

    const text = document.getElementById('preview-text')?.value?.trim()
      || "Hello, I'm Sara from Prestige Properties Dubai. Are you looking to invest in a property, or is this somewhere you'd like to live?";
    const sid = voice.id.replace(/-/g, '').slice(0, 8);

    try {
      const resp = await rawFetch('/api/voice/preview', {
        method: 'POST',
        body: JSON.stringify({ voice_id: voice.id, text }),
      });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      curAudio = audio;

      btn.className = 'prev-btn pb-play';
      btn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg> Playing';
      startWf(sid);

      audio.onended = () => {
        resetPBtn(btnId); stopWf(sid); curAudio = null; curBtnId = null;
        URL.revokeObjectURL(url);
      };
      audio.onerror = () => { resetPBtn(btnId); stopWf(sid); curAudio = null; curBtnId = null; };
      audio.play();
    } catch (err) {
      console.error(err);
      resetPBtn(btnId); stopWf(sid); curAudio = null; curBtnId = null;
      toast('Preview failed: ' + err.message, 'err');
    }
  }

  function resetPBtn(id) {
    const b = document.getElementById(id);
    if (!b) return;
    b.className = 'prev-btn';
    b.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg> Preview';
  }

  function startWf(sid) {
    stopWf(sid);
    wfTimers[sid] = setInterval(() => {
      for (let i = 0; i < 18; i++) {
        const b = document.getElementById(`wb-${sid}-${i}`);
        if (b) { b.className = 'wb wa'; b.style.height = (4 + Math.random() * 20) + 'px'; }
      }
    }, 100);
  }

  function stopWf(sid) {
    if (wfTimers[sid]) { clearInterval(wfTimers[sid]); delete wfTimers[sid]; }
    for (let i = 0; i < 18; i++) {
      const b = document.getElementById(`wb-${sid}-${i}`);
      if (b) { b.className = 'wb'; b.style.height = (3 + Math.random() * 7) + 'px'; }
    }
  }

  async function setActive(voice) {
    if (curAudio) { curAudio.pause(); curAudio = null; }
    if (curBtnId) { resetPBtn(curBtnId); curBtnId = null; }
    Object.keys(wfTimers).forEach(stopWf);
    try {
      const d = await api('/api/settings', 'POST', { voice_id: voice.id });
      if (d.success) {
        activeVoiceId = voice.id;
        renderGrid('indian-voices', VOICES.indian);
        renderGrid('arabic-voices', VOICES.arabic);
        updateActiveLabel();
        toast('✓ Voice set to ' + voice.name, 'ok');
      }
    } catch (_) {
      toast('Failed to save voice', 'err');
    }
  }

  function updateActiveLabel() {
    const v = allVoices().find((x) => x.id === activeVoiceId);
    const el = document.getElementById('active-voice-name');
    if (el) {
      el.textContent = v
        ? (v.avCls === 'av-in' ? '🇮🇳' : '🇦🇪') + ' ' + v.name + ' — ' + v.lang
        : 'Custom';
    }
  }

  window.CallSaraVoice = { load, preview, setActive };
})();

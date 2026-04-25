/* ============================================================
 * voice.js — AI Voice page (per-user)
 *
 * Voice catalogue is laid out in three sections:
 *   • Cartesia Indian voices (existing)
 *   • ElevenLabs Indian voices (NEW in v12)
 *   • Cartesia Arabic voices (existing)
 *
 * Each voice card stores its voice_id verbatim. The server uses the
 * shape of the ID (UUID with dashes = Cartesia, 20-char no dashes =
 * ElevenLabs) to dispatch synthesis to the correct provider.
 *
 * The picker stays unaware of provider details — it just sends
 * voice_id to /api/settings (to save) or /api/voice/preview (to
 * preview), and the server figures out where to route.
 * ============================================================ */
(function () {
  const { api, rawFetch } = window.CallSaraAPI;
  const L = window.CallSaraLayout;

  // Default fallback if user has no voice_id stored yet.
  // Stays as the existing Cartesia Indian voice for backwards-compat
  // with users who signed up before ElevenLabs was an option.
  const DEFAULT_VOICE = '95d51f79-c397-46f9-b49a-23763d3eaa2d';

  /**
   * Provider detection mirrors the server's `looks_like_elevenlabs_id`:
   * Cartesia voice IDs are UUIDs with dashes; ElevenLabs IDs are 20-char
   * alphanumeric without dashes. Used purely for UI labelling here.
   */
  function isElevenLabsId(id) {
    return id && id.indexOf('-') === -1;
  }

  const VOICES = {
    // ── Cartesia: Indian (Hinglish) ────────────────────────────
    indian: [
      {
        id: '95d51f79-c397-46f9-b49a-23763d3eaa2d',
        name: 'Priya',
        lang: 'Hindi / Hinglish · Female',
        avCls: 'av-in',
        note: 'Warm, natural Indian accent. Best for Hinglish calls to India.',
      },
    ],

    // ── ElevenLabs: Indian / Indian-English / Hindi ────────────
    // These three voice IDs are Twilio's own published defaults for
    // their ConversationRelay product, sourced from:
    //   https://www.twilio.com/docs/voice/conversationrelay/voice-configuration
    // They're known-good IDs that work reliably with ElevenLabs Flash 2.5.
    //
    // To add more voices: open https://elevenlabs.io/app/voice-library,
    // pick any voice (e.g. "Anika - Hindi Customer Care"), copy the
    // 20-char ID from its Voice Settings page, paste a new entry here.
    indianEleven: [
      {
        id: 'UgBBYS2sOqTuMpoF3BR0',
        name: 'Aria',
        lang: 'English (US) · Female',
        avCls: 'av-in',
        provider: 'elevenlabs',
        note: 'ElevenLabs default. Warm professional female voice — works well for both English and Hinglish customers.',
      },
      {
        id: 'mCQMfsqGDT6IDkEKR20a',
        name: 'Meera',
        lang: 'English (India) · Female',
        avCls: 'av-in',
        provider: 'elevenlabs',
        note: 'ElevenLabs Indian English. Natural Indian accent, clearer than Cartesia for English-heavy customers.',
      },
      {
        id: 'IvLWq57RKibBrqZGpQrC',
        name: 'Anjali',
        lang: 'Hindi · Female',
        avCls: 'av-in',
        provider: 'elevenlabs',
        note: 'ElevenLabs Hindi. Best choice if your customers prefer pure Hindi over Hinglish.',
      },
    ],

    // ── Cartesia: Arabic (Gulf) ────────────────────────────────
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
  const allVoices = () =>
    [...VOICES.indian, ...VOICES.indianEleven, ...VOICES.arabic];

  async function load() {
    try {
      const s = await api('/api/settings');
      activeVoiceId = s.voice_id || DEFAULT_VOICE;
    } catch (_) { /* use default */ }
    renderGrid('indian-voices', VOICES.indian);
    renderGrid('indian-eleven-voices', VOICES.indianEleven);
    renderGrid('arabic-voices', VOICES.arabic);
    updateActiveLabel();
  }

  function renderGrid(containerId, voices) {
    const el = document.getElementById(containerId);
    if (el) el.innerHTML = voices.map(vcHtml).join('');
    // Bind after insert
    voices.forEach((v) => {
      const sid = sidOf(v.id);
      const pbId = 'pb-' + sid;
      const ubId = 'ub-' + sid;
      document.getElementById(pbId)?.addEventListener('click', () => preview(v, pbId));
      document.getElementById(ubId)?.addEventListener('click', () => setActive(v));
    });
  }

  /**
   * Build a stable short ID for DOM element lookups. Cartesia IDs have
   * dashes; ElevenLabs IDs don't. Strip dashes from both (no-op on EL)
   * and take the first 8 chars — collision-free across our seed list.
   */
  function sidOf(voiceId) {
    return voiceId.replace(/-/g, '').slice(0, 8);
  }

  function vcHtml(v) {
    const flag = v.avCls === 'av-in' ? '🇮🇳' : '🇦🇪';
    const sid = sidOf(v.id);
    const isActive = v.id === activeVoiceId;
    const isEleven = isElevenLabsId(v.id);
    const elBadge = isEleven
      ? '<span style="font-size:8px;color:#3b82f6;background:rgba(59,130,246,0.12);padding:1px 6px;border-radius:20px;margin-left:6px;letter-spacing:1px;vertical-align:middle">11LABS</span>'
      : '';

    return `<div class="vc ${isActive ? 'vc-active' : ''}" id="vc-${sid}">
      <div class="vc-head">
        <div class="vc-info">
          <div class="vc-av ${v.avCls}">${flag}</div>
          <div>
            <div class="vc-name">${L.esc(v.name)} ${elBadge} ${isActive ? '<span style="font-size:9px;color:var(--gold);background:var(--gold3);padding:1px 7px;border-radius:20px;vertical-align:middle;letter-spacing:1px">ACTIVE</span>' : ''}</div>
            <div class="vc-lang">${L.esc(v.lang)}</div>
          </div>
        </div>
      </div>
      <div class="vc-note">${L.esc(v.note)}</div>
      <div class="wf" id="wf-${sid}">${Array(18).fill(0).map((_, i) => `<div class="wb" id="wb-${sid}-${i}" style="height:${3 + Math.random() * 8}px"></div>`).join('')}</div>
      <div class="vc-foot">
        <button class="prev-btn" id="pb-${sid}">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg> Preview
        </button>
        <button class="use-btn ${isActive ? 'ub-active' : ''}" id="ub-${sid}">
          ${isActive ? '✓ Active' : 'Use This Voice'}
        </button>
      </div>
    </div>`;
  }

  async function preview(voice, btnId) {
    if (curAudio) { curAudio.pause(); curAudio = null; }
    if (curBtnId && curBtnId !== btnId) resetPBtn(curBtnId);
    if (curBtnId === btnId) {
      const sid = sidOf(voice.id);
      stopWf(sid);
      curBtnId = null;
      resetPBtn(btnId);
      return;
    }

    const btn = document.getElementById(btnId);
    if (!btn) return;
    btn.className = 'prev-btn pb-load';
    btn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg> Loading...';
    curBtnId = btnId;

    const text = document.getElementById('preview-text')?.value?.trim()
      || "Hi, this is your AI assistant calling. I'm just testing the voice quality — how does it sound?";
    const sid = sidOf(voice.id);

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
      L.toast('Preview failed: ' + err.message, 'err');
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
        renderGrid('indian-eleven-voices', VOICES.indianEleven);
        renderGrid('arabic-voices', VOICES.arabic);
        updateActiveLabel();
        const providerLabel = isElevenLabsId(voice.id) ? '11Labs' : 'Cartesia';
        L.toast(`✓ Voice set to ${voice.name} (${providerLabel})`, 'ok');
      }
    } catch (_) {
      L.toast('Failed to save voice', 'err');
    }
  }

  function updateActiveLabel() {
    const v = allVoices().find((x) => x.id === activeVoiceId);
    const el = document.getElementById('active-voice-name');
    if (el) {
      if (v) {
        const flag = v.avCls === 'av-in' ? '🇮🇳' : '🇦🇪';
        const tag = isElevenLabsId(v.id) ? ' · 11Labs' : ' · Cartesia';
        el.textContent = flag + ' ' + v.name + ' — ' + v.lang + tag;
      } else {
        el.textContent = 'Custom';
      }
    }
  }

  document.addEventListener('DOMContentLoaded', load);
})();

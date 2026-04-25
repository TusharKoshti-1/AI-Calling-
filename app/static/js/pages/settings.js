/* ============================================================
 * settings.js — Settings page
 * ============================================================ */
(function () {
  const { api } = window.CallSaraAPI;
  const L = window.CallSaraLayout;

  async function load() {
    try {
      const s = await api('/api/settings');
      setVal('s-agent-name',    s.agent_name);
      setVal('s-agency-name',   s.agency_name);
      setVal('s-transfer-number', s.transfer_number);
      setVal('s-language',      s.language || 'en-US');
      setVal('s-system-prompt', s.system_prompt);
      const provider = (s.llm_provider || 'openai').toLowerCase();
      setLLMButtons(provider);
      setVal('s-groq-model',   s.groq_model   || 'llama-3.3-70b-versatile');
      setVal('s-openai-model', s.openai_model || 'gpt-4o-mini');
      const key = document.getElementById('s-openai-key');
      if (key) {
        key.value = '';
        key.placeholder = s.openai_api_key_present
          ? '(key saved — enter new key to change)'
          : 'sk-proj-...';
      }
    } catch (err) {
      L.toast('Failed to load settings', 'err');
    }
  }

  function setLLMButtons(provider) {
    const isOpenAI = provider === 'openai';
    document.getElementById('llm-btn-groq').className   = isOpenAI ? 'btn btn-ghost' : 'btn btn-gold';
    document.getElementById('llm-btn-openai').className = isOpenAI ? 'btn btn-gold'  : 'btn btn-ghost';
    document.getElementById('llm-groq-section').style.display   = isOpenAI ? 'none' : '';
    document.getElementById('llm-openai-section').style.display = isOpenAI ? ''     : 'none';
    const label = document.getElementById('llm-active-label');
    if (label) label.textContent = isOpenAI ? '🟢 Active: OpenAI' : '🟢 Active: Groq';
  }

  async function saveLLM() {
    const groqShown = document.getElementById('llm-groq-section').style.display !== 'none';
    const provider = groqShown ? 'groq' : 'openai';
    const body = { llm_provider: provider };

    if (!groqShown) {
      const key = document.getElementById('s-openai-key').value.trim();
      if (key) body.openai_api_key = key;
      body.openai_model = document.getElementById('s-openai-model').value.trim() || 'gpt-4o-mini';
    } else {
      body.groq_model = document.getElementById('s-groq-model').value.trim() || 'llama-3.3-70b-versatile';
    }

    try {
      const d = await api('/api/settings', 'POST', body);
      if (d.success) {
        flashSaved(groqShown ? 'llm-saved' : 'llm-saved-2');
        L.toast('✓ Switched to ' + (provider === 'openai' ? 'OpenAI' : 'Groq'), 'ok');
      }
    } catch (err) { L.toast('✗ ' + (err.message || 'Save failed'), 'err'); }
  }

  async function saveIdentity() {
    const langEl = document.getElementById('s-language');
    const body = {
      agent_name:  document.getElementById('s-agent-name').value.trim(),
      agency_name: document.getElementById('s-agency-name').value.trim(),
      transfer_number: document.getElementById('s-transfer-number').value.trim(),
      language: (langEl && langEl.value.trim()) || 'en-US',
    };
    try {
      const d = await api('/api/settings', 'POST', body);
      if (d.success) {
        flashSaved('identity-saved');
        L.toast('✓ Identity saved', 'ok');
      }
    } catch (err) { L.toast('✗ ' + (err.message || 'Save failed'), 'err'); }
  }

  async function savePrompt() {
    const body = { system_prompt: document.getElementById('s-system-prompt').value.trim() };
    try {
      const d = await api('/api/settings', 'POST', body);
      if (d.success) { flashSaved('prompt-saved'); L.toast('✓ Prompt saved', 'ok'); }
    } catch (err) { L.toast('✗ ' + (err.message || 'Save failed'), 'err'); }
  }

  async function resetPrompt() {
    if (!confirm('Reset to default prompt?')) return;
    try {
      await api('/api/settings', 'POST', { system_prompt: 'default' });
      await load();
      L.toast('✓ Reset to default', 'info');
    } catch (err) { L.toast('✗ ' + (err.message || 'Failed'), 'err'); }
  }

  function setVal(id, v) { const e = document.getElementById(id); if (e) e.value = v || ''; }

  function flashSaved(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.add('show');
    setTimeout(() => el.classList.remove('show'), 3000);
  }

  document.addEventListener('DOMContentLoaded', () => {
    load();
    document.getElementById('saveIdentityBtn')?.addEventListener('click', saveIdentity);
    document.getElementById('savePromptBtn')?.addEventListener('click', savePrompt);
    document.getElementById('resetPromptBtn')?.addEventListener('click', resetPrompt);
    document.getElementById('saveLLMBtn1')?.addEventListener('click', saveLLM);
    document.getElementById('saveLLMBtn2')?.addEventListener('click', saveLLM);
    document.getElementById('llm-btn-groq')?.addEventListener('click', () => setLLMButtons('groq'));
    document.getElementById('llm-btn-openai')?.addEventListener('click', () => setLLMButtons('openai'));
  });
})();

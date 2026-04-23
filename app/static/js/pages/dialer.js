/* ============================================================
 * dialer.js — single + bulk dial
 * Wires up every phone input + button it can find.
 * ============================================================ */
(function () {
  const { api } = window.CallSaraAPI;
  const L = window.CallSaraLayout;

  async function makeCall(inputId, btnId, onDone) {
    const inp = document.getElementById(inputId);
    const btn = document.getElementById(btnId);
    if (!inp || !btn) return;

    const num = inp.value.trim();
    if (!num) { L.toast('Enter a phone number', 'err'); return; }

    const originalLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Calling...';
    try {
      const d = await api('/api/call', 'POST', { phone: num });
      if (d.success) {
        L.toast('✓ Calling ' + num, 'ok');
        inp.value = '';
        if (onDone) onDone();
      } else {
        L.toast('✗ ' + (d.error || 'Call failed'), 'err');
      }
    } catch (err) {
      L.toast('✗ ' + (err.message || 'Network error'), 'err');
    }
    btn.disabled = false;
    btn.textContent = originalLabel;
  }

  async function bulkCall() {
    const input = document.getElementById('bulkInput');
    const prog = document.getElementById('bulkProg');
    if (!input) return;

    const nums = input.value.split('\n').map((n) => n.trim()).filter((n) => n.length > 6);
    if (!nums.length) { L.toast('No valid numbers', 'err'); return; }

    if (prog) prog.style.display = 'block';
    for (let i = 0; i < nums.length; i++) {
      if (prog) prog.textContent = `📞 Calling ${i + 1} of ${nums.length}: ${nums[i]}`;
      try {
        const d = await api('/api/call', 'POST', { phone: nums[i] });
        if (!d.success) L.toast('✗ ' + nums[i] + ': ' + (d.error || 'failed'), 'err');
      } catch (err) {
        L.toast('✗ ' + nums[i] + ': ' + (err.message || 'network'), 'err');
      }
      if (i < nums.length - 1) await new Promise((r) => setTimeout(r, 2500));
    }
    if (prog) prog.textContent = `✅ Done — ${nums.length} calls placed`;
    L.toast(`✓ ${nums.length} calls placed`, 'ok');
  }

  function bindAutoCallHandlers() {
    const q = document.getElementById('qPhoneInput');
    const qb = document.getElementById('qCallBtn');
    if (q && qb) {
      qb.addEventListener('click', () => {
        makeCall('qPhoneInput', 'qCallBtn', () => {
          if (window.CallSaraDashboard) window.CallSaraDashboard.refreshTable();
        });
      });
      q.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') qb.click();
      });
    }

    const s = document.getElementById('sPhoneInput');
    const sb = document.getElementById('sCallBtn');
    if (s && sb) {
      sb.addEventListener('click', () => makeCall('sPhoneInput', 'sCallBtn'));
      s.addEventListener('keydown', (e) => { if (e.key === 'Enter') sb.click(); });
    }

    const bulkBtn = document.getElementById('bulkCallBtn');
    if (bulkBtn) bulkBtn.addEventListener('click', bulkCall);
  }

  document.addEventListener('DOMContentLoaded', bindAutoCallHandlers);

  window.CallSaraDialer = { makeCall, bulkCall };
})();

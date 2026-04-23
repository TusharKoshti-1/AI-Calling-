/* ============================================================
 * api.js — thin HTTP wrapper around fetch()
 *
 * Handles:
 *   • Admin API-key header injection (from localStorage)
 *   • Centralised error surface (401 → re-prompt; other → throw)
 *   • JSON request/response normalisation
 * ============================================================ */
(function (global) {
  const API_KEY_STORAGE = 'callsara_admin_key';

  function getApiKey() {
    try { return localStorage.getItem(API_KEY_STORAGE) || ''; }
    catch (_) { return ''; }
  }
  function setApiKey(key) {
    try { localStorage.setItem(API_KEY_STORAGE, key || ''); }
    catch (_) { /* ignore — Safari private mode */ }
  }
  function clearApiKey() {
    try { localStorage.removeItem(API_KEY_STORAGE); }
    catch (_) { /* ignore */ }
  }

  function buildHeaders(extra) {
    const h = Object.assign({ 'Content-Type': 'application/json' }, extra || {});
    const key = getApiKey();
    if (key) h['X-API-Key'] = key;
    return h;
  }

  async function api(url, method = 'GET', body = null) {
    const opts = { method, headers: buildHeaders() };
    if (body != null) opts.body = JSON.stringify(body);
    const r = await fetch(url, opts);

    if (r.status === 401) {
      // Server requires auth — prompt once, persist, retry.
      const entered = promptForKey();
      if (entered) {
        setApiKey(entered);
        return api(url, method, body);
      }
      throw new Error('401 Unauthorized');
    }
    if (!r.ok) throw new Error(String(r.status));

    // Some endpoints return non-JSON (e.g. audio) — caller will use rawFetch.
    const ct = r.headers.get('content-type') || '';
    if (ct.includes('application/json')) return r.json();
    return r;
  }

  // Variant that returns the raw Response (used for audio blobs).
  async function rawFetch(url, opts) {
    const final = Object.assign({}, opts || {});
    final.headers = buildHeaders(final.headers);
    return fetch(url, final);
  }

  function promptForKey() {
    try {
      // eslint-disable-next-line no-alert
      return prompt('Admin API key required. Paste it here (stored in this browser only):') || '';
    } catch (_) {
      return '';
    }
  }

  global.CallSaraAPI = {
    api,
    rawFetch,
    getApiKey,
    setApiKey,
    clearApiKey,
  };
})(window);

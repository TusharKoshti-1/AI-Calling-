/* ============================================================
 * api.js — cookie-based auth HTTP wrapper
 *
 * Session auth is done via HttpOnly cookies set by the /api/auth/*
 * endpoints. JS never touches the cookie. A 401 response means the
 * session expired — we redirect to /signin.
 * ============================================================ */
(function (global) {

  async function api(url, method = 'GET', body = null) {
    const opts = {
      method,
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
    };
    if (body != null) opts.body = JSON.stringify(body);
    const r = await fetch(url, opts);

    if (r.status === 401) {
      // Session expired or missing — bounce to signin.
      // Preserve where the user was so we can bring them back afterwards.
      const here = location.pathname + location.search;
      if (!location.pathname.startsWith('/signin')) {
        location.assign('/signin?next=' + encodeURIComponent(here));
      }
      throw new Error('401');
    }

    const ct = r.headers.get('content-type') || '';
    if (ct.includes('application/json')) {
      const data = await r.json();
      if (!r.ok) {
        const err = new Error(data.error || `HTTP ${r.status}`);
        err.status = r.status;
        err.code = data.code;
        throw err;
      }
      return data;
    }
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r;
  }

  async function rawFetch(url, opts) {
    const final = Object.assign({ credentials: 'same-origin' }, opts || {});
    final.headers = Object.assign(
      { 'Content-Type': 'application/json' },
      final.headers || {},
    );
    return fetch(url, final);
  }

  global.CallSaraAPI = { api, rawFetch };
})(window);

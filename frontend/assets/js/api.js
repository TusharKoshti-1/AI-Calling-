/**
 * frontend/assets/js/api.js
 * Centralised API client — all fetch calls go through here.
 * Import this on every page.
 */

const API_BASE = '/api/v1';

async function apiFetch(path, method = 'GET', body = null) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body) opts.body = JSON.stringify(body);

  const resp = await fetch(API_BASE + path, opts);
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ error: resp.statusText }));
    throw new Error(err.error || `HTTP ${resp.status}`);
  }
  return resp.json();
}

// ── Calls ────────────────────────────────────────────────────
const CallsAPI = {
  initiate: (phone)            => apiFetch('/calls', 'POST', { phone }),
  list: (params = {})          => apiFetch('/calls?' + new URLSearchParams(params)),
  stats: ()                    => apiFetch('/calls/stats'),
  messages: (sid)              => apiFetch(`/calls/${sid}/messages`),
};

// ── Settings ─────────────────────────────────────────────────
const SettingsAPI = {
  get: ()             => apiFetch('/settings'),
  save: (updates)     => apiFetch('/settings', 'POST', updates),
  resetPrompt: ()     => apiFetch('/settings/reset-prompt', 'POST'),
};

// ── Status ───────────────────────────────────────────────────
const StatusAPI = {
  get: () => apiFetch('/status'),
};

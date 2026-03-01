// SAT — Global helpers
// Each page's inline script handles its own logic;
// this file provides shared utilities.

/**
 * Lightweight fetch wrapper that throws on non-2xx.
 */
async function apiFetch(url, options = {}) {
  const res = await fetch(url, {
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

/**
 * Format an ISO date string to locale date+time.
 */
function fmtDate(iso) {
  return new Date(iso).toLocaleString();
}

window.apiFetch = apiFetch;
window.fmtDate = fmtDate;

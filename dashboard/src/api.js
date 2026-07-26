// Set VITE_API_BASE at build time to point the dashboard at a deployed API.
// Falls back to the local backend, so `npm run dev` needs no configuration.
export const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

// The API key is never a build-time constant -- this is a static site, so
// anything baked into the bundle is readable by anyone who views source (see
// SECURITY.md). Instead the analyst enters it at runtime; it lives only in
// this tab's sessionStorage, never in the built JS.
const STORAGE_KEY = "threat-hunter-api-key";
let apiKey = sessionStorage.getItem(STORAGE_KEY) || "";
const authListeners = new Set();

export function getApiKey() {
  return apiKey;
}

export function setApiKey(key) {
  apiKey = key ? key.trim() : "";
  if (apiKey) sessionStorage.setItem(STORAGE_KEY, apiKey);
  else sessionStorage.removeItem(STORAGE_KEY);
}

// Fires whenever a request comes back 401, so the app can prompt for a key
// regardless of which component triggered the request.
export function onAuthRequired(callback) {
  authListeners.add(callback);
  return () => authListeners.delete(callback);
}

async function getJson(path) {
  const headers = apiKey ? { "X-API-Key": apiKey } : {};
  const res = await fetch(`${API_BASE}${path}`, { headers });
  if (res.status === 401) {
    authListeners.forEach((callback) => callback());
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const err = new Error(body.detail || `Request failed: ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

export function getStats() {
  return getJson("/stats");
}

export function getThreats({
  minSeverity = "Low",
  includeDrift = false,
  user = "",
  limit = 500,
  offset = 0,
} = {}) {
  const params = new URLSearchParams({
    min_severity: minSeverity,
    limit: String(limit),
    offset: String(offset),
  });
  if (includeDrift) params.set("include_drift", "true");
  if (user) params.set("user", user);
  return getJson(`/threats?${params.toString()}`);
}

export function getUserTimeline(userId) {
  return getJson(`/user/${encodeURIComponent(userId)}/timeline`);
}

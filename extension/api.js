// Talking to the local RoleRadar server. Shared by the background worker and the popup.

const DEFAULT_SERVER = "http://127.0.0.1:8787";

export async function serverUrl() {
  const { server } = await chrome.storage.local.get("server");
  return (server || DEFAULT_SERVER).replace(/\/$/, "");
}

export async function api(path, { method = "GET", body } = {}) {
  const response = await fetch((await serverUrl()) + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data);
    throw new Error(detail || `RoleRadar answered ${response.status}`);
  }
  return data;
}

export async function currentProfile() {
  const { profile } = await chrome.storage.local.get("profile");
  return profile || null;
}

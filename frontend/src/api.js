async function getJSON(path, params = {}) {
  const query = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null),
  ).toString();
  const url = query ? `${path}?${query}` : path;
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(body.error || `request failed: ${res.status}`);
  }
  return res.json();
}

export const getSummary = (params) => getJSON("/api/summary", params);
export const getHeatmap = (days) => getJSON("/api/heatmap", { days });
export const getTrend = (days) => getJSON("/api/trend", { days });
export const getProjects = (params) => getJSON("/api/projects", params);
export const getProjectDetail = (project, params) =>
  getJSON("/api/projects/detail", { ...params, project });
export const getSyncStatus = () => getJSON("/api/sync-status");
export const getMeta = () => getJSON("/api/meta");

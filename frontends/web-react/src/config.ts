/**
 * Where the API lives. By default the dashboard is served from the same origin as the API
 * (nginx in docker compose, or the Vite dev proxy), so relative URLs just work.
 */
const apiBase: string = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '');

export function websocketUrl(): string {
  if (import.meta.env.VITE_WS_URL) return import.meta.env.VITE_WS_URL;
  const origin = apiBase ? new URL(apiBase) : window.location;
  const protocol = origin.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${origin.host}/ws/live`;
}

export async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(`${apiBase}${path}`);
  if (!response.ok) throw new Error(`GET ${path} failed with ${response.status}`);
  return (await response.json()) as T;
}

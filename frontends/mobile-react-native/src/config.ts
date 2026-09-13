import { Platform } from 'react-native';

/**
 * - Web build (served by nginx in docker compose): same origin as the page.
 * - Phone with Expo Go: set EXPO_PUBLIC_API_URL to your computer's LAN address,
 *   e.g. http://192.168.1.20:8080 (Android emulator: http://10.0.2.2:8080).
 */
export function apiBase(): string {
  const configured = process.env.EXPO_PUBLIC_API_URL;
  if (configured) return configured.replace(/\/$/, '');
  if (Platform.OS === 'web' && typeof window !== 'undefined') return window.location.origin;
  return 'http://localhost:8080';
}

export function websocketUrl(): string {
  // React Native's URL polyfill is incomplete, so derive the ws:// URL with a string replace.
  return `${apiBase().replace(/^http/, 'ws')}/ws/live`;
}

export async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(`${apiBase()}${path}`);
  if (!response.ok) throw new Error(`GET ${path} failed with ${response.status}`);
  return (await response.json()) as T;
}

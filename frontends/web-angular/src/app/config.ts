import { InjectionToken } from '@angular/core';

import type { WebSocketLike } from '@shared/index';

/** Same-origin by default: nginx (docker compose) or `ng serve --proxy-config` forwards /api and /ws. */
export function websocketUrl(): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}/ws/live`;
}

/** Lets tests replace the real WebSocket with a fake one. */
export const LIVE_SOCKET_FACTORY = new InjectionToken<(url: string) => WebSocketLike>('LIVE_SOCKET_FACTORY', {
  providedIn: 'root',
  factory: () => (url: string) => new WebSocket(url) as unknown as WebSocketLike,
});

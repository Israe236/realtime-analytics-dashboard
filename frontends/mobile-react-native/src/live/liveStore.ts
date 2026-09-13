import type { QueryClient } from '@tanstack/react-query';

import {
  type Alert,
  type ConnectionState,
  type FeedEvent,
  LiveClient,
  mergeFeed,
  type ServerMessage,
  type SnapshotMessage,
} from '@shared/index';

export const queryKeys = {
  snapshot: ['snapshot'],
  feed: ['feed'],
  activeAlerts: ['alerts', 'active'],
  alertHistory: ['alerts', 'history'],
} as const;

/** Same data flow as the React web app: WebSocket messages go into the React Query cache. */
export function applyMessage(queryClient: QueryClient, message: ServerMessage): void {
  switch (message.type) {
    case 'hello':
      queryClient.setQueryData<Alert[]>(queryKeys.activeAlerts, message.active_alerts);
      queryClient.setQueryData<FeedEvent[]>(queryKeys.feed, (current = []) =>
        mergeFeed(current, message.recent_events, 20),
      );
      break;
    case 'snapshot':
      queryClient.setQueryData<SnapshotMessage>(queryKeys.snapshot, message);
      break;
    case 'events':
      queryClient.setQueryData<FeedEvent[]>(queryKeys.feed, (current = []) => mergeFeed(current, message.items, 20));
      break;
    case 'alert': {
      const { alert } = message;
      queryClient.setQueryData<Alert[]>(queryKeys.activeAlerts, (current = []) => {
        const others = current.filter((a) => a.rule !== alert.rule);
        return alert.state === 'firing' ? [...others, alert] : others;
      });
      queryClient.setQueryData<Alert[]>(queryKeys.alertHistory, (current = []) =>
        [alert, ...current.filter((a) => a.id !== alert.id)].slice(0, 20),
      );
      break;
    }
    case 'ping':
      break;
  }
}

export interface LiveStore {
  readonly client: LiveClient;
  subscribe(listener: () => void): () => void;
  getState(): ConnectionState;
}

export function createLiveStore(queryClient: QueryClient, url: string): LiveStore {
  const listeners = new Set<() => void>();
  const client = new LiveClient({
    url,
    onMessage: (message) => applyMessage(queryClient, message),
    onState: () => listeners.forEach((listener) => listener()),
  });
  return {
    client,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    getState: () => client.connection,
  };
}

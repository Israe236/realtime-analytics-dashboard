import { useQuery, useQueryClient } from '@tanstack/react-query';
import { createContext, type ReactNode, useContext, useEffect, useState, useSyncExternalStore } from 'react';
import { AppState } from 'react-native';

import type { Alert, ConnectionState, FeedEvent, SnapshotMessage } from '@shared/index';

import { fetchJson, websocketUrl } from '../config';
import { createLiveStore, type LiveStore, queryKeys } from './liveStore';

const LiveStoreContext = createContext<LiveStore | null>(null);

export function LiveProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [store] = useState(() => createLiveStore(queryClient, websocketUrl()));

  useEffect(() => {
    store.client.start();
    // Mobile OSes suspend background apps and silently kill their sockets. When the app
    // returns to the foreground, reconnect immediately instead of waiting for the heartbeat.
    const subscription = AppState.addEventListener('change', (state) => {
      if (state === 'active') store.client.reconnectNow();
    });
    return () => {
      subscription.remove();
      store.client.stop();
    };
  }, [store]);

  return <LiveStoreContext.Provider value={store}>{children}</LiveStoreContext.Provider>;
}

export function useConnectionState(): ConnectionState {
  const store = useContext(LiveStoreContext);
  if (!store) throw new Error('useConnectionState must be used inside <LiveProvider>');
  return useSyncExternalStore(store.subscribe, store.getState);
}

export function useSnapshot<T>(select: (snapshot: SnapshotMessage) => T) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: queryKeys.snapshot,
    queryFn: async () => {
      const fetched = await fetchJson<SnapshotMessage>('/api/metrics/snapshot');
      const live = queryClient.getQueryData<SnapshotMessage>(queryKeys.snapshot);
      return live && live.generated_at > fetched.generated_at ? live : fetched;
    },
    staleTime: Infinity,
    select,
  });
}

export function useFeed() {
  return useQuery<FeedEvent[]>({ queryKey: queryKeys.feed, queryFn: () => Promise.resolve([]), initialData: [], staleTime: Infinity });
}

export function useActiveAlerts() {
  return useQuery<Alert[]>({
    queryKey: queryKeys.activeAlerts,
    queryFn: () => Promise.resolve([]),
    initialData: [],
    staleTime: Infinity,
  });
}

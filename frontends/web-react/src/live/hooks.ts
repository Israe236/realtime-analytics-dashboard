import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useContext, useSyncExternalStore } from 'react';

import type { Alert, ConnectionState, FeedEvent, SnapshotMessage } from '@shared/index';

import { fetchJson } from '../config';
import { LiveStoreContext } from './context';
import { queryKeys } from './liveStore';

export function useConnectionState(): ConnectionState {
  const store = useContext(LiveStoreContext);
  if (!store) throw new Error('useConnectionState must be used inside <LiveProvider>');
  return useSyncExternalStore(store.subscribe, store.getState);
}

/**
 * Subscribe to one slice of the latest snapshot. The first value comes from REST (so the
 * page is not empty while the WebSocket connects); after that the WebSocket keeps the
 * cache fresh, so the query never refetches (`staleTime: Infinity`).
 */
export function useSnapshot<T>(select: (snapshot: SnapshotMessage) => T) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: queryKeys.snapshot,
    queryFn: async () => {
      const fetched = await fetchJson<SnapshotMessage>('/api/metrics/snapshot');
      const live = queryClient.getQueryData<SnapshotMessage>(queryKeys.snapshot);
      // The WebSocket may have delivered a newer snapshot while the request was in flight.
      return live && live.generated_at > fetched.generated_at ? live : fetched;
    },
    staleTime: Infinity,
    select,
  });
}

export function useFeed() {
  return useQuery<FeedEvent[]>({
    queryKey: queryKeys.feed,
    queryFn: () => Promise.resolve([]),
    initialData: [],
    staleTime: Infinity,
  });
}

export function useActiveAlerts() {
  return useQuery<Alert[]>({
    queryKey: queryKeys.activeAlerts,
    queryFn: () => Promise.resolve([]),
    initialData: [],
    staleTime: Infinity,
  });
}

export function useAlertHistory() {
  return useQuery({
    queryKey: queryKeys.alertHistory,
    queryFn: () => fetchJson<Alert[]>('/api/alerts?limit=20'),
    staleTime: Infinity,
  });
}

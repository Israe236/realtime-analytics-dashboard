import { useQueryClient } from '@tanstack/react-query';
import { type ReactNode, useEffect, useState } from 'react';

import { websocketUrl } from '../config';
import { LiveStoreContext } from './context';
import { createLiveStore } from './liveStore';

/** Owns the single WebSocket connection for the whole app. */
export function LiveProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [store] = useState(() => createLiveStore(queryClient, websocketUrl()));

  useEffect(() => {
    store.client.start();
    // Coming back online or to a hidden tab: don't wait for the backoff timer.
    const reconnect = () => store.client.reconnectNow();
    const onVisible = () => document.visibilityState === 'visible' && reconnect();
    window.addEventListener('online', reconnect);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      window.removeEventListener('online', reconnect);
      document.removeEventListener('visibilitychange', onVisible);
      store.client.stop();
    };
  }, [store]);

  return <LiveStoreContext.Provider value={store}>{children}</LiveStoreContext.Provider>;
}

import { createContext } from 'react';

import type { LiveStore } from './liveStore';

export const LiveStoreContext = createContext<LiveStore | null>(null);

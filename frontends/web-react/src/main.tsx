import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import App from './App.tsx';
import { LiveProvider } from './live/LiveProvider.tsx';
import './index.css';

const queryClient = new QueryClient({
  defaultOptions: {
    // Live data arrives over the WebSocket; REST is only used for the first paint.
    queries: { refetchOnWindowFocus: false, retry: 2 },
  },
});

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <LiveProvider>
        <App />
      </LiveProvider>
    </QueryClientProvider>
  </StrictMode>,
);

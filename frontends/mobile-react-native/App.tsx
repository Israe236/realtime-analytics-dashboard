import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StatusBar } from 'expo-status-bar';

import { Dashboard } from './src/components/Dashboard';
import { LiveProvider } from './src/live/LiveProvider';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 2, refetchOnWindowFocus: false } },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <LiveProvider>
        <StatusBar style="light" />
        <Dashboard />
      </LiveProvider>
    </QueryClientProvider>
  );
}

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it } from 'vitest';

import { type ConnectionState, formatMad, type LiveClient, type Kpis, type SnapshotMessage } from '@shared/index';

import { LiveStoreContext } from '../live/context';
import { queryKeys } from '../live/liveStore';
import { ConnectionBadge } from './ConnectionBadge';
import { KpiCards } from './KpiCards';

afterEach(cleanup);

function withConnection(state: ConnectionState, children: ReactNode) {
  const store = { client: {} as LiveClient, subscribe: () => () => {}, getState: () => state };
  return <LiveStoreContext.Provider value={store}>{children}</LiveStoreContext.Provider>;
}

describe('ConnectionBadge', () => {
  it('shows live when the connection is open', () => {
    render(withConnection({ status: 'open', attempt: 0, nextRetryAt: null, lastMessageAt: Date.now() }, <ConnectionBadge />));
    expect(screen.getByRole('status').textContent).toContain('Live');
  });

  it('shows the reconnect countdown and attempt number', () => {
    const state: ConnectionState = { status: 'reconnecting', attempt: 3, nextRetryAt: Date.now() + 2_500, lastMessageAt: null };
    render(withConnection(state, <ConnectionBadge />));
    expect(screen.getByRole('status').textContent).toMatch(/Reconnecting in 3s \(attempt 3\)/);
  });
});

describe('KpiCards', () => {
  const kpis = (revenue: number, cancellation: number): Kpis => ({
    revenue_mad: revenue,
    orders: 100,
    paid_orders: 80,
    shipped_orders: 70,
    cancelled_orders: 10,
    average_order_value_mad: revenue / 80,
    cancellation_rate: cancellation,
  });

  it('renders values and colours deltas by whether the change is good', () => {
    const client = new QueryClient();
    client.setQueryData(queryKeys.snapshot, {
      type: 'snapshot',
      window_minutes: 60,
      kpis: kpis(12_000, 0.1),
      previous_kpis: kpis(10_000, 0.05),
    } as SnapshotMessage);

    const { container } = render(
      <QueryClientProvider client={client}>
        <KpiCards />
      </QueryClientProvider>,
    );

    expect(container.textContent).toContain(formatMad(12_000));
    const deltas = [...container.querySelectorAll('.kpi__delta')];
    // Revenue went up 20 %: good. Cancellation rate doubled: bad, even though it "went up".
    expect(deltas[0]?.className).toContain('kpi__delta--good');
    expect(deltas[0]?.textContent).toContain('20.0 %');
    expect(deltas[3]?.className).toContain('kpi__delta--bad');
  });
});

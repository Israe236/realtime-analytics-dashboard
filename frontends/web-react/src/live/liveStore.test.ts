import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';

import type { Alert, FeedEvent, SnapshotMessage } from '@shared/index';

import { applyMessage, queryKeys } from './liveStore';

function feedEvent(id: string): FeedEvent {
  return {
    event_id: id,
    order_id: 'o1',
    event_type: 'order_paid',
    occurred_at: '2026-09-14T10:00:00Z',
    amount_mad: 120,
    category: 'books',
    city: 'Rabat',
    payment_method: 'card',
  };
}

function alert(state: Alert['state']): Alert {
  return {
    id: 7,
    rule: 'revenue_drop',
    severity: 'critical',
    state,
    message: 'Revenue down 61%',
    value: 0.61,
    threshold: 0.5,
    fired_at: '2026-09-14T10:00:00Z',
    resolved_at: state === 'resolved' ? '2026-09-14T10:06:00Z' : null,
  };
}

describe('applyMessage', () => {
  it('stores hello state and merges the feed newest first without duplicates', () => {
    const client = new QueryClient();
    applyMessage(client, {
      type: 'hello',
      server_time: '2026-09-14T10:00:00Z',
      active_alerts: [alert('firing')],
      recent_events: [feedEvent('a'), feedEvent('b')],
    });
    applyMessage(client, { type: 'events', items: [feedEvent('b'), feedEvent('c')] });

    expect(client.getQueryData<Alert[]>(queryKeys.activeAlerts)?.map((a) => a.rule)).toEqual(['revenue_drop']);
    expect(client.getQueryData<FeedEvent[]>(queryKeys.feed)?.map((e) => e.event_id)).toEqual(['c', 'b', 'a']);
  });

  it('replaces the snapshot wholesale', () => {
    const client = new QueryClient();
    const snapshot = { type: 'snapshot', through_seq: 42 } as SnapshotMessage;
    applyMessage(client, snapshot);
    expect(client.getQueryData(queryKeys.snapshot)).toBe(snapshot);
  });

  it('moves a resolved alert from active to history', () => {
    const client = new QueryClient();
    applyMessage(client, { type: 'alert', alert: alert('firing') });
    expect(client.getQueryData<Alert[]>(queryKeys.activeAlerts)).toHaveLength(1);

    applyMessage(client, { type: 'alert', alert: alert('resolved') });
    expect(client.getQueryData<Alert[]>(queryKeys.activeAlerts)).toEqual([]);
    const history = client.getQueryData<Alert[]>(queryKeys.alertHistory) ?? [];
    expect(history.map((a) => a.state)).toEqual(['resolved']); // same id: replaced, not duplicated
  });

  it('ignores pings', () => {
    const client = new QueryClient();
    applyMessage(client, { type: 'ping', server_time: '2026-09-14T10:00:00Z' });
    expect(client.getQueryCache().getAll()).toHaveLength(0);
  });
});

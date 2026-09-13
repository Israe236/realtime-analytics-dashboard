import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import type { Alert, SnapshotMessage, WebSocketLike } from '@shared/index';

import { LIVE_SOCKET_FACTORY } from './config';
import { LiveDashboardService } from './live-dashboard.service';

class FakeSocket implements WebSocketLike {
  onopen: WebSocketLike['onopen'] = null;
  onmessage: WebSocketLike['onmessage'] = null;
  onclose: WebSocketLike['onclose'] = null;
  onerror: WebSocketLike['onerror'] = null;
  close(): void {}
  emit(message: object): void {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
}

function alert(state: Alert['state']): Alert {
  return {
    id: 1,
    rule: 'cancellation_rate',
    severity: 'warning',
    state,
    message: 'Cancellation rate 40%',
    value: 0.4,
    threshold: 0.25,
    fired_at: '2026-09-13T10:00:00Z',
    resolved_at: state === 'resolved' ? '2026-09-13T10:05:00Z' : null,
  };
}

function snapshot(orders: number): SnapshotMessage {
  const kpis = {
    revenue_mad: 1000,
    orders,
    paid_orders: 1,
    shipped_orders: 0,
    cancelled_orders: 0,
    average_order_value_mad: 1000,
    cancellation_rate: 0,
  };
  return {
    type: 'snapshot',
    generated_at: new Date().toISOString(),
    through_seq: orders,
    window_minutes: 60,
    kpis,
    previous_kpis: kpis,
    revenue_per_minute: [],
    revenue_per_hour: [],
    orders_by_status: [],
    top_categories: [{ name: 'books', revenue_mad: 1000, orders: 3 }],
    top_cities: [],
    payment_methods: [],
    pipeline: { events_per_second: 1, dead_letter_rate: null, freshness_lag_ms: 10, queued_events: 0, connected_clients: 1 },
  };
}

describe('LiveDashboardService', () => {
  let socket: FakeSocket;
  let service: LiveDashboardService;

  beforeEach(() => {
    socket = new FakeSocket();
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), { provide: LIVE_SOCKET_FACTORY, useValue: () => socket }],
    });
    service = TestBed.inject(LiveDashboardService);
  });

  it('is live after hello and exposes the open alerts', () => {
    expect(service.connection().status).toBe('connecting');
    socket.emit({ type: 'hello', server_time: '2026-09-13T10:00:00Z', active_alerts: [alert('firing')], recent_events: [] });
    expect(service.connection().status).toBe('open');
    expect(service.activeAlerts().map((a) => a.rule)).toEqual(['cancellation_rate']);
  });

  it('removes a resolved alert and keeps it in the history', () => {
    socket.emit({ type: 'alert', alert: alert('firing') });
    socket.emit({ type: 'alert', alert: alert('resolved') });
    expect(service.activeAlerts()).toEqual([]);
    expect(service.alertHistory().map((a) => a.state)).toEqual(['resolved']);
  });

  it('keeps unchanged slices referentially stable across snapshots', () => {
    socket.emit(snapshot(5));
    const categories = service.categories();
    socket.emit(snapshot(6)); // new object, same categories
    expect(service.kpis()?.current.orders).toBe(6);
    expect(service.categories()).toBe(categories);
  });
});

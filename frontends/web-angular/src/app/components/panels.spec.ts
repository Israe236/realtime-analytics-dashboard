import { signal, type Type } from '@angular/core';
import { type ComponentFixture, TestBed } from '@angular/core/testing';

import { type Alert, type ConnectionState, type FeedEvent, formatMad, type Kpis } from '@shared/index';

import { LiveDashboardService } from '../live-dashboard.service';
import { AlertBanner, ConnectionBadge, KpiCards, LiveFeed } from './panels';

/** Only the signals the panels read; tests set them directly. */
function fakeLiveService() {
  return {
    connection: signal<ConnectionState>({ status: 'idle', attempt: 0, nextRetryAt: null, lastMessageAt: null }),
    kpis: signal<{ current: Kpis; previous: Kpis; windowMinutes: number } | null>(null),
    activeAlerts: signal<Alert[]>([]),
    alertHistory: signal<Alert[]>([]),
    feed: signal<FeedEvent[]>([]),
    pipeline: signal(null),
    cities: signal([]),
    ordersByStatus: signal([]),
    paymentMethods: signal([]),
  };
}

type FakeLive = ReturnType<typeof fakeLiveService>;

function mount<T>(component: Type<T>): { fixture: ComponentFixture<T>; live: FakeLive; text: () => string } {
  const live = fakeLiveService();
  TestBed.configureTestingModule({ providers: [{ provide: LiveDashboardService, useValue: live }] });
  const fixture = TestBed.createComponent(component);
  fixture.detectChanges();
  const text = () => (fixture.nativeElement as HTMLElement).textContent ?? '';
  return { fixture, live, text };
}

function kpis(revenue: number, cancellationRate: number): Kpis {
  return {
    revenue_mad: revenue,
    orders: 100,
    paid_orders: 80,
    shipped_orders: 70,
    cancelled_orders: 10,
    average_order_value_mad: revenue / 80,
    cancellation_rate: cancellationRate,
  };
}

function feedEvent(id: number): FeedEvent {
  return {
    event_id: `e-${id}`,
    order_id: `o-${id}`,
    event_type: 'order_paid',
    occurred_at: '2026-09-15T10:00:00Z',
    amount_mad: 250,
    category: 'fashion',
    city: 'Casablanca',
    payment_method: 'card',
  };
}

describe('ConnectionBadge', () => {
  it('shows Live when open and a countdown while reconnecting', () => {
    const { fixture, live, text } = mount(ConnectionBadge);

    live.connection.set({ status: 'open', attempt: 0, nextRetryAt: null, lastMessageAt: Date.now() });
    fixture.detectChanges();
    expect(text()).toContain('Live');

    live.connection.set({ status: 'reconnecting', attempt: 2, nextRetryAt: Date.now() + 3_500, lastMessageAt: null });
    fixture.detectChanges();
    expect(text()).toMatch(/Reconnecting in 4s \(attempt 2\)/);
  });
});

describe('KpiCards', () => {
  it('renders nothing until data arrives, then values with meaningful delta colours', () => {
    const { fixture, live, text } = mount(KpiCards);
    expect((fixture.nativeElement as HTMLElement).querySelectorAll('.kpi')).toHaveLength(0);

    live.kpis.set({ current: kpis(12_000, 0.1), previous: kpis(10_000, 0.05), windowMinutes: 60 });
    fixture.detectChanges();

    expect(text()).toContain(formatMad(12_000));
    const deltas = (fixture.nativeElement as HTMLElement).querySelectorAll('.kpi__delta');
    expect(deltas[0]?.classList).toContain('kpi__delta--good'); // revenue up
    expect(deltas[0]?.textContent).toContain('20.0 %');
    expect(deltas[3]?.classList).toContain('kpi__delta--bad'); // cancellation rate up
  });
});

describe('AlertBanner', () => {
  it('is hidden without alerts and lists each firing alert', () => {
    const { fixture, live, text } = mount(AlertBanner);
    expect((fixture.nativeElement as HTMLElement).querySelector('[role="alert"]')).toBeNull();

    live.activeAlerts.set([
      {
        id: 1,
        rule: 'cancellation_rate',
        severity: 'warning',
        state: 'firing',
        message: 'Cancellation rate 32% over the last 5 min',
        value: 0.32,
        threshold: 0.25,
        fired_at: '2026-09-15T10:00:00Z',
        resolved_at: null,
      },
    ]);
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelectorAll('[role="alert"]')).toHaveLength(1);
    expect(text()).toContain('High cancellation rate');
    expect(text()).toContain('Cancellation rate 32%');
  });
});

describe('LiveFeed', () => {
  it('caps the list at 25 rows and updates when events arrive', () => {
    const { fixture, live, text } = mount(LiveFeed);
    expect(text()).toContain('Waiting for events');

    live.feed.set(Array.from({ length: 40 }, (_, i) => feedEvent(i)));
    fixture.detectChanges();
    const rows = () => (fixture.nativeElement as HTMLElement).querySelectorAll('.feed__row');
    expect(rows()).toHaveLength(25);

    const firstRow = rows()[0];
    live.feed.update((events) => [feedEvent(99), ...events]);
    fixture.detectChanges();
    expect(rows()).toHaveLength(25);
    // Existing rows are kept (tracked by event_id), so the old first row is now second.
    expect(rows()[1]).toBe(firstRow);
  });
});

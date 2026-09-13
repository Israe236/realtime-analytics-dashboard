import { HttpClient } from '@angular/common/http';
import { computed, DestroyRef, inject, Injectable, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import {
  type Alert,
  type ConnectionState,
  type FeedEvent,
  LiveClient,
  mergeFeed,
  type ServerMessage,
  type SnapshotMessage,
} from '@shared/index';

import { LIVE_SOCKET_FACTORY, websocketUrl } from './config';

/**
 * Snapshots arrive up to 4× per second as brand-new objects. Comparing slices by value
 * means a computed signal only notifies its readers (and triggers change detection for
 * them) when that slice actually changed.
 */
function sameValue<T>(a: T, b: T): boolean {
  return a === b || JSON.stringify(a) === JSON.stringify(b);
}

@Injectable({ providedIn: 'root' })
export class LiveDashboardService {
  private readonly http = inject(HttpClient);
  private readonly client: LiveClient;
  private readonly snapshot = signal<SnapshotMessage | null>(null);

  readonly connection = signal<ConnectionState>({ status: 'idle', attempt: 0, nextRetryAt: null, lastMessageAt: null });
  readonly feed = signal<FeedEvent[]>([]);
  readonly activeAlerts = signal<Alert[]>([]);
  readonly alertHistory = signal<Alert[]>([]);

  readonly kpis = computed(
    () => {
      const s = this.snapshot();
      return s ? { current: s.kpis, previous: s.previous_kpis, windowMinutes: s.window_minutes } : null;
    },
    { equal: sameValue },
  );
  readonly revenuePerMinute = computed(() => this.snapshot()?.revenue_per_minute ?? [], { equal: sameValue });
  readonly revenuePerHour = computed(() => this.snapshot()?.revenue_per_hour ?? [], { equal: sameValue });
  readonly categories = computed(() => this.snapshot()?.top_categories ?? [], { equal: sameValue });
  readonly cities = computed(() => this.snapshot()?.top_cities ?? [], { equal: sameValue });
  readonly ordersByStatus = computed(() => this.snapshot()?.orders_by_status ?? [], { equal: sameValue });
  readonly paymentMethods = computed(() => this.snapshot()?.payment_methods ?? [], { equal: sameValue });
  readonly pipeline = computed(() => this.snapshot()?.pipeline ?? null, { equal: sameValue });

  constructor() {
    this.client = new LiveClient({
      url: websocketUrl(),
      createSocket: inject(LIVE_SOCKET_FACTORY),
      onMessage: (message) => this.apply(message),
      onState: (state) => this.connection.set(state),
    });
    this.client.start();

    const reconnect = () => this.client.reconnectNow();
    window.addEventListener('online', reconnect);
    inject(DestroyRef).onDestroy(() => {
      window.removeEventListener('online', reconnect);
      this.client.stop();
    });

    void this.loadInitialData();
  }

  /** REST fills the page while the WebSocket connects; newer live data always wins. */
  private async loadInitialData(): Promise<void> {
    try {
      const [snapshot, history] = await Promise.all([
        firstValueFrom(this.http.get<SnapshotMessage>('/api/metrics/snapshot')),
        firstValueFrom(this.http.get<Alert[]>('/api/alerts?limit=20')),
      ]);
      const live = this.snapshot();
      if (!live || live.generated_at < snapshot.generated_at) this.snapshot.set(snapshot);
      if (this.alertHistory().length === 0) this.alertHistory.set(history);
    } catch (error) {
      console.warn('initial REST load failed; waiting for the WebSocket', error);
    }
  }

  apply(message: ServerMessage): void {
    switch (message.type) {
      case 'hello':
        this.activeAlerts.set(message.active_alerts);
        this.feed.update((current) => mergeFeed(current, message.recent_events));
        break;
      case 'snapshot':
        this.snapshot.set(message);
        break;
      case 'events':
        this.feed.update((current) => mergeFeed(current, message.items));
        break;
      case 'alert': {
        const { alert } = message;
        this.activeAlerts.update((current) => {
          const others = current.filter((a) => a.rule !== alert.rule);
          return alert.state === 'firing' ? [...others, alert] : others;
        });
        this.alertHistory.update((current) => [alert, ...current.filter((a) => a.id !== alert.id)].slice(0, 20));
        break;
      }
      case 'ping':
        break;
    }
  }
}

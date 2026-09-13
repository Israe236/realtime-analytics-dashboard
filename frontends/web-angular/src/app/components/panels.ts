import { ChangeDetectionStrategy, Component, computed, DestroyRef, inject, signal } from '@angular/core';

import {
  EVENT_LABELS,
  formatClock,
  formatCount,
  formatMad,
  formatPercent,
  KPI_DEFINITIONS,
  relativeChange,
  ruleLabel,
} from '@shared/index';

import { LiveDashboardService } from '../live-dashboard.service';

@Component({
  selector: 'app-connection-badge',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <span class="badge badge--{{ state().status }}" role="status" aria-live="polite">
      <span class="badge__dot" aria-hidden="true"></span>{{ label() }}
    </span>
  `,
})
export class ConnectionBadge {
  protected readonly state = inject(LiveDashboardService).connection;
  private readonly now = signal(Date.now());

  protected readonly label = computed(() => {
    const { status, attempt, nextRetryAt } = this.state();
    switch (status) {
      case 'open':
        return 'Live';
      case 'reconnecting': {
        const seconds = nextRetryAt ? Math.max(0, Math.ceil((nextRetryAt - this.now()) / 1000)) : 0;
        return seconds > 0 ? `Reconnecting in ${seconds}s (attempt ${attempt})` : `Reconnecting… (attempt ${attempt})`;
      }
      case 'stopped':
        return 'Disconnected';
      default:
        return 'Connecting…';
    }
  });

  constructor() {
    const timer = setInterval(() => this.now.set(Date.now()), 250);
    inject(DestroyRef).onDestroy(() => clearInterval(timer));
  }
}

@Component({
  selector: 'app-pipeline-stats',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <dl class="pipeline">
      @for (item of items(); track item.label) {
        <div>
          <dt>{{ item.label }}</dt>
          <dd class="num">{{ item.value }}</dd>
        </div>
      }
    </dl>
  `,
})
export class PipelineStats {
  private readonly pipeline = inject(LiveDashboardService).pipeline;
  protected readonly items = computed(() => {
    const p = this.pipeline();
    const lag = p?.freshness_lag_ms ?? null;
    return [
      { label: 'Ingest rate', value: p ? `${p.events_per_second.toFixed(0)} ev/s` : '—' },
      { label: 'Data freshness', value: lag === null ? '—' : lag < 1000 ? `${Math.round(lag)} ms` : `${(lag / 1000).toFixed(1)} s` },
      { label: 'Rejected', value: formatPercent(p?.dead_letter_rate) },
      { label: 'Queue', value: formatCount(p?.queued_events) },
      { label: 'Viewers', value: formatCount(p?.connected_clients) },
    ];
  });
}

@Component({
  selector: 'app-kpi-cards',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="kpis" aria-label="Key metrics">
      @for (card of cards(); track card.key) {
        <article class="card kpi">
          <h3 class="kpi__label">{{ card.label }}</h3>
          <p class="kpi__value">{{ card.value }}</p>
          <p class="kpi__delta" [class.kpi__delta--good]="card.good === true" [class.kpi__delta--bad]="card.good === false">
            {{ card.delta }}<span class="muted"> vs previous period</span>
          </p>
        </article>
      }
    </section>
  `,
})
export class KpiCards {
  private readonly kpis = inject(LiveDashboardService).kpis;
  protected readonly cards = computed(() => {
    const data = this.kpis();
    if (!data) return [];
    return KPI_DEFINITIONS.map((definition) => {
      const value = data.current[definition.key];
      const change = relativeChange(value, data.previous[definition.key]);
      const arrow = change === null ? '' : change > 0 ? '▲' : change < 0 ? '▼' : '■';
      return {
        key: definition.key,
        label: definition.label,
        value: definition.format(value),
        delta: change === null ? 'no baseline' : `${arrow} ${Math.abs(change * 100).toFixed(1)} %`,
        good: change === null || change === 0 ? null : change > 0 === definition.higherIsBetter,
      };
    });
  });
}

@Component({
  selector: 'app-top-cities',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="card">
      <header class="card__header"><h2>Top cities</h2></header>
      @if (top().length === 0) {
        <div class="placeholder">No sales yet</div>
      } @else {
        <ol class="ranked">
          @for (city of top(); track city.name) {
            <li>
              <div class="ranked__row">
                <span>{{ city.name }}</span><span class="num">{{ formatMad(city.revenue_mad) }}</span>
              </div>
              <div class="ranked__bar" [style.width.%]="(city.revenue_mad / max()) * 100"></div>
              <div class="muted small">{{ formatCount(city.orders) }} orders</div>
            </li>
          }
        </ol>
      }
    </section>
  `,
})
export class TopCities {
  private readonly cities = inject(LiveDashboardService).cities;
  protected readonly top = computed(() => this.cities().slice(0, 6));
  protected readonly max = computed(() => Math.max(1, ...this.top().map((c) => c.revenue_mad)));
  protected readonly formatMad = formatMad;
  protected readonly formatCount = formatCount;
}

@Component({
  selector: 'app-status-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="card">
      <header class="card__header"><h2>Orders by status</h2></header>
      <ul class="status">
        @for (item of status(); track item.status) {
          <li class="status__item status__item--{{ item.status }}">
            <span>{{ labels[item.status] }}</span>
            <span class="status__track"><span class="status__fill" [style.width.%]="(item.count / max()) * 100"></span></span>
            <span class="num">{{ formatCount(item.count) }}</span>
          </li>
        }
      </ul>
      <h3 class="subheading">Payment methods</h3>
      <ul class="chips">
        @for (method of payments(); track method.name) {
          <li class="chip">{{ method.name.replaceAll('_', ' ') }} <span class="num">{{ formatMad(method.revenue_mad) }}</span></li>
        }
      </ul>
    </section>
  `,
})
export class StatusPanel {
  private readonly live = inject(LiveDashboardService);
  protected readonly status = this.live.ordersByStatus;
  protected readonly payments = this.live.paymentMethods;
  protected readonly max = computed(() => Math.max(1, ...this.status().map((s) => s.count)));
  protected readonly labels = EVENT_LABELS;
  protected readonly formatMad = formatMad;
  protected readonly formatCount = formatCount;
}

@Component({
  selector: 'app-live-feed',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="card feed">
      <header class="card__header"><h2>Live events</h2><span class="muted small">sampled</span></header>
      @if (events().length === 0) {
        <div class="placeholder">Waiting for events…</div>
      } @else {
        <ul class="feed__list">
          <!-- track by event_id: existing rows are kept, only new ones are created (and fade in) -->
          @for (event of events(); track event.event_id) {
            <li class="feed__row">
              <span class="feed__time num">{{ formatClock(event.occurred_at) }}</span>
              <span class="pill pill--{{ event.event_type }}">{{ labels[event.event_type] }}</span>
              <span class="feed__where">{{ event.category }} · {{ event.city }}</span>
              <span class="num">{{ formatMad(event.amount_mad, true) }}</span>
            </li>
          }
        </ul>
      }
    </section>
  `,
})
export class LiveFeed {
  private readonly feed = inject(LiveDashboardService).feed;
  protected readonly events = computed(() => this.feed().slice(0, 25));
  protected readonly labels = EVENT_LABELS;
  protected readonly formatClock = formatClock;
  protected readonly formatMad = formatMad;
}

@Component({
  selector: 'app-alert-banner',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (active().length > 0) {
      <section class="alerts" aria-live="assertive">
        @for (alert of active(); track alert.rule) {
          <div class="alert alert--{{ alert.severity }}" role="alert">
            <strong>{{ ruleLabel(alert.rule) }}</strong>
            <span>{{ alert.message }}</span>
            <span class="muted small">since {{ formatClock(alert.fired_at) }}</span>
          </div>
        }
      </section>
    }
  `,
})
export class AlertBanner {
  protected readonly active = inject(LiveDashboardService).activeAlerts;
  protected readonly ruleLabel = ruleLabel;
  protected readonly formatClock = formatClock;
}

@Component({
  selector: 'app-alert-history',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="card">
      <header class="card__header"><h2>Alert history</h2></header>
      @if (history().length === 0) {
        <div class="placeholder">No alerts so far</div>
      } @else {
        <ul class="history">
          @for (alert of history(); track alert.id) {
            <li class="history__row">
              <span class="pill pill--{{ alert.state === 'firing' ? alert.severity : 'resolved' }}">{{ alert.state }}</span>
              <span>{{ ruleLabel(alert.rule) }}</span>
              <span class="muted small num">
                {{ formatClock(alert.fired_at) }}{{ alert.resolved_at ? ' → ' + formatClock(alert.resolved_at) : '' }}
              </span>
            </li>
          }
        </ul>
      }
    </section>
  `,
})
export class AlertHistory {
  private readonly all = inject(LiveDashboardService).alertHistory;
  protected readonly history = computed(() => this.all().slice(0, 8));
  protected readonly ruleLabel = ruleLabel;
  protected readonly formatClock = formatClock;
}

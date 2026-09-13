import {
  afterNextRender,
  ChangeDetectionStrategy,
  Component,
  computed,
  DestroyRef,
  effect,
  type ElementRef,
  inject,
  signal,
  viewChild,
} from '@angular/core';

import { formatCount, formatMad, formatMinute, type SeriesPoint, theme } from '@shared/index';

import { Chart } from '../charts';
import { LiveDashboardService } from '../live-dashboard.service';

type Range = 'minute' | 'hour';

@Component({
  selector: 'app-revenue-chart',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="card">
      <header class="card__header">
        <h2>Revenue &amp; orders</h2>
        <div class="segmented" role="tablist" aria-label="Time range">
          @for (option of ranges; track option.value) {
            <button
              role="tab"
              [class.active]="range() === option.value"
              [attr.aria-selected]="range() === option.value"
              (click)="range.set(option.value)"
            >
              {{ option.label }}
            </button>
          }
        </div>
      </header>
      <div class="chart-canvas"><canvas #canvas aria-label="Revenue and orders over time"></canvas></div>
    </section>
  `,
})
export class RevenueChart {
  private readonly live = inject(LiveDashboardService);
  private readonly canvas = viewChild.required<ElementRef<HTMLCanvasElement>>('canvas');
  private chart: Chart<'line'> | null = null;

  protected readonly ranges = [
    { value: 'minute' as const, label: 'Last 60 min' },
    { value: 'hour' as const, label: 'Last 24 h' },
  ];
  protected readonly range = signal<Range>('minute');
  private readonly points = computed(() =>
    this.range() === 'minute' ? this.live.revenuePerMinute() : this.live.revenuePerHour(),
  );

  constructor() {
    afterNextRender(() => {
      this.chart = new Chart(this.canvas().nativeElement, {
        type: 'line',
        data: {
          labels: [],
          datasets: [
            {
              label: 'Revenue',
              data: [],
              yAxisID: 'mad',
              borderColor: theme.accent,
              backgroundColor: 'rgba(108, 140, 255, 0.18)',
              fill: true,
              tension: 0.35,
              pointRadius: 0,
              borderWidth: 2,
            },
            { label: 'Orders', data: [], yAxisID: 'orders', borderColor: theme.accent2, pointRadius: 0, tension: 0.35 },
            { label: 'Cancelled', data: [], yAxisID: 'orders', borderColor: theme.bad, pointRadius: 0, tension: 0.35 },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: 'index', intersect: false },
          scales: {
            x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } },
            mad: { position: 'left', grid: { color: theme.grid }, ticks: { callback: (v) => formatMad(Number(v)) } },
            orders: { position: 'right', grid: { display: false } },
          },
          plugins: {
            tooltip: {
              callbacks: {
                label: (item) =>
                  `${item.dataset.label}: ${item.datasetIndex === 0 ? formatMad(item.parsed.y) : formatCount(item.parsed.y)}`,
              },
            },
          },
        },
      });
      this.render(this.points());
    });

    // Runs whenever the selected series changes by value; the chart is updated in place.
    effect(() => {
      const points = this.points();
      if (this.chart) this.render(points);
    });

    inject(DestroyRef).onDestroy(() => this.chart?.destroy());
  }

  private render(points: readonly SeriesPoint[]): void {
    const chart = this.chart;
    if (!chart) return;
    chart.data.labels = points.map((p) => formatMinute(p.bucket));
    const [revenue, orders, cancelled] = chart.data.datasets;
    revenue!.data = points.map((p) => p.revenue_mad);
    orders!.data = points.map((p) => p.orders);
    cancelled!.data = points.map((p) => p.cancelled);
    chart.update('none'); // 'none' = no animation: new data appears without a redraw flash
  }
}

import {
  afterNextRender,
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  effect,
  type ElementRef,
  inject,
  viewChild,
} from '@angular/core';

import { CATEGORY_PALETTE, formatMad, type RankedItem, theme } from '@shared/index';

import { Chart } from '../charts';
import { LiveDashboardService } from '../live-dashboard.service';

@Component({
  selector: 'app-category-chart',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="card">
      <header class="card__header"><h2>Revenue by category</h2></header>
      <div class="chart-canvas chart-canvas--short">
        <canvas #canvas aria-label="Revenue by category"></canvas>
      </div>
    </section>
  `,
})
export class CategoryChart {
  private readonly live = inject(LiveDashboardService);
  private readonly canvas = viewChild.required<ElementRef<HTMLCanvasElement>>('canvas');
  private chart: Chart<'bar'> | null = null;

  constructor() {
    afterNextRender(() => {
      this.chart = new Chart(this.canvas().nativeElement, {
        type: 'bar',
        data: { labels: [], datasets: [{ label: 'Revenue', data: [], borderRadius: 4 }] },
        options: {
          indexAxis: 'y',
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            x: { display: false },
            y: { grid: { display: false }, ticks: { color: theme.muted } },
          },
          plugins: { tooltip: { callbacks: { label: (item) => formatMad(item.parsed.x) } } },
        },
      });
      this.render(this.live.categories());
    });
    effect(() => {
      const items = this.live.categories();
      if (this.chart) this.render(items);
    });
    inject(DestroyRef).onDestroy(() => this.chart?.destroy());
  }

  private render(items: readonly RankedItem[]): void {
    const chart = this.chart;
    if (!chart) return;
    chart.data.labels = items.map((i) => i.name);
    const dataset = chart.data.datasets[0]!;
    dataset.data = items.map((i) => i.revenue_mad);
    dataset.backgroundColor = items.map((_, index) => CATEGORY_PALETTE[index % CATEGORY_PALETTE.length]!);
    chart.update('none');
  }
}

import { ChangeDetectionStrategy, Component } from '@angular/core';

import { CategoryChart } from './components/category-chart';
import {
  AlertBanner,
  AlertHistory,
  ConnectionBadge,
  KpiCards,
  LiveFeed,
  PipelineStats,
  StatusPanel,
  TopCities,
} from './components/panels';
import { RevenueChart } from './components/revenue-chart';

@Component({
  selector: 'app-root',
  imports: [
    AlertBanner,
    AlertHistory,
    CategoryChart,
    ConnectionBadge,
    KpiCards,
    LiveFeed,
    PipelineStats,
    RevenueChart,
    StatusPanel,
    TopCities,
  ],
  templateUrl: './app.html',
  styleUrl: './app.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class App {}

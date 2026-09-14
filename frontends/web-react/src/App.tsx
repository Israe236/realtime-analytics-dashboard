import { lazy, Suspense } from 'react';

import { AlertBanner, AlertHistory } from './components/Alerts';
import { StatusAndPayments, TopCities } from './components/Breakdowns';
import { ConnectionBadge } from './components/ConnectionBadge';
import { KpiCards } from './components/KpiCards';
import { LiveFeed } from './components/LiveFeed';
import { PipelineStats } from './components/PipelineStats';
import './App.css';

// Recharts is most of the bundle. Loading the chart components lazily lets the KPI cards,
// alerts and feed paint first; the charts follow as a separate chunk.
const RevenueChart = lazy(() => import('./components/RevenueChart').then((m) => ({ default: m.RevenueChart })));
const CategoryBreakdown = lazy(() =>
  import('./components/Breakdowns').then((m) => ({ default: m.CategoryBreakdown })),
);

function ChartFallback({ title }: { title: string }) {
  return (
    <section className="card">
      <header className="card__header">
        <h2>{title}</h2>
      </header>
      <div className="placeholder">Loading chart…</div>
    </section>
  );
}

export default function App() {
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand__mark" aria-hidden>
            ◆
          </span>
          <div>
            <h1>Order analytics</h1>
            <p className="muted small">Moroccan e-commerce · last 60 minutes · React</p>
          </div>
        </div>
        <PipelineStats />
        <ConnectionBadge />
      </header>

      <AlertBanner />
      <KpiCards />

      <main className="grid">
        <div className="grid__wide">
          <Suspense fallback={<ChartFallback title="Revenue & orders" />}>
            <RevenueChart />
          </Suspense>
        </div>
        <div className="grid__tall">
          <LiveFeed />
        </div>
        <Suspense fallback={<ChartFallback title="Revenue by category" />}>
          <CategoryBreakdown />
        </Suspense>
        <TopCities />
        <StatusAndPayments />
        <AlertHistory />
      </main>
    </div>
  );
}

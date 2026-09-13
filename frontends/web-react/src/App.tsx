import { AlertBanner, AlertHistory } from './components/Alerts';
import { CategoryBreakdown, StatusAndPayments, TopCities } from './components/Breakdowns';
import { ConnectionBadge } from './components/ConnectionBadge';
import { KpiCards } from './components/KpiCards';
import { LiveFeed } from './components/LiveFeed';
import { PipelineStats } from './components/PipelineStats';
import { RevenueChart } from './components/RevenueChart';
import './App.css';

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
          <RevenueChart />
        </div>
        <div className="grid__tall">
          <LiveFeed />
        </div>
        <CategoryBreakdown />
        <TopCities />
        <StatusAndPayments />
        <AlertHistory />
      </main>
    </div>
  );
}

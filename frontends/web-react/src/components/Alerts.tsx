import { type Alert, formatClock, ruleLabel } from '@shared/index';

import { useActiveAlerts, useAlertHistory } from '../live/hooks';

export function AlertBanner() {
  const { data: active } = useActiveAlerts();
  if (active.length === 0) return null;
  return (
    <section className="alerts" aria-live="assertive">
      {active.map((alert) => (
        <div key={alert.rule} className={`alert alert--${alert.severity}`} role="alert">
          <strong>{ruleLabel(alert.rule)}</strong>
          <span>{alert.message}</span>
          <span className="muted small">since {formatClock(alert.fired_at)}</span>
        </div>
      ))}
    </section>
  );
}

function HistoryRow({ alert }: { alert: Alert }) {
  return (
    <li className="history__row">
      <span className={`pill pill--${alert.state === 'firing' ? alert.severity : 'resolved'}`}>{alert.state}</span>
      <span>{ruleLabel(alert.rule)}</span>
      <span className="muted small num">
        {formatClock(alert.fired_at)}
        {alert.resolved_at ? ` → ${formatClock(alert.resolved_at)}` : ''}
      </span>
    </li>
  );
}

export function AlertHistory() {
  const { data } = useAlertHistory();
  return (
    <section className="card">
      <header className="card__header">
        <h2>Alert history</h2>
      </header>
      {!data || data.length === 0 ? (
        <div className="placeholder">No alerts so far</div>
      ) : (
        <ul className="history">
          {data.slice(0, 8).map((alert) => (
            <HistoryRow key={alert.id} alert={alert} />
          ))}
        </ul>
      )}
    </section>
  );
}

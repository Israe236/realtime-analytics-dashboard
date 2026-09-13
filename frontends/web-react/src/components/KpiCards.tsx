import { memo } from 'react';

import { KPI_DEFINITIONS, type KpiDefinition, type Kpis, relativeChange, type SnapshotMessage } from '@shared/index';

import { useSnapshot } from '../live/hooks';

const selectKpis = (s: SnapshotMessage) => ({ current: s.kpis, previous: s.previous_kpis, window: s.window_minutes });

const KpiCard = memo(function KpiCard({
  definition,
  current,
  previous,
}: {
  definition: KpiDefinition;
  current: Kpis;
  previous: Kpis;
}) {
  const value = current[definition.key];
  const change = relativeChange(value, previous[definition.key]);
  const good = change === null || change === 0 ? null : change > 0 === definition.higherIsBetter;
  return (
    <article className="card kpi">
      <h3 className="kpi__label">{definition.label}</h3>
      <p className="kpi__value">{definition.format(value)}</p>
      <p className={`kpi__delta ${good === null ? '' : good ? 'kpi__delta--good' : 'kpi__delta--bad'}`}>
        {change === null ? 'no baseline' : `${change > 0 ? '▲' : change < 0 ? '▼' : '■'} ${Math.abs(change * 100).toFixed(1)} %`}
        <span className="muted"> vs previous period</span>
      </p>
    </article>
  );
});

export function KpiCards() {
  const { data } = useSnapshot(selectKpis);
  if (!data) return <section className="kpis" aria-busy />;
  return (
    <section className="kpis" aria-label={`Key metrics, last ${data.window} minutes`}>
      {KPI_DEFINITIONS.map((definition) => (
        <KpiCard key={definition.key} definition={definition} current={data.current} previous={data.previous} />
      ))}
    </section>
  );
}

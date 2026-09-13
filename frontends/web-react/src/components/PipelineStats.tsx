import { formatCount, formatPercent, type SnapshotMessage } from '@shared/index';

import { useSnapshot } from '../live/hooks';

const selectPipeline = (s: SnapshotMessage) => s.pipeline;

function formatLag(ms: number | null): string {
  if (ms === null) return '—';
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function PipelineStats() {
  const { data } = useSnapshot(selectPipeline);
  const items: [string, string][] = [
    ['Ingest rate', data ? `${data.events_per_second.toFixed(0)} ev/s` : '—'],
    ['Data freshness', formatLag(data?.freshness_lag_ms ?? null)],
    ['Rejected', formatPercent(data?.dead_letter_rate)],
    ['Queue', formatCount(data?.queued_events)],
    ['Viewers', formatCount(data?.connected_clients)],
  ];
  return (
    <dl className="pipeline">
      {items.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd className="num">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

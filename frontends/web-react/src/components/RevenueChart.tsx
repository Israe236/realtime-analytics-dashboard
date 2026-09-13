import { memo, useMemo, useState } from 'react';
import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import { formatCount, formatMad, formatMinute, type SeriesPoint, type SnapshotMessage } from '@shared/index';

import { useSnapshot } from '../live/hooks';

type Range = 'minute' | 'hour';

const selectMinutes = (s: SnapshotMessage) => s.revenue_per_minute;
const selectHours = (s: SnapshotMessage) => s.revenue_per_hour;

const compactMad = new Intl.NumberFormat('fr-MA', { notation: 'compact', maximumFractionDigits: 1 });

/**
 * Smooth updates without flicker:
 * - animations are off (an animation restarting every 250 ms is what looks like flicker),
 * - the chart component stays mounted; only its `data` prop changes,
 * - `memo` + React Query structural sharing skip re-renders when the series is unchanged.
 */
const Chart = memo(function Chart({ points }: { points: readonly SeriesPoint[] }) {
  const data = useMemo(
    () => points.map((p) => ({ bucket: p.bucket, revenue: p.revenue_mad, orders: p.orders, cancelled: p.cancelled })),
    [points],
  );
  return (
    <ResponsiveContainer width="100%" height={280}>
      <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id="revenueFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.35} />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="var(--grid)" vertical={false} />
        <XAxis dataKey="bucket" tickFormatter={formatMinute} minTickGap={40} stroke="var(--muted)" fontSize={12} />
        <YAxis yAxisId="mad" tickFormatter={(v: number) => compactMad.format(v)} stroke="var(--muted)" fontSize={12} width={48} />
        <YAxis yAxisId="orders" orientation="right" stroke="var(--muted)" fontSize={12} width={36} />
        <Tooltip
          contentStyle={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 8 }}
          labelFormatter={(label) => formatMinute(String(label))}
          formatter={(value, name) => [name === 'Revenue' ? formatMad(Number(value)) : formatCount(Number(value)), name]}
        />
        <Area
          yAxisId="mad"
          type="monotone"
          dataKey="revenue"
          name="Revenue"
          stroke="var(--accent)"
          strokeWidth={2}
          fill="url(#revenueFill)"
          isAnimationActive={false}
        />
        <Line yAxisId="orders" type="monotone" dataKey="orders" name="Orders" stroke="var(--accent-2)" dot={false} isAnimationActive={false} />
        <Line yAxisId="orders" type="monotone" dataKey="cancelled" name="Cancelled" stroke="var(--bad)" dot={false} isAnimationActive={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
});

export function RevenueChart() {
  const [range, setRange] = useState<Range>('minute');
  const { data } = useSnapshot(range === 'minute' ? selectMinutes : selectHours);
  return (
    <section className="card chart">
      <header className="card__header">
        <h2>Revenue &amp; orders</h2>
        <div className="segmented" role="tablist" aria-label="Time range">
          {(['minute', 'hour'] as const).map((r) => (
            <button key={r} role="tab" aria-selected={range === r} className={range === r ? 'active' : ''} onClick={() => setRange(r)}>
              {r === 'minute' ? 'Last 60 min' : 'Last 24 h'}
            </button>
          ))}
        </div>
      </header>
      {data ? <Chart points={data} /> : <div className="placeholder">Waiting for data…</div>}
    </section>
  );
}

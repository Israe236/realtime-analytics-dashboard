import { memo } from 'react';
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import {
  CATEGORY_PALETTE as PALETTE,
  EVENT_LABELS,
  formatCount,
  formatMad,
  type RankedItem,
  type SnapshotMessage,
} from '@shared/index';

import { useSnapshot } from '../live/hooks';

const selectCategories = (s: SnapshotMessage) => s.top_categories;
const selectCities = (s: SnapshotMessage) => s.top_cities;
const selectStatus = (s: SnapshotMessage) => s.orders_by_status;
const selectPayments = (s: SnapshotMessage) => s.payment_methods;

const CategoryChart = memo(function CategoryChart({ items }: { items: readonly RankedItem[] }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={items as RankedItem[]} layout="vertical" margin={{ left: 8, right: 16 }}>
        <XAxis type="number" hide />
        <YAxis type="category" dataKey="name" width={90} stroke="var(--muted)" fontSize={12} tickLine={false} axisLine={false} />
        <Tooltip
          cursor={{ fill: 'var(--grid)' }}
          contentStyle={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 8 }}
          formatter={(value) => [formatMad(Number(value)), 'Revenue']}
        />
        <Bar dataKey="revenue_mad" radius={[0, 4, 4, 0]} isAnimationActive={false}>
          {items.map((item, index) => (
            <Cell key={item.name} fill={PALETTE[index % PALETTE.length]} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
});

export function CategoryBreakdown() {
  const { data } = useSnapshot(selectCategories);
  return (
    <section className="card">
      <header className="card__header">
        <h2>Revenue by category</h2>
      </header>
      {data && data.length > 0 ? <CategoryChart items={data} /> : <div className="placeholder">No sales yet</div>}
    </section>
  );
}

function RankedBars({ items, limit }: { items: readonly RankedItem[]; limit: number }) {
  const top = items.slice(0, limit);
  const max = Math.max(1, ...top.map((i) => i.revenue_mad));
  return (
    <ol className="ranked">
      {top.map((item) => (
        <li key={item.name}>
          <div className="ranked__row">
            <span>{item.name}</span>
            <span className="num">{formatMad(item.revenue_mad)}</span>
          </div>
          <div className="ranked__bar" style={{ width: `${(item.revenue_mad / max) * 100}%` }} />
          <div className="muted small">{formatCount(item.orders)} orders</div>
        </li>
      ))}
    </ol>
  );
}

export function TopCities() {
  const { data } = useSnapshot(selectCities);
  return (
    <section className="card">
      <header className="card__header">
        <h2>Top cities</h2>
      </header>
      {data && data.length > 0 ? <RankedBars items={data} limit={6} /> : <div className="placeholder">No sales yet</div>}
    </section>
  );
}

export function StatusAndPayments() {
  const { data: status } = useSnapshot(selectStatus);
  const { data: payments } = useSnapshot(selectPayments);
  const total = Math.max(1, ...(status ?? []).map((s) => s.count));
  return (
    <section className="card">
      <header className="card__header">
        <h2>Orders by status</h2>
      </header>
      <ul className="status">
        {(status ?? []).map((s) => (
          <li key={s.status} className={`status__item status__item--${s.status}`}>
            <span>{EVENT_LABELS[s.status]}</span>
            <span className="status__track">
              <span className="status__fill" style={{ width: `${(s.count / total) * 100}%` }} />
            </span>
            <span className="num">{formatCount(s.count)}</span>
          </li>
        ))}
      </ul>
      <h3 className="subheading">Payment methods</h3>
      <ul className="chips">
        {(payments ?? []).map((p) => (
          <li key={p.name} className="chip">
            {p.name.replaceAll('_', ' ')} <span className="num">{formatMad(p.revenue_mad)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

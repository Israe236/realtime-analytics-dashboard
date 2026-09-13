import type { FeedEvent, Kpis } from './protocol';

const mad = new Intl.NumberFormat('fr-MA', { style: 'currency', currency: 'MAD', maximumFractionDigits: 0 });
const madPrecise = new Intl.NumberFormat('fr-MA', { style: 'currency', currency: 'MAD', maximumFractionDigits: 2 });
const integer = new Intl.NumberFormat('fr-MA', { maximumFractionDigits: 0 });

export function formatMad(value: number | null | undefined, precise = false): string {
  if (value === null || value === undefined) return '—';
  return (precise ? madPrecise : mad).format(value);
}

export function formatCount(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : integer.format(value);
}

export function formatPercent(ratio: number | null | undefined, digits = 1): string {
  return ratio === null || ratio === undefined ? '—' : `${(ratio * 100).toFixed(digits)} %`;
}

/** Relative change from `previous` to `current`, or null when there is no baseline. */
export function relativeChange(current: number | null, previous: number | null): number | null {
  if (current === null || previous === null || previous === 0) return null;
  return (current - previous) / previous;
}

export type KpiKey = 'revenue_mad' | 'orders' | 'average_order_value_mad' | 'cancellation_rate';

export interface KpiDefinition {
  readonly key: KpiKey;
  readonly label: string;
  readonly format: (value: number | null) => string;
  /** For cancellations, going up is bad. */
  readonly higherIsBetter: boolean;
}

export const KPI_DEFINITIONS: readonly KpiDefinition[] = [
  { key: 'revenue_mad', label: 'Revenue', format: (v) => formatMad(v), higherIsBetter: true },
  { key: 'orders', label: 'Orders', format: formatCount, higherIsBetter: true },
  { key: 'average_order_value_mad', label: 'Avg order value', format: (v) => formatMad(v, true), higherIsBetter: true },
  { key: 'cancellation_rate', label: 'Cancellation rate', format: (v) => formatPercent(v), higherIsBetter: false },
];

export function kpiValue(kpis: Kpis, key: KpiKey): number | null {
  return kpis[key];
}

/** Newest-first feed without duplicates, capped at `limit` items. */
export function mergeFeed(current: readonly FeedEvent[], incoming: readonly FeedEvent[], limit = 50): FeedEvent[] {
  const seen = new Set<string>();
  const merged: FeedEvent[] = [];
  const newestFirst = [...incoming].reverse();
  for (const event of [...newestFirst, ...current]) {
    if (seen.has(event.event_id)) continue;
    seen.add(event.event_id);
    merged.push(event);
    if (merged.length === limit) break;
  }
  return merged;
}

export const EVENT_LABELS: Readonly<Record<FeedEvent['event_type'], string>> = {
  order_placed: 'Placed',
  order_paid: 'Paid',
  order_shipped: 'Shipped',
  order_cancelled: 'Cancelled',
};

export function formatClock(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function formatMinute(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}

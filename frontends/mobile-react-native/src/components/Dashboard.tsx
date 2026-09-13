import { memo, useEffect, useMemo, useState } from 'react';
import { ScrollView, StyleSheet, Text, useWindowDimensions, View } from 'react-native';

import {
  CATEGORY_PALETTE,
  EVENT_LABELS,
  type FeedEvent,
  formatClock,
  formatCount,
  formatMad,
  formatMinute,
  formatPercent,
  KPI_DEFINITIONS,
  type RankedItem,
  relativeChange,
  ruleLabel,
  type SnapshotMessage,
  theme,
} from '@shared/index';

import { useActiveAlerts, useConnectionState, useFeed, useSnapshot } from '../live/LiveProvider';
import { AreaChart } from './AreaChart';

// ---- selectors (module-level so their identity is stable) ---------------------------------
const selectKpis = (s: SnapshotMessage) => ({ current: s.kpis, previous: s.previous_kpis });
const selectMinutes = (s: SnapshotMessage) => s.revenue_per_minute;
const selectCategories = (s: SnapshotMessage) => s.top_categories;
const selectCities = (s: SnapshotMessage) => s.top_cities;
const selectStatus = (s: SnapshotMessage) => s.orders_by_status;
const selectPipeline = (s: SnapshotMessage) => s.pipeline;

function Card({ title, children, right }: { title: string; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <Text style={styles.cardTitle}>{title}</Text>
        {right}
      </View>
      {children}
    </View>
  );
}

function ConnectionBadge() {
  const { status, attempt, nextRetryAt } = useConnectionState();
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (status !== 'reconnecting') return;
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, [status]);

  const color = status === 'open' ? theme.good : status === 'stopped' ? theme.bad : theme.warn;
  const seconds = nextRetryAt ? Math.max(0, Math.ceil((nextRetryAt - now) / 1000)) : 0;
  const label =
    status === 'open'
      ? 'Live'
      : status === 'reconnecting'
        ? seconds > 0
          ? `Retry in ${seconds}s (#${attempt})`
          : `Reconnecting (#${attempt})`
        : status === 'stopped'
          ? 'Disconnected'
          : 'Connecting…';
  return (
    <View style={styles.badge} accessibilityRole="text" accessibilityLabel={`Connection: ${label}`}>
      <View style={[styles.dot, { backgroundColor: color }]} />
      <Text style={styles.badgeText}>{label}</Text>
    </View>
  );
}

function AlertBanner() {
  const { data } = useActiveAlerts();
  if (data.length === 0) return null;
  return (
    <View style={{ gap: 8 }}>
      {data.map((alert) => (
        <View
          key={alert.rule}
          accessibilityRole="alert"
          style={[styles.alert, alert.severity === 'critical' ? styles.alertCritical : styles.alertWarning]}
        >
          <Text style={styles.alertTitle}>{ruleLabel(alert.rule)}</Text>
          <Text style={styles.text}>{alert.message}</Text>
        </View>
      ))}
    </View>
  );
}

const KpiGrid = memo(function KpiGrid({ columns }: { columns: number }) {
  const { data } = useSnapshot(selectKpis);
  if (!data) return null;
  return (
    <View style={styles.kpiGrid}>
      {KPI_DEFINITIONS.map((definition) => {
        const value = data.current[definition.key];
        const change = relativeChange(value, data.previous[definition.key]);
        const good = change === null || change === 0 ? null : change > 0 === definition.higherIsBetter;
        return (
          <View key={definition.key} style={[styles.card, styles.kpi, { flexBasis: `${100 / columns - 3}%` }]}>
            <Text style={styles.label}>{definition.label}</Text>
            <Text style={styles.kpiValue}>{definition.format(value)}</Text>
            <Text style={[styles.small, { color: good === null ? theme.muted : good ? theme.good : theme.bad }]}>
              {change === null ? 'no baseline' : `${change > 0 ? '▲' : '▼'} ${Math.abs(change * 100).toFixed(1)} %`}
            </Text>
          </View>
        );
      })}
    </View>
  );
});

function RevenueCard() {
  const { data } = useSnapshot(selectMinutes);
  const revenue = useMemo(() => data?.map((p) => p.revenue_mad) ?? [], [data]);
  const orders = useMemo(() => data?.map((p) => p.orders) ?? [], [data]);
  const first = data?.[0];
  const last = data?.[data.length - 1];
  return (
    <Card title="Revenue per minute" right={<Text style={styles.legend}>— revenue  — orders</Text>}>
      <AreaChart values={revenue} overlay={orders} />
      {first && last && (
        <View style={styles.axis}>
          <Text style={styles.small}>{formatMinute(first.bucket)}</Text>
          <Text style={styles.small}>{formatMinute(last.bucket)}</Text>
        </View>
      )}
    </Card>
  );
}

function Bars({ items, limit }: { items: readonly RankedItem[]; limit: number }) {
  const top = items.slice(0, limit);
  const max = Math.max(1, ...top.map((i) => i.revenue_mad));
  return (
    <View style={{ gap: 10 }}>
      {top.map((item, index) => (
        <View key={item.name}>
          <View style={styles.row}>
            <Text style={[styles.text, { textTransform: 'capitalize' }]}>{item.name}</Text>
            <Text style={styles.num}>{formatMad(item.revenue_mad)}</Text>
          </View>
          <View style={styles.track}>
            <View
              style={[
                styles.fill,
                { width: `${(item.revenue_mad / max) * 100}%`, backgroundColor: CATEGORY_PALETTE[index % CATEGORY_PALETTE.length] },
              ]}
            />
          </View>
        </View>
      ))}
    </View>
  );
}

function Breakdown({ title, select, limit }: { title: string; select: (s: SnapshotMessage) => readonly RankedItem[]; limit: number }) {
  const { data } = useSnapshot(select);
  return (
    <Card title={title}>{data && data.length > 0 ? <Bars items={data} limit={limit} /> : <Text style={styles.muted}>No sales yet</Text>}</Card>
  );
}

function StatusCard() {
  const { data } = useSnapshot(selectStatus);
  return (
    <Card title="Orders by status">
      <View style={styles.statusRow}>
        {(data ?? []).map((s) => (
          <View key={s.status} style={styles.statusItem}>
            <Text style={styles.kpiValueSmall}>{formatCount(s.count)}</Text>
            <Text style={styles.label}>{EVENT_LABELS[s.status]}</Text>
          </View>
        ))}
      </View>
    </Card>
  );
}

const FeedRow = memo(function FeedRow({ event }: { event: FeedEvent }) {
  return (
    <View style={styles.feedRow}>
      <Text style={[styles.small, styles.num]}>{formatClock(event.occurred_at)}</Text>
      <Text style={[styles.pill, { color: event.event_type === 'order_cancelled' ? theme.bad : theme.accent2 }]}>
        {EVENT_LABELS[event.event_type]}
      </Text>
      <Text style={[styles.text, styles.feedWhere]} numberOfLines={1}>
        {event.category} · {event.city}
      </Text>
      <Text style={styles.num}>{formatMad(event.amount_mad)}</Text>
    </View>
  );
});

function FeedCard() {
  const { data } = useFeed();
  return (
    <Card title="Live events">
      {data.length === 0 ? (
        <Text style={styles.muted}>Waiting for events…</Text>
      ) : (
        data.slice(0, 12).map((event) => <FeedRow key={event.event_id} event={event} />)
      )}
    </Card>
  );
}

function PipelineLine() {
  const { data } = useSnapshot(selectPipeline);
  if (!data) return null;
  const lag = data.freshness_lag_ms;
  return (
    <Text style={styles.small}>
      {data.events_per_second.toFixed(0)} ev/s · freshness {lag === null ? '—' : `${(lag / 1000).toFixed(1)} s`} · rejected{' '}
      {formatPercent(data.dead_letter_rate)}
    </Text>
  );
}

export function Dashboard() {
  const { width } = useWindowDimensions();
  const wide = width >= 900;
  return (
    <ScrollView style={styles.screen} contentContainerStyle={[styles.content, wide && styles.contentWide]}>
      <View style={styles.header}>
        <View style={{ flexShrink: 1 }}>
          <Text style={styles.title}>Order analytics</Text>
          <Text style={styles.small}>React Native · last 60 minutes</Text>
          <PipelineLine />
        </View>
        <ConnectionBadge />
      </View>
      <AlertBanner />
      <KpiGrid columns={wide ? 4 : 2} />
      <View style={wide ? styles.columns : undefined}>
        <View style={[styles.column, wide && { flex: 2 }]}>
          <RevenueCard />
          <Breakdown title="Revenue by category" select={selectCategories} limit={8} />
          <StatusCard />
        </View>
        <View style={styles.column}>
          <FeedCard />
          <Breakdown title="Top cities" select={selectCities} limit={6} />
        </View>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: theme.bg },
  content: { padding: 16, paddingTop: 56, gap: 12 },
  contentWide: { maxWidth: 1280, width: '100%', alignSelf: 'center', paddingTop: 24 },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 12 },
  title: { color: theme.text, fontSize: 20, fontWeight: '700' },
  text: { color: theme.text, fontSize: 14 },
  muted: { color: theme.muted, fontSize: 14 },
  small: { color: theme.muted, fontSize: 12 },
  num: { color: theme.text, fontSize: 13, fontVariant: ['tabular-nums'] },
  label: { color: theme.muted, fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.6 },
  legend: { color: theme.muted, fontSize: 11 },
  badge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: theme.border,
    backgroundColor: theme.surface,
  },
  badgeText: { color: theme.text, fontSize: 12, fontWeight: '600' },
  dot: { width: 8, height: 8, borderRadius: 4 },
  alert: { padding: 12, borderRadius: 12, borderWidth: 1, gap: 2 },
  alertCritical: { backgroundColor: 'rgba(255,107,139,0.1)', borderColor: 'rgba(255,107,139,0.45)' },
  alertWarning: { backgroundColor: 'rgba(255,181,71,0.1)', borderColor: 'rgba(255,181,71,0.45)' },
  alertTitle: { color: theme.text, fontWeight: '700' },
  card: { backgroundColor: theme.surface, borderColor: theme.border, borderWidth: 1, borderRadius: 12, padding: 14 },
  cardHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 },
  cardTitle: { color: theme.text, fontSize: 14, fontWeight: '600' },
  kpiGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10, justifyContent: 'space-between' },
  kpi: { flexGrow: 1, gap: 4 },
  kpiValue: { color: theme.text, fontSize: 22, fontWeight: '700', fontVariant: ['tabular-nums'] },
  kpiValueSmall: { color: theme.text, fontSize: 18, fontWeight: '700', fontVariant: ['tabular-nums'] },
  axis: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 4 },
  row: { flexDirection: 'row', justifyContent: 'space-between' },
  track: { height: 6, borderRadius: 3, backgroundColor: theme.surface2, marginTop: 4, overflow: 'hidden' },
  fill: { height: 6, borderRadius: 3 },
  statusRow: { flexDirection: 'row', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 },
  statusItem: { alignItems: 'center', minWidth: 70 },
  feedRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingVertical: 6,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: theme.grid,
  },
  pill: { fontSize: 11, fontWeight: '700', width: 62 },
  feedWhere: { flex: 1, textTransform: 'capitalize' },
  columns: { flexDirection: 'row', gap: 12, alignItems: 'flex-start' },
  column: { flex: 1, gap: 12 },
});

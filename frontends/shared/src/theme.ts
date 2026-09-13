/** Design tokens shared by every dashboard (CSS custom properties mirror these values). */
export const theme = {
  bg: '#0b0e14',
  surface: '#121722',
  surface2: '#192030',
  border: '#242c3d',
  grid: '#1c2331',
  text: '#e6e9f0',
  muted: '#8b93a7',
  accent: '#6c8cff',
  accent2: '#44d7b6',
  good: '#3ecf8e',
  bad: '#ff6b8b',
  warn: '#ffb547',
} as const;

export const CATEGORY_PALETTE = ['#6c8cff', '#44d7b6', '#ffb547', '#ff6b8b', '#a78bfa', '#38bdf8', '#f472b6', '#94a3b8'] as const;

const RULE_LABELS: Readonly<Record<string, string>> = {
  cancellation_rate: 'High cancellation rate',
  revenue_drop: 'Revenue drop',
  dead_letter_rate: 'Rejected events',
  ingestion_stalled: 'Ingestion stalled',
};

export function ruleLabel(rule: string): string {
  return RULE_LABELS[rule] ?? rule;
}

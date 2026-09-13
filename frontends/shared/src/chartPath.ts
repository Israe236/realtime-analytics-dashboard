export interface ChartPaths {
  /** SVG path for the line itself. */
  readonly line: string;
  /** Same line closed down to the baseline, for a filled area. */
  readonly area: string;
  /** The value mapped to the top of the chart. */
  readonly max: number;
}

/**
 * Turns a series into SVG path strings. Used by the React Native dashboard, which draws its
 * charts with react-native-svg; kept here (framework-free) so it can be unit tested.
 */
export function chartPaths(values: readonly number[], width: number, height: number, padding = 4): ChartPaths {
  if (values.length === 0 || width <= 0 || height <= 0) return { line: '', area: '', max: 0 };
  const max = Math.max(0, ...values) || 1;
  const usable = Math.max(0, height - padding * 2);
  const step = values.length > 1 ? width / (values.length - 1) : 0;
  const points = values.map((value, index) => {
    const x = index * step;
    const y = padding + usable - (Math.max(0, value) / max) * usable;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const line = `M${points.join(' L')}`;
  const lastX = ((values.length - 1) * step).toFixed(1);
  const area = `${line} L${lastX},${height.toFixed(1)} L0.0,${height.toFixed(1)} Z`;
  return { line, area, max };
}

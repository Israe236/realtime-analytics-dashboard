import { describe, expect, it } from 'vitest';

import { chartPaths } from './chartPath';

describe('chartPaths', () => {
  it('maps values onto the drawing area', () => {
    const paths = chartPaths([0, 5, 10], 100, 50, 0);
    expect(paths.line).toBe('M0.0,50.0 L50.0,25.0 L100.0,0.0');
    expect(paths.area).toBe('M0.0,50.0 L50.0,25.0 L100.0,0.0 L100.0,50.0 L0.0,50.0 Z');
    expect(paths.max).toBe(10);
  });

  it('keeps a flat zero series on the baseline instead of dividing by zero', () => {
    expect(chartPaths([0, 0], 10, 20, 0).line).toBe('M0.0,20.0 L10.0,20.0');
  });

  it('returns empty paths when there is nothing to draw', () => {
    expect(chartPaths([], 100, 50).line).toBe('');
    expect(chartPaths([1, 2], 0, 50).area).toBe('');
  });
});

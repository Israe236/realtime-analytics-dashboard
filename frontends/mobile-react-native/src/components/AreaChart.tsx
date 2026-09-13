import { memo, useMemo, useState } from 'react';
import { View } from 'react-native';
import Svg, { Defs, Line, LinearGradient, Path, Stop } from 'react-native-svg';

import { chartPaths, theme } from '@shared/index';

interface Props {
  readonly values: readonly number[];
  readonly overlay?: readonly number[];
  readonly height?: number;
}

/**
 * A lightweight live chart drawn with react-native-svg (Recharts is DOM-only).
 * No animation: paths are recomputed only when the series changes, and the <Svg> element
 * stays mounted, so updates appear without flicker.
 */
export const AreaChart = memo(function AreaChart({ values, overlay, height = 170 }: Props) {
  const [width, setWidth] = useState(0);
  const main = useMemo(() => chartPaths(values, width, height), [values, width, height]);
  const second = useMemo(() => (overlay ? chartPaths(overlay, width, height) : null), [overlay, width, height]);

  return (
    <View style={{ height }} onLayout={(event) => setWidth(Math.round(event.nativeEvent.layout.width))}>
      {width > 0 && (
        <Svg width={width} height={height}>
          <Defs>
            <LinearGradient id="areaFill" x1="0" y1="0" x2="0" y2="1">
              <Stop offset="0" stopColor={theme.accent} stopOpacity={0.35} />
              <Stop offset="1" stopColor={theme.accent} stopOpacity={0} />
            </LinearGradient>
          </Defs>
          {[0.25, 0.5, 0.75].map((ratio) => (
            <Line key={ratio} x1={0} x2={width} y1={height * ratio} y2={height * ratio} stroke={theme.grid} strokeWidth={1} />
          ))}
          <Path d={main.area} fill="url(#areaFill)" />
          <Path d={main.line} stroke={theme.accent} strokeWidth={2} fill="none" />
          {second && <Path d={second.line} stroke={theme.accent2} strokeWidth={1.5} fill="none" opacity={0.8} />}
        </Svg>
      )}
    </View>
  );
});

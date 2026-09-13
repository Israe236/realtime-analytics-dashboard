import {
  BarController,
  BarElement,
  CategoryScale,
  Chart,
  Filler,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
} from 'chart.js';

import { theme } from '@shared/index';

// Register only what we use: keeps the bundle small (Chart.js is tree-shakeable).
Chart.register(BarController, BarElement, CategoryScale, Filler, LinearScale, LineController, LineElement, PointElement, Tooltip);

Chart.defaults.color = theme.muted;
Chart.defaults.borderColor = theme.grid;
Chart.defaults.font.family = "'Inter', system-ui, 'Segoe UI', Roboto, sans-serif";
// Animations restarting on every update are what makes a live chart look like it flickers.
Chart.defaults.animation = false;

export { Chart };

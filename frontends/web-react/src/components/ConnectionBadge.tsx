import { useEffect, useState } from 'react';

import { useConnectionState } from '../live/hooks';

function useNow(intervalMs: number, enabled: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs, enabled]);
  return now;
}

export function ConnectionBadge() {
  const { status, attempt, nextRetryAt } = useConnectionState();
  const now = useNow(250, status === 'reconnecting');

  let label: string;
  switch (status) {
    case 'open':
      label = 'Live';
      break;
    case 'reconnecting': {
      const seconds = nextRetryAt ? Math.max(0, Math.ceil((nextRetryAt - now) / 1000)) : 0;
      label = seconds > 0 ? `Reconnecting in ${seconds}s (attempt ${attempt})` : `Reconnecting… (attempt ${attempt})`;
      break;
    }
    case 'stopped':
      label = 'Disconnected';
      break;
    default:
      label = 'Connecting…';
  }

  return (
    <span className={`badge badge--${status}`} role="status" aria-live="polite">
      <span className="badge__dot" aria-hidden />
      {label}
    </span>
  );
}

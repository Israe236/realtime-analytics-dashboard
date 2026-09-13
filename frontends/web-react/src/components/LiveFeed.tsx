import { memo } from 'react';

import { EVENT_LABELS, type FeedEvent, formatClock, formatMad } from '@shared/index';

import { useFeed } from '../live/hooks';

// Rows are keyed by event_id, so React keeps existing rows and only mounts new ones —
// which is also what makes the CSS "fade in" play for new events only.
const FeedRow = memo(function FeedRow({ event }: { event: FeedEvent }) {
  return (
    <li className={`feed__row feed__row--${event.event_type}`}>
      <span className="feed__time num">{formatClock(event.occurred_at)}</span>
      <span className={`pill pill--${event.event_type}`}>{EVENT_LABELS[event.event_type]}</span>
      <span className="feed__where">
        {event.category} · {event.city}
      </span>
      <span className="feed__amount num">{formatMad(event.amount_mad, true)}</span>
    </li>
  );
});

export function LiveFeed() {
  const { data } = useFeed();
  return (
    <section className="card feed">
      <header className="card__header">
        <h2>Live events</h2>
        <span className="muted small">sampled</span>
      </header>
      {data.length === 0 ? (
        <div className="placeholder">Waiting for events…</div>
      ) : (
        <ul className="feed__list">
          {data.slice(0, 25).map((event) => (
            <FeedRow key={event.event_id} event={event} />
          ))}
        </ul>
      )}
    </section>
  );
}

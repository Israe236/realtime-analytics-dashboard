import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { backoffDelay } from './backoff';
import { mergeFeed, relativeChange } from './format';
import { type ConnectionState, LiveClient, type WebSocketLike } from './liveClient';
import type { FeedEvent, ServerMessage } from './protocol';

class FakeSocket implements WebSocketLike {
  onopen: WebSocketLike['onopen'] = null;
  onmessage: WebSocketLike['onmessage'] = null;
  onclose: WebSocketLike['onclose'] = null;
  onerror: WebSocketLike['onerror'] = null;
  closedWith: number | null = null;

  open(): void {
    this.onopen?.({});
  }
  send(message: object | string): void {
    this.onmessage?.({ data: typeof message === 'string' ? message : JSON.stringify(message) });
  }
  serverClose(code = 1006): void {
    this.onclose?.({ code, reason: '' });
  }
  close(code = 1000): void {
    this.closedWith = code;
  }
}

const hello = { type: 'hello', server_time: '2026-09-13T10:00:00Z', active_alerts: [], recent_events: [] };

function setup(overrides: { random?: () => number } = {}) {
  const sockets: FakeSocket[] = [];
  const messages: ServerMessage[] = [];
  const states: ConnectionState[] = [];
  const client = new LiveClient({
    url: 'ws://test/ws/live',
    onMessage: (m) => messages.push(m),
    onState: (s) => states.push(s),
    baseDelayMs: 500,
    maxDelayMs: 8_000,
    heartbeatTimeoutMs: 35_000,
    random: overrides.random ?? (() => 0.999),
    createSocket: () => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket;
    },
  });
  const latest = () => sockets[sockets.length - 1]!;
  return { client, sockets, messages, states, latest };
}

describe('backoffDelay', () => {
  it('grows exponentially and is capped', () => {
    const max = { baseMs: 500, maxMs: 8_000, random: () => 0.999999 };
    expect([0, 1, 2, 3, 4, 5, 10].map((a) => backoffDelay(a, max))).toEqual([499, 999, 1999, 3999, 7999, 7999, 7999]);
  });

  it('is jittered between zero and the ceiling', () => {
    expect(backoffDelay(3, { baseMs: 500, maxMs: 8_000, random: () => 0 })).toBe(0);
    expect(backoffDelay(3, { baseMs: 500, maxMs: 8_000, random: () => 0.5 })).toBe(2000);
  });
});

describe('LiveClient', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('is open after hello and delivers messages', () => {
    const { client, messages, latest } = setup();
    client.start();
    expect(client.connection.status).toBe('connecting');
    latest().open();
    latest().send(hello);
    latest().send({ type: 'ping', server_time: 'x' });
    expect(client.connection.status).toBe('open');
    expect(messages.map((m) => m.type)).toEqual(['hello', 'ping']);
  });

  it('reconnects with growing delays and resets after hello', () => {
    const { client, sockets, latest } = setup();
    client.start();

    latest().serverClose();
    expect(client.connection).toMatchObject({ status: 'reconnecting', attempt: 1 });
    vi.advanceTimersByTime(498);
    expect(sockets).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(sockets).toHaveLength(2);

    latest().serverClose();
    vi.advanceTimersByTime(998);
    expect(sockets).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(sockets).toHaveLength(3);

    latest().open();
    latest().send(hello);
    expect(client.connection).toMatchObject({ status: 'open', attempt: 0 });
  });

  it('keeps backing off when the server accepts then closes without hello (at capacity)', () => {
    const { client, latest } = setup();
    client.start();
    for (let i = 0; i < 4; i += 1) {
      latest().open();
      latest().serverClose(1013);
      vi.runOnlyPendingTimers();
    }
    expect(client.connection.attempt).toBe(4);
  });

  it('replaces a silent connection after the heartbeat timeout', () => {
    const { client, sockets, latest } = setup();
    client.start();
    latest().open();
    latest().send(hello);
    vi.advanceTimersByTime(30_000);
    latest().send({ type: 'ping', server_time: 'x' }); // re-arms the timer
    vi.advanceTimersByTime(34_999);
    expect(sockets[0]!.closedWith).toBeNull();
    vi.advanceTimersByTime(1);
    expect(sockets[0]!.closedWith).toBe(4000);
    expect(client.connection.status).toBe('reconnecting');
    vi.runOnlyPendingTimers();
    expect(sockets).toHaveLength(2);
  });

  it('ignores malformed and unknown messages', () => {
    const { client, messages, latest } = setup();
    client.start();
    latest().send('not json');
    latest().send({ type: 'from_the_future' });
    latest().send(hello);
    expect(messages.map((m) => m.type)).toEqual(['hello']);
  });

  it('stop() closes the socket and never reconnects', () => {
    const { client, sockets, latest } = setup();
    client.start();
    latest().send(hello);
    client.stop();
    expect(sockets[0]!.closedWith).toBe(1000);
    sockets[0]!.serverClose();
    vi.runAllTimers();
    expect(sockets).toHaveLength(1);
    expect(client.connection.status).toBe('stopped');
  });

  it('reconnectNow() skips the remaining backoff', () => {
    const { client, sockets, latest } = setup();
    client.start();
    latest().serverClose();
    expect(sockets).toHaveLength(1);
    client.reconnectNow();
    expect(sockets).toHaveLength(2);
  });
});

describe('format helpers', () => {
  const event = (id: string): FeedEvent => ({
    event_id: id,
    order_id: 'o',
    event_type: 'order_paid',
    occurred_at: '2026-09-13T10:00:00Z',
    amount_mad: 10,
    category: 'books',
    city: 'Rabat',
    payment_method: 'card',
  });

  it('merges the feed newest first without duplicates', () => {
    const merged = mergeFeed([event('b'), event('a')], [event('b'), event('c'), event('d')], 3);
    expect(merged.map((e) => e.event_id)).toEqual(['d', 'c', 'b']);
  });

  it('computes relative change only with a baseline', () => {
    expect(relativeChange(150, 100)).toBe(0.5);
    expect(relativeChange(150, 0)).toBeNull();
    expect(relativeChange(null, 100)).toBeNull();
  });
});

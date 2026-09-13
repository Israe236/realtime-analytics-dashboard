import { backoffDelay } from './backoff';
import type { ServerMessage, ServerMessageType } from './protocol';

/** The subset of the browser / React Native WebSocket API the client needs. */
export interface WebSocketLike {
  onopen: ((event: unknown) => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onclose: ((event: { code: number; reason: string }) => void) | null;
  onerror: ((event: unknown) => void) | null;
  close(code?: number, reason?: string): void;
}

export type ConnectionStatus = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'stopped';

export interface ConnectionState {
  readonly status: ConnectionStatus;
  /** Failed attempts since the last successful connection. */
  readonly attempt: number;
  /** Epoch ms of the next reconnect attempt, when one is scheduled. */
  readonly nextRetryAt: number | null;
  /** Epoch ms of the last message received. */
  readonly lastMessageAt: number | null;
}

export interface LiveClientOptions {
  readonly url: string;
  readonly onMessage: (message: ServerMessage) => void;
  readonly onState?: (state: ConnectionState) => void;
  readonly baseDelayMs?: number;
  readonly maxDelayMs?: number;
  /** The server pings every 15 s; silence longer than this means the connection is dead. */
  readonly heartbeatTimeoutMs?: number;
  readonly createSocket?: (url: string) => WebSocketLike;
  readonly random?: () => number;
  readonly now?: () => number;
}

const KNOWN_TYPES: ReadonlySet<ServerMessageType> = new Set(['hello', 'snapshot', 'events', 'alert', 'ping']);

/**
 * A reconnecting WebSocket client for `/ws/live`, independent of any UI framework.
 * React and React Native wrap it in a hook; Angular wraps it in a service.
 *
 * Reconnection rules:
 * - Any close (server restart, network loss, 1013 "server full") schedules a reconnect with
 *   exponential backoff + full jitter.
 * - The failure counter is reset only when the server sends `hello`, not when the socket
 *   opens: a server that accepts and immediately closes (e.g. at capacity) must still see
 *   the delay grow.
 * - If nothing arrives for `heartbeatTimeoutMs`, the connection is assumed dead (a laptop
 *   waking from sleep often keeps a "open" socket that will never deliver) and replaced.
 */
export class LiveClient {
  private readonly options: Required<Omit<LiveClientOptions, 'onState'>> & Pick<LiveClientOptions, 'onState'>;
  private socket: WebSocketLike | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setTimeout> | null = null;
  private running = false;
  private state: ConnectionState = { status: 'idle', attempt: 0, nextRetryAt: null, lastMessageAt: null };

  constructor(options: LiveClientOptions) {
    this.options = {
      baseDelayMs: 500,
      maxDelayMs: 15_000,
      heartbeatTimeoutMs: 35_000,
      createSocket: (url) => new WebSocket(url) as unknown as WebSocketLike,
      random: Math.random,
      now: Date.now,
      ...options,
    };
  }

  get connection(): ConnectionState {
    return this.state;
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    this.connect();
  }

  stop(): void {
    this.running = false;
    this.clearTimers();
    this.dropSocket(1000, 'client stopped');
    this.setState({ status: 'stopped', nextRetryAt: null });
  }

  /** Skip the remaining backoff (e.g. the browser just came back online). */
  reconnectNow(): void {
    if (!this.running || this.state.status === 'open') return;
    this.clearTimers();
    this.dropSocket(1000, 'reconnecting');
    this.connect();
  }

  private connect(): void {
    this.setState({ status: this.state.attempt === 0 ? 'connecting' : 'reconnecting', nextRetryAt: null });
    let socket: WebSocketLike;
    try {
      socket = this.options.createSocket(this.options.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;
    socket.onopen = () => this.armHeartbeat();
    socket.onmessage = (event) => {
      if (this.socket !== socket) return;
      this.armHeartbeat();
      this.handleMessage(event.data);
    };
    socket.onclose = () => {
      if (this.socket !== socket) return; // a socket we already replaced
      this.socket = null;
      if (this.running) this.scheduleReconnect();
    };
    socket.onerror = () => {
      // Browsers always follow an error with a close event; reconnect is handled there.
    };
  }

  private handleMessage(data: unknown): void {
    if (typeof data !== 'string') return;
    let message: ServerMessage;
    try {
      message = JSON.parse(data) as ServerMessage;
    } catch {
      return; // ignore garbage rather than crash the dashboard
    }
    if (!message || !KNOWN_TYPES.has(message.type)) return; // newer server, older client
    const now = this.options.now();
    if (message.type === 'hello') {
      this.setState({ status: 'open', attempt: 0, lastMessageAt: now });
    } else {
      this.setState({ lastMessageAt: now });
    }
    this.options.onMessage(message);
  }

  private scheduleReconnect(): void {
    this.clearTimers();
    const delay = backoffDelay(this.state.attempt, {
      baseMs: this.options.baseDelayMs,
      maxMs: this.options.maxDelayMs,
      random: this.options.random,
    });
    this.setState({
      status: 'reconnecting',
      attempt: this.state.attempt + 1,
      nextRetryAt: this.options.now() + delay,
    });
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      if (this.running) this.connect();
    }, delay);
  }

  private armHeartbeat(): void {
    if (this.heartbeatTimer !== null) clearTimeout(this.heartbeatTimer);
    this.heartbeatTimer = setTimeout(() => {
      this.heartbeatTimer = null;
      this.dropSocket(4000, 'heartbeat timeout');
      if (this.running) this.scheduleReconnect();
    }, this.options.heartbeatTimeoutMs);
  }

  private dropSocket(code: number, reason: string): void {
    const socket = this.socket;
    this.socket = null; // detach first so its close event is ignored
    if (socket) {
      try {
        socket.close(code, reason);
      } catch {
        // already closed
      }
    }
  }

  private clearTimers(): void {
    if (this.retryTimer !== null) clearTimeout(this.retryTimer);
    if (this.heartbeatTimer !== null) clearTimeout(this.heartbeatTimer);
    this.retryTimer = null;
    this.heartbeatTimer = null;
  }

  private setState(patch: Partial<ConnectionState>): void {
    this.state = { ...this.state, ...patch };
    this.options.onState?.(this.state);
  }
}

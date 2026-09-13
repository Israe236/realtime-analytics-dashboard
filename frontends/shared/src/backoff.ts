export interface BackoffOptions {
  readonly baseMs: number;
  readonly maxMs: number;
  /** Injectable for deterministic tests. */
  readonly random?: () => number;
}

/**
 * Exponential backoff with "full jitter": a random delay in [0, min(max, base * 2^attempt)).
 *
 * The exponential part stops a failing server from being hammered; the random part stops
 * hundreds of dashboards that lost the connection at the same moment from reconnecting at
 * the same moment (a "thundering herd").
 */
export function backoffDelay(attempt: number, { baseMs, maxMs, random = Math.random }: BackoffOptions): number {
  const ceiling = Math.min(maxMs, baseMs * 2 ** Math.max(0, attempt));
  return Math.floor(random() * ceiling);
}

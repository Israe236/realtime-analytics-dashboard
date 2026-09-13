"""Runtime configuration, read from environment variables prefixed with ``APP_``."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", extra="ignore")

    # --- database -----------------------------------------------------------------------
    database_url: str = "postgresql://analytics:analytics@localhost:55432/analytics"
    db_pool_min_size: int = 2
    db_pool_max_size: int = 10

    # --- ingestion ----------------------------------------------------------------------
    # Upper bound on events waiting for the writer. When full, the API answers 429 so the
    # producer slows down instead of the process growing memory until it dies.
    ingest_queue_max_events: int = 50_000
    # Largest number of events written in one transaction.
    writer_batch_max_events: int = 5_000
    # How long a request waits for its events to be committed before answering 503.
    ingest_ack_timeout_s: float = 10.0
    max_request_bytes: int = 10 * 1024 * 1024
    max_events_per_request: int = 5_000
    # Timestamps too far in the future (clock skew) or the past (replays) are rejected.
    max_future_skew_s: int = 300
    max_event_age_s: int = 7 * 24 * 3600

    # --- metrics & WebSocket fan-out ----------------------------------------------------
    metrics_window_minutes: int = 60
    hourly_series_hours: int = 24
    # Snapshots are pushed at most this often, however many batches commit.
    broadcast_min_interval_ms: int = 250
    # ...and at least this often, so rolling windows move even when no events arrive.
    broadcast_idle_interval_ms: int = 2_000
    feed_events_per_tick: int = 12
    ws_max_clients: int = 1_000
    ws_send_timeout_s: float = 5.0
    ws_heartbeat_interval_s: float = 15.0
    ws_max_pending_messages: int = 200

    # --- alerting -----------------------------------------------------------------------
    alert_eval_interval_s: float = 5.0
    alert_window_minutes: int = 5
    alert_cancellation_rate_threshold: float = 0.25
    alert_cancellation_min_orders: int = 30
    alert_revenue_drop_threshold: float = 0.5
    alert_revenue_min_baseline_mad: float = 2_000.0
    alert_dead_letter_rate_threshold: float = 0.05
    alert_dead_letter_min_events: int = 100
    alert_stall_seconds: float = 30.0
    # Resolve thresholds = fire threshold x this ratio (hysteresis against flapping).
    alert_resolve_ratio: float = 0.8
    # Consecutive evaluations needed before an alert fires / resolves.
    alert_fire_after_evaluations: int = 2
    alert_resolve_after_evaluations: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()

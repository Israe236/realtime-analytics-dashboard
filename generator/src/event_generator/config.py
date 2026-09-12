from pydantic_settings import BaseSettings, SettingsConfigDict


class GeneratorSettings(BaseSettings):
    """Environment variables prefixed with ``GEN_``."""

    model_config = SettingsConfigDict(env_prefix="GEN_", extra="ignore")

    api_url: str = "http://localhost:8000"
    # Average target rate over a simulated day; the daily curve moves around it.
    events_per_second: float = 50.0
    # How fast the simulated clock runs: 12 means a simulated day lasts two real hours.
    time_scale: float = 12.0
    # Simulated hour of day at start-up (default: the current hour in Morocco, UTC+1).
    start_hour: float | None = None

    tick_ms: int = 100
    flush_interval_ms: int = 200
    max_batch: int = 2_000
    # Events waiting to be sent. Beyond this the oldest are dropped (and counted): the
    # generator must not run out of memory while the API is down.
    max_buffer: int = 200_000
    http_timeout_s: float = 15.0

    malformed_ratio: float = 0.01
    base_cancel_probability: float = 0.07

    bursts: bool = True
    burst_mean_interval_s: float = 180.0
    anomalies: bool = True
    anomaly_interval_s: float = 420.0

    seed: int | None = None
    log_interval_s: float = 10.0

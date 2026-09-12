"""Entry point: ``python -m event_generator`` (or the ``event-generator`` script)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import signal
import time
from datetime import UTC, datetime, timedelta

import httpx

from event_generator.config import GeneratorSettings
from event_generator.lifecycle import OrderSimulator, expected_events_per_order
from event_generator.malformed import maybe_corrupt
from event_generator.rate import daily_multiplier, simulated_hour
from event_generator.scenarios import ScenarioScheduler
from event_generator.sender import Sender, backoff_delay

log = logging.getLogger("event_generator")


async def wait_for_api(client: httpx.AsyncClient, url: str, rng: random.Random) -> None:
    attempt = 0
    while True:
        try:
            if (await client.get(url)).status_code == 200:
                return
        except httpx.TransportError:
            pass
        delay = backoff_delay(attempt, rng, base_s=0.5, cap_s=5.0)
        log.info("waiting for API at %s (retry in %.1fs)", url, delay)
        attempt += 1
        await asyncio.sleep(delay)


async def run(settings: GeneratorSettings, stop: asyncio.Event) -> None:
    rng = random.Random(settings.seed)
    start = time.monotonic()
    start_hour = settings.start_hour
    if start_hour is None:
        morocco = datetime.now(UTC) + timedelta(hours=1)
        start_hour = morocco.hour + morocco.minute / 60
    simulator = OrderSimulator(rng)
    scheduler = ScenarioScheduler(
        rng,
        start=start,
        base_cancel_probability=settings.base_cancel_probability,
        base_malformed_ratio=settings.malformed_ratio,
        bursts=settings.bursts,
        burst_mean_interval_s=settings.burst_mean_interval_s,
        anomalies=settings.anomalies,
        anomaly_interval_s=settings.anomaly_interval_s,
    )
    base = settings.api_url.rstrip("/")
    async with httpx.AsyncClient(timeout=settings.http_timeout_s) as client:
        await wait_for_api(client, f"{base}/api/health", rng)
        sender = Sender(
            client,
            f"{base}/api/events/batch",
            max_batch=settings.max_batch,
            max_buffer=settings.max_buffer,
            flush_interval_s=settings.flush_interval_ms / 1000,
            rng=rng,
        )
        sender_task = asyncio.create_task(sender.run(stop))
        log.info(
            "generating ~%.0f events/s (time scale x%.0f)",
            settings.events_per_second,
            settings.time_scale,
        )

        carry = 0.0
        generated = 0
        last_tick = last_log = time.monotonic()
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), settings.tick_ms / 1000)
            now = time.monotonic()
            elapsed, last_tick = now - last_tick, now

            effects = scheduler.effects(now)
            hour = simulated_hour(start_hour, now - start, settings.time_scale)
            events_rate = (
                settings.events_per_second * daily_multiplier(hour) * effects.rate_multiplier
            )
            # Convert an events/s target into new orders/s; follow-up events come from the
            # lifecycle. A little noise keeps the stream from looking machine-made.
            orders_rate = events_rate / expected_events_per_order(settings.base_cancel_probability)
            carry += orders_rate * elapsed * rng.uniform(0.8, 1.2)
            new_orders = int(carry)
            carry -= new_orders

            payloads = [
                simulator.new_order(now, effects.cancel_probability) for _ in range(new_orders)
            ]
            payloads.extend(simulator.due_events(now))
            generated += len(payloads)
            sender.enqueue([maybe_corrupt(p, rng, effects.malformed_ratio) for p in payloads])

            if now - last_log >= settings.log_interval_s:
                s = sender.stats
                log.info(
                    "sim %05.2fh target %.0f ev/s | generated %d accepted %d rejected %d "
                    "retries %d buffered %d dropped %d | %s",
                    hour, events_rate, generated, s.accepted, s.rejected, s.retries,
                    sender.buffered, s.dropped_buffer_full + s.dropped_unretryable,
                    ",".join(effects.labels) or "normal",
                )  # fmt: skip
                last_log = now

        await sender_task


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request drowns the stats
    settings = GeneratorSettings()

    async def _main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):  # not available on Windows
                loop.add_signal_handler(sig, stop.set)
        await run(settings, stop)

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main())


if __name__ == "__main__":
    main()

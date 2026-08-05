"""Concurrent fetch→save engine shared by both market pipelines. Each
company is transformed and written as its fetch completes (durable against
crashes/Ctrl-C), on a thread pool throttled by a shared adaptive rate
limiter, with one bad ticker failing only that ticker.
"""

import random
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import (
    FETCH_MAX_RETRIES,
    FETCH_RETRY_BASE_DELAY,
    RATE_LIMIT_DELAY,
    RATE_LIMIT_MAX_PENALTY,
    RATE_LIMIT_RECOVERY_STREAK,
)

# ── Rate limiting ───────────────────────────────────────────────────

_RATE_LIMIT_MARKERS = (
    "too many requests",
    "rate limit",
    "429",
)


def is_rate_limit_error(err: str | None) -> bool:
    """True if an error string looks like upstream throttling."""
    if not err:
        return False
    low = str(err).lower()
    return any(m in low for m in _RATE_LIMIT_MARKERS)


class AdaptiveRateLimiter:
    """Enforces a minimum interval between request starts across all threads.

    The interval widens when the upstream signals throttling and decays back
    toward the baseline after a streak of clean responses, so a run that trips
    a host's rate limiter slows down instead of burning through the rest of
    the batch collecting 429s. One instance per rate-limited host (yfinance,
    SEC EDGAR, screener.in each get their own).
    """

    def __init__(self, base_interval: float = RATE_LIMIT_DELAY,
                 max_penalty: float = RATE_LIMIT_MAX_PENALTY,
                 recovery_streak: int = RATE_LIMIT_RECOVERY_STREAK):
        self.base_interval = base_interval
        self.max_penalty = max_penalty
        self.recovery_streak = recovery_streak
        self._penalty = 0.0
        self._streak = 0
        self._next_slot = 0.0
        self._lock = threading.Lock()

    @property
    def interval(self) -> float:
        return self.base_interval + self._penalty

    def acquire(self) -> None:
        """Block until this thread may start its next fetch."""
        with self._lock:
            now = time.monotonic()
            start_at = max(now, self._next_slot)
            # Jitter avoids threads releasing in lockstep after a cooldown.
            self._next_slot = start_at + self.interval + random.uniform(0, 0.2)
        delay = start_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def penalize(self) -> float:
        """Widen the interval after a throttling response. Returns new penalty."""
        with self._lock:
            self._streak = 0
            self._penalty = min(self._penalty * 2 + 0.5, self.max_penalty)
            return self._penalty

    def reward(self) -> None:
        """Decay the penalty after sustained clean responses."""
        with self._lock:
            if self._penalty <= 0:
                return
            self._streak += 1
            if self._streak >= self.recovery_streak:
                self._streak = 0
                self._penalty = max(0.0, self._penalty / 2 - 0.25)


# One shared limiter for screener.in, used by both the shareholding/ratings
# enrichment and the NSE annual-report scrape -- they hit the same host, so a
# limiter each would double the real request rate under either one's back.
SCREENER_LIMITER = AdaptiveRateLimiter(base_interval=RATE_LIMIT_DELAY)


# ── Results ─────────────────────────────────────────────────────────

@dataclass
class RunReport:
    """Outcome of a pipeline run."""
    saved: list[Any] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    elapsed: float = 0.0


def write_failure_log(path: Path, failures: list[tuple[str, str]]) -> None:
    """Persist failed symbols so they can be retried with --symbols.

    Always rewritten (including to empty) so a clean run clears stale entries.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{sym}: {err}" for sym, err in failures]
    path.write_text("\n".join(lines) + ("\n" if lines else ""))


# ── Engine ──────────────────────────────────────────────────────────

def run_fetch_pipeline(
    symbols: list[str],
    fetch_fn: Callable[[str], dict],
    handle_fn: Callable[[str, dict], Any],
    *,
    workers: int,
    limiter: AdaptiveRateLimiter | None = None,
    max_retries: int = FETCH_MAX_RETRIES,
    progress_every: int = 25,
    label: str = "symbols",
) -> RunReport:
    """Fetch, transform and persist each symbol concurrently.

    `fetch_fn` returns a raw dict (may carry a non-None "error" key); `handle_fn`
    does the transform + write per successful fetch, so results become durable
    immediately. Returns a RunReport of saved records and (symbol, error) failures.
    """
    report = RunReport()
    if not symbols:
        return report

    limiter = limiter or AdaptiveRateLimiter()
    total = len(symbols)
    start = time.time()
    done = 0
    counter_lock = threading.Lock()

    def process(symbol: str) -> tuple[str, Any, str | None]:
        """Fetch with retry, then hand off for transform+save. Runs in a worker."""
        last_err = "unknown error"
        for attempt in range(max_retries + 1):
            limiter.acquire()
            try:
                raw = fetch_fn(symbol)
            except Exception as e:  # fetch_fn should trap, but never trust it
                raw = {"error": str(e)}

            err = raw.get("error")
            if not err:
                limiter.reward()
                # Transform+save inside the worker: the big DataFrames in `raw`
                # become garbage as soon as this returns.
                return symbol, handle_fn(symbol, raw), None

            last_err = str(err)
            if attempt == max_retries:
                break
            if is_rate_limit_error(err):
                penalty = limiter.penalize()
                backoff = FETCH_RETRY_BASE_DELAY * (2 ** attempt) + penalty
            else:
                backoff = FETCH_RETRY_BASE_DELAY * (2 ** attempt)
            time.sleep(backoff + random.uniform(0, 0.5))

        return symbol, None, last_err

    print(f"\nFetching {total} {label} with {workers} workers "
          f"(≥{limiter.base_interval:.1f}s apart, {max_retries} retries)...")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process, s): s for s in symbols}
        try:
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    _, record, err = future.result()
                    if err:
                        report.failed.append((symbol, err))
                    elif record is not None:
                        report.saved.append(record)
                except Exception as e:
                    # handle_fn blew up — that symbol is lost, the batch is not.
                    report.failed.append((symbol, f"save failed: {e}"))

                with counter_lock:
                    done += 1
                    n = done
                if n % progress_every == 0 or n == total:
                    rate = n / max(time.time() - start, 1e-6)
                    eta = (total - n) / rate if rate else 0
                    extra = f", throttled +{limiter.interval - limiter.base_interval:.1f}s" \
                        if limiter.interval > limiter.base_interval else ""
                    print(f"  {n}/{total} ({n * 100 // total}%)  "
                          f"ok={len(report.saved)} fail={len(report.failed)}  "
                          f"eta {eta / 60:.1f}m{extra}")
        except KeyboardInterrupt:
            print("\n  Interrupted — cancelling pending fetches "
                  f"({len(report.saved)} already saved).")
            for f in futures:
                f.cancel()
            raise

    report.elapsed = time.time() - start
    return report

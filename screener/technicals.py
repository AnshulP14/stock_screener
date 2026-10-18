"""Daily technical screens: transparent inputs, completed candles, no trading calls."""

import math
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from .config import FETCH_TIMEOUT, RATE_LIMIT_DELAY
from .desk import instrument
from .fetch import _YFINANCE_LOCK, _yf_session


def wilder(series, period=14):
    """Seed with an arithmetic mean, then Wilder's recursive smoothing."""
    result = pd.Series(float("nan"), index=series.index, dtype=float)
    if len(series) >= period:
        result.iloc[period - 1] = series.iloc[:period].mean()
        for i in range(period, len(series)):
            result.iloc[i] = (result.iloc[i - 1] * (period - 1) + series.iloc[i]) / period
    return result


def indicators(frame):
    close, high, low, volume = (frame[c].astype(float) for c in ("Close", "High", "Low", "Volume"))
    delta = close.diff().iloc[1:]
    gain, loss = wilder(delta.clip(lower=0)), wilder(-delta.clip(upper=0))
    rsi = 100 - 100 / (1 + gain / loss)
    rsi = rsi.mask((gain == 0) & (loss == 0), 50).mask((gain > 0) & (loss == 0), 100)
    tr = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1
    ).max(axis=1)
    last = close.iloc[-1]
    prior_volume = volume.shift().rolling(20).mean().iloc[-1]
    metrics = {f"sma_{p}": close.rolling(p).mean().iloc[-1] for p in (20, 50, 200)}
    metrics.update(
        {
            "close": last,
            "rsi_14": rsi.iloc[-1] if len(rsi) else None,
            "atr_14": wilder(tr).iloc[-1],
            "volume_ratio_20": volume.iloc[-1] / prior_volume if prior_volume > 0 else None,
            "return_63": last / close.iloc[-64] - 1 if len(close) >= 64 else None,
            "drawdown_252": last / close.tail(252).max() - 1,
            "prior_high_20": high.shift().rolling(20).max().iloc[-1],
            "annualised_volatility_20": close.pct_change().rolling(20).std().iloc[-1]
            * math.sqrt(252),
            "average_daily_turnover_20": (close * volume).rolling(20).mean().iloc[-1],
        }
    )
    return {k: float(v) if v is not None and math.isfinite(v) else None for k, v in metrics.items()}


def fetch_history(market, symbol):
    if market == "nse" and symbol == "^NSEI":
        yahoo = symbol
    else:
        instrument(market, symbol)
        yahoo = symbol + ".NS" if market == "nse" else symbol.replace(".", "-")
    with _YFINANCE_LOCK:
        time.sleep(RATE_LIMIT_DELAY)
        return yf.Ticker(yahoo, session=_yf_session()).history(
            period="2y",
            interval="1d",
            auto_adjust=False,
            actions=True,
            timeout=FETCH_TIMEOUT,
            raise_errors=True,
        )


def snapshot(market, symbol, frame, *, now=None, benchmark=None):
    """Exclude today's bar conservatively, even after close. Preserve raw mark separately."""
    market, symbol = instrument(market, symbol)
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local = now.astimezone(ZoneInfo("Asia/Kolkata" if market == "nse" else "America/New_York"))
    if frame.empty or not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("No dated price history")
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError("Price dates must be unique and increasing")
    required = ["Open", "High", "Low", "Close", "Volume", "Adj Close"]
    if not set(required).issubset(frame.columns):
        raise ValueError("Missing OHLCV/Adj Close fields")
    frame = frame.copy()
    # Yahoo daily indexes are exchange-local; never reinterpret them as UTC dates.
    dates = frame.index.date
    if any(d > local.date() for d in dates):
        raise ValueError("Future price bar")
    values = frame[required].apply(pd.to_numeric, errors="raise")
    if (
        not values.apply(lambda column: column.map(math.isfinite)).all().all()
        or (values[["Open", "High", "Low", "Close", "Adj Close"]] <= 0).any().any()
        or (values["Volume"] < 0).any()
        or (values["High"] < values[["Open", "Close", "Low"]].max(axis=1)).any()
        or (values["Low"] > values[["Open", "Close", "High"]].min(axis=1)).any()
    ):
        raise ValueError("Invalid OHLCV data; do not silently repair financial inputs")
    frame[required] = values
    complete = frame.loc[dates < local.date()].copy()
    if len(complete) < 21:
        raise ValueError("Need at least 21 completed daily candles")
    # Adjust all OHLC consistently for signals, retain unadjusted latest mark for valuation.
    factor = complete["Adj Close"] / complete["Close"]
    for col in ("Open", "High", "Low", "Close"):
        complete[col] *= factor
    metrics = indicators(complete)
    metrics["excess_return_63"] = None
    if benchmark is not None:
        left = pd.Series(complete["Close"].to_numpy(), index=complete.index.date)
        right = pd.Series(benchmark["Adj Close"].to_numpy(), index=benchmark.index.date)
        aligned = pd.concat([left, right], axis=1, join="inner").dropna()
        if len(aligned) >= 64:
            a, b = aligned.iloc[-64], aligned.iloc[-1]
            metrics["excess_return_63"] = float(b.iloc[0] / a.iloc[0] - b.iloc[1] / a.iloc[1])
    warnings = [
        "Yahoo unofficial/delayed data; not execution-grade.",
        "Today's daily candle is excluded from signals, including after close.",
    ]
    age = (local.date() - complete.index[-1].date()).days
    if age > 4:
        warnings.append("STALE: latest completed candle is over four calendar days old.")
    if len(complete) < 252:
        warnings.append(
            "Short history: long-window indicators may be missing; drawdown uses available bars."
        )
    if benchmark is None:
        warnings.append("Relative strength unavailable: no benchmark supplied.")
    signals = []
    if age <= 4:
        if (
            metrics["sma_200"] is not None
            and metrics["close"] > metrics["sma_50"] > metrics["sma_200"]
        ):
            signals.append("uptrend")
        if (
            metrics["prior_high_20"] is not None
            and metrics["close"] > metrics["prior_high_20"]
            and metrics["volume_ratio_20"] is not None
            and metrics["volume_ratio_20"] >= 1.5
        ):
            signals.append("volume_breakout")
        if metrics["sma_200"] is not None and metrics["close"] < metrics["sma_200"]:
            signals.append("below_200dma")
    return {
        "market": market,
        "symbol": symbol,
        "currency": "INR" if market == "nse" else "USD",
        "fetched_at": now.isoformat(),
        "signal_date": complete.index[-1].date().isoformat(),
        "stale": age > 4,
        "completed_bars": len(complete),
        "metrics": metrics,
        "mark": {
            "price": float(frame["Close"].iloc[-1]),
            "bar_date": dates[-1].isoformat(),
            "retrieved_at": now.isoformat(),
            "potentially_incomplete": dates[-1] == local.date(),
            "kind": "daily_close_or_partial; exact quote timestamp unavailable",
        },
        "signals": signals,
        "warnings": warnings,
    }

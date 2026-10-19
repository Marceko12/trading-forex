from datetime import datetime, timezone

import pandas as pd
import pytest

from app.strategies.signal_engine import evaluate_signal


def candles(count=160, freq="1h"):
    times = pd.date_range("2025-01-01", periods=count, freq=freq, tz="UTC")
    close = [100 + i * 0.1 for i in range(count)]
    return pd.DataFrame({"time": times, "open": close, "high": [x + .2 for x in close],
                         "low": [x - .2 for x in close], "close": close,
                         "tick_volume": 100, "spread": 1})


def test_signal_uses_only_closed_bars_and_reports_bar_timestamp():
    frame = candles()
    now = (frame.time.iloc[-1] + pd.Timedelta(minutes=30)).to_pydatetime()
    result = evaluate_signal("EURUSD", "H1", frame, now=now)
    assert result["bar_time"] == frame.time.iloc[-2].isoformat()
    assert result["parameters"]["ema_fast"] == 12


def test_signal_rejects_when_only_incomplete_bar_exists():
    now = datetime(2025, 1, 1, 0, 30, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="velas cerradas"):
        evaluate_signal("EURUSD", "H1", candles(2), now=now)


def test_spread_filter_forces_no_trade():
    result = evaluate_signal("EURUSD", "H1", candles(), bid=1.0, ask=1.02, point=.0001,
                             max_spread_points=10, now=datetime(2025, 1, 8, tzinfo=timezone.utc))
    assert result["decision"] == "NO TRADE"
    assert "Spread excede" in result["reason"]

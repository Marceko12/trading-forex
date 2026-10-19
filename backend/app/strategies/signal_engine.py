"""Closed-candle deterministic signal evaluation, independent of the UI."""
from __future__ import annotations

from datetime import datetime, timezone
import pandas as pd

from ..core.models import Decision
from ..market_data.validation import validate_candles
from .baseline import decide
from .indicators import add_indicators

TIMEFRAME_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}


def evaluate_signal(symbol: str, timeframe: str, candles: pd.DataFrame, *, bid: float | None = None,
                    ask: float | None = None, point: float | None = None,
                    max_spread_points: float = 50.0, now: datetime | None = None,
                    source: str = "MT5", bars_are_closed: bool = False) -> dict:
    """Evaluate only completed bars. Returns actual values and a reason; never sends orders."""
    tf = timeframe.upper()
    if tf not in TIMEFRAME_MINUTES:
        raise ValueError(f"Temporalidad no admitida: {timeframe}")
    frame = validate_candles(candles)
    current = now or datetime.now(timezone.utc)
    current = current.replace(tzinfo=timezone.utc) if current.tzinfo is None else current.astimezone(timezone.utc)
    if not bars_are_closed:
        cutoff = pd.Timestamp(current) - pd.Timedelta(minutes=TIMEFRAME_MINUTES[tf])
        frame = frame[frame.time <= cutoff].reset_index(drop=True)
    if frame.empty:
        raise ValueError("MT5 no proporcionó velas cerradas para evaluar")
    decision, reason = decide(frame)
    last = add_indicators(frame).iloc[-1]
    spread_price = max(0.0, float(ask - bid)) if bid is not None and ask is not None else None
    if spread_price is not None and point and point > 0 and spread_price / point > max_spread_points:
        decision, reason = Decision.NO_TRADE, f"Spread excede el límite ({spread_price / point:.1f} > {max_spread_points:.1f} puntos)"
    atr = float(last.atr) if pd.notna(last.atr) else None
    close = float(last.close)
    entry = (float(ask) if decision == Decision.BUY and ask is not None else
             float(bid) if decision == Decision.SELL and bid is not None else close) if decision in (Decision.BUY, Decision.SELL) else None
    stop = entry - 1.5 * atr if decision == Decision.BUY and atr else entry + 1.5 * atr if decision == Decision.SELL and atr else None
    target = entry + 3 * atr if decision == Decision.BUY and atr else entry - 3 * atr if decision == Decision.SELL and atr else None
    return {"symbol": symbol, "timeframe": tf, "decision": decision.value, "reason": reason,
            "timestamp": current.isoformat(), "bar_time": frame.time.iloc[-1].isoformat(),
            "entry": entry, "stop_loss": stop, "take_profit": target, "spread_price": spread_price,
            "source": source, "parameters": {"ema_fast": 12, "ema_slow": 26, "rsi_period": 14,
            "adx_min": 18, "atr_stop_multiple": 1.5, "atr_target_multiple": 3.0,
            "max_spread_points": max_spread_points}}

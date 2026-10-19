from .indicators import add_indicators
from ..core.models import Decision

def decide(frame):
    """Deterministic EMA/RSI/MACD/ADX baseline; no trained AI is claimed."""
    d = add_indicators(frame)
    row = d.iloc[-1]
    if row[["ema_fast", "ema_slow", "rsi", "macd", "macd_signal", "atr", "adx"]].isna().any():
        return Decision.NO_TRADE, "Historial insuficiente para estabilizar indicadores"
    if row.adx < 18:
        return Decision.HOLD, "ADX bajo: mercado posiblemente lateral; no se abre posición"
    if row.ema_fast > row.ema_slow and row.macd > row.macd_signal and 45 <= row.rsi <= 70:
        return Decision.BUY, "Tendencia alcista: EMA rápida > lenta, MACD positivo y RSI dentro de rango"
    if row.ema_fast < row.ema_slow and row.macd < row.macd_signal and 30 <= row.rsi <= 55:
        return Decision.SELL, "Tendencia bajista: EMA rápida < lenta, MACD negativo y RSI dentro de rango"
    return Decision.HOLD, "Los filtros de tendencia y momentum no coinciden"

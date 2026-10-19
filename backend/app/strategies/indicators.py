import pandas as pd

def add_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    d = frame.copy()
    close, high, low = d.close, d.high, d.low
    d["ema_fast"] = close.ewm(span=12, adjust=False).mean()
    d["ema_slow"] = close.ewm(span=26, adjust=False).mean()
    delta = close.diff()
    gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
    avg_gain, avg_loss = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean(), loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    d["rsi"] = 100 - 100 / (1 + avg_gain / avg_loss.replace(0, float("nan")))
    fast = close.ewm(span=12, adjust=False).mean()
    slow = close.ewm(span=26, adjust=False).mean()
    d["macd"] = fast - slow
    d["macd_signal"] = d.macd.ewm(span=9, adjust=False).mean()
    previous = close.shift()
    tr = pd.concat([(high-low), (high-previous).abs(), (low-previous).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    up, down = high.diff(), -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    plus_di = 100 * plus_dm.ewm(alpha=1/14, min_periods=14, adjust=False).mean() / d.atr.replace(0, float("nan"))
    minus_di = 100 * minus_dm.ewm(alpha=1/14, min_periods=14, adjust=False).mean() / d.atr.replace(0, float("nan"))
    dx = 100 * (plus_di-minus_di).abs() / (plus_di+minus_di).replace(0, float("nan"))
    d["adx"] = dx.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    d["support"] = low.rolling(20, min_periods=20).min()
    d["resistance"] = high.rolling(20, min_periods=20).max()
    return d

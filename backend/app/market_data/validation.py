import pandas as pd

REQUIRED = ("time", "open", "high", "low", "close")

def validate_candles(frame: pd.DataFrame) -> pd.DataFrame:
    missing = set(REQUIRED) - set(frame.columns)
    if missing:
        raise ValueError(f"Faltan columnas: {', '.join(sorted(missing))}")
    out = frame.copy()
    out["time"] = pd.to_datetime(out["time"], utc=True, errors="coerce")
    for col in ("open", "high", "low", "close"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=list(REQUIRED)).sort_values("time").drop_duplicates("time").reset_index(drop=True)
    if out.empty:
        raise ValueError("No hay velas válidas")
    if ((out.high < out.low) | (out.high < out[["open", "close"]].max(axis=1)) | (out.low > out[["open", "close"]].min(axis=1))).any():
        raise ValueError("Vela OHLC inconsistente")
    return out

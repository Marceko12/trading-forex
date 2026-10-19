import pandas as pd
import numpy as np

REQUIRED = ("time", "open", "high", "low", "close")
OPTIONAL_NUMERIC = ("tick_volume", "spread", "real_volume")

def validate_candles(frame: pd.DataFrame) -> pd.DataFrame:
    missing = set(REQUIRED) - set(frame.columns)
    if missing:
        raise ValueError(f"Faltan columnas: {', '.join(sorted(missing))}")
    out = frame.copy()
    out["time"] = pd.to_datetime(out["time"], utc=True, errors="coerce")
    for col in ("open", "high", "low", "close"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    for col in OPTIONAL_NUMERIC:
        if col not in out:
            out[col] = 0
        raw = out[col]
        converted = pd.to_numeric(raw, errors="coerce")
        if (raw.notna() & converted.isna()).any():
            raise ValueError(f"La columna {col} contiene valores no numéricos")
        out[col] = converted.fillna(0)
    out = out.dropna(subset=list(REQUIRED)).sort_values("time").drop_duplicates("time").reset_index(drop=True)
    if out.empty:
        raise ValueError("No hay velas válidas")
    if not np.isfinite(out[["open", "high", "low", "close"]].to_numpy(dtype=float)).all():
        raise ValueError("Precios OHLC no finitos")
    if not np.isfinite(out[list(OPTIONAL_NUMERIC)].to_numpy(dtype=float)).all() or (out[list(OPTIONAL_NUMERIC)] < 0).any().any():
        raise ValueError("Volumen o spread contiene valores no válidos")
    if (out[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("Los precios OHLC deben ser positivos")
    if ((out.high < out.low) | (out.high < out[["open", "close"]].max(axis=1)) | (out.low > out[["open", "close"]].min(axis=1))).any():
        raise ValueError("Vela OHLC inconsistente")
    return out

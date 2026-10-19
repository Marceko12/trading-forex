"""MT5 historical bars discovery, paged download, validation and durable CSV storage."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
from uuid import uuid4
from pathlib import Path
from typing import Callable

import pandas as pd

from .validation import validate_candles

TIMEFRAMES = {"M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15", "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4"}
FOREX = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD")
GOLD = ("GOLD", "XAUUSD")
SYMBOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
PAGE_DAYS = {"M1": 30, "M5": 90, "M15": 180, "H1": 730, "H4": 1460}
CSV_COLUMNS = ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]


def _canonical(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def discover_symbols(names: list[str]) -> dict:
    """Map canonical instruments to exact broker symbols; never collapse micro and standard."""
    available = sorted(set(names), key=str.casefold)
    matches = []
    for name in available:
        normalized = _canonical(name)
        for instrument in (*GOLD, *FOREX):
            if instrument in normalized:
                idx = normalized.index(instrument)
                remainder = normalized[:idx] + normalized[idx + len(instrument):]
                is_micro = "MICRO" in remainder or normalized.startswith("M" + instrument) or normalized.endswith(instrument + "M")
                matches.append({"symbol": name, "instrument": instrument, "micro": is_micro, "variant": "micro" if is_micro else ("broker suffix/prefix" if remainder else "standard")})
                break
    return {"available": available, "matches": matches, "instruments": sorted(set(m["instrument"] for m in matches))}


def _aware(value: datetime | str | None, default: datetime) -> datetime:
    if value is None or value == "":
        return default
    result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def rates_to_frame(rates) -> pd.DataFrame:
    if rates is None:
        return pd.DataFrame(columns=CSV_COLUMNS)
    frame = pd.DataFrame(rates)
    if frame.empty:
        return pd.DataFrame(columns=CSV_COLUMNS)
    if "time" not in frame:
        raise ValueError("MT5 devolvió velas sin campo time")
    frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True, errors="coerce")
    return validate_candles(frame)[CSV_COLUMNS]


@dataclass
class HistoryResult:
    symbol: str
    timeframe: str
    count_downloaded: int
    count_total: int
    first_time: str | None
    last_time: str | None
    csv_path: str
    metadata_path: str
    validation: str
    limitations: list[str]


class HistoryService:
    def __init__(self, mt5, data_dir: str | Path = "data", clock: Callable[[], datetime] | None = None):
        self.mt5 = mt5
        self.data_dir = Path(data_dir)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def download(self, symbol: str, timeframe: str, start=None, end=None, progress=None) -> HistoryResult:
        if not SYMBOL_RE.fullmatch(symbol):
            raise ValueError("Nombre de símbolo inválido")
        timeframe = timeframe.upper()
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"Temporalidad no admitida: {timeframe}")
        names = self.mt5.symbols()
        if symbol not in names:
            raise ValueError(f"El símbolo exacto {symbol} no existe en los símbolos del terminal")
        end_dt = _aware(end, self.clock())
        start_dt = _aware(start, datetime(1970, 1, 1, tzinfo=timezone.utc))
        if start_dt >= end_dt:
            raise ValueError("La fecha inicial debe ser anterior a la fecha final")
        timeframe_value = self.mt5.timeframe_value(TIMEFRAMES[timeframe])
        if timeframe_value is None:
            raise RuntimeError(f"Temporalidad {timeframe} no disponible en MT5")

        parts, cursor, total_pages = [], start_dt, 0
        page = timedelta(days=PAGE_DAYS[timeframe])
        while cursor < end_dt:
            upper = min(cursor + page, end_dt)
            rates = self.mt5.copy_rates_range(symbol, timeframe_value, cursor, upper)
            error = self.mt5.last_error()
            if rates is None:
                raise RuntimeError(f"MT5 falló descargando {symbol} {timeframe}: {error}")
            batch = rates_to_frame(rates)
            if not batch.empty:
                parts.append(batch)
            cursor = upper
            total_pages += 1
            if progress:
                progress({"pages_done": total_pages, "pages_total": max(1, (end_dt-start_dt+page-timedelta(seconds=1))//page), "candles_received": sum(len(x) for x in parts)})

        downloaded = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=CSV_COLUMNS)
        if downloaded.empty:
            raise ValueError(f"MT5 no devolvió velas para {symbol} {timeframe} en el rango solicitado; compruebe el historial del terminal")
        downloaded = validate_candles(downloaded)[CSV_COLUMNS]
        self.data_dir.mkdir(parents=True, exist_ok=True)
        archive_dir = self.data_dir / "mt5" / symbol
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_csv = archive_dir / f"{timeframe}.csv"
        active_csv = self.data_dir / f"{symbol}.csv"
        existing_path = archive_csv
        if existing_path.exists():
            try:
                existing = validate_candles(pd.read_csv(existing_path))[CSV_COLUMNS]
                merged = pd.concat([existing, downloaded], ignore_index=True)
            except Exception as exc:
                raise ValueError(f"No se pudo combinar el CSV previo {existing_path}: {exc}") from exc
        else:
            merged = downloaded
        merged = validate_candles(merged)[CSV_COLUMNS]
        # Replace atomically; exact broker symbol is preserved in the public filename.
        nonce = uuid4().hex
        archive_tmp = archive_csv.with_name(f".{archive_csv.name}.{nonce}.tmp")
        active_tmp = active_csv.with_name(f".{active_csv.name}.{nonce}.tmp")
        merged.to_csv(archive_tmp, index=False)
        merged.to_csv(active_tmp, index=False)
        archive_tmp.replace(archive_csv)
        active_tmp.replace(active_csv)
        info = self.mt5.symbol_info(symbol)
        spec = {}
        if info:
            for source, target in (("trade_tick_size", "tick_size"), ("trade_tick_value", "tick_value"), ("volume_min", "volume_min"), ("volume_max", "volume_max"), ("volume_step", "volume_step")):
                value = getattr(info, source, None)
                if value is not None:
                    spec[target] = float(value)
            spec["currency_profit"] = getattr(info, "currency_profit", None)
        metadata = {"symbol": symbol, "timeframe": timeframe, "requested_start": start_dt.isoformat(), "requested_end": end_dt.isoformat(), "first_time": merged.time.min().isoformat(), "last_time": merged.time.max().isoformat(), "count": len(merged), "downloaded_this_run": len(downloaded), "downloaded_at": self.clock().isoformat(), "contract_spec": spec, "source": "MetaTrader5 terminal", "csv_path": str(active_csv), "archive_csv_path": str(archive_csv)}
        metadata_path = archive_dir / f"{timeframe}.metadata.json"
        meta_tmp = metadata_path.with_suffix(".json.tmp")
        meta_tmp.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        meta_tmp.replace(metadata_path)
        limitations = []
        if downloaded.time.min() > start_dt:
            limitations.append("MT5/terminal no entregó datos desde el inicio solicitado; el historial puede estar limitado por barras máximas del terminal o por datos del bróker.")
        if downloaded.time.max() < end_dt - timedelta(minutes=1):
            limitations.append("El histórico recibido termina antes del final solicitado; los periodos recientes pueden no tener vela cerrada.")
        return HistoryResult(symbol, timeframe, len(downloaded), len(merged), merged.time.min().isoformat(), merged.time.max().isoformat(), str(active_csv), str(metadata_path), "válido: cronológico, OHLC consistente y sin timestamps duplicados", limitations)


def read_metadata(symbol: str, timeframe: str, data_dir: str | Path = "data") -> dict:
    if not SYMBOL_RE.fullmatch(symbol) or timeframe.upper() not in TIMEFRAMES:
        raise ValueError("Símbolo o temporalidad inválidos")
    path = Path(data_dir) / "mt5" / symbol / f"{timeframe.upper()}.metadata.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_history(symbol: str, timeframe: str, data_dir: str | Path = "data") -> pd.DataFrame:
    if not SYMBOL_RE.fullmatch(symbol):
        raise ValueError("Nombre de símbolo inválido")
    timeframe = timeframe.upper()
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Temporalidad no admitida: {timeframe}")
    root = Path(data_dir)
    archive = root / "mt5" / symbol / f"{timeframe}.csv"
    path = archive if archive.exists() else root / f"{symbol}.csv"
    if not path.exists():
        raise FileNotFoundError(f"No hay histórico guardado para {symbol} {timeframe}")
    return validate_candles(pd.read_csv(path))[CSV_COLUMNS]

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
from ..mt5.adapter import TIMEFRAME_ATTRIBUTES

TIMEFRAMES = dict(TIMEFRAME_ATTRIBUTES)
FOREX = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD")
FX_CURRENCIES = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD", "CNH", "CNY", "HKD", "SGD", "MXN", "ZAR", "TRY", "PLN", "SEK", "NOK", "DKK", "HUF", "CZK", "ILS", "THB"}
GOLD = ("GOLD", "XAUUSD")
SYMBOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
PAGE_DAYS = {"M1": 30, "M5": 90, "M15": 180, "M30": 365, "H1": 730, "H4": 1460}
CSV_COLUMNS = ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]


def _canonical(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def discover_symbols(names: list[str]) -> dict:
    """Map canonical instruments to exact broker symbols; never collapse micro and standard."""
    available = sorted(set(names), key=str.casefold)
    matches = []
    for name in available:
        normalized = _canonical(name)
        found = False
        for instrument in (*GOLD, *FOREX):
            if instrument in normalized:
                idx = normalized.index(instrument)
                remainder = normalized[:idx] + normalized[idx + len(instrument):]
                is_micro = "MICRO" in remainder or normalized.startswith("M" + instrument) or normalized.endswith(instrument + "M")
                matches.append({"symbol": name, "instrument": instrument, "asset_type": "metal" if instrument in GOLD else "forex", "micro": is_micro, "variant": "micro" if is_micro else ("broker suffix/prefix" if remainder else "standard")})
                found = True
                break
        if found:
            continue
        # Also classify broker-listed non-major crosses such as EURPLN, using
        # recognizable ISO currency codes while preserving the exact symbol.
        for idx in range(max(0, len(normalized) - 5)):
            pair = normalized[idx:idx + 6]
            base, quote = pair[:3], pair[3:]
            if base in FX_CURRENCIES and quote in FX_CURRENCIES and base != quote:
                remainder = normalized[:idx] + normalized[idx + 6:]
                is_micro = "MICRO" in remainder or normalized.endswith(pair + "M")
                matches.append({"symbol": name, "instrument": pair, "asset_type": "forex", "micro": is_micro, "variant": "micro" if is_micro else ("broker suffix/prefix" if remainder else "standard")})
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


def candles_to_json(frame: pd.DataFrame) -> list[dict]:
    """Serialize validated candle rows with stable UTC timestamps and native JSON scalars."""
    candles=[]
    for row in frame.itertuples(index=False):
        timestamp=pd.Timestamp(row.time)
        timestamp=timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")
        candles.append({"time":timestamp.isoformat().replace("+00:00","Z"),"open":float(row.open),"high":float(row.high),"low":float(row.low),"close":float(row.close),"tick_volume":int(row.tick_volume),"spread":int(row.spread),"real_volume":int(row.real_volume)})
    return candles


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

    def fetch_recent_closed(self, symbol: str, timeframe: str, count: int = 500) -> pd.DataFrame:
        """Read recent closed bars from the connected terminal without persisting them."""
        if not SYMBOL_RE.fullmatch(symbol):
            raise ValueError("Nombre de símbolo inválido")
        timeframe = timeframe.upper()
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"Temporalidad no admitida: {timeframe}")
        if not isinstance(count, int) or count < 1 or count > 2000:
            raise ValueError("La cantidad de velas debe estar entre 1 y 2000")
        if symbol not in self.mt5.symbols():
            raise LookupError(f"El símbolo exacto {symbol} no existe en los símbolos del terminal")
        if self.mt5.symbol_info(symbol) is None:
            raise LookupError(f"MT5 no devuelve especificaciones para el símbolo {symbol}")
        tf_value = self.mt5.timeframe_value(timeframe)
        if tf_value is None:
            raise ValueError(f"Temporalidad {timeframe} no disponible en MT5")
        rates = self.mt5.copy_rates_from_pos(symbol, tf_value, 1, count)
        if rates is None:
            raise RuntimeError(f"MT5 devolvió None al solicitar velas cerradas de {symbol} {timeframe}: {self.mt5.last_error()}")
        frame = rates_to_frame(rates)
        if frame.empty:
            raise FileNotFoundError(f"MT5 no devolvió velas cerradas para {symbol} {timeframe}; compruebe el historial del terminal")
        return validate_candles(frame)[CSV_COLUMNS].reset_index(drop=True)

    def download(self, symbol: str, timeframe: str, start=None, end=None, progress=None) -> HistoryResult:
        if not SYMBOL_RE.fullmatch(symbol):
            raise ValueError("Nombre de símbolo inválido")
        timeframe = timeframe.upper()
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"Temporalidad no admitida: {timeframe}")
        names = self.mt5.symbols()
        if symbol not in names:
            raise ValueError(f"El símbolo exacto {symbol} no existe en los símbolos del terminal")
        end_value=f"{end}T23:59:59.999999Z" if isinstance(end,str) and len(end)==10 else end
        end_dt = _aware(end_value, self.clock())
        start_dt = _aware(start, datetime(1970, 1, 1, tzinfo=timezone.utc))
        if start_dt >= end_dt:
            raise ValueError("La fecha inicial debe ser anterior a la fecha final")
        timeframe_value = self.mt5.timeframe_value(timeframe)
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
        archive_unchanged = active_unchanged = False
        try:
            if archive_csv.exists():
                archive_unchanged = validate_candles(pd.read_csv(archive_csv))[CSV_COLUMNS].equals(merged.reset_index(drop=True))
            if active_csv.exists():
                active_unchanged = validate_candles(pd.read_csv(active_csv))[CSV_COLUMNS].equals(merged.reset_index(drop=True))
        except Exception:
            archive_unchanged = active_unchanged = False
        if archive_unchanged: archive_tmp.unlink()
        else: archive_tmp.replace(archive_csv)
        if active_unchanged: active_tmp.unlink()
        else: active_tmp.replace(active_csv)
        info = self.mt5.symbol_info(symbol)
        spec = {}
        if info:
            for source, target in (("trade_tick_size", "tick_size"), ("trade_tick_value", "tick_value"), ("volume_min", "volume_min"), ("volume_max", "volume_max"), ("volume_step", "volume_step"), ("trade_contract_size", "contract_size"), ("point", "point"), ("digits", "digits")):
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

    def download_count(self, symbol: str, timeframe: str, count: int, progress=None) -> HistoryResult:
        """Fetch the most recent *closed* bars via MT5 position indexing."""
        if not SYMBOL_RE.fullmatch(symbol):
            raise ValueError("Nombre de símbolo inválido")
        timeframe = timeframe.upper()
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"Temporalidad no admitida: {timeframe}")
        if not isinstance(count, int) or count < 1 or count > 250000:
            raise ValueError("La cantidad de velas debe estar entre 1 y 250000")
        if symbol not in self.mt5.symbols():
            raise ValueError(f"El símbolo exacto {symbol} no existe en los símbolos del terminal")
        tf_value = self.mt5.timeframe_value(timeframe)
        if tf_value is None:
            raise RuntimeError(f"Temporalidad {timeframe} no disponible en MT5")
        rates = self.mt5.copy_rates_from_pos(symbol, tf_value, 1, count)
        error = self.mt5.last_error()
        if rates is None:
            raise RuntimeError(f"MT5 falló descargando {symbol} {timeframe} desde la posición 1, cantidad {count}: {error}")
        downloaded = rates_to_frame(rates)
        if downloaded.empty:
            raise ValueError(f"MT5 no devolvió velas cerradas para {symbol} {timeframe} (solicitadas: {count}); compruebe el historial del terminal y el límite de barras")
        downloaded = validate_candles(downloaded)[CSV_COLUMNS]
        self.data_dir.mkdir(parents=True, exist_ok=True)
        archive_dir = self.data_dir / "mt5" / symbol
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_csv = archive_dir / f"{timeframe}.csv"
        active_csv = self.data_dir / f"{symbol}.csv"
        if archive_csv.exists():
            try:
                existing = validate_candles(pd.read_csv(archive_csv))[CSV_COLUMNS]
            except Exception as exc:
                raise ValueError(f"No se pudo combinar el CSV previo {archive_csv}: {exc}") from exc
            merged = validate_candles(pd.concat([existing, downloaded], ignore_index=True))[CSV_COLUMNS]
        else:
            merged = downloaded
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
            for source, target in (("trade_tick_size", "tick_size"), ("trade_tick_value", "tick_value"), ("volume_min", "volume_min"), ("volume_max", "volume_max"), ("volume_step", "volume_step"), ("trade_contract_size", "contract_size"), ("point", "point"), ("digits", "digits")):
                value = getattr(info, source, None)
                if value is not None:
                    spec[target] = float(value)
            spec["currency_profit"] = getattr(info, "currency_profit", None)
        now = self.clock()
        metadata = {"symbol": symbol, "timeframe": timeframe, "requested_count": count, "downloaded_this_run": len(downloaded), "count": len(merged), "first_time": merged.time.min().isoformat(), "last_time": merged.time.max().isoformat(), "downloaded_at": now.isoformat(), "contract_spec": spec, "source": "MetaTrader5 terminal", "csv_path": str(active_csv), "archive_csv_path": str(archive_csv)}
        metadata_path = archive_dir / f"{timeframe}.metadata.json"
        meta_tmp = metadata_path.with_name(f".{metadata_path.name}.{nonce}.tmp")
        meta_tmp.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        meta_tmp.replace(metadata_path)
        limitations = []
        if len(downloaded) < count:
            limitations.append(f"MT5 entregó {len(downloaded)} de {count} velas solicitadas; el terminal o bróker puede limitar el histórico disponible.")
        return HistoryResult(symbol, timeframe, len(downloaded), len(merged), merged.time.min().isoformat(), merged.time.max().isoformat(), str(active_csv), str(metadata_path), "válido: cronológico, OHLC consistente y sin timestamps duplicados", limitations)

    def download_ticks(self, symbol: str, start, end, progress=None) -> dict:
        """Save raw broker ticks separately; equal timestamps can be valid distinct events."""
        if not SYMBOL_RE.fullmatch(symbol):
            raise ValueError("Nombre de símbolo inválido")
        if symbol not in self.mt5.symbols():
            raise ValueError(f"El símbolo exacto {symbol} no existe en los símbolos del terminal")
        start_dt, end_dt = _aware(start, datetime(1970, 1, 1, tzinfo=timezone.utc)), _aware(end, self.clock())
        if start_dt >= end_dt:
            raise ValueError("La fecha inicial debe ser anterior a la fecha final")
        parts, cursor, pages = [], start_dt, 0
        page = timedelta(days=1)
        while cursor < end_dt:
            upper = min(cursor + page, end_dt)
            ticks = self.mt5.copy_ticks_range(symbol, cursor, upper)
            error = self.mt5.last_error()
            if ticks is None:
                raise RuntimeError(f"MT5 falló descargando ticks de {symbol}: {error}")
            batch = pd.DataFrame(ticks)
            if not batch.empty:
                if "time_msc" in batch:
                    batch["timestamp"] = pd.to_datetime(batch.time_msc, unit="ms", utc=True, errors="coerce")
                elif "time" in batch:
                    batch["timestamp"] = pd.to_datetime(batch.time, unit="s", utc=True, errors="coerce")
                else:
                    raise ValueError("MT5 devolvió ticks sin time_msc ni time")
                for col in ("bid", "ask", "last", "volume", "volume_real", "flags"):
                    if col not in batch: batch[col] = 0
                    batch[col] = pd.to_numeric(batch[col], errors="coerce").fillna(0)
                batch = batch.dropna(subset=["timestamp"])
                if (batch[["bid", "ask", "last", "volume", "volume_real"]] < 0).any().any():
                    raise ValueError("MT5 devolvió ticks con precios o volúmenes negativos")
                parts.append(batch[["timestamp", "bid", "ask", "last", "volume", "volume_real", "flags"]])
            cursor = upper; pages += 1
            if progress: progress({"pages_done": pages, "pages_total": max(1, (end_dt-start_dt+page-timedelta(seconds=1))//page), "ticks_received": sum(len(x) for x in parts)})
        if not parts:
            raise ValueError(f"MT5 no devolvió ticks para {symbol} en el rango solicitado; compruebe el historial de ticks del terminal")
        frame = pd.concat(parts, ignore_index=True).drop_duplicates().sort_values("timestamp", kind="stable").reset_index(drop=True)
        if not frame.timestamp.is_monotonic_increasing:
            raise ValueError("Los ticks no se pudieron ordenar cronológicamente")
        directory = self.data_dir / "mt5" / symbol
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "ticks.csv"
        tmp = path.with_name(f".ticks.{uuid4().hex}.tmp")
        frame.to_csv(tmp, index=False); tmp.replace(path)
        metadata = {"symbol": symbol, "source": "MetaTrader5 terminal", "start": start_dt.isoformat(), "end": end_dt.isoformat(), "first_time": frame.timestamp.iloc[0].isoformat(), "last_time": frame.timestamp.iloc[-1].isoformat(), "count": len(frame), "downloaded_at": self.clock().isoformat(), "csv_path": str(path)}
        meta = directory / "ticks.metadata.json"; meta_tmp = meta.with_suffix(".json.tmp")
        meta_tmp.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"); meta_tmp.replace(meta)
        return metadata


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


def read_ticks(symbol: str, data_dir: str | Path = "data") -> pd.DataFrame | None:
    if not SYMBOL_RE.fullmatch(symbol):
        raise ValueError("Nombre de símbolo inválido")
    path=Path(data_dir)/"mt5"/symbol/"ticks.csv"
    if not path.exists(): return None
    frame=pd.read_csv(path)
    required={"timestamp","bid","ask"}
    if not required.issubset(frame.columns): raise ValueError(f"CSV de ticks inválido; faltan columnas: {', '.join(sorted(required-set(frame.columns)))}")
    frame["timestamp"]=pd.to_datetime(frame.timestamp,utc=True,errors="coerce")
    for col in ("bid","ask"):
        frame[col]=pd.to_numeric(frame[col],errors="coerce")
    frame=frame.dropna(subset=["timestamp","bid","ask"]).sort_values("timestamp",kind="stable").reset_index(drop=True)
    if frame.empty or (frame[["bid","ask"]] < 0).any().any(): raise ValueError("CSV de ticks vacío o con cotizaciones inválidas")
    return frame

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from app.market_data.history import HistoryService, discover_symbols, read_history, read_metadata
from app.market_data.validation import validate_candles
from app.backtesting.engine import run_backtest


class FakeMT5:
    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 15
    TIMEFRAME_H1 = 60
    TIMEFRAME_H4 = 240

    def __init__(self, rates):
        self.rates = rates
        self.calls = []

    def symbols(self):
        return ["EURUSD", "EURUSDmicro", "xGOLDmicro", "XAUUSDm"]

    def timeframe_value(self, name):
        return getattr(self, name)

    def copy_rates_range(self, symbol, timeframe, start, end):
        self.calls.append((symbol, timeframe, start, end))
        return [r for r in self.rates if start.timestamp() <= r["time"] <= end.timestamp()]

    def last_error(self):
        return (0, "ok")

    def symbol_info(self, symbol):
        return SimpleNamespace(trade_tick_size=0.00001, trade_tick_value=1.0, volume_min=.01, volume_max=100, volume_step=.01, currency_profit="USD")


def rates(n=120, start=None):
    start = start or datetime(2024, 1, 1, tzinfo=timezone.utc)
    result = []
    price = 1.1
    for i in range(n):
        price += .0002 if i % 9 < 5 else -.0001
        result.append({"time": int((start + timedelta(minutes=i)).timestamp()), "open": price, "high": price + .00015, "low": price - .00015, "close": price + .00003, "tick_volume": 12+i, "spread": 2, "real_volume": 0})
    return result


def test_symbol_discovery_keeps_standard_and_micro_separate():
    result = discover_symbols(["EURUSD", "EURUSDmicro", "xGOLDmicro", "XAUUSDm", "junk"])
    assert {m["symbol"] for m in result["matches"]} == {"EURUSD", "EURUSDmicro", "xGOLDmicro", "XAUUSDm"}
    assert next(m for m in result["matches"] if m["symbol"] == "EURUSD")["micro"] is False
    assert next(m for m in result["matches"] if m["symbol"] == "EURUSDmicro")["micro"] is True
    assert next(m for m in result["matches"] if m["symbol"] == "XAUUSDm")["micro"] is True
    assert {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"}.issubset({m["instrument"] for m in discover_symbols(["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"])["matches"]})


def test_download_saves_exact_symbol_metadata_and_deduplicates(tmp_path):
    api = FakeMT5(rates())
    service = HistoryService(api, tmp_path)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(hours=3)
    first = service.download("EURUSDmicro", "M1", start, end)
    again = service.download("EURUSDmicro", "M1", start, end)
    assert first.count_downloaded == 120
    assert again.count_total == 120
    assert (tmp_path / "EURUSDmicro.csv").exists()
    assert first.csv_path.endswith("EURUSDmicro.csv")
    frame = read_history("EURUSDmicro", "M1", tmp_path)
    assert list(frame.columns) == ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]
    assert frame.time.is_monotonic_increasing and not frame.time.duplicated().any()
    assert read_metadata("EURUSDmicro", "M1", tmp_path)["contract_spec"]["tick_value"] == 1


def test_download_paginates_and_merges_extended_range(tmp_path):
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    api = FakeMT5(rates(60, start) + rates(60, start + timedelta(days=31)))
    service = HistoryService(api, tmp_path)
    result = service.download("EURUSD", "M1", start, start + timedelta(days=32))
    assert len(api.calls) == 2
    assert result.count_total == 120


def test_empty_mt5_history_has_clear_error(tmp_path):
    with pytest.raises(ValueError, match="no devolvió velas"):
        HistoryService(FakeMT5([]), tmp_path).download("EURUSD", "M5", "2024-01-01", "2024-01-02")


def test_backtest_reports_insufficient_history_clearly():
    with pytest.raises(ValueError, match=">=60 velas de entrenamiento y >=20 fuera de muestra"):
        run_backtest(pd.DataFrame(rates(79)))


def test_invalid_ohlc_is_rejected_and_uploaded_csv_readable(tmp_path):
    frame = pd.DataFrame(rates(4))
    frame.loc[0, "high"] = -1
    with pytest.raises(ValueError):
        validate_candles(frame)
    with pytest.raises(ValueError, match="Nombre de símbolo inválido"):
        read_history("..", "H1", tmp_path)


def test_existing_csv_is_normalized_and_read(tmp_path):
    root = tmp_path / "mt5" / "EURUSD"
    root.mkdir(parents=True)
    pd.DataFrame(rates(3)).iloc[::-1].to_csv(root / "H1.csv", index=False)
    frame = read_history("EURUSD", "H1", tmp_path)
    assert len(frame) == 3
    assert frame.time.is_monotonic_increasing
    assert set(("tick_volume", "spread", "real_volume")).issubset(frame.columns)


def test_downloaded_csv_can_feed_backtest_with_contract_metadata(tmp_path):
    api = FakeMT5(rates(240))
    HistoryService(api, tmp_path).download("EURUSD", "M1", "2024-01-01", "2024-01-02")
    frame = read_history("EURUSD", "M1", tmp_path)
    spec = read_metadata("EURUSD", "M1", tmp_path)["contract_spec"]
    result = run_backtest(frame, contract_spec=spec)
    assert result["out_of_sample_start"]
    assert result["trade_count"] >= 0


def test_contract_sizing_and_execution_costs_are_applied(monkeypatch):
    import app.strategies.indicators as indicators
    n = 100
    frame = pd.DataFrame({"time": pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC"), "open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0})
    split = int(n * .7)
    frame.loc[split + 2, "low"] = 98.4
    def forced_signal(data):
        out = data.copy()
        out["ema_fast"], out["ema_slow"] = 0.0, 0.0
        out["macd"], out["macd_signal"] = 0.0, 0.0
        out["rsi"], out["adx"], out["atr"] = 50.0, 0.0, 1.0
        out.loc[split, ["ema_fast", "ema_slow", "macd", "macd_signal", "rsi", "adx"]] = [2, 1, 1, 0, 60, 20]
        return out
    monkeypatch.setattr(indicators, "add_indicators", forced_signal)
    result = run_backtest(frame, risk_fraction=.005, spread=.02, commission=.5, slippage=.01, contract_spec={"tick_size": .1, "tick_value": 2, "volume_min": .01, "volume_max": 100, "volume_step": .01})
    assert result["trade_count"] == 1
    trade = result["trades"][0]
    assert trade["qty"] == pytest.approx(1.66)
    assert trade["pnl"] < -50  # price move, spread, slippage and both commission sides

import numpy as np
import pandas as pd
import pytest

from app.backtesting.vectorbt_sweep import run_vectorbt_sweep, crossover_signals


def test_vectorbt_sweep_vectorizes_parameters_and_reports_out_of_sample(tmp_path, monkeypatch):
    monkeypatch.setenv("NUMBA_CACHE_DIR", str(tmp_path / "numba"))
    n = 180
    close = 100 + np.arange(n) * .02 + np.sin(np.arange(n) / 5)
    frame = pd.DataFrame({
        "time": pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC"),
        "open": close - .01,
        "close": close,
    })
    result = run_vectorbt_sweep(frame, commission=.1, spread=.02, slippage=.01)
    assert result["engine"] == "VectorBT"
    assert result["selected_strategy"] in {"SMA 5/21", "SMA 5/34", "SMA 5/55", "SMA 8/21", "SMA 8/34", "SMA 8/55", "SMA 13/21", "SMA 13/34", "SMA 13/55"}
    assert len(result["candidates"]) == 9
    assert result["out_of_sample_bars"] == 55
    assert "apertura de la vela siguiente" in result["execution"]
    assert result["gross_profit"] - result["total_costs"] == pytest.approx(result["net_profit"])
    assert "trades" in result and "signals" in result
    assert result["cost_assumptions"]["commission"].startswith("comisión monetaria fija")
    if result["signals"]:
        first = result["signals"][0]
        assert first["fill_time"] > first["time"]
        assert first["reason"]


def test_crossover_indicators_and_signals_do_not_look_ahead():
    close = pd.Series(100 + np.sin(np.arange(120) / 4) + np.arange(120) * .01)
    _, _, entries, exits = crossover_signals(close, 5, 21)
    changed_future = close.copy()
    changed_future.iloc[80:] += 1000
    _, _, altered_entries, altered_exits = crossover_signals(changed_future, 5, 21)
    pd.testing.assert_series_equal(entries.iloc[:80], altered_entries.iloc[:80])
    pd.testing.assert_series_equal(exits.iloc[:80], altered_exits.iloc[:80])


def test_vectorbt_costs_reduce_net_return_and_store_trade_reasons():
    n = 240
    close = 100 + np.arange(n) * .015 + np.sin(np.arange(n) / 4)
    frame = pd.DataFrame({"time": pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC"), "open": close - .02, "close": close})
    no_cost = run_vectorbt_sweep(frame)
    with_cost = run_vectorbt_sweep(frame, commission=.25, spread=.04, slippage=.01)
    assert with_cost["total_costs"] > 0
    assert with_cost["net_profit"] <= no_cost["net_profit"]
    assert with_cost["gross_profit"] - with_cost["total_costs"] == pytest.approx(with_cost["net_profit"])
    assert all(trade["entry_reason"] and trade["exit_reason"] for trade in with_cost["trades"])

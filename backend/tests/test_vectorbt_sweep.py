import numpy as np
import pandas as pd
import pytest

from app.backtesting.vectorbt_sweep import run_vectorbt_sweep


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

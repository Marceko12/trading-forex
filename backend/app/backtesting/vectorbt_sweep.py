"""Vectorized moving-average parameter sweep, kept separate from the rule engine."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd


FAST_WINDOWS = (5, 8, 13)
SLOW_WINDOWS = (21, 34, 55)


def run_vectorbt_sweep(frame, initial_balance=10_000, commission=0.0, spread=0.0, slippage=0.0, train_fraction=.7):
    """Compare long-only MA crossovers; choose on train, report held-out result.

    A signal from close[t] is shifted to the next bar and filled at open[t+1].
    This research sweep intentionally omits stop/target logic; use the existing
    event-driven engine for its ATR stops and conservative same-candle handling.
    """
    # The sandboxed Windows launcher may not provide a writable user temp folder.
    # Give Numba an isolated, project-local cache without importing it at API startup.
    cache = Path(__file__).resolve().parents[2] / ".numba_cache"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("NUMBA_CACHE_DIR", str(cache))
    try:
        import vectorbt as vbt
    except Exception as exc:
        raise RuntimeError(f"VectorBT no se pudo importar en este Python: {exc}") from exc

    data = frame.copy().reset_index(drop=True)
    if len(data) < 80:
        raise ValueError("VectorBT requiere al menos 80 velas para la separación de entrenamiento y prueba")
    if "open" not in data or "close" not in data:
        raise ValueError("VectorBT requiere columnas open y close")
    split = int(len(data) * train_fraction)
    if split < 40 or len(data) - split < 20:
        raise ValueError("Se requieren >=40 velas de entrenamiento y >=20 fuera de muestra")

    close = pd.Series(data.close.to_numpy(dtype=float), index=data.index)
    price = pd.Series(data.open.to_numpy(dtype=float), index=data.index)
    fast = {w: close.rolling(w, min_periods=w).mean() for w in FAST_WINDOWS}
    slow = {w: close.rolling(w, min_periods=w).mean() for w in SLOW_WINDOWS}
    names, entries, exits = [], [], []
    for fw in FAST_WINDOWS:
        for sw in SLOW_WINDOWS:
            if fw >= sw:
                continue
            names.append(f"SMA {fw}/{sw}")
            entries.append(((fast[fw] > slow[sw]) & (fast[fw].shift(1) <= slow[sw].shift(1))).fillna(False))
            exits.append(((fast[fw] < slow[sw]) & (fast[fw].shift(1) >= slow[sw].shift(1))).fillna(False))
    entry = pd.concat(entries, axis=1); entry.columns = names
    exit_ = pd.concat(exits, axis=1); exit_.columns = names
    # Signals form at a completed close; orders execute on the following open.
    entry = entry.shift(1, fill_value=False)
    exit_ = exit_.shift(1, fill_value=False)
    train_entry, train_exit = entry.copy(), exit_.copy()
    train_entry.iloc[split:, :] = False; train_exit.iloc[split:, :] = False
    train_pf = vbt.Portfolio.from_signals(
        price, train_entry, train_exit, init_cash=initial_balance,
        size=np.inf, fixed_fees=commission,
        slippage=((spread / 2 + slippage) / price).replace([np.inf, -np.inf], 0).fillna(0),
        freq="1D",
    )
    train_returns = train_pf.total_return()
    selected = str(train_returns.astype(float).idxmax())
    oos_entry, oos_exit = entry[[selected]].copy(), exit_[[selected]].copy()
    # Avoid carrying an in-sample open position or executing a signal formed
    # on the final training close on the first held-out bar.
    oos_entry.iloc[:split + 1, :] = False; oos_exit.iloc[:split + 1, :] = False
    oos_pf = vbt.Portfolio.from_signals(
        price, oos_entry, oos_exit, init_cash=initial_balance,
        size=np.inf, fixed_fees=commission,
        slippage=((spread / 2 + slippage) / price).replace([np.inf, -np.inf], 0).fillna(0),
        freq="1D",
    )
    total_return = float(oos_pf.total_return().iloc[0])
    max_dd = float(oos_pf.max_drawdown().iloc[0])
    equity_values = oos_pf.value().to_numpy(dtype=float).reshape(-1)
    equity_curve = [{"time": str(data.time.iloc[i]), "equity": float(value)}
                    for i, value in enumerate(equity_values) if i >= split]
    trades = oos_pf.trades
    trade_count = int(trades.count().iloc[0])
    win_rate = float(trades.win_rate().iloc[0] * 100) if trade_count else 0.0
    profit_factor = float(trades.profit_factor().iloc[0]) if trade_count else 0.0
    order_records = oos_pf.orders.records
    order_count = len(order_records)
    traded_units = float(order_records["size"].abs().sum()) if order_count else 0.0
    commission_costs = float(order_records["fees"].sum()) if order_count else 0.0
    spread_costs = traded_units * spread / 2
    slippage_costs = traded_units * slippage
    # Return all training candidates, but expose OOS only for the training winner.
    candidates = [{"strategy": str(key), "train_return_pct": float(value) * 100}
                  for key, value in train_returns.items()]
    candidates.sort(key=lambda row: row["train_return_pct"], reverse=True)
    return {
        "engine": "VectorBT",
        "selected_strategy": selected,
        "selection_basis": "solo rentabilidad del periodo de entrenamiento",
        "train_start": str(data.time.iloc[0]) if "time" in data else None,
        "out_of_sample_start": str(data.time.iloc[split]) if "time" in data else None,
        "out_of_sample_end": str(data.time.iloc[-1]) if "time" in data else None,
        "out_of_sample_bars": len(data) - split,
        "data_count": len(data),
        "net_profit": float(initial_balance * total_return),
        "gross_profit": float(initial_balance * total_return + commission_costs + spread_costs + slippage_costs),
        "return_pct": total_return * 100,
        "max_drawdown_pct": abs(max_dd) * 100,
        "trade_count": trade_count,
        "win_rate_pct": win_rate,
        "profit_factor": profit_factor,
        "commission_per_order": commission,
        "commission_costs": commission_costs,
        "spread_costs": spread_costs,
        "slippage_costs": slippage_costs,
        "total_costs": commission_costs + spread_costs + slippage_costs,
        "spread_price_units": spread,
        "slippage_price_units": slippage,
        "execution": "señal al cierre, orden en apertura de la vela siguiente; sin SL/TP; spread y slippage aplicados como fricción proporcional",
        "candidates": candidates,
        "equity_curve": equity_curve,
    }

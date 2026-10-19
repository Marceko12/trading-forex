"""Causal moving-average crossover sweep executed by VectorBT."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd


FAST_WINDOWS = (5, 8, 13)
SLOW_WINDOWS = (21, 34, 55)


def crossover_signals(close: pd.Series, fast_window: int, slow_window: int):
    """Return close-confirmed crossover booleans using trailing data only."""
    fast = close.rolling(fast_window, min_periods=fast_window).mean()
    slow = close.rolling(slow_window, min_periods=slow_window).mean()
    entries = ((fast > slow) & (fast.shift(1) <= slow.shift(1))).fillna(False)
    exits = ((fast < slow) & (fast.shift(1) >= slow.shift(1))).fillna(False)
    return fast, slow, entries, exits


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
        size=0.95, size_type="percent", fixed_fees=commission,
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
        size=0.95, size_type="percent", fixed_fees=commission,
        slippage=((spread / 2 + slippage) / price).replace([np.inf, -np.inf], 0).fillna(0),
        freq="1D",
    )
    total_return = float(oos_pf.total_return().iloc[0])
    if not np.isfinite(total_return):
        raise RuntimeError("VectorBT produjo un retorno no finito; revise los datos y los costes")
    max_dd = float(oos_pf.max_drawdown().iloc[0])
    equity_values = oos_pf.value().to_numpy(dtype=float).reshape(-1)
    curve_indexes = np.arange(split, len(data))
    if len(curve_indexes) > 600:
        curve_indexes = np.unique(np.linspace(split, len(data) - 1, 600, dtype=int))
    equity_curve = [{"time": str(data.time.iloc[i]), "equity": float(equity_values[i])}
                    for i in curve_indexes]
    trades = oos_pf.trades
    trade_count = int(trades.count().iloc[0])
    win_rate = float(trades.win_rate().iloc[0] * 100) if trade_count else 0.0
    profit_factor_raw = float(trades.profit_factor().iloc[0]) if trade_count else 0.0
    profit_factor = profit_factor_raw if np.isfinite(profit_factor_raw) else (None if np.isnan(profit_factor_raw) else "Infinity")
    order_records = oos_pf.orders.records
    order_count = len(order_records)
    traded_units = float(order_records["size"].abs().sum()) if order_count else 0.0
    commission_costs = float(order_records["fees"].sum()) if order_count else 0.0
    spread_costs = traded_units * spread / 2
    slippage_costs = traded_units * slippage
    # Attach each completed OOS round trip to the causal crossover that opened it.
    signal_events = []
    fast_window, slow_window = (int(value) for value in selected.replace("SMA ", "").split("/"))
    selected_fast, selected_slow, raw_entry, raw_exit = crossover_signals(close, fast_window, slow_window)
    signal_reasons = {}
    for i in range(split + 1, len(data) - 1):
        if bool(raw_entry.iloc[i]):
            signal_reasons[i + 1] = "BUY: SMA rápida cruzó por encima de SMA lenta al cierre; ejecución en la apertura siguiente"
            signal_events.append({"time": str(data.time.iloc[i]), "fill_time": str(data.time.iloc[i + 1]), "decision": "BUY", "reason": signal_reasons[i + 1], "close": float(data.close.iloc[i]), "sma_fast": float(selected_fast.iloc[i]), "sma_slow": float(selected_slow.iloc[i])})
        elif bool(raw_exit.iloc[i]):
            signal_reasons[i + 1] = "CLOSE: SMA rápida cruzó por debajo de SMA lenta al cierre; ejecución en la apertura siguiente"
            signal_events.append({"time": str(data.time.iloc[i]), "fill_time": str(data.time.iloc[i + 1]), "decision": "CLOSE", "reason": signal_reasons[i + 1], "close": float(data.close.iloc[i]), "sma_fast": float(selected_fast.iloc[i]), "sma_slow": float(selected_slow.iloc[i])})
    trade_rows = []
    records = trades.records
    for record in records.to_dict("records"):
        entry_idx = int(record.get("entry_idx", 0))
        exit_idx = int(record.get("exit_idx", len(data) - 1))
        status = record.get("status", 0)
        closed = int(status) == 1 if isinstance(status, (int, np.integer)) else str(status).lower() in {"closed", "1"}
        pnl = float(record.get("pnl", 0.0))
        trade_rows.append({
            "entry_time": str(data.time.iloc[entry_idx]), "exit_time": str(data.time.iloc[exit_idx]),
            "side": "long", "size_units": float(record.get("size", 0.0)),
            "entry_price": float(record.get("entry_price", 0.0)), "exit_price": float(record.get("exit_price", 0.0)),
            "gross_pnl": float(record.get("pnl", 0.0)) + float(record.get("entry_fees", 0.0)) + float(record.get("exit_fees", 0.0)),
            "net_pnl": pnl, "return_pct": float(record.get("return", 0.0)) * 100,
            "entry_reason": signal_reasons.get(entry_idx, "Entrada de señal SMA cerrada antes de inicio OOS"),
            "exit_reason": signal_reasons.get(exit_idx, "Cierre al final del periodo OOS" if not closed else "Salida de estrategia SMA"),
            "status": "closed" if closed else "open_at_oos_end",
        })
    # Return all training candidates, but expose OOS only for the training winner.
    candidates = [{"strategy": str(key), "train_return_pct": float(value) * 100}
                  for key, value in train_returns.items()]
    candidates.sort(key=lambda row: row["train_return_pct"], reverse=True)
    return {
        "engine": "VectorBT",
        "initial_balance": float(initial_balance),
        "selected_strategy": selected,
        "strategy_description": f"Cruce SMA {fast_window}/{slow_window}: entrada larga al cruce alcista, salida al cruce bajista; señal al cierre y ejecución a la apertura posterior.",
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
        "execution": "Señal al cierre, ejecución en la apertura de la vela siguiente; sin SL/TP; VectorBT asigna 95% del efectivo disponible a cada posición, permite unidades fraccionarias y no aplica mínimos/pasos de lote MT5.",
        "cost_assumptions": {"commission": "comisión monetaria fija por cada orden (entrada y salida)", "spread": "spread total expresado en unidades de precio; media aplicada a cada lado", "slippage": "deslizamiento en unidades de precio aplicado en entrada y salida"},
        "candidates": candidates,
        "equity_curve": equity_curve,
        "trades": trade_rows,
        "signals": signal_events,
    }

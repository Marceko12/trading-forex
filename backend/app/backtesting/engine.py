import numpy as np
import pandas as pd
import math
from ..market_data.validation import validate_candles

def run_backtest(frame, initial_balance=10_000, risk_fraction=.005, spread=0, commission=0, slippage=0, train_fraction=.7, contract_spec=None):
    """Conservative next-bar execution; stop/target ambiguity resolves to stop first."""
    from ..strategies.indicators import add_indicators
    d = add_indicators(validate_candles(frame))
    split = int(len(d) * train_fraction)
    if split < 60 or len(d)-split < 20: raise ValueError("Se requieren >=60 velas de entrenamiento y >=20 fuera de muestra")
    equity, peak, max_dd = initial_balance, initial_balance, 0.0
    trades=[]; curve=[]; position=None
    # Signals are computed from the completed prior bar and filled at current open.
    for i in range(split+1, len(d)):
        prior, bar = d.iloc[i-1], d.iloc[i]
        if position:
            hit_sl = bar.low <= position["sl"] if position["side"] == 1 else bar.high >= position["sl"]
            hit_tp = bar.high >= position["tp"] if position["side"] == 1 else bar.low <= position["tp"]
            if hit_sl or hit_tp:
                raw_exit = position["sl"] if hit_sl else position["tp"]
                exit_price = raw_exit - position["side"]*(spread/2+slippage)
                price_pnl = (exit_price-position["entry"]) * position["side"]
                gross_pnl = price_pnl * position["qty"] if position.get("tick_size") is None else price_pnl / position["tick_size"] * position["tick_value"] * position["qty"]
                trade_cost = commission * position["qty"] if position.get("tick_size") is not None else commission
                equity += gross_pnl - trade_cost
                trades.append({**position,"exit":float(exit_price),"pnl":float(gross_pnl - 2*trade_cost),"duration_bars":i-position["index"],"exit_reason":"SL" if hit_sl else "TP"})
                position=None
        if position is None and pd.notna(prior.atr) and pd.notna(prior.adx) and prior.adx >= 18:
            long = prior.ema_fast > prior.ema_slow and prior.macd > prior.macd_signal and 45 <= prior.rsi <= 70
            short = prior.ema_fast < prior.ema_slow and prior.macd < prior.macd_signal and 30 <= prior.rsi <= 55
            side = 1 if long else (-1 if short else 0)
            if side:
                entry=float(bar.open + side*(spread/2+slippage)); stop_dist=float(prior.atr*1.5)
                if contract_spec and contract_spec.get("tick_size", 0) > 0 and contract_spec.get("tick_value", 0) > 0:
                    tick_size, tick_value = float(contract_spec["tick_size"]), float(contract_spec["tick_value"])
                    raw_qty = equity*risk_fraction / (stop_dist/tick_size*tick_value)
                    volume_min=float(contract_spec.get("volume_min", 0.01)); volume_max=float(contract_spec.get("volume_max", 100)); volume_step=float(contract_spec.get("volume_step", volume_min))
                    qty = min(volume_max, volume_min + math.floor((raw_qty-volume_min)/volume_step + 1e-10)*volume_step) if volume_step > 0 and raw_qty >= volume_min else 0
                    if qty < volume_min: qty = 0
                else:
                    tick_size = tick_value = None
                    qty=equity*risk_fraction/stop_dist
                if qty > 0:
                    position={"side":side,"entry":entry,"sl":entry-side*stop_dist,"tp":entry+side*stop_dist*2,"qty":qty,"index":i,"tick_size":tick_size,"tick_value":tick_value}
                    # Costs at entry; quantity is price-unit based for generic OHLC data.
                    equity-=commission*qty if tick_size is not None else commission
        peak=max(peak,equity); max_dd=max(max_dd,(peak-equity)/peak if peak else 0)
        curve.append({"time":bar.time.isoformat(),"equity":float(equity)})
    if position:
        last=float(d.iloc[-1].close)-position["side"]*(spread/2+slippage); price_pnl=(last-position["entry"])*position["side"]
        gross_pnl=price_pnl*position["qty"] if position.get("tick_size") is None else price_pnl/position["tick_size"]*position["tick_value"]*position["qty"]
        trade_cost=commission*position["qty"] if position.get("tick_size") is not None else commission
        equity+=gross_pnl-trade_cost; trades.append({**position,"exit":last,"pnl":float(gross_pnl-2*trade_cost),"duration_bars":len(d)-1-position["index"],"exit_reason":"END"})
        if curve:
            curve[-1]["equity"] = float(equity)
        peak=max(peak,equity); max_dd=max(max_dd,(peak-equity)/peak if peak else 0)
    pnls=np.array([t["pnl"] for t in trades],dtype=float)
    wins=pnls[pnls>0]; losses=pnls[pnls<0]
    returns=np.diff(np.array([initial_balance]+[x["equity"] for x in curve]))
    sharpe=float(np.mean(returns)/np.std(returns,ddof=1)*np.sqrt(252)) if len(returns)>1 and np.std(returns,ddof=1)>0 else 0.0
    return {"initial_balance":initial_balance,"final_balance":float(equity),"net_profit":float(equity-initial_balance),"return_pct":float((equity/initial_balance-1)*100),"profit_factor":float(wins.sum()/abs(losses.sum())) if len(losses) and len(wins) else (None if len(wins) else 0),"win_rate_pct":float(len(wins)/len(trades)*100) if trades else 0,"expectancy":float(pnls.mean()) if trades else 0,"max_drawdown_pct":float(max_dd*100),"sharpe_ratio":sharpe,"sharpe_assumption":"Ratio de Sharpe: media/desviación estándar de cambios de equity por barra, anualizada con sqrt(252); no es retorno diario; comparar solo misma temporalidad.","trade_count":len(trades),"commission_per_side":commission,"spread_price_units":spread,"slippage_price_units":slippage,"out_of_sample_start":d.iloc[split].time.isoformat(),"trades":trades,"equity_curve":curve}

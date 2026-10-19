import numpy as np
import pandas as pd
from ..market_data.validation import validate_candles

def run_backtest(frame, initial_balance=10_000, risk_fraction=.005, spread=0, commission=0, slippage=0, train_fraction=.7):
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
                exit_price = position["sl"] if hit_sl else position["tp"]
                pnl = (exit_price-position["entry"]) * position["side"] * position["qty"] - commission
                equity += pnl
                trades.append({**position,"exit":float(exit_price),"pnl":float(pnl),"duration_bars":i-position["index"],"exit_reason":"SL" if hit_sl else "TP"})
                position=None
        if position is None and pd.notna(prior.atr) and pd.notna(prior.adx) and prior.adx >= 18:
            long = prior.ema_fast > prior.ema_slow and prior.macd > prior.macd_signal and 45 <= prior.rsi <= 70
            short = prior.ema_fast < prior.ema_slow and prior.macd < prior.macd_signal and 30 <= prior.rsi <= 55
            side = 1 if long else (-1 if short else 0)
            if side:
                entry=float(bar.open + side*(spread/2+slippage)); stop_dist=float(prior.atr*1.5)
                qty=equity*risk_fraction/stop_dist
                if qty > 0:
                    position={"side":side,"entry":entry,"sl":entry-side*stop_dist,"tp":entry+side*stop_dist*2,"qty":qty,"index":i}
                    # Costs at entry; quantity is price-unit based for generic OHLC data.
                    equity-=commission
        peak=max(peak,equity); max_dd=max(max_dd,(peak-equity)/peak if peak else 0)
        curve.append({"time":bar.time.isoformat(),"equity":float(equity)})
    if position:
        last=float(d.iloc[-1].close); pnl=(last-position["entry"])*position["side"]*position["qty"]-commission
        equity+=pnl; trades.append({**position,"exit":last,"pnl":float(pnl),"duration_bars":len(d)-1-position["index"],"exit_reason":"END"})
    pnls=np.array([t["pnl"] for t in trades],dtype=float)
    wins=pnls[pnls>0]; losses=pnls[pnls<0]
    returns=np.diff(np.array([initial_balance]+[x["equity"] for x in curve]))
    sharpe=float(np.mean(returns)/np.std(returns,ddof=1)*np.sqrt(252)) if len(returns)>1 and np.std(returns,ddof=1)>0 else 0.0
    return {"initial_balance":initial_balance,"final_balance":float(equity),"net_profit":float(equity-initial_balance),"return_pct":float((equity/initial_balance-1)*100),"profit_factor":float(wins.sum()/abs(losses.sum())) if len(losses) and len(wins) else (float("inf") if len(wins) else 0),"win_rate_pct":float(len(wins)/len(trades)*100) if trades else 0,"expectancy":float(pnls.mean()) if trades else 0,"max_drawdown_pct":float(max_dd*100),"sharpe_ratio":sharpe,"sharpe_assumption":"Ratio de Sharpe: media/desviación estándar de cambios de equity por barra, anualizada con sqrt(252); no es retorno diario; comparar solo misma temporalidad.","trade_count":len(trades),"commission_per_side":commission,"spread_price_units":spread,"slippage_price_units":slippage,"out_of_sample_start":d.iloc[split].time.isoformat(),"trades":trades,"equity_curve":curve}

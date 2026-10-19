import pandas as pd
import pytest
from app.market_data.validation import validate_candles
from app.risk.manager import position_size, within_daily_limit, within_drawdown
from app.strategies.baseline import decide
from app.backtesting.engine import run_backtest

def sample(n=240):
    close=[100+i*.08+2*__import__('math').sin(i/7) for i in range(n)]
    return pd.DataFrame({"time":pd.date_range("2020-01-01",periods=n,freq="h",tz="UTC"),"open":close,"high":[x+.5 for x in close],"low":[x-.5 for x in close],"close":close})

def test_validation_sorts_and_rejects_bad_ohlc():
    d=sample(4).iloc[::-1]
    assert validate_candles(d).time.is_monotonic_increasing
    d.loc[d.index[0],"high"]=0
    with pytest.raises(ValueError): validate_candles(d)

def test_position_size_obeys_money_risk_and_volume_step():
    assert position_size(10_000,.005,2000,1990,.1,1,.01,100,.01)==.5

def test_risk_limits():
    assert not within_daily_limit(10_000,9_800)
    assert not within_drawdown(10_000,9_500)

def test_decision_no_trade_with_short_history():
    decision,_=decide(sample(10))
    assert decision.value=="NO TRADE"

def test_backtest_out_of_sample_and_metrics():
    result=run_backtest(sample())
    assert result["trade_count"]>=0
    assert result["out_of_sample_start"]
    assert "Sharpe" in result["sharpe_assumption"]
    assert len(result["equity_curve"])==240-int(240*.7)-1

from types import SimpleNamespace
from time import time
from datetime import datetime, timedelta, timezone

from app.core.auto_trader import AutoTradingEngine


class FakeBroker:
    def __init__(self, *, mode=0, check=0, positions=None):
        self.account = SimpleNamespace(trade_mode=mode, trade_allowed=True, equity=10_000, margin_free=10_000)
        self.terminal = SimpleNamespace(trade_allowed=True)
        self.tick = SimpleNamespace(time=int(time()), bid=110, ask=110.1)
        self.info = SimpleNamespace(point=.01, trade_tick_size=.01, trade_tick_value=1, volume_min=.01,
                                    volume_max=100, volume_step=.01, trade_stops_level=5, trade_freeze_level=0,
                                    trade_mode=4, digits=2, filling_mode=2)
        self.constants = {"ACCOUNT_TRADE_MODE_DEMO": 0, "ACCOUNT_TRADE_MODE_REAL": 2,
                          "SYMBOL_TRADE_MODE_DISABLED": 0, "ORDER_TYPE_BUY": 0, "ORDER_TYPE_SELL": 1,
                          "TRADE_ACTION_DEAL": 1, "TRADE_RETCODE_DONE": 10009, "TRADE_RETCODE_PLACED": 10008,
                          "TRADE_RETCODE_DONE_PARTIAL": 10010, "ORDER_FILLING_IOC": 1, "ORDER_FILLING_FOK": 0,
                          "SYMBOL_FILLING_IOC": 2, "SYMBOL_FILLING_FOK": 1, "SYMBOL_TRADE_MODE_LONGONLY": 1,
                          "SYMBOL_TRADE_MODE_SHORTONLY": 2, "SYMBOL_TRADE_MODE_CLOSEONLY": 3,
                          "ORDER_TIME_GTC": 0}
        self.check_retcode, self.positions, self.checks, self.orders = check, list(positions or []), [], []

    def account_info(self): return self.account
    def terminal_info(self): return self.terminal
    def constant(self, name): return self.constants.get(name)
    def timeframe_value(self, name):
        return {"M1": 1, "M5": 5, "M15": 15, "H1": 16385, "H4": 16388}.get(str(name).upper().removeprefix("TIMEFRAME_"))
    def symbol_info(self, symbol): return self.info
    def symbol_info_tick(self, symbol): return self.tick
    def positions_get(self, symbol=None): return self.positions
    def last_error(self): return (0, "ok")
    def order_calc_margin(self, *args): return 100
    def check_order(self, request):
        self.checks.append(dict(request)); return SimpleNamespace(retcode=self.check_retcode, comment="check")
    def send_order(self, request):
        self.orders.append(dict(request))
        self.positions.append(SimpleNamespace(ticket=88, symbol=request["symbol"], magic=request["magic"],
            volume=request["volume"], type=request["type"]))
        return SimpleNamespace(retcode=10009, comment="done")


def make_engine(broker, signal=None):
    logs = []
    signal = signal or {"symbol": "GOLDmicro", "timeframe": "H1", "decision": "BUY", "source": "MT5",
                        "entry": 110.1, "stop_loss": 109.0, "take_profit": 112.6,
                        "bar_time": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(), "reason": "EMA cruzó"}
    engine = AutoTradingEngine(broker, lambda: True, lambda symbols, tf: [signal],
                               lambda kind, data: logs.append((kind, data)), interval_seconds=3600)
    return engine, logs


def test_paper_signal_sized_with_broker_specs_and_never_sends_order():
    broker = FakeBroker()
    engine, logs = make_engine(broker)
    engine.start(mode="PAPER", symbol="GOLDmicro", timeframe="H1", limits={}, background=False)
    state = engine.process_once()
    assert not broker.orders and not broker.checks
    assert len(state["paper_positions"]) == 1
    assert state["paper_positions"][0]["volume"] == .45
    assert any(kind == "auto_order_proposed" for kind, _ in logs)
    engine.process_once()
    assert len(state["paper_positions"]) == 1


def test_real_account_and_real_mode_are_blocked_and_demo_requires_explicit_confirmation():
    broker = FakeBroker(mode=2)
    engine, _ = make_engine(broker)
    try:
        engine.start(mode="DEMO", symbol="GOLDmicro", timeframe="H1", limits={}, confirm_demo="OPERAR EN DEMO")
        assert False, "REAL account must be blocked"
    except ValueError as exc: assert "REAL" in str(exc)
    broker.account.trade_mode = 0
    try:
        engine.start(mode="DEMO", symbol="GOLDmicro", timeframe="H1", limits={}, background=False)
        assert False, "DEMO needs opt-in"
    except ValueError as exc: assert "OPERAR EN DEMO" in str(exc)
    try:
        engine.start(mode="REAL", symbol="GOLDmicro", timeframe="H1", limits={})
        assert False, "REAL mode must be blocked"
    except ValueError as exc: assert "REAL está bloqueado" in str(exc)


def test_demo_order_checks_then_confirms_mt5_position():
    broker = FakeBroker()
    engine, logs = make_engine(broker)
    engine.start(mode="DEMO", symbol="GOLDmicro", timeframe="H1", limits={}, confirm_demo="OPERAR EN DEMO", background=False)
    engine.process_once()
    assert len(broker.checks) == 2 and len(broker.orders) == 1
    assert any("DEMO confirmada" in payload["reason"] for kind, payload in logs if kind == "auto_order_result")


def test_order_check_rejection_and_mt5_disconnect_never_send_order():
    broker = FakeBroker(check=10030)
    engine, logs = make_engine(broker)
    engine.start(mode="DEMO", symbol="GOLDmicro", timeframe="H1", limits={}, confirm_demo="OPERAR EN DEMO", background=False)
    engine.process_once()
    assert not broker.orders
    assert any("order_check rechazó" in payload.get("reason", "") for _, payload in logs)
    connected = [True]
    disconnected, disconnect_logs = make_engine(broker)
    disconnected.connected = lambda: connected[0]
    disconnected.start(mode="PAPER", symbol="GOLDmicro", timeframe="H1", limits={}, background=False)
    connected[0] = False
    disconnected.process_once()
    assert any("MT5 desconectado" in payload.get("reason", "") for _, payload in disconnect_logs)


def test_auto_engine_rejects_invalid_mt5_timeframe_before_starting():
    broker = FakeBroker()
    engine, _ = make_engine(broker)
    try:
        engine.start(mode="PAPER", symbol="GOLDmicro", timeframe="D1", limits={}, background=False)
        assert False, "Expected timeframe validation to reject D1"
    except ValueError as exc:
        assert "Temporalidad MT5 inválida: D1" in str(exc)


def test_stale_quote_spread_and_risk_limits_block_entries():
    for mutate, expected in [
        (lambda b: setattr(b.tick, "time", 1), "obsoleto"),
        (lambda b: setattr(b.tick, "ask", 111), "spread supera"),
        (lambda b: setattr(b.account, "equity", 9700), "pérdida diaria"),
        (lambda b: setattr(b.account, "equity", 9400), "drawdown máximo"),
    ]:
        broker = FakeBroker(); mutate(broker)
        engine, logs = make_engine(broker)
        if expected in {"pérdida diaria", "drawdown máximo"}:
            engine._day = "2026-10-19"; engine._day_start = 10_000; engine._peak = 10_000; engine._risk_identity = (None, None)
        engine.start(mode="PAPER", symbol="GOLDmicro", timeframe="H1",
                     limits={"max_spread_points": 10, **({"daily_loss_limit": .1} if expected == "drawdown máximo" else {})},
                     background=False)
        if expected in {"pérdida diaria", "drawdown máximo"}:
            engine._day = "2026-10-19"; engine._day_start = 10_000; engine._peak = 10_000; engine._risk_identity = (None, None)
        engine.process_once()
        assert not broker.orders
        assert any(expected in payload.get("reason", "") for _, payload in logs)


def test_strategy_signal_deduplication_and_hold_reason_logged():
    signal = {"symbol": "GOLDmicro", "timeframe": "H1", "decision": "HOLD", "source": "MT5",
              "bar_time": "same", "reason": "ADX bajo"}
    engine, logs = make_engine(FakeBroker(), signal)
    engine.start(mode="PAPER", symbol="GOLDmicro", timeframe="H1", limits={}, background=False)
    engine.process_once(); engine.process_once()
    assert len([1 for kind, _ in logs if kind == "signal_held"]) == 1
    restored = AutoTradingEngine(FakeBroker(), lambda: True, lambda *_: [signal], lambda kind, data: logs.append((kind, data)),
                                seen_loader=lambda: [data for kind, data in logs if kind == "signal_held"])
    restored.start(mode="PAPER", symbol="GOLDmicro", timeframe="H1", limits={}, background=False)
    restored.process_once()
    assert len([1 for kind, _ in logs if kind == "signal_held"]) == 1


def test_paper_position_closes_on_real_mt5_quote_without_broker_order():
    broker = FakeBroker()
    engine, logs = make_engine(broker)
    engine.start(mode="PAPER", symbol="GOLDmicro", timeframe="H1", limits={}, background=False)
    engine.process_once()
    broker.tick.bid = 112.6
    engine.process_once()
    assert engine.status()["paper_positions"] == [] and broker.orders == []
    assert any(kind == "auto_paper_closed" and payload["exit_reason"] == "take_profit" for kind, payload in logs)

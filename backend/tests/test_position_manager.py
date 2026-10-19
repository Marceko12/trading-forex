from types import SimpleNamespace
from datetime import datetime, timezone
from time import time

import pytest

from app.positions.manager import PositionManager


class FakeMT5:
    def __init__(self, *, trade_mode=0, positions=None):
        self.account = SimpleNamespace(trade_allowed=True, trade_mode=trade_mode, equity=10_000)
        self.terminal = SimpleNamespace(trade_allowed=True)
        self.positions = list(positions or [])
        self.orders = []
        self.spec = SimpleNamespace(point=.01, digits=2, trade_tick_size=.01, trade_tick_value=1,
                                    trade_stops_level=5, volume_min=.01, volume_max=100, volume_step=.01,
                                    currency_profit="USD")
        self.tick = SimpleNamespace(time=int(time()), bid=110.0, ask=110.1)
        self.constants = {"ACCOUNT_TRADE_MODE_REAL": 2, "ACCOUNT_TRADE_MODE_DEMO": 0,
                          "POSITION_TYPE_BUY": 0, "TRADE_ACTION_SLTP": 6,
                          "TRADE_ACTION_DEAL": 1, "ORDER_TYPE_SELL": 1, "ORDER_TYPE_BUY": 0,
                          "TRADE_RETCODE_DONE": 10009}

    def positions_get(self, symbol=None):
        return [p for p in self.positions if symbol is None or p.symbol == symbol]
    def symbol_info(self, symbol): return self.spec
    def symbol_info_tick(self, symbol): return self.tick
    def account_info(self): return self.account
    def terminal_info(self): return self.terminal
    def constant(self, name): return self.constants.get(name)
    def last_error(self): return (0, "ok")
    def send_order(self, request):
        self.orders.append(dict(request))
        if request["action"] == self.constants["TRADE_ACTION_SLTP"]:
            p = next(x for x in self.positions if x.ticket == request["position"])
            p.sl, p.tp = request["sl"], request["tp"]
        elif request["action"] == self.constants["TRADE_ACTION_DEAL"]:
            self.positions = [p for p in self.positions if p.ticket != request["position"]]
        return SimpleNamespace(retcode=10009, order=101, deal=102, comment="done")


def position(ticket=11, *, magic=0, sl=95, tp=120, profit=1, volume=.1):
    return SimpleNamespace(ticket=ticket, identifier=ticket, symbol="EURUSD", type=0, magic=magic,
                           comment="", volume=volume, price_open=100, price_current=110,
                           sl=sl, tp=tp, profit=profit, swap=0,
                           time=int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp()))


def build_manager(mt5, *, allow_real=False):
    events = []
    manager = PositionManager(mt5, lambda: True, lambda kind, data: events.append((kind, data)), interval_seconds=.01, allow_real_actions=allow_real)
    return manager, events


def test_observer_detects_manual_automated_changes_closures_and_missing_protection():
    manual, automated = position(11), position(12, magic=9001, sl=0, tp=0)
    mt5 = FakeMT5(positions=[manual, automated])
    manager, events = build_manager(mt5)
    state = manager.poll_once()
    assert state["position_count"] == 2
    assert {p["origin"] for p in state["positions"]} == {"manual", "automated"}
    assert state["alerts"] == [{"ticket": 12, "symbol": "EURUSD", "missing_sl": True,
                                "missing_tp": True, "origin": "automated"}]
    assert state["positions"][0]["symbol_spec"]["trade_tick_size"] == .01
    automated.volume, automated.sl, automated.profit = .2, 90, 3
    manager.poll_once()
    assert any(kind == "position_updated" and "volume" in data["changes"] for kind, data in events)
    mt5.positions.clear()
    final = manager.poll_once()
    assert final["positions"] == [] and final["alerts"] == []
    assert sum(kind == "position_closed" for kind, _ in events) == 2


def test_management_is_disarmed_by_default_and_real_account_needs_explicit_ack():
    mt5 = FakeMT5(trade_mode=2, positions=[position(profit=-100)])
    manager, _ = build_manager(mt5)
    rule = [{"action": "close", "max_loss": -50}]
    manager.configure(False, rule)
    manager.poll_once()
    assert not manager.status()["enabled"] and mt5.orders == []
    with pytest.raises(ValueError, match="confirm_real"):
        manager.configure(True, rule)
    with pytest.raises(ValueError, match="POSITION_MANAGER_ALLOW_REAL"):
        manager.configure(True, rule, real_acknowledged=True)
    real_manager, _ = build_manager(mt5, allow_real=True)
    real_manager.configure(True, rule, real_acknowledged=True)
    assert real_manager.status()["real_acknowledged"]
    manager.disarm()
    assert not manager.status()["enabled"] and not manager.status()["real_acknowledged"]


def test_mocked_demo_rule_modifies_only_after_ticket_quote_broker_and_risk_checks():
    mt5 = FakeMT5(positions=[position(profit=20)])
    manager, events = build_manager(mt5)
    manager.configure(True, [{"id": "protect-profit", "action": "modify", "origin": "manual",
                              "min_profit": 10, "stop_loss": 105}])
    state = manager.poll_once()
    assert len(mt5.orders) == 1
    assert mt5.orders[0]["position"] == 11 and mt5.orders[0]["action"] == 6
    assert state["positions"][0]["sl"] == 105
    result = [payload for kind, payload in events if kind == "position_management_action"][-1]
    assert result["success"] is True and result["reason"] == "Estado MT5 verificado"


def test_mocked_demo_close_checks_ticket_and_verifies_position_disappeared():
    mt5 = FakeMT5(positions=[position(profit=-100)])
    manager, events = build_manager(mt5)
    manager.configure(True, [{"id": "cut-loss", "action": "close", "origin": "manual", "max_loss": -50}])
    state = manager.poll_once()
    assert len(mt5.orders) == 1
    assert mt5.orders[0]["position"] == 11 and mt5.orders[0]["volume"] == .1
    assert state["positions"] == []
    result = [payload for kind, payload in events if kind == "position_management_action"][-1]
    assert result["success"] is True


def test_modify_rule_cannot_widen_stop_risk_or_use_stale_quote():
    mt5 = FakeMT5(positions=[position(profit=20)])
    manager, events = build_manager(mt5)
    manager.configure(True, [{"action": "modify", "min_profit": 10, "stop_loss": 94}])
    manager.poll_once()
    assert mt5.orders == []
    result = [payload for kind, payload in events if kind == "position_management_action"][-1]
    assert result["success"] is False and "aumentaría riesgo" in result["reason"]


def test_supervisor_thread_runs_and_stops_without_signal_engine():
    manager, _ = build_manager(FakeMT5())
    assert manager.start()["running"]
    assert manager.stop()["running"] is False

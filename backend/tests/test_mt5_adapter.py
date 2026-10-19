import sys
from types import SimpleNamespace

from app.mt5.adapter import MT5Adapter


def test_adapter_connect_reports_terminal_account_without_order_calls(monkeypatch):
    calls = []
    fake = SimpleNamespace(
        initialize=lambda **kwargs: calls.append(("initialize", kwargs)) or True,
        account_info=lambda: SimpleNamespace(login=123, server="XMGlobal-MT5 20", balance=1000, equity=1001),
        terminal_info=lambda: SimpleNamespace(name="MT5"),
        shutdown=lambda: calls.append(("shutdown", {})),
    )
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    adapter = MT5Adapter(connect_timeout_ms=3210)
    result = adapter.connect()
    assert result["connected"] is True
    assert result["server"] == "XMGlobal-MT5 20"
    assert calls[0] == ("initialize", {"timeout": 3210})


def test_adapter_cleans_up_after_failed_initialization(monkeypatch):
    calls = []
    fake = SimpleNamespace(
        initialize=lambda **kwargs: False,
        last_error=lambda: (-10004, "IPC timeout"),
        shutdown=lambda: calls.append("shutdown"),
    )
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    adapter = MT5Adapter(connect_timeout_ms=10)
    result = adapter.connect()
    assert result == {"connected": False, "error": "(-10004, 'IPC timeout')"}
    assert calls == ["shutdown"]
    assert adapter._mt5 is None


def test_adapter_rejects_initialized_terminal_without_account_or_terminal_info(monkeypatch):
    calls = []
    fake = SimpleNamespace(
        initialize=lambda **kwargs: True,
        account_info=lambda: SimpleNamespace(login=123, server="demo", balance=1000, equity=1000),
        terminal_info=lambda: None,
        last_error=lambda: (-10004, "terminal unavailable"),
        shutdown=lambda: calls.append("shutdown"),
    )
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    adapter = MT5Adapter()
    result = adapter.connect()
    assert result["connected"] is False
    assert "terminal_info()" in result["error"]
    assert calls == ["shutdown"]
    assert adapter._mt5 is None


def test_copy_rates_accepts_mt5_timeframe_constant_from_history_service():
    calls = []
    fake = SimpleNamespace(
        symbol_info=lambda symbol: SimpleNamespace(visible=True),
        copy_rates_from_pos=lambda *args: calls.append(args) or [{"time": 1}],
        TIMEFRAME_H1=16385,
        last_error=lambda: (0, "ok"),
    )
    adapter = MT5Adapter()
    adapter._mt5 = fake
    result = adapter.copy_rates_from_pos("GOLDmicro", 16385, 1, 10)
    assert result == [{"time": 1}]
    assert calls == [("GOLDmicro", 16385, 1, 10)]

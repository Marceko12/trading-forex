"""Lazy MT5 adapter. Real trading is deliberately not exposed by this starter."""
class MT5Adapter:
    def __init__(self): self._mt5 = None
    def connect(self):
        try:
            import MetaTrader5 as mt5
            self._mt5 = mt5
            if not mt5.initialize():
                return {"connected": False, "error": str(mt5.last_error())}
            account = mt5.account_info()
            terminal = mt5.terminal_info()
            return {"connected": True, "account": account.login if account else None,
                    "server": account.server if account else None,
                    "balance": account.balance if account else None,
                    "equity": account.equity if account else None,
                    "terminal": terminal.name if terminal else None}
        except Exception as exc:
            return {"connected": False, "error": str(exc)}
    def disconnect(self):
        if self._mt5:
            self._mt5.shutdown()
            self._mt5 = None
    def symbols(self):
        if not self._mt5: return []
        return [s.name for s in (self._mt5.symbols_get() or [])]
    def timeframe_value(self, name):
        return getattr(self._mt5, name, None) if self._mt5 else None
    def copy_rates_range(self, symbol, timeframe, start, end):
        if not self._mt5: raise RuntimeError("MT5 desconectado")
        info = self._mt5.symbol_info(symbol)
        if info is None: raise ValueError(f"Símbolo inexistente: {symbol}")
        if not info.visible and not self._mt5.symbol_select(symbol, True):
            raise RuntimeError(f"MT5 no pudo habilitar {symbol}: {self._mt5.last_error()}")
        return self._mt5.copy_rates_range(symbol, timeframe, start, end)
    def symbol_info(self, symbol):
        return self._mt5.symbol_info(symbol) if self._mt5 else None
    def last_error(self):
        return self._mt5.last_error() if self._mt5 else "MT5 desconectado"

"""Lazy MetaTrader 5 adapter. Callers still enforce the demo-only order gate."""
class MT5Adapter:
    def __init__(self, connect_timeout_ms=10000):
        self._mt5 = None
        self.connect_timeout_ms = connect_timeout_ms
    def connect(self):
        try:
            import MetaTrader5 as mt5
            self._mt5 = mt5
            if not mt5.initialize(timeout=self.connect_timeout_ms):
                error = str(mt5.last_error())
                mt5.shutdown()
                self._mt5 = None
                return {"connected": False, "error": error}
            account = mt5.account_info()
            terminal = mt5.terminal_info()
            return {"connected": True, "account": account.login if account else None,
                    "server": account.server if account else None,
                    "balance": account.balance if account else None,
                    "equity": account.equity if account else None,
                    "terminal": terminal.name if terminal else None}
        except Exception as exc:
            if self._mt5:
                try:
                    self._mt5.shutdown()
                except Exception:
                    pass
            self._mt5 = None
            return {"connected": False, "error": str(exc)}
    def disconnect(self):
        if self._mt5:
            self._mt5.shutdown()
            self._mt5 = None
    def symbols(self):
        if not self._mt5: return []
        return [s.name for s in (self._mt5.symbols_get() or [])]
    def account_info(self):
        if not self._mt5: raise RuntimeError("MT5 desconectado")
        return self._mt5.account_info()
    def terminal_info(self):
        if not self._mt5: raise RuntimeError("MT5 desconectado")
        return self._mt5.terminal_info()
    def symbol_info_tick(self, symbol):
        if not self._mt5: raise RuntimeError("MT5 desconectado")
        return self._mt5.symbol_info_tick(symbol)
    def positions_get(self, symbol=None):
        if not self._mt5: raise RuntimeError("MT5 desconectado")
        return self._mt5.positions_get(symbol=symbol) if symbol else self._mt5.positions_get()
    def copy_rates_from_pos(self, symbol, timeframe, start_pos=1, count=500):
        if not self._mt5: raise RuntimeError("MT5 desconectado")
        info=self._mt5.symbol_info(symbol)
        if info is None: raise ValueError(f"Símbolo inexistente: {symbol}")
        if not info.visible and not self._mt5.symbol_select(symbol, True):
            raise RuntimeError(f"MT5 no pudo habilitar {symbol}: {self._mt5.last_error()}")
        value=self.timeframe_value(timeframe)
        if value is None: raise ValueError(f"Temporalidad MT5 inválida: {timeframe}")
        return self._mt5.copy_rates_from_pos(symbol,value,start_pos,count)
    def order_calc_margin(self, order_type, symbol, volume, price):
        if not self._mt5: raise RuntimeError("MT5 desconectado")
        return self._mt5.order_calc_margin(order_type,symbol,volume,price)
    def send_order(self, request):
        if not self._mt5: raise RuntimeError("MT5 desconectado")
        return self._mt5.order_send(request)
    def constant(self, name):
        return getattr(self._mt5,name,None) if self._mt5 else None
    def timeframe_value(self, name):
        return getattr(self._mt5, name, None) if self._mt5 else None
    def copy_rates_range(self, symbol, timeframe, start, end):
        if not self._mt5: raise RuntimeError("MT5 desconectado")
        info = self._mt5.symbol_info(symbol)
        if info is None: raise ValueError(f"Símbolo inexistente: {symbol}")
        if not info.visible and not self._mt5.symbol_select(symbol, True):
            raise RuntimeError(f"MT5 no pudo habilitar {symbol}: {self._mt5.last_error()}")
        return self._mt5.copy_rates_range(symbol, timeframe, start, end)
    def copy_ticks_range(self, symbol, start, end):
        if not self._mt5: raise RuntimeError("MT5 desconectado")
        info = self._mt5.symbol_info(symbol)
        if info is None: raise ValueError(f"Símbolo inexistente: {symbol}")
        if not info.visible and not self._mt5.symbol_select(symbol, True):
            raise RuntimeError(f"MT5 no pudo habilitar {symbol}: {self._mt5.last_error()}")
        flags = getattr(self._mt5, "COPY_TICKS_ALL", None)
        if flags is None: raise RuntimeError("Esta versión del paquete MT5 no admite COPY_TICKS_ALL")
        return self._mt5.copy_ticks_range(symbol, start, end, flags)
    def symbol_info(self, symbol):
        return self._mt5.symbol_info(symbol) if self._mt5 else None
    def last_error(self):
        return self._mt5.last_error() if self._mt5 else "MT5 desconectado"

"""Opt-in strategy executor. PAPER is inert; broker orders are DEMO-only and confirmed."""
from __future__ import annotations

from datetime import datetime, timezone
from threading import Event, RLock, Thread
from time import time

from ..risk.manager import position_size, within_daily_limit, within_drawdown


class AutoTradingEngine:
    def __init__(self, adapter, connected, scan, log, *, interval_seconds=5.0, risk_state_loader=None, seen_loader=None):
        self.adapter, self.connected, self.scan, self.log = adapter, connected, scan, log
        self.interval = interval_seconds
        self._lock, self._wake = RLock(), Event()
        self._thread = None
        self._enabled = False
        self._config = {"mode": "PAPER", "symbol": "GOLDmicro", "timeframe": "H1", "risk_per_trade": .005,
                        "daily_loss_limit": .02, "max_drawdown": .05, "max_positions": 1,
                        "max_spread_points": 50, "deviation": 20}
        self._seen = set()
        if seen_loader:
            try:
                for item in seen_loader() or []:
                    if item.get("symbol") and item.get("timeframe") and item.get("bar_time") and item.get("decision"):
                        self._seen.add((item["symbol"], item["timeframe"], item["bar_time"], item["decision"]))
            except Exception:
                pass
        self._paper = []
        self._day, self._day_start, self._peak = None, None, None
        self._risk_identity = None
        if risk_state_loader:
            try:
                saved = risk_state_loader()
                if saved:
                    self._day, self._day_start, self._peak = saved.get("day"), saved.get("day_start"), saved.get("peak")
                    self._risk_identity = (saved.get("login"), saved.get("server"))
            except Exception:
                pass
        self._last_reason = "Motor detenido; habilítelo explícitamente."
        self._last_action = None

    def status(self):
        with self._lock:
            return {"enabled": self._enabled, "mode": self._config["mode"], "symbol": self._config["symbol"],
                    "timeframe": self._config["timeframe"], "limits": dict(self._config),
                    "blocked_reason": self._last_reason, "last_action": self._last_action,
                    "paper_positions": list(self._paper), "seen_signals": len(self._seen)}

    def start(self, *, mode, symbol, timeframe, limits, confirm_demo="", background=True):
        mode = str(mode).upper()
        if mode not in {"PAPER", "DEMO"}: raise ValueError("El motor solo admite PAPER o DEMO; REAL está bloqueado.")
        if not self.connected(): raise ValueError("Conecte MT5 para verificar cotizaciones y especificaciones reales.")
        account, terminal = self.adapter.account_info(), self.adapter.terminal_info()
        if account is None or terminal is None: raise ValueError("No se pudo verificar account_info y terminal_info de MT5.")
        real, demo = self.adapter.constant("ACCOUNT_TRADE_MODE_REAL"), self.adapter.constant("ACCOUNT_TRADE_MODE_DEMO")
        if real is None or demo is None: raise ValueError("MT5 no expone constantes para identificar la cuenta.")
        if getattr(account, "trade_mode", None) == real:
            if mode == "DEMO": raise ValueError("Se solicitó DEMO pero MT5 está conectado a una cuenta REAL; órdenes bloqueadas.")
        elif getattr(account, "trade_mode", None) != demo:
            raise ValueError("La cuenta MT5 no está identificada como DEMO o REAL.")
        if mode == "DEMO":
            if getattr(account, "trade_mode", None) != demo: raise ValueError("El modo DEMO exige una cuenta DEMO verificada.")
            if confirm_demo != "OPERAR EN DEMO": raise ValueError("Confirme escribiendo OPERAR EN DEMO para habilitar órdenes DEMO.")
            if not getattr(account, "trade_allowed", False) or not getattr(terminal, "trade_allowed", False):
                raise ValueError("MT5 bloquea trading para esta cuenta o terminal.")
        if not symbol or not timeframe: raise ValueError("Símbolo y temporalidad son obligatorios.")
        normalized = dict(self._config)
        normalized.update(limits)
        if not 0 < float(normalized["risk_per_trade"]) <= .02: raise ValueError("Riesgo por operación fuera del rango (0, 2%].")
        if not 0 < float(normalized["daily_loss_limit"]) <= .2: raise ValueError("Límite diario fuera de rango.")
        if not 0 < float(normalized["max_drawdown"]) <= .5: raise ValueError("Límite de drawdown fuera de rango.")
        if int(normalized["max_positions"]) < 1: raise ValueError("max_positions debe ser al menos 1.")
        if float(normalized["max_spread_points"]) <= 0: raise ValueError("max_spread_points debe ser positivo.")
        normalized.update(mode=mode, symbol=symbol, timeframe=timeframe)
        with self._lock:
            self._config, self._enabled = normalized, True
            if background and (self._thread is None or not self._thread.is_alive()):
                self._thread = Thread(target=self._run, name="mt5-auto-trader", daemon=True)
                self._thread.start()
            if background: self._wake.set()
        self.log("auto_engine_started", {"mode": mode, "symbol": symbol, "timeframe": timeframe, "limits": normalized})
        return self.status()

    def stop(self):
        with self._lock: self._enabled = False
        self._wake.set()
        self.log("auto_engine_stopped", {"note": "Entradas detenidas; posiciones abiertas no se liquidan."})
        return self.status()

    def process_once(self):
        with self._lock:
            if not self._enabled: return self.status()
            cfg = dict(self._config)
        if not self.connected(): return self._block("MT5 desconectado; entradas bloqueadas.")
        try: self._update_paper_positions(cfg)
        except Exception as exc: return self._block(f"Error actualizando posiciones PAPER desde MT5: {exc}")
        try: signals = self.scan([cfg["symbol"]], cfg["timeframe"])
        except Exception as exc: return self._block(f"Error de datos/estrategia: {exc}")
        if not signals: return self._block("No hay señal nueva para procesar.")
        for signal in signals:
            key = (signal.get("symbol"), signal.get("timeframe"), signal.get("bar_time"), signal.get("decision"))
            with self._lock:
                if key in self._seen: continue
                self._seen.add(key)
            if signal.get("decision") not in {"BUY", "SELL"}:
                self._decision("signal_held", signal, signal.get("reason") or "La estrategia no propone entrada.")
                continue
            try:
                reason = self._validate_and_execute(signal, cfg)
            except Exception as exc:
                reason = f"Entrada bloqueada por error de MT5: {exc}"
            self._decision("auto_order_proposed" if cfg["mode"] == "PAPER" and reason.startswith("PAPER:") else "auto_order_result", signal, reason)
        return self.status()

    def _update_paper_positions(self, cfg):
        if cfg["mode"] != "PAPER": return
        with self._lock: positions = list(self._paper)
        for item in positions:
            tick = self.adapter.symbol_info_tick(item["symbol"])
            if tick is None or time() - int(getattr(tick, "time", 0)) > 30: continue
            buy = item["direction"] == "BUY"
            current = float(tick.bid if buy else tick.ask)
            stop_hit = current <= item["stop_loss"] if buy else current >= item["stop_loss"]
            target_hit = current >= item["take_profit"] if buy else current <= item["take_profit"]
            if stop_hit or target_hit:
                reason = "stop_loss" if stop_hit else "take_profit"
                with self._lock:
                    self._paper = [p for p in self._paper if p is not item]
                self.log("auto_paper_closed", {**item, "exit_price": current, "exit_reason": reason,
                                                "at": datetime.now(timezone.utc).isoformat()})

    def _validate_and_execute(self, signal, cfg):
        if signal.get("source") != "MT5": return "Entrada bloqueada: la señal no usa datos de MT5."
        try:
            bar_time = datetime.fromisoformat(str(signal["bar_time"]).replace("Z", "+00:00"))
            if bar_time.tzinfo is None: bar_time = bar_time.replace(tzinfo=timezone.utc)
            seconds = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400}.get(cfg["timeframe"], 3600)
            if (datetime.now(timezone.utc) - bar_time.astimezone(timezone.utc)).total_seconds() > seconds * 2 + 60:
                return "Entrada bloqueada: la última vela cerrada de MT5 está obsoleta para la temporalidad."
        except (KeyError, TypeError, ValueError):
            return "Entrada bloqueada: timestamp de vela MT5 ausente o inválido."
        if not signal.get("stop_loss") or not signal.get("take_profit") or not signal.get("entry"):
            return "Entrada bloqueada: faltan precio, SL o TP calculados por la estrategia."
        account, terminal = self.adapter.account_info(), self.adapter.terminal_info()
        if account is None or terminal is None: return "Entrada bloqueada: MT5 no devuelve cuenta o terminal."
        if cfg["mode"] == "DEMO":
            demo = self.adapter.constant("ACCOUNT_TRADE_MODE_DEMO")
            if getattr(account, "trade_mode", None) != demo: return "Cuenta cambió; ya no es DEMO."
            if not getattr(account, "trade_allowed", False) or not getattr(terminal, "trade_allowed", False): return "MT5 bloquea trading."
        info, tick = self.adapter.symbol_info(signal["symbol"]), self.adapter.symbol_info_tick(signal["symbol"])
        if info is None or tick is None: return f"Entrada bloqueada: especificaciones o cotización ausente para {signal['symbol']}."
        if time() - int(getattr(tick, "time", 0)) > 30: return "Entrada bloqueada: tick MT5 obsoleto (>30 s)."
        point = float(getattr(info, "point", 0) or 0)
        bid, ask = float(getattr(tick, "bid", 0) or 0), float(getattr(tick, "ask", 0) or 0)
        if point <= 0 or bid <= 0 or ask < bid: return "Entrada bloqueada: point o bid/ask inválidos según MT5."
        if (ask - bid) / point > float(cfg["max_spread_points"]): return "Entrada bloqueada: spread supera el límite configurado."
        symbol_mode = int(getattr(info, "trade_mode", 0) or 0)
        if symbol_mode in {self.adapter.constant("SYMBOL_TRADE_MODE_DISABLED"), self.adapter.constant("SYMBOL_TRADE_MODE_CLOSEONLY")}:
            return "Entrada bloqueada: símbolo deshabilitado o solo cierre según MT5."
        buy = signal["decision"] == "BUY"
        if symbol_mode == self.adapter.constant("SYMBOL_TRADE_MODE_LONGONLY") and not buy:
            return "Entrada bloqueada: bróker solo permite posiciones BUY para este símbolo."
        if symbol_mode == self.adapter.constant("SYMBOL_TRADE_MODE_SHORTONLY") and buy:
            return "Entrada bloqueada: bróker solo permite posiciones SELL para este símbolo."
        entry = ask if buy else bid
        sl, tp = float(signal["stop_loss"]), float(signal["take_profit"])
        if (buy and not sl < entry < tp) or (not buy and not tp < entry < sl):
            return "Entrada bloqueada: SL/TP no respetan dirección o precio actual."
        minimum = point * max(int(getattr(info, "trade_stops_level", 0) or 0), int(getattr(info, "trade_freeze_level", 0) or 0))
        if abs(entry - sl) < minimum or abs(tp - entry) < minimum: return "Entrada bloqueada: SL/TP incumplen distancia mínima del bróker."
        equity = float(getattr(account, "equity", 0) or 0)
        now = datetime.now(timezone.utc).date().isoformat()
        identity = (getattr(account, "login", None), getattr(account, "server", None))
        persist_baseline = False
        with self._lock:
            if self._day != now or self._risk_identity != identity:
                self._day, self._day_start, self._peak = now, equity, equity
                self._risk_identity, persist_baseline = identity, True
            elif equity > (self._peak or 0):
                self._peak, persist_baseline = equity, True
            day_start, peak = self._day_start, self._peak
        if persist_baseline:
            self.log("auto_risk_baseline", {"day": now, "day_start": day_start, "peak": peak,
                                            "login": identity[0], "server": identity[1]})
        if not within_daily_limit(day_start, equity, cfg["daily_loss_limit"]): return "Entradas bloqueadas: se alcanzó el límite de pérdida diaria."
        if not within_drawdown(peak, equity, cfg["max_drawdown"]): return "Entradas bloqueadas: se alcanzó el drawdown máximo."
        tick_size, tick_value = float(getattr(info, "trade_tick_size", 0) or 0), float(getattr(info, "trade_tick_value", 0) or 0)
        try:
            volume = position_size(equity, float(cfg["risk_per_trade"]), entry, sl, tick_size, tick_value,
                                   float(info.volume_min), float(info.volume_max), float(info.volume_step))
        except (ValueError, TypeError, AttributeError) as exc: return f"Entrada bloqueada: especificaciones de riesgo inválidas: {exc}"
        if volume <= 0: return "Entrada bloqueada: el volumen de riesgo calculado es inferior al mínimo del símbolo."
        if volume > float(info.volume_max) or volume < float(info.volume_min): return "Entrada bloqueada: volumen fuera de min/max del símbolo."
        volume_step = float(getattr(info, "volume_step", 0) or 0)
        if volume_step <= 0 or abs((volume - float(info.volume_min)) / volume_step - round((volume - float(info.volume_min)) / volume_step)) > 1e-6:
            return "Entrada bloqueada: volumen calculado no respeta volume_step del símbolo."
        order_type = self.adapter.constant("ORDER_TYPE_BUY" if buy else "ORDER_TYPE_SELL")
        if order_type is None: return "Entrada bloqueada: constante de tipo de orden MT5 no disponible."
        margin = self.adapter.order_calc_margin(order_type, signal["symbol"], volume, entry)
        if margin is None: return f"Entrada bloqueada: order_calc_margin falló: {self.adapter.last_error()}"
        if float(margin) > float(getattr(account, "margin_free", 0) or 0): return "Entrada bloqueada: margen libre insuficiente."
        positions = self.adapter.positions_get()
        if positions is None: return f"Entrada bloqueada: positions_get falló: {self.adapter.last_error()}"
        magic = 617001
        if cfg["mode"] == "DEMO" and any(str(getattr(p, "symbol", "")) == signal["symbol"] and int(getattr(p, "magic", 0) or 0) == magic for p in positions): return "Entrada bloqueada: ya hay una posición de este motor en el símbolo."
        if cfg["mode"] == "PAPER":
            with self._lock:
                if not self._enabled: return "Motor detenido antes de crear la propuesta PAPER."
                if len(self._paper) >= int(cfg["max_positions"]): return "Entrada PAPER bloqueada: se alcanzó el máximo de posiciones simultáneas."
                self._paper.append({"symbol": signal["symbol"], "direction": signal["decision"], "volume": volume,
                                    "entry": entry, "stop_loss": sl, "take_profit": tp, "bar_time": signal.get("bar_time"), "status": "PAPER; sin orden al bróker"})
            return f"PAPER: señal validada; propuesta {signal['decision']} {signal['symbol']} volumen {volume:g} a {entry:g}; no se envió orden."
        if len(positions) >= int(cfg["max_positions"]): return "Entrada DEMO bloqueada: límite global de posiciones simultáneas alcanzado."
        action, done = self.adapter.constant("TRADE_ACTION_DEAL"), self.adapter.constant("TRADE_RETCODE_DONE")
        fill_mode = int(getattr(info, "filling_mode", 0) or 0)
        ioc_flag, fok_flag = self.adapter.constant("SYMBOL_FILLING_IOC"), self.adapter.constant("SYMBOL_FILLING_FOK")
        if ioc_flag is not None and fill_mode & int(ioc_flag): fill = self.adapter.constant("ORDER_FILLING_IOC")
        elif fok_flag is not None and fill_mode & int(fok_flag): fill = self.adapter.constant("ORDER_FILLING_FOK")
        else: fill = None
        if None in (order_type, action, done, fill): return "Entrada DEMO bloqueada: faltan constantes de orden MT5."
        request = {"action": action, "symbol": signal["symbol"], "volume": volume, "type": order_type,
                   "price": entry, "sl": sl, "tp": tp, "deviation": int(cfg["deviation"]),
                   "magic": magic, "comment": "AI Trading Engine", "type_time": self.adapter.constant("ORDER_TIME_GTC"), "type_filling": fill}
        check = self.adapter.check_order(request)
        if check is None or getattr(check, "retcode", None) not in {0, done}: return f"order_check rechazó la orden: retcode={getattr(check, 'retcode', None)}; {getattr(check, 'comment', '')}"
        # Revalidate account and quote just before the only broker-side mutation.
        account2, terminal2 = self.adapter.account_info(), self.adapter.terminal_info()
        tick2 = self.adapter.symbol_info_tick(signal["symbol"])
        if account2 is None or getattr(account2, "trade_mode", None) != self.adapter.constant("ACCOUNT_TRADE_MODE_DEMO"):
            return "Cuenta cambió antes de order_send; orden no enviada."
        if terminal2 is None or not getattr(account2, "trade_allowed", False) or not getattr(terminal2, "trade_allowed", False):
            return "MT5 dejó de autorizar trading antes de order_send; orden no enviada."
        positions2 = self.adapter.positions_get()
        if positions2 is None: return f"positions_get falló antes de order_send: {self.adapter.last_error()}"
        if len(positions2) >= int(cfg["max_positions"]): return "Límite de posiciones alcanzado antes de order_send."
        if any(str(getattr(p, "symbol", "")) == signal["symbol"] and int(getattr(p, "magic", 0) or 0) == magic for p in positions2):
            return "Ya existe una posición del motor en el símbolo; orden no enviada."
        if tick2 is None or time() - int(getattr(tick2, "time", 0)) > 30: return "Cotización dejó de ser válida antes de order_send; orden no enviada."
        bid2, ask2 = float(getattr(tick2, "bid", 0) or 0), float(getattr(tick2, "ask", 0) or 0)
        if bid2 <= 0 or ask2 < bid2 or (ask2 - bid2) / point > float(cfg["max_spread_points"]):
            return "Spread/cotización cambió antes de order_send; orden no enviada."
        entry2 = ask2 if buy else bid2
        if (buy and not sl < entry2 < tp) or (not buy and not tp < entry2 < sl):
            return "Precio cambió y SL/TP dejaron de ser válidos antes de order_send."
        if abs(entry2 - sl) < minimum or abs(tp - entry2) < minimum:
            return "Precio cambió y SL/TP incumplen distancia mínima antes de order_send."
        equity2 = float(getattr(account2, "equity", 0) or 0)
        if not within_daily_limit(day_start, equity2, cfg["daily_loss_limit"]):
            return "Pérdida diaria alcanzó el límite antes de order_send; orden no enviada."
        if not within_drawdown(peak, equity2, cfg["max_drawdown"]):
            return "Drawdown alcanzó el límite antes de order_send; orden no enviada."
        if abs(entry2 - sl) / tick_size * tick_value * volume > equity2 * float(cfg["risk_per_trade"]):
            return "Precio cambió y el riesgo supera el límite antes de order_send."
        margin2 = self.adapter.order_calc_margin(order_type, signal["symbol"], volume, entry2)
        if margin2 is None or float(margin2) > float(getattr(account2, "margin_free", 0) or 0):
            return "Margen cambió o es insuficiente antes de order_send."
        request["price"] = entry2
        check = self.adapter.check_order(request)
        if check is None or getattr(check, "retcode", None) not in {0, done}: return f"Segundo order_check rechazó la orden: retcode={getattr(check, 'retcode', None)}."
        with self._lock:
            if not self._enabled: return "Motor detenido antes de order_send; orden no enviada."
            result = self.adapter.send_order(request)
        accepted = {done, self.adapter.constant("TRADE_RETCODE_DONE_PARTIAL"), self.adapter.constant("TRADE_RETCODE_PLACED")}
        if result is None or getattr(result, "retcode", None) not in accepted: return f"order_send rechazado: retcode={getattr(result, 'retcode', None)}; {getattr(result, 'comment', '')}"
        actual = self.adapter.positions_get(signal["symbol"])
        match = next((p for p in (actual or []) if int(getattr(p, "magic", 0) or 0) == magic and
                      abs(float(getattr(p, "volume", 0) or 0) - volume) < 1e-7 and
                      int(getattr(p, "type", -1)) == int(order_type)), None)
        if match is None: return f"MT5 aceptó retcode={result.retcode}, pero positions_get no confirmó ticket/volumen/dirección; ejecución no confirmada."
        return f"DEMO confirmada por MT5: ticket {match.ticket}, volumen {match.volume:g}, {signal['decision']} {signal['symbol']}."

    def _block(self, reason):
        self._decision("auto_entry_blocked", {}, reason)
        return self.status()

    def _decision(self, kind, signal, reason):
        entry = {"symbol": signal.get("symbol"), "timeframe": signal.get("timeframe"), "decision": signal.get("decision"),
                 "reason": reason, "strategy_reason": signal.get("reason"), "entry": signal.get("entry"),
                 "stop_loss": signal.get("stop_loss"), "take_profit": signal.get("take_profit"),
                 "bar_time": signal.get("bar_time"), "mode": self._config["mode"], "at": datetime.now(timezone.utc).isoformat()}
        with self._lock: self._last_reason, self._last_action = reason, kind
        self.log(kind, entry)

    def _run(self):
        while True:
            with self._lock: enabled = self._enabled
            if enabled: self.process_once()
            self._wake.wait(self.interval); self._wake.clear()

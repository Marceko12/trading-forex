"""Independent MT5 position supervisor and explicitly armed management rules."""
from __future__ import annotations

from datetime import datetime, timezone
from threading import Event, RLock, Thread, current_thread
from time import monotonic, time
from typing import Callable


OBSERVED_FIELDS = ("volume", "sl", "tp", "profit", "price_open", "type", "magic", "comment", "identifier", "time")


def _number(value, default=0.0):
    try: return float(value or 0.0)
    except (TypeError, ValueError): return default


class PositionManager:
    """Polls positions regardless of signal-engine state. Management starts disarmed."""
    def __init__(self, adapter, connected: Callable[[], bool], log: Callable[[str, dict], None],
                 risk_per_trade: float = 0.005, interval_seconds: float = 2.0,
                 allow_real_actions: bool = False):
        self.adapter, self.connected, self.log = adapter, connected, log
        self.risk_per_trade, self.interval = risk_per_trade, interval_seconds
        self.allow_real_actions = bool(allow_real_actions)
        self._lock, self._stop_event = RLock(), Event()
        self._thread: Thread | None = None
        self._positions: dict[int, dict] = {}
        self._alerts: dict[int, dict] = {}
        self._rules: list[dict] = []
        self._enabled = False
        self._real_acknowledged = False
        self._last_poll = None
        self._last_error = None
        self._management_lock = RLock()
        self._acted: dict[tuple, float | None] = {}

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive(): return self.status()
            self._stop_event.clear()
            self._thread = Thread(target=self._run, name="mt5-position-manager", daemon=True)
            self._thread.start()
            return self.status()

    def stop(self):
        self._stop_event.set()
        thread = self._thread
        if thread and thread is not current_thread():
            thread.join(timeout=2)
        with self._lock:
            self._enabled = False
            if thread and not thread.is_alive(): self._thread = None
            return self.status()

    def disarm(self):
        with self._lock:
            self._enabled = False
            self._real_acknowledged = False
        return self.status()

    def status(self):
        with self._lock:
            return {"running": bool(self._thread and self._thread.is_alive()),
                    "connected": bool(self.connected()), "enabled": self._enabled,
                    "stale": not bool(self.connected()),
                    "real_acknowledged": self._real_acknowledged,
                    "real_actions_allowed_by_config": self.allow_real_actions,
                    "interval_seconds": self.interval,
                    "last_poll": self._last_poll, "last_error": self._last_error,
                    "position_count": len(self._positions), "positions": list(self._positions.values()),
                    "alerts": list(self._alerts.values()), "rules": [dict(r) for r in self._rules]}

    def configure(self, enabled: bool, rules: list[dict], *, real_acknowledged: bool = False):
        normalized = [self._validate_rule(rule, i) for i, rule in enumerate(rules)]
        if enabled:
            if not self.connected(): raise ValueError("Conecte MT5 antes de habilitar reglas de gestión")
            account = self.adapter.account_info()
            terminal = self.adapter.terminal_info()
            if account is None or terminal is None: raise ValueError("No se pudo verificar la cuenta y el terminal MT5")
            if not bool(getattr(account, "trade_allowed", False)) or not bool(getattr(terminal, "trade_allowed", False)):
                raise ValueError("MT5 no permite trading en esta cuenta o terminal")
            if any(rule["action"] in {"close", "modify"} for rule in normalized):
                real_mode = self.adapter.constant("ACCOUNT_TRADE_MODE_REAL")
                demo_mode = self.adapter.constant("ACCOUNT_TRADE_MODE_DEMO")
                if real_mode is None or demo_mode is None: raise ValueError("MT5 no expone constantes seguras para identificar DEMO/REAL")
                mode = getattr(account, "trade_mode", None)
                if mode == real_mode and not real_acknowledged:
                    raise ValueError("Cuenta REAL: confirme escribiendo HABILITAR GESTION REAL en confirm_real")
                if mode == real_mode and not self.allow_real_actions:
                    raise ValueError("Acciones REAL bloqueadas por defecto; tras validarlas en DEMO, configure POSITION_MANAGER_ALLOW_REAL=true y confirme de nuevo")
                if mode not in {real_mode, demo_mode}:
                    raise ValueError("Acciones automáticas permitidas solo tras identificar una cuenta DEMO o REAL")
        with self._lock:
            self._rules, self._enabled = normalized, bool(enabled)
            self._real_acknowledged = bool(enabled and real_acknowledged)
            self._acted.clear()
        return self.status()

    @staticmethod
    def _validate_rule(rule: dict, index: int) -> dict:
        action = rule.get("action", "maintain")
        origin = rule.get("origin", "any")
        if action not in {"maintain", "close", "modify"}: raise ValueError(f"Regla {index}: acción no admitida")
        if origin not in {"any", "manual", "automated"}: raise ValueError(f"Regla {index}: origen no admitido")
        normalized = {"id": str(rule.get("id") or f"rule-{index+1}"), "symbol": rule.get("symbol") or None,
                      "origin": origin, "action": action}
        for field in ("max_loss", "min_profit", "stop_loss", "take_profit", "trailing_stop"):
            value = rule.get(field)
            if value is not None:
                try: value = float(value)
                except (ValueError, TypeError) as exc: raise ValueError(f"Regla {index}: {field} debe ser numérico") from exc
                if value <= 0 and field in {"stop_loss", "take_profit", "trailing_stop"}: raise ValueError(f"Regla {index}: {field} debe ser positivo")
                normalized[field] = value
        if action == "close" and "max_loss" not in normalized and "min_profit" not in normalized:
            raise ValueError(f"Regla {index}: close exige max_loss o min_profit como condición")
        if action == "modify" and not ({"stop_loss", "take_profit", "trailing_stop"} & normalized.keys()):
            raise ValueError(f"Regla {index}: modify exige stop_loss, take_profit o trailing_stop")
        if action == "close" and "max_loss" not in normalized and "min_profit" not in normalized:
            raise ValueError(f"Regla {index}: close exige max_loss o min_profit como condición")
        if "max_loss" in normalized and normalized["max_loss"] >= 0:
            raise ValueError(f"Regla {index}: max_loss debe ser negativo")
        if "min_profit" in normalized and normalized["min_profit"] < 0:
            raise ValueError(f"Regla {index}: min_profit no puede ser negativo")
        return normalized

    def poll_once(self):
        if not self.connected(): raise RuntimeError("MT5 desconectado; no se pueden supervisar posiciones")
        rows = self.adapter.positions_get()
        if rows is None: raise RuntimeError(f"MT5 positions_get falló: {self.adapter.last_error()}")
        observed = {int(p.ticket): self._serialize(p) for p in rows}
        with self._lock:
            previous = self._positions
            for ticket, item in observed.items():
                old = previous.get(ticket)
                same_position = old is not None and old["symbol"] == item["symbol"] and old["identifier"] == item["identifier"] and old["time"] == item["time"]
                if old is not None and not same_position:
                    self.log("position_closed", old)
                    self._alerts.pop(ticket, None)
                if not same_position:
                    self.log("position_opened", item)
                else:
                    changes = {field: {"from": old.get(field), "to": item.get(field)}
                               for field in OBSERVED_FIELDS if old.get(field) != item.get(field)}
                    if changes: self.log("position_updated", {"ticket": ticket, "symbol": item["symbol"], "changes": changes, "position": item})
                missing = not item["sl"] or not item["tp"]
                if missing:
                    alert = {"ticket": ticket, "symbol": item["symbol"], "missing_sl": not bool(item["sl"]),
                             "missing_tp": not bool(item["tp"]), "origin": item["origin"]}
                    if ticket not in self._alerts: self.log("position_protection_missing", alert)
                    self._alerts[ticket] = alert
                elif ticket in self._alerts:
                    self._alerts.pop(ticket, None)
                    self.log("position_protection_restored", {"ticket": ticket, "symbol": item["symbol"]})
            for ticket, old in previous.items():
                if ticket not in observed:
                    self._alerts.pop(ticket, None)
                    self.log("position_closed", old)
            self._positions = observed
            self._last_poll = datetime.now(timezone.utc).isoformat()
            self._last_error = None
        if self._apply_rules(observed):
            return self.poll_once()
        return self.status()

    def _serialize(self, p) -> dict:
        magic = int(getattr(p, "magic", 0) or 0)
        item = {"ticket": int(p.ticket), "identifier": int(getattr(p, "identifier", p.ticket)),
                "symbol": str(p.symbol), "type": int(p.type), "type_name": "BUY" if int(p.type) == 0 else "SELL",
                "origin": "manual" if magic == 0 else "automated", "magic": magic,
                "comment": str(getattr(p, "comment", "") or ""), "volume": _number(p.volume),
                "price_open": _number(p.price_open), "price_current": _number(getattr(p, "price_current", 0)),
                "sl": _number(p.sl), "tp": _number(p.tp), "profit": _number(p.profit),
                "swap": _number(getattr(p, "swap", 0)),
                "time": datetime.fromtimestamp(int(getattr(p, "time", 0)), timezone.utc).isoformat()
                if getattr(p, "time", None) else None}
        info = self.adapter.symbol_info(item["symbol"])
        if info is not None:
            item["symbol_spec"] = {"point": _number(getattr(info, "point", 0)),
                                   "digits": int(getattr(info, "digits", 0) or 0),
                                   "trade_tick_size": _number(getattr(info, "trade_tick_size", 0)),
                                   "trade_tick_value": _number(getattr(info, "trade_tick_value", 0)),
                                   "trade_stops_level": int(getattr(info, "trade_stops_level", 0) or 0),
                                   "volume_min": _number(getattr(info, "volume_min", 0)),
                                   "volume_max": _number(getattr(info, "volume_max", 0)),
                                   "volume_step": _number(getattr(info, "volume_step", 0)),
                                   "currency_profit": getattr(info, "currency_profit", None)}
            spec = item["symbol_spec"]
            if item["sl"] and spec["trade_tick_size"] > 0 and spec["trade_tick_value"] > 0:
                item["risk_to_sl"] = abs(item["price_open"] - item["sl"]) / spec["trade_tick_size"] * spec["trade_tick_value"] * item["volume"]
            else: item["risk_to_sl"] = None
        else:
            item["symbol_spec"], item["risk_to_sl"] = None, None
        return item

    def _apply_rules(self, positions):
        with self._management_lock:
            with self._lock:
                enabled, rules, real_ack = self._enabled, [dict(x) for x in self._rules], self._real_acknowledged
            if not enabled: return False
            attempted = False
            for ticket, position in positions.items():
                for rule in rules:
                    if rule["action"] == "maintain": continue
                    if rule.get("symbol") and rule["symbol"] != position["symbol"]: continue
                    if rule.get("origin", "any") not in {"any", position["origin"]}: continue
                    if "max_loss" in rule and position["profit"] > rule["max_loss"]: continue
                    if "min_profit" in rule and position["profit"] < rule["min_profit"]: continue
                    trigger = tuple(sorted((key, rule[key]) for key in ("max_loss", "min_profit") if key in rule))
                    action_key = (ticket, position["time"], rule["id"], trigger)
                    attempted_at = self._acted.get(action_key, "never")
                    if attempted_at is None or (attempted_at != "never" and monotonic() - attempted_at < 15): continue
                    result = self._execute_rule(position, rule, real_ack)
                    self._acted[action_key] = (monotonic() if "trailing_stop" in rule else None) if result["success"] else monotonic()
                    attempted = True
                    self.log("position_management_action", result)
            return attempted

    def _execute_rule(self, tracked: dict, rule: dict, real_ack: bool) -> dict:
        """Revalidate identity/quote/specs immediately before an action and verify MT5 state after."""
        ticket, symbol = tracked["ticket"], tracked["symbol"]
        current_rows = self.adapter.positions_get()
        current = next((p for p in (current_rows or []) if int(p.ticket) == ticket), None)
        if current is None or str(current.symbol) != symbol or int(getattr(current, "identifier", current.ticket)) != tracked["identifier"] or not tracked["time"] or int(getattr(current, "time", 0)) != int(datetime.fromisoformat(tracked["time"]).timestamp()):
            return {"ticket": ticket, "action": rule["action"], "success": False, "reason": "Ticket, símbolo o tiempo de apertura ya no coincide"}
        if (int(getattr(current, "type", -1)) != int(tracked["type"]) or
                abs(_number(getattr(current, "volume", 0)) - _number(tracked["volume"])) > 1e-8):
            return {"ticket": ticket, "action": rule["action"], "success": False, "reason": "Dirección o volumen de posición cambió desde la validación"}
        account, terminal = self.adapter.account_info(), self.adapter.terminal_info()
        if account is None or terminal is None or not getattr(account, "trade_allowed", False) or not getattr(terminal, "trade_allowed", False):
            return {"ticket": ticket, "action": rule["action"], "success": False, "reason": "MT5 no autoriza trading en cuenta/terminal"}
        real_mode = self.adapter.constant("ACCOUNT_TRADE_MODE_REAL")
        demo_mode = self.adapter.constant("ACCOUNT_TRADE_MODE_DEMO")
        if real_mode is None or demo_mode is None:
            return {"ticket": ticket, "action": rule["action"], "success": False, "reason": "Constantes de cuenta MT5 no disponibles"}
        if getattr(account, "trade_mode", None) == real_mode and not real_ack:
            return {"ticket": ticket, "action": rule["action"], "success": False, "reason": "Cuenta REAL sin confirmación explícita"}
        if getattr(account, "trade_mode", None) == real_mode and not self.allow_real_actions:
            return {"ticket": ticket, "action": rule["action"], "success": False, "reason": "Gestión en REAL deshabilitada por POSITION_MANAGER_ALLOW_REAL"}
        if getattr(account, "trade_mode", None) not in {real_mode, demo_mode}:
            return {"ticket": ticket, "action": rule["action"], "success": False, "reason": "Tipo de cuenta no verificado"}
        info, tick = self.adapter.symbol_info(symbol), self.adapter.symbol_info_tick(symbol)
        if info is None or tick is None:
            return {"ticket": ticket, "action": rule["action"], "success": False, "reason": "MT5 no devolvió especificación o cotización del símbolo"}
        if time() - int(getattr(tick, "time", 0)) > 30:
            return {"ticket": ticket, "action": rule["action"], "success": False, "reason": "Cotización MT5 obsoleta (>30 s)"}
        buy_position_type = self.adapter.constant("POSITION_TYPE_BUY")
        if buy_position_type is None:
            return {"ticket": ticket, "action": rule["action"], "success": False, "reason": "Constantes de posición MT5 no disponibles"}
        buy = int(current.type) == int(buy_position_type)
        point, stop_level = _number(getattr(info, "point", 0)), int(getattr(info, "trade_stops_level", 0) or 0)
        if point <= 0:
            return {"ticket": ticket, "action": rule["action"], "success": False, "reason": "MT5 no devolvió el point del símbolo"}
        min_distance = point * max(stop_level, int(getattr(info, "trade_freeze_level", 0) or 0))
        request = {"symbol": symbol, "position": ticket}
        if rule["action"] == "modify":
            bid, ask = _number(tick.bid), _number(tick.ask)
            trailing = _number(rule.get("trailing_stop"))
            sl = _number(rule.get("stop_loss"), _number(current.sl))
            if trailing:
                candidate = (bid - trailing) if buy else (ask + trailing)
                if not sl or (candidate > sl if buy else candidate < sl): sl = candidate
            tp = _number(rule.get("take_profit"), _number(current.tp))
            if buy:
                if sl and (sl >= bid - min_distance or (current.sl and sl < current.sl)):
                    return {"ticket": ticket, "action": "modify", "success": False, "reason": "SL BUY fuera de límites o aumentaría riesgo"}
                if tp and tp <= bid + min_distance:
                    return {"ticket": ticket, "action": "modify", "success": False, "reason": "TP BUY viola distancia mínima del bróker"}
            else:
                if sl and (sl <= ask + min_distance or (current.sl and sl > current.sl)):
                    return {"ticket": ticket, "action": "modify", "success": False, "reason": "SL SELL fuera de límites o aumentaría riesgo"}
                if tp and tp >= ask - min_distance:
                    return {"ticket": ticket, "action": "modify", "success": False, "reason": "TP SELL viola distancia mínima del bróker"}
            tick_size, tick_value, equity = _number(getattr(info, "trade_tick_size", 0)), _number(getattr(info, "trade_tick_value", 0)), _number(getattr(account, "equity", 0))
            if not sl or tick_size <= 0 or tick_value <= 0 or equity <= 0:
                return {"ticket": ticket, "action": "modify", "success": False, "reason": "No se puede calcular el riesgo sin SL y especificaciones de tick válidas"}
            risk_cash = abs(_number(current.price_open) - sl) / tick_size * tick_value * _number(current.volume)
            if risk_cash > equity * self.risk_per_trade:
                return {"ticket": ticket, "action": "modify", "success": False, "reason": f"Riesgo al nuevo SL ({risk_cash:.2f}) supera límite monetario ({equity*self.risk_per_trade:.2f})"}
            request.update({"action": self.adapter.constant("TRADE_ACTION_SLTP"), "sl": sl, "tp": tp})
            if sl == _number(current.sl) and tp == _number(current.tp):
                return {"ticket": ticket, "action": "modify", "success": True, "reason": "SL/TP ya están en esos niveles"}
        else:
            volume, vmin = _number(current.volume), _number(getattr(info, "volume_min", 0))
            vmax, vstep = _number(getattr(info, "volume_max", 0)), _number(getattr(info, "volume_step", 0))
            if not vstep or not vmin or volume < vmin or volume > vmax or abs((volume-vmin)/vstep-round((volume-vmin)/vstep)) > 1e-6:
                return {"ticket": ticket, "action": "close", "success": False, "reason": "Volumen de posición fuera de min/max/step del símbolo"}
            action_type = self.adapter.constant("TRADE_ACTION_DEAL")
            order_type = self.adapter.constant("ORDER_TYPE_SELL" if buy else "ORDER_TYPE_BUY")
            if action_type is None or order_type is None:
                return {"ticket": ticket, "action": "close", "success": False, "reason": "Constantes de orden MT5 no disponibles"}
            request.update({"action": action_type,
                            "type": order_type,
                            "volume": _number(current.volume), "price": _number(tick.bid if buy else tick.ask),
                            "deviation": 20, "comment": "PositionManager rule"})
            if not request["price"] or not request["volume"]:
                return {"ticket": ticket, "action": "close", "success": False, "reason": "Precio o volumen actual no válido"}
        checker = getattr(self.adapter, "check_order", None)
        if checker is None:
            return {"ticket": ticket, "action": rule["action"], "success": False, "reason": "MT5 order_check no está disponible; acción bloqueada"}
        checked = checker(request)
        if checked is None or getattr(checked, "retcode", None) not in {0, self.adapter.constant("TRADE_RETCODE_DONE")}:
            return {"ticket": ticket, "action": rule["action"], "success": False, "retcode": getattr(checked, "retcode", None), "reason": "MT5 order_check rechazó la acción"}
        latest_rows = self.adapter.positions_get()
        latest = next((p for p in (latest_rows or []) if int(p.ticket) == ticket), None)
        latest_tick = self.adapter.symbol_info_tick(symbol)
        latest_account, latest_terminal = self.adapter.account_info(), self.adapter.terminal_info()
        if (latest is None or str(latest.symbol) != symbol or
                int(getattr(latest, "identifier", latest.ticket)) != tracked["identifier"] or
                int(getattr(latest, "time", 0)) != int(datetime.fromisoformat(tracked["time"]).timestamp()) or
                int(getattr(latest, "type", -1)) != int(tracked["type"]) or
                abs(_number(getattr(latest, "volume", 0)) - _number(tracked["volume"])) > 1e-8):
            return {"ticket": ticket, "action": rule["action"], "success": False, "reason": "La posición cambió durante order_check; no se envió la acción"}
        if latest_tick is None or time() - int(getattr(latest_tick, "time", 0)) > 30:
            return {"ticket": ticket, "action": rule["action"], "success": False, "reason": "Cotización dejó de ser válida durante order_check"}
        if (latest_account is None or latest_terminal is None or not getattr(latest_account, "trade_allowed", False) or
                not getattr(latest_terminal, "trade_allowed", False)):
            return {"ticket": ticket, "action": rule["action"], "success": False, "reason": "MT5 dejó de autorizar trading durante order_check"}
        latest_mode = getattr(latest_account, "trade_mode", None)
        if latest_mode == real_mode and (not real_ack or not self.allow_real_actions):
            return {"ticket": ticket, "action": rule["action"], "success": False, "reason": "La cuenta cambió a REAL sin habilitación de gestión"}
        if latest_mode not in {real_mode, demo_mode}:
            return {"ticket": ticket, "action": rule["action"], "success": False, "reason": "Tipo de cuenta cambió durante order_check"}
        latest_bid, latest_ask = _number(latest_tick.bid), _number(latest_tick.ask)
        if rule["action"] == "modify":
            latest_sl, latest_tp = request["sl"], request["tp"]
            if ((buy and latest_sl and (latest_sl < _number(latest.sl) or latest_sl >= latest_bid - min_distance)) or
                    (not buy and latest_sl and (latest_sl > _number(latest.sl) or latest_sl <= latest_ask + min_distance))):
                return {"ticket": ticket, "action": rule["action"], "success": False, "reason": "SL cambió de validez o ampliaría riesgo durante order_check"}
            if ((buy and latest_tp and latest_tp <= latest_bid + min_distance) or
                    (not buy and latest_tp and latest_tp >= latest_ask - min_distance)):
                return {"ticket": ticket, "action": rule["action"], "success": False, "reason": "TP dejó de cumplir la distancia mínima durante order_check"}
            latest_risk = abs(_number(latest.price_open) - latest_sl) / tick_size * tick_value * _number(latest.volume)
            if latest_risk > _number(latest_account.equity) * self.risk_per_trade:
                return {"ticket": ticket, "action": rule["action"], "success": False, "reason": "El riesgo excede el límite antes de enviar la modificación"}
        else:
            request["price"] = _number(latest_tick.bid if buy else latest_tick.ask)
            checked_again = checker(request)
            if checked_again is None or getattr(checked_again, "retcode", None) not in {0, self.adapter.constant("TRADE_RETCODE_DONE")}:
                return {"ticket": ticket, "action": rule["action"], "success": False, "retcode": getattr(checked_again, "retcode", None), "reason": "MT5 order_check rechazó el cierre al precio actualizado"}
        response = self.adapter.send_order(request)
        retcode = getattr(response, "retcode", None)
        done = self.adapter.constant("TRADE_RETCODE_DONE")
        if response is None or retcode != done:
            return {"ticket": ticket, "action": rule["action"], "success": False, "retcode": retcode,
                    "broker_comment": getattr(response, "comment", None), "reason": "MT5 no confirmó la acción"}
        after_rows = self.adapter.positions_get()
        after = next((p for p in (after_rows or []) if int(p.ticket) == ticket), None)
        if rule["action"] == "close": verified = after is None
        else:
            digits = int(getattr(info, "digits", 5) or 5); tolerance = 10 ** (-digits)
            verified = after is not None and abs(_number(after.sl) - request["sl"]) < tolerance and abs(_number(after.tp) - request["tp"]) < tolerance
        return {"ticket": ticket, "symbol": symbol, "action": rule["action"], "success": verified,
                "retcode": retcode, "order": getattr(response, "order", None), "deal": getattr(response, "deal", None),
                "broker_comment": getattr(response, "comment", None),
                "reason": "Estado MT5 verificado" if verified else "MT5 aceptó la solicitud pero el estado final no confirma el resultado"}

    def _run(self):
        while not self._stop_event.is_set():
            try:
                if self.connected(): self.poll_once()
                with self._lock: self._last_error = None
            except Exception as exc:
                with self._lock: self._last_error = str(exc)
                self.log("position_manager_error", {"error": str(exc)})
            self._stop_event.wait(self.interval)

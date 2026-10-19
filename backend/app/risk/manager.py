def risk_budget(equity: float, risk_fraction: float = 0.005) -> float:
    if equity <= 0 or not 0 < risk_fraction <= 0.02:
        raise ValueError("Equity o fracción de riesgo fuera de límites")
    return equity * risk_fraction

def position_size(equity: float, risk_fraction: float, entry: float, stop: float,
                  tick_size: float, tick_value: float, volume_min: float,
                  volume_max: float, volume_step: float) -> float:
    """Size by broker contract tick economics; returns zero if below min volume."""
    if min(entry, stop, tick_size, tick_value, volume_step) <= 0 or volume_min <= 0 or volume_max < volume_min:
        raise ValueError("Especificaciones de símbolo inválidas")
    loss_per_lot = abs(entry-stop) / tick_size * tick_value
    raw = risk_budget(equity, risk_fraction) / loss_per_lot
    steps = int((raw - volume_min) / volume_step) if raw >= volume_min else -1
    volume = volume_min + max(steps, 0) * volume_step
    return round(min(volume, volume_max), 8) if raw >= volume_min else 0.0

def within_daily_limit(day_start_equity: float, current_equity: float, limit: float = 0.02) -> bool:
    return day_start_equity > 0 and current_equity > day_start_equity * (1-limit)

def within_drawdown(peak_equity: float, current_equity: float, limit: float = 0.05) -> bool:
    return peak_equity > 0 and current_equity > peak_equity * (1-limit)

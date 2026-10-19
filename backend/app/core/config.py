from dataclasses import dataclass
import os

@dataclass(frozen=True)
class Settings:
    app_name: str = "AI Trading Engine"
    mode: str = os.getenv("TRADING_MODE", "PAPER").upper()
    database_path: str = os.getenv("DATABASE_PATH", "data/trading.db")
    risk_per_trade: float = float(os.getenv("RISK_PER_TRADE", "0.005"))
    daily_loss_limit: float = float(os.getenv("DAILY_LOSS_LIMIT", "0.02"))
    max_drawdown: float = float(os.getenv("MAX_DRAWDOWN", "0.05"))
    position_manager_allow_real: bool = os.getenv("POSITION_MANAGER_ALLOW_REAL", "false").strip().lower() == "true"
    symbols: tuple[str, ...] = tuple(s.strip() for s in os.getenv("SYMBOLS", "XAUUSD,EURUSD,GBPUSD,USDJPY").split(",") if s.strip())

settings = Settings()
if settings.mode not in {"BACKTEST", "PAPER", "DEMO", "REAL"}:
    settings = Settings(mode="PAPER")

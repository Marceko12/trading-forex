from enum import Enum
from pydantic import BaseModel, Field

class HistoryDownloadRequest(BaseModel):
    symbol: str
    timeframe: str
    start: str
    end: str

class Decision(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    CLOSE = "CLOSE"
    NO_TRADE = "NO TRADE"

class Signal(BaseModel):
    symbol: str
    decision: Decision
    reason: str
    timestamp: str
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None

class BacktestRequest(BaseModel):
    symbol: str = "XAUUSD"
    timeframe: str = "H1"
    initial_balance: float = Field(default=10_000, gt=0)
    risk_per_trade: float = Field(default=0.005, gt=0, le=0.02)
    spread: float = Field(default=0, ge=0)
    commission: float = Field(default=0, ge=0)
    slippage: float = Field(default=0, ge=0)
    train_fraction: float = Field(default=0.7, gt=0.5, lt=0.9)
    start: str | None = None
    end: str | None = None

class TickDownloadRequest(BaseModel):
    symbol: str
    start: str
    end: str

class EngineStartRequest(BaseModel):
    symbols: list[str] | None = None
    symbol: str | None = None
    timeframe: str = "H1"

class EnginePauseRequest(BaseModel):
    paused: bool

class PositionManagerConfigRequest(BaseModel):
    enabled: bool = False
    rules: list[dict] = Field(default_factory=list)
    confirm_real: str = ""


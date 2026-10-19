from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from .core.config import settings
from .core.models import BacktestRequest, Signal, Decision
from .database.store import Store
from .mt5.adapter import MT5Adapter
from .strategies.baseline import decide
from .strategies.indicators import add_indicators
from .backtesting.engine import run_backtest

app = FastAPI(title=settings.app_name, version="0.1.0", description="Motor inicial seguro: análisis y backtest; no envía órdenes.")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])
store=Store(settings.database_path)
mt5=MT5Adapter()
runtime={"running":False,"paused":False,"mode":settings.mode}
mt5_state={"connected":False,"error":"No se ha solicitado conexión a MT5"}

@app.get("/api/status")
def status():
    return {"mode":runtime["mode"],"engine_running":runtime["running"],"paused":runtime["paused"],"mt5":mt5_state,"symbols":list(settings.symbols),"risk":{"per_trade":settings.risk_per_trade,"daily_loss_limit":settings.daily_loss_limit,"max_drawdown":settings.max_drawdown},"execution_enabled":False}

@app.post("/api/mt5/connect")
def connect_mt5():
    global mt5_state
    mt5_state=mt5.connect()
    store.log("mt5_connection",mt5_state)
    return mt5_state

@app.post("/api/mt5/disconnect")
def disconnect_mt5():
    global mt5_state
    mt5.disconnect(); mt5_state={"connected":False,"error":"Desconectado por el usuario"}
    return mt5_state

@app.get("/api/market/{symbol}")
def market(symbol: str):
    if not mt5_state.get("connected"):
        raise HTTPException(503,"MT5 desconectado; no hay cotización disponible")
    info=mt5._mt5.symbol_info_tick(symbol)
    if info is None: raise HTTPException(404,"Símbolo sin tick disponible")
    return {"symbol":symbol,"bid":info.bid,"ask":info.ask,"spread":info.ask-info.bid,"time":datetime.fromtimestamp(info.time,timezone.utc).isoformat()}

def load_csv(symbol):
    path=Path("data")/f"{symbol.upper()}.csv"
    if not path.exists(): raise HTTPException(404,f"No existe {path}; coloque velas OHLC en UTC con columnas time,open,high,low,close")
    try: return pd.read_csv(path)
    except Exception as exc: raise HTTPException(400,f"CSV inválido: {exc}")

@app.get("/api/signals")
def signals():
    result=[]
    for symbol in settings.symbols:
        try:
            frame=load_csv(symbol); decision,reason=decide(frame)
            last=add_indicators(frame).iloc[-1]
            signal=Signal(symbol=symbol,decision=decision,reason=reason,timestamp=datetime.now(timezone.utc).isoformat(),entry=float(last.close) if decision in (Decision.BUY,Decision.SELL) else None,stop_loss=float(last.close-1.5*last.atr) if decision==Decision.BUY and pd.notna(last.atr) else (float(last.close+1.5*last.atr) if decision==Decision.SELL and pd.notna(last.atr) else None),take_profit=float(last.close+3*last.atr) if decision==Decision.BUY and pd.notna(last.atr) else (float(last.close-3*last.atr) if decision==Decision.SELL and pd.notna(last.atr) else None))
            item=signal.model_dump(mode="json"); result.append(item); store.log("signal",item)
        except HTTPException: continue
    return {"signals":result,"source":"CSV local","note":"Reglas deterministas; no es un modelo de IA entrenado"}

@app.get("/api/positions")
def positions():
    if not mt5_state.get("connected"): return {"available":False,"positions":[]}
    rows=mt5._mt5.positions_get()
    return {"available":True,"positions":[{"ticket":p.ticket,"symbol":p.symbol,"type":p.type,"volume":p.volume,"price_open":p.price_open,"sl":p.sl,"tp":p.tp,"profit":p.profit} for p in (rows or [])]}

@app.get("/api/events")
def events(): return {"events":store.events()}

@app.get("/api/backtests")
def backtests(): return {"backtests":store.backtests()}

@app.post("/api/backtests")
def backtest(request: BacktestRequest):
    frame=load_csv(request.symbol)
    try: result=run_backtest(frame,request.initial_balance,request.risk_per_trade,request.spread,request.commission,request.slippage,request.train_fraction)
    except ValueError as exc: raise HTTPException(400,str(exc))
    result["symbol"]=request.symbol; result["timeframe"]=request.timeframe
    ident=store.save_backtest(request.symbol,result); result["id"]=ident
    return result

@app.post("/api/engine/start")
def start(): runtime["running"]=True;runtime["paused"]=False; return {"ok":True,"running":True,"note":"Solo análisis de archivos CSV; ejecución de órdenes deshabilitada"}
@app.post("/api/engine/pause")
def pause(): runtime["paused"]=not runtime["paused"]; return {"ok":True,"paused":runtime["paused"]}
@app.post("/api/engine/stop")
def stop(): runtime["running"]=False;runtime["paused"]=False; return {"ok":True,"running":False}
@app.post("/api/engine/emergency-stop")
def emergency_stop():
    runtime["running"]=False;runtime["paused"]=False
    store.log("emergency_stop",{"at":datetime.now(timezone.utc).isoformat()})
    return {"ok":True,"running":False,"note":"Se detuvo el análisis local. No se liquidaron posiciones MT5."}

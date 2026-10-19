from datetime import datetime, timezone
from pathlib import Path
import io, json, uuid
import pandas as pd
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from .core.config import settings
from .core.models import BacktestRequest, Signal, Decision, HistoryDownloadRequest, HistoryCountDownloadRequest, TickDownloadRequest, EngineStartRequest, EnginePauseRequest, PositionManagerConfigRequest, AutoTradingStartRequest
from .core.runner import AnalysisRunner
from .database.store import Store
from .mt5.adapter import MT5Adapter
from .strategies.baseline import decide
from .strategies.indicators import add_indicators
from .backtesting.engine import run_backtest
from .market_data.history import HistoryService, TIMEFRAMES, discover_symbols, read_history, read_ticks, read_metadata, CSV_COLUMNS, SYMBOL_RE, rates_to_frame
from .market_data.validation import validate_candles
from .strategies.signal_engine import evaluate_signal
from .positions.manager import PositionManager
from .core.auto_trader import AutoTradingEngine

app = FastAPI(title=settings.app_name, version="0.1.0", description="PAPER por defecto; operaciones solo con habilitación DEMO explícita. REAL bloqueado para el motor automático.")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173","http://127.0.0.1:5173","http://localhost:5174","http://127.0.0.1:5174"], allow_methods=["*"], allow_headers=["*"])
store=Store(settings.database_path)
mt5=MT5Adapter()
runtime={"mode":settings.mode}
mt5_state={"connected":False,"error":"No se ha solicitado conexión a MT5"}
history_jobs={}
signal_cache={}
signal_bars=set()

def _scan_market(symbols, timeframe):
    results=[]
    for symbol in symbols:
        if mt5_state.get("connected") and mt5._mt5 is not None:
            rates=mt5.copy_rates_from_pos(symbol,timeframe,1,500)
            error=mt5.last_error()
            if rates is None: raise RuntimeError(f"MT5 no devolvió velas de {symbol} {timeframe}: {error}")
            frame=rates_to_frame(rates)
            tick=mt5.symbol_info_tick(symbol); info=mt5.symbol_info(symbol)
            if tick is None: raise RuntimeError(f"MT5 no devolvió cotización para {symbol}: {mt5.last_error()}")
            signal=evaluate_signal(symbol,timeframe,frame,bid=tick.bid,ask=tick.ask,point=getattr(info,"point",None),source="MT5",bars_are_closed=True)
        else:
            frame=read_history(symbol,timeframe)
            signal=evaluate_signal(symbol,timeframe,frame,source="CSV local")
        key=(symbol,timeframe,signal["bar_time"])
        signal_cache[(symbol,timeframe)]=signal
        results.append(signal)
        if key not in signal_bars:
            signal_bars.add(key); store.log("signal",signal)
    return results

runner=AnalysisRunner(_scan_market)
position_manager=PositionManager(mt5,lambda: bool(mt5_state.get("connected") and mt5._mt5 is not None),store.log,settings.risk_per_trade,allow_real_actions=settings.position_manager_allow_real)
auto_trader=AutoTradingEngine(mt5,lambda: bool(mt5_state.get("connected") and mt5._mt5 is not None),_scan_market,store.log,
    risk_state_loader=lambda: (store.latest_event("auto_risk_baseline") or {}).get("payload"),
    seen_loader=store.recent_auto_signal_keys)

@app.on_event("startup")
def start_position_supervisor():
    position_manager.start()

@app.on_event("shutdown")
def stop_position_supervisor():
    position_manager.stop()

def _filter_backtest_range(frame, start, end):
    if start:
        frame=frame[frame.time >= pd.to_datetime(start,utc=True)]
    if end:
        end_value=f"{end}T23:59:59.999999Z" if isinstance(end,str) and len(end)==10 else end
        frame=frame[frame.time <= pd.to_datetime(end_value,utc=True)]
    return frame

def _read_or_download_history(request):
    try:
        return read_history(request.symbol,request.timeframe),read_metadata(request.symbol,request.timeframe)
    except FileNotFoundError:
        if not mt5_state.get("connected") or mt5._mt5 is None:
            raise
        end=request.end or datetime.now(timezone.utc).isoformat()
        start=request.start or (datetime.now(timezone.utc)-pd.Timedelta(days=365)).isoformat()
        try:
            HistoryService(mt5).download(request.symbol,request.timeframe,start,end)
        except Exception as exc:
            raise RuntimeError(f"No hay CSV y MT5 no pudo descargar {request.symbol} {request.timeframe}: {exc}") from exc
        return read_history(request.symbol,request.timeframe),read_metadata(request.symbol,request.timeframe)

@app.get("/api/status")
def status():
    engine=runner.status()
    actual_mode=mt5_state.get("account_mode","UNKNOWN")
    return {"mode":runtime["mode"],"account_mode":actual_mode,"engine_running":engine["running"],"paused":engine["paused"],"engine":engine,"auto_trading":auto_trader.status(),"mt5":mt5_state,"position_manager":position_manager.status(),"symbols":list(settings.symbols),"risk":{"per_trade":settings.risk_per_trade,"daily_loss_limit":settings.daily_loss_limit,"max_drawdown":settings.max_drawdown},"execution_enabled":auto_trader.status()["enabled"]}

@app.post("/api/mt5/connect")
def connect_mt5():
    global mt5_state
    mt5_state=mt5.connect()
    store.log("mt5_connection",mt5_state)
    return mt5_state

@app.post("/api/mt5/disconnect")
def disconnect_mt5():
    global mt5_state
    position_manager.disarm()
    auto_trader.stop()
    runner.stop()
    mt5.disconnect(); mt5_state={"connected":False,"error":"Desconectado por el usuario"}
    return mt5_state

@app.get("/api/mt5/account")
def account_info():
    if not mt5_state.get("connected") or mt5._mt5 is None:
        raise HTTPException(503,"MT5 desconectado; información de cuenta no disponible")
    account=mt5.account_info(); terminal=mt5.terminal_info()
    if account is None: raise HTTPException(503,f"MT5 no devolvió account_info: {mt5.last_error()}")
    return {"login":account.login,"server":account.server,"currency":account.currency,"balance":account.balance,"equity":account.equity,"margin":account.margin,"margin_free":account.margin_free,"trade_allowed":bool(getattr(account,"trade_allowed",False)),"trade_mode":getattr(account,"trade_mode",None),"terminal_connected":bool(getattr(terminal,"connected",False)) if terminal else False,"terminal_trade_allowed":bool(getattr(terminal,"trade_allowed",False)) if terminal else False}

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
def signals(symbol: str | None = None, timeframe: str | None = None):
    if (symbol is None) != (timeframe is None):
        raise HTTPException(400,"Indique símbolo y temporalidad juntos")
    if symbol is not None:
        if not SYMBOL_RE.fullmatch(symbol): raise HTTPException(400,"Símbolo inválido")
        timeframe=timeframe.upper()
        if timeframe not in TIMEFRAMES: raise HTTPException(400,f"Temporalidad no admitida: {timeframe}")
        try: result=_scan_market([symbol],timeframe)
        except FileNotFoundError as exc: raise HTTPException(404,str(exc))
        except ValueError as exc: raise HTTPException(422,str(exc))
        except Exception as exc: raise HTTPException(503,str(exc))
    else:
        result=list(signal_cache.values())
        if not result:
            for configured_symbol in settings.symbols:
                try: _scan_market([configured_symbol],"H1")
                except Exception: continue
            result=list(signal_cache.values())
    return {"signals":result,"source":"MT5" if mt5_state.get("connected") else "CSV local","note":"Reglas deterministas evaluadas sobre velas cerradas; no es un modelo de IA entrenado"}

@app.get("/api/market-data/candles/{symbol}/{timeframe}")
def market_candles(symbol: str, timeframe: str, count: int = Query(500, ge=1, le=2000)):
    if not SYMBOL_RE.fullmatch(symbol): raise HTTPException(400,"Símbolo inválido")
    timeframe=timeframe.upper()
    if timeframe not in TIMEFRAMES: raise HTTPException(400,f"Temporalidad no admitida: {timeframe}")
    if not mt5_state.get("connected") or mt5._mt5 is None:
        raise HTTPException(503,"MT5 desconectado; no se pueden consultar velas en vivo")
    try:
        terminal=mt5.terminal_info()
        if terminal is None: raise RuntimeError(f"MT5 terminal_info() no disponible: {mt5.last_error()}")
        if getattr(terminal,"connected",True) is False: raise RuntimeError("El terminal MT5 está desconectado del servidor del bróker")
        account=mt5.account_info()
        if account is None: raise RuntimeError(f"MT5 account_info() no disponible: {mt5.last_error()}")
        frame=HistoryService(mt5).fetch_recent_closed(symbol,timeframe,count)
        candles=[]
        for row in frame.itertuples(index=False):
            timestamp=pd.Timestamp(row.time)
            if timestamp.tzinfo is None: timestamp=timestamp.tz_localize("UTC")
            else: timestamp=timestamp.tz_convert("UTC")
            candles.append({"time":timestamp.isoformat().replace("+00:00","Z"),"open":float(row.open),"high":float(row.high),"low":float(row.low),"close":float(row.close),"tick_volume":int(row.tick_volume),"spread":int(row.spread),"real_volume":int(row.real_volume)})
    except LookupError as exc: raise HTTPException(404,str(exc))
    except FileNotFoundError as exc: raise HTTPException(404,str(exc))
    except ValueError as exc: raise HTTPException(422,str(exc))
    except RuntimeError as exc: raise HTTPException(503,str(exc))
    except Exception as exc: raise HTTPException(500,f"No se pudieron serializar las velas MT5: {exc}")
    return {"symbol":symbol,"timeframe":timeframe,"source":"MT5","closed_only":True,"count":len(candles),"last_candle_time":candles[-1]["time"] if candles else None,"candles":candles}

@app.get("/api/positions")
def positions():
    if mt5_state.get("connected"):
        try: position_manager.poll_once()
        except Exception: pass
    state=position_manager.status()
    return {"available":state["connected"],"positions":state["positions"],"alerts":state["alerts"],"manager":state}

@app.get("/api/position-manager")
def position_manager_status():
    if mt5_state.get("connected"):
        try: position_manager.poll_once()
        except Exception: pass
    return position_manager.status()

@app.put("/api/position-manager/config")
def configure_position_manager(request: PositionManagerConfigRequest):
    try:
        state=position_manager.configure(request.enabled,request.rules,real_acknowledged=request.confirm_real == "HABILITAR GESTION REAL")
    except ValueError as exc:
        raise HTTPException(400,str(exc))
    store.log("position_manager_config",{"enabled":state["enabled"],"rules":state["rules"],"real_acknowledged":state["real_acknowledged"]})
    return state

@app.get("/api/events")
def events(): return {"events":store.events()}

@app.get("/api/backtests")
def backtests(limit: int = 10): return {"backtests":store.backtests(max(1,min(limit,50)))}

@app.get("/api/history/symbols")
def history_symbols():
    if not mt5_state.get("connected") or mt5._mt5 is None:
        raise HTTPException(503,"MT5 está desconectado. Abra el terminal autenticado y pulse Conectar MT5.")
    return discover_symbols(mt5.symbols())

@app.post("/api/history/download")
def download_history(request: HistoryDownloadRequest, background: BackgroundTasks):
    if not mt5_state.get("connected") or mt5._mt5 is None:
        raise HTTPException(503,"MT5 está desconectado. Conecte el terminal antes de descargar histórico.")
    if not SYMBOL_RE.fullmatch(request.symbol): raise HTTPException(400,"Símbolo inválido")
    job_id=str(uuid.uuid4())
    history_jobs[job_id]={"id":job_id,"status":"queued","progress":{"pages_done":0,"candles_received":0}}
    def run():
        job=history_jobs[job_id];job["status"]="running"
        def progress(values): job["progress"].update(values)
        try:
            service=HistoryService(mt5)
            result=service.download(request.symbol,request.timeframe,request.start,request.end,progress)
            job["result"]=result.__dict__;job["status"]="completed"
            store.log("history_download",job["result"])
        except Exception as exc:
            job["status"]="failed";job["error"]=str(exc)
            store.log("history_download_error",{"symbol":request.symbol,"timeframe":request.timeframe,"error":str(exc)})
    background.add_task(run)
    return {"job_id":job_id,"status":"queued"}

@app.post("/api/history/download-count")
def download_history_count(request: HistoryCountDownloadRequest, background: BackgroundTasks):
    if not mt5_state.get("connected") or mt5._mt5 is None:
        raise HTTPException(503,"MT5 está desconectado. Conecte el terminal antes de descargar histórico.")
    if not SYMBOL_RE.fullmatch(request.symbol): raise HTTPException(400,"Símbolo inválido")
    timeframe=request.timeframe.upper()
    if timeframe not in TIMEFRAMES: raise HTTPException(400,f"Temporalidad no admitida: {timeframe}")
    job_id=str(uuid.uuid4())
    history_jobs[job_id]={"id":job_id,"status":"queued","kind":"history_count_download","progress":{"requested":request.count,"candles_received":0}}
    def run():
        job=history_jobs[job_id];job["status"]="running"
        try:
            result=HistoryService(mt5).download_count(request.symbol,timeframe,request.count)
            job["result"]=result.__dict__;job["status"]="completed"
            store.log("history_count_download",job["result"])
        except Exception as exc:
            job["status"]="failed";job["error"]=str(exc)
            store.log("history_download_error",{"symbol":request.symbol,"timeframe":timeframe,"count":request.count,"error":str(exc)})
    background.add_task(run)
    return {"job_id":job_id,"status":"queued"}

@app.post("/api/history/ticks/download")
def download_ticks(request: TickDownloadRequest, background: BackgroundTasks):
    if not mt5_state.get("connected") or mt5._mt5 is None:
        raise HTTPException(503,"MT5 está desconectado. Conecte el terminal antes de descargar ticks.")
    if not SYMBOL_RE.fullmatch(request.symbol): raise HTTPException(400,"Símbolo inválido")
    job_id=str(uuid.uuid4())
    history_jobs[job_id]={"id":job_id,"status":"queued","kind":"tick_download","progress":{"pages_done":0,"ticks_received":0}}
    def run():
        job=history_jobs[job_id];job["status"]="running"
        try:
            service=HistoryService(mt5)
            job["result"]=service.download_ticks(request.symbol,request.start,request.end,lambda values: job["progress"].update(values))
            job["status"]="completed";store.log("tick_download",job["result"])
        except Exception as exc:
            job["status"]="failed";job["error"]=str(exc)
            store.log("tick_download_error",{"symbol":request.symbol,"error":str(exc)})
    background.add_task(run)
    return {"job_id":job_id,"status":"queued"}

@app.get("/api/history/jobs/{job_id}")
def history_job(job_id: str):
    if job_id not in history_jobs: raise HTTPException(404,"Trabajo de descarga no encontrado")
    return history_jobs[job_id]

@app.post("/api/backtests/vectorbt")
def vectorbt_backtest(request: BacktestRequest, background: BackgroundTasks):
    """Queue parameter sweep in Starlette's worker pool; the API event loop stays free."""
    try:
        frame,metadata=_read_or_download_history(request)
        frame=_filter_backtest_range(frame,request.start,request.end)
    except ValueError as exc: raise HTTPException(400,str(exc))
    except FileNotFoundError as exc: raise HTTPException(404,str(exc))
    except RuntimeError as exc: raise HTTPException(503,str(exc))
    job_id=str(uuid.uuid4())
    history_jobs[job_id]={"id":job_id,"status":"queued","kind":"vectorbt_backtest"}
    def run():
        job=history_jobs[job_id];job["status"]="running"
        try:
            from .backtesting.vectorbt_sweep import run_vectorbt_sweep
            result=run_vectorbt_sweep(frame,request.initial_balance,request.commission,request.spread,request.slippage,request.train_fraction)
            result.update({"symbol":request.symbol,"timeframe":request.timeframe,"provider":metadata.get("source","CSV local"),"contract_spec":metadata.get("contract_spec",{}),"requested_range":{"start":request.start,"end":request.end}})
            job["result"]=result;job["status"]="completed"
            result["id"]=store.save_backtest(request.symbol,result)
            store.log("vectorbt_backtest",result)
        except Exception as exc:
            job["status"]="failed";job["error"]=str(exc)
            store.log("vectorbt_backtest_error",{"symbol":request.symbol,"timeframe":request.timeframe,"error":str(exc)})
    background.add_task(run)
    return {"job_id":job_id,"status":"queued","engine":"VectorBT"}

@app.get("/api/history/{symbol}/{timeframe}")
def history_info(symbol: str, timeframe: str):
    try:
        frame=read_history(symbol,timeframe)
        return {"symbol":symbol,"timeframe":timeframe.upper(),"count":len(frame),"first_time":frame.time.min().isoformat(),"last_time":frame.time.max().isoformat(),"validation":"válido","metadata":read_metadata(symbol,timeframe)}
    except (ValueError,FileNotFoundError) as exc: raise HTTPException(404,str(exc))

@app.post("/api/history/import/{symbol}/{timeframe}")
async def import_history(symbol: str,timeframe: str,request: Request):
    """Import an existing CSV as raw text body; no multipart dependency required."""
    if not SYMBOL_RE.fullmatch(symbol): raise HTTPException(400,"Símbolo inválido")
    timeframe=timeframe.upper()
    if timeframe not in TIMEFRAMES: raise HTTPException(400,"Temporalidad no admitida")
    try:
        body=await request.body()
        frame=validate_candles(pd.read_csv(io.BytesIO(body)))[CSV_COLUMNS]
    except Exception as exc: raise HTTPException(400,f"CSV rechazado: {exc}")
    root=Path("data");archive=root/"mt5"/symbol/f"{timeframe.upper()}.csv";active=root/f"{symbol}.csv"
    archive.parent.mkdir(parents=True,exist_ok=True);root.mkdir(exist_ok=True)
    nonce=uuid.uuid4().hex
    archive_tmp=archive.with_name(f".{archive.name}.{nonce}.tmp");active_tmp=active.with_name(f".{active.name}.{nonce}.tmp")
    frame.to_csv(archive_tmp,index=False);frame.to_csv(active_tmp,index=False);archive_tmp.replace(archive);active_tmp.replace(active)
    metadata={"symbol":symbol,"timeframe":timeframe.upper(),"first_time":frame.time.min().isoformat(),"last_time":frame.time.max().isoformat(),"count":len(frame),"downloaded_at":datetime.now(timezone.utc).isoformat(),"source":"CSV importado por usuario","contract_spec":{}}
    (archive.parent/f"{timeframe.upper()}.metadata.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding="utf-8")
    store.log("history_csv_import",metadata)
    return metadata

@app.post("/api/backtests")
def backtest(request: BacktestRequest):
    try:
        frame,metadata=_read_or_download_history(request)
        frame=_filter_backtest_range(frame,request.start,request.end)
        ticks=read_ticks(request.symbol)
        result=run_backtest(frame,request.initial_balance,request.risk_per_trade,request.spread,request.commission,request.slippage,request.train_fraction,metadata.get("contract_spec"),ticks)
    except ValueError as exc: raise HTTPException(400,str(exc))
    except FileNotFoundError as exc: raise HTTPException(404,str(exc))
    except RuntimeError as exc: raise HTTPException(503,str(exc))
    result["symbol"]=request.symbol; result["timeframe"]=request.timeframe
    ident=store.save_backtest(request.symbol,result); result["id"]=ident
    return result

@app.post("/api/engine/start")
def start(request: EngineStartRequest = EngineStartRequest()):
    timeframe=request.timeframe.upper()
    if timeframe not in TIMEFRAMES: raise HTTPException(400,f"Temporalidad no admitida: {timeframe}")
    symbols=request.symbols or ([request.symbol] if request.symbol else list(settings.symbols))
    if not symbols or any(not SYMBOL_RE.fullmatch(s) for s in symbols): raise HTTPException(400,"Símbolo inválido")
    state=runner.start(symbols,timeframe)
    store.log("engine_started",state)
    return {"ok":True,"engine":state,"note":"Análisis periódico activo; las órdenes permanecen deshabilitadas"}
@app.post("/api/engine/pause")
def pause(request: EnginePauseRequest | None = None):
    current=runner.status()["paused"]
    state=runner.pause(not current if request is None else request.paused)
    store.log("engine_paused" if state["paused"] else "engine_resumed",state)
    return {"ok":True,"engine":state}
@app.post("/api/engine/stop")
def stop():
    state=runner.stop();store.log("engine_stopped",state)
    return {"ok":True,"engine":state}
@app.post("/api/engine/emergency-stop")
def emergency_stop():
    auto_trader.stop()
    state=runner.stop()
    store.log("emergency_stop",{"at":datetime.now(timezone.utc).isoformat()})
    return {"ok":True,"engine":state,"note":"Se detuvo el análisis local. No se liquidaron posiciones MT5."}

@app.post("/api/trading-engine/start")
def start_trading_engine(request: AutoTradingStartRequest):
    timeframe=request.timeframe.upper()
    if timeframe not in TIMEFRAMES: raise HTTPException(400,f"Temporalidad no admitida: {timeframe}")
    if not SYMBOL_RE.fullmatch(request.symbol): raise HTTPException(400,"Símbolo MT5 inválido")
    try:
        state=auto_trader.start(mode=request.mode,symbol=request.symbol,timeframe=timeframe,
            limits={"risk_per_trade":request.risk_per_trade,"daily_loss_limit":request.daily_loss_limit,
                    "max_drawdown":request.max_drawdown,"max_positions":request.max_positions,
                    "max_spread_points":request.max_spread_points},confirm_demo=request.confirm_demo)
    except ValueError as exc: raise HTTPException(400,str(exc))
    return {"ok":True,"trading_engine":state}

@app.post("/api/trading-engine/stop")
def stop_trading_engine():
    state=auto_trader.stop()
    return {"ok":True,"trading_engine":state,"note":"Se detuvieron nuevas entradas. No se cerró ninguna posición existente."}

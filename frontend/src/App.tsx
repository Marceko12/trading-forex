import { ChangeEvent, useEffect, useRef, useState } from 'react'
import CandlestickChart from './CandlestickChart'

const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000/api'
const TIMEFRAMES = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4']
type SymbolMatch = { symbol: string; instrument: string; micro: boolean; variant: string }
type Status = { mode: string; account_mode?: string; engine_running: boolean; paused: boolean; engine?: { last_scan?: string; scan_count: number; last_error?: string }; auto_trading?: any; mt5: { connected: boolean; error?: string; balance?: number; equity?: number }; symbols: string[]; risk: { per_trade: number; daily_loss_limit: number; max_drawdown: number }; execution_enabled: boolean }
type PositionManagerStatus = { running: boolean; connected: boolean; stale: boolean; enabled: boolean; real_acknowledged: boolean; real_actions_allowed_by_config: boolean; interval_seconds: number; last_poll?: string; last_error?: string; position_count: number; positions: any[]; alerts: any[]; rules: any[] }
type HistoryInfo = { symbol: string; timeframe: string; count: number; first_time: string; last_time: string; validation: string; metadata?: { source?: string; downloaded_at?: string; requested_start?: string; requested_end?: string } }
type LiveCandle = { time: string; open: number; high: number; low: number; close: number; tick_volume: number; spread: number; real_volume: number }

export default function App() {
  const [status, setStatus] = useState<Status | null>(null)
  const [signals, setSignals] = useState<any[]>([])
  const [liveCandles, setLiveCandles] = useState<LiveCandle[]>([])
  const [candleError, setCandleError] = useState('')
  const [candlesLoading, setCandlesLoading] = useState(false)
  const [lastCandleReceived, setLastCandleReceived] = useState('')
  const [positions, setPositions] = useState<any[]>([])
  const [positionManager, setPositionManager] = useState<PositionManagerStatus | null>(null)
  const [managerEnabled, setManagerEnabled] = useState(false)
  const [managerRules, setManagerRules] = useState('[]')
  const [realManagementPhrase, setRealManagementPhrase] = useState('')
  const [manualMaxLoss, setManualMaxLoss] = useState(50)
  const [manualTarget, setManualTarget] = useState(50)
  const [manualSymbol, setManualSymbol] = useState('')
  const [manualStop, setManualStop] = useState('')
  const [manualTake, setManualTake] = useState('')
  const [manualTrail, setManualTrail] = useState('')
  const [autoMode, setAutoMode] = useState('PAPER')
  const [autoSymbol, setAutoSymbol] = useState('GOLDmicro')
  const [autoTimeframe, setAutoTimeframe] = useState('H1')
  const [autoRisk, setAutoRisk] = useState(0.005)
  const [autoDailyLoss, setAutoDailyLoss] = useState(0.02)
  const [autoDrawdown, setAutoDrawdown] = useState(0.05)
  const [autoMaxPositions, setAutoMaxPositions] = useState(1)
  const [autoMaxSpread, setAutoMaxSpread] = useState(50)
  const [demoPhrase, setDemoPhrase] = useState('')
  const managerConfigLoaded = useRef(false)
  const [events, setEvents] = useState<any[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [matches, setMatches] = useState<SymbolMatch[]>([])
  const [availableSymbols, setAvailableSymbols] = useState<string[]>([])
  const [selected, setSelected] = useState('')
  const [selectedCompare, setSelectedCompare] = useState<string[]>([])
  const [timeframe, setTimeframe] = useState('H1')
  const [candleCount, setCandleCount] = useState(5000)
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [job, setJob] = useState<any>(null)
  const [tickResult, setTickResult] = useState<any | null>(null)
  const [history, setHistory] = useState<HistoryInfo | null>(null)
  const [backtestRows, setBacktestRows] = useState<any[]>([])
  const [vectorbtResult, setVectorbtResult] = useState<any | null>(null)
  const [initialBalance, setInitialBalance] = useState(10000)
  const [spread, setSpread] = useState(0)
  const [commission, setCommission] = useState(0)
  const [slippage, setSlippage] = useState(0)

  async function load() {
    try {
      const [a, c, d, e] = await Promise.all([fetch(`${API}/status`), fetch(`${API}/positions`), fetch(`${API}/events`), fetch(`${API}/backtests?limit=10`)])
      const s = await a.json(); setStatus(s)
      const positionState = await c.json(); setPositions(positionState.positions || []); setPositionManager(positionState.manager || null)
      if (!managerConfigLoaded.current && positionState.manager) {
        setManagerEnabled(positionState.manager.enabled || false)
        setManagerRules(JSON.stringify(positionState.manager.rules || [], null, 2))
        managerConfigLoaded.current = true
      }
      setEvents((await d.json()).events || [])
      const savedBacktests = (await e.json()).backtests || []
      setBacktestRows(savedBacktests.map((item: any) => ({ ...item.result, id: item.id, symbol: item.symbol, created_at: item.created_at })))
      setError('')
      if (s.mt5.connected) {
        const response = await fetch(`${API}/history/symbols`)
        if (response.ok) {
          const value = await response.json(); setMatches(value.matches || []); setAvailableSymbols(value.available || [])
          setSelected(current => current || value.matches?.[0]?.symbol || '')
        }
      }
    } catch { setError('No se pudo conectar con la API. Inicie el backend en http://127.0.0.1:8000') }
  }

  useEffect(() => { void load(); const id = setInterval(() => void load(), 10000); return () => clearInterval(id) }, [])

  useEffect(() => {
    if (!selected || !status?.mt5.connected) {
      setLiveCandles([]); setSignals([]); setCandleError(''); setLastCandleReceived('')
      return
    }
    let active = true
    let controller: AbortController | null = null
    const key = `${selected}::${timeframe}`
    setLiveCandles([]); setSignals([]); setCandleError(''); setLastCandleReceived(''); setCandlesLoading(true)
    const refresh = async () => {
      controller?.abort(); controller = new AbortController()
      try {
        const [candleResponse, signalResponse] = await Promise.all([
          fetch(`${API}/market-data/candles/${encodeURIComponent(selected)}/${timeframe}?count=500`, { signal: controller.signal }),
          fetch(`${API}/signals?symbol=${encodeURIComponent(selected)}&timeframe=${timeframe}`, { signal: controller.signal }),
        ])
        const candleData = await candleResponse.json()
        if (!candleResponse.ok) throw Error(candleData.detail || `Error HTTP ${candleResponse.status} consultando velas`)
        const signalData = await signalResponse.json()
        if (!signalResponse.ok) throw Error(signalData.detail || `Error HTTP ${signalResponse.status} consultando señales`)
        if (!active) return
        if (`${candleData.symbol}::${candleData.timeframe}` !== key) return
        setLiveCandles(Array.isArray(candleData.candles) ? candleData.candles : [])
        setLastCandleReceived(candleData.last_candle_time || '')
        setSignals(signalData.signals || [])
        setCandleError(candleData.count ? '' : `MT5 no devolvió velas cerradas para ${selected} ${timeframe}`)
      } catch (e) {
        if (!active || (e instanceof DOMException && e.name === 'AbortError')) return
        setCandleError(String(e).replace(/^Error: /, ''))
      } finally { if (active) setCandlesLoading(false) }
    }
    void refresh()
    const id = setInterval(() => void refresh(), 15000)
    return () => { active = false; clearInterval(id); controller?.abort() }
  }, [selected, timeframe, status?.mt5.connected])

  async function refreshPositions() {
    try {
      const r = await fetch(`${API}/positions`)
      if (!r.ok) return
      const value = await r.json(); setPositions(value.positions || []); setPositionManager(value.manager || null)
    } catch { /* The main refresh reports API connectivity errors. */ }
  }

  useEffect(() => { const id = setInterval(() => void refreshPositions(), 2000); return () => clearInterval(id) }, [])

  async function savePositionManagerConfig() {
    let rules: any[]
    try { rules = JSON.parse(managerRules); if (!Array.isArray(rules)) throw Error('Las reglas deben ser un arreglo JSON.') }
    catch (e) { setError(`Configuración de reglas inválida: ${String(e)}`); return }
    setBusy(true); setError('')
    try {
      const r = await fetch(`${API}/position-manager/config`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: managerEnabled, rules, confirm_real: realManagementPhrase }) })
      const value = await r.json(); if (!r.ok) throw Error(value.detail || 'No se pudo guardar la configuración')
      setPositionManager(value); setManagerRules(JSON.stringify(value.rules || [], null, 2)); setRealManagementPhrase(''); managerConfigLoaded.current = true
    } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }

  function addPositionRule() {
    try {
      const current = JSON.parse(managerRules)
      if (!Array.isArray(current)) throw Error('Las reglas deben ser un arreglo.')
      setManagerRules(JSON.stringify([...current, { id: `rule-${current.length + 1}`, action: 'maintain', origin: 'any' }], null, 2))
    } catch (e) { setError(`No se pudo añadir la regla: ${String(e)}`) }
  }

  function addManualPolicy() {
    try {
      const current = JSON.parse(managerRules)
      if (!Array.isArray(current)) throw Error('Las reglas deben ser un arreglo.')
      const symbol = manualSymbol.trim() || undefined
      const added: any[] = []
      if (manualMaxLoss > 0 || manualTarget > 0) added.push({ id: `manual-close-${current.length + 1}`, origin: 'manual', symbol, action: 'close', ...(manualMaxLoss > 0 ? { max_loss: -Math.abs(manualMaxLoss) } : {}), ...(manualTarget > 0 ? { min_profit: Math.abs(manualTarget) } : {}) })
      if (manualStop.trim() || manualTake.trim() || manualTrail.trim()) {
        const rule: any = { id: `manual-protect-${current.length + added.length + 1}`, origin: 'manual', symbol, action: 'modify' }
        if (manualStop.trim()) rule.stop_loss = Number(manualStop)
        if (manualTake.trim()) rule.take_profit = Number(manualTake)
        if (manualTrail.trim()) rule.trailing_stop = Number(manualTrail)
        if (!Number.isFinite(rule.stop_loss ?? 1) || !Number.isFinite(rule.take_profit ?? 1) || !Number.isFinite(rule.trailing_stop ?? 1)) throw Error('SL, TP y trailing deben ser numéricos.')
        added.push(rule)
      }
      if (!added.length) throw Error('Configure al menos una condición o nivel de protección.')
      setManagerRules(JSON.stringify([...current, ...added], null, 2))
    } catch (e) { setError(`No se pudo añadir la política: ${String(e)}`) }
  }

  async function post(path: string) {
    setBusy(true)
    try { const r = await fetch(API + path, { method: 'POST' }); const v = await r.json(); if (!r.ok) throw Error(v.detail || 'Error'); await load() }
    catch (e) { setError(String(e)) } finally { setBusy(false) }
  }

  async function startAnalysis() {
    if (!selected) { setError('Seleccione un símbolo exacto antes de iniciar el análisis.'); return }
    setBusy(true)
    try {
      const r = await fetch(`${API}/engine/start`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ symbol: selected, timeframe }) })
      const value = await r.json(); if (!r.ok) throw Error(value.detail || 'No se pudo iniciar el análisis')
      await load()
    } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }

  async function startAutoTrading() {
    setBusy(true); setError('')
    try {
      const r = await fetch(`${API}/trading-engine/start`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: autoMode, symbol: autoSymbol, timeframe: autoTimeframe, risk_per_trade: autoRisk,
          daily_loss_limit: autoDailyLoss, max_drawdown: autoDrawdown, max_positions: autoMaxPositions,
          max_spread_points: autoMaxSpread, confirm_demo: demoPhrase }) })
      const value = await r.json(); if (!r.ok) throw Error(value.detail || 'No se pudo iniciar el motor automático')
      setDemoPhrase(''); await load()
    } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }

  async function toggleAnalysisPause() {
    setBusy(true)
    try {
      const r = await fetch(`${API}/engine/pause`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ paused: !status?.paused }) })
      const value = await r.json(); if (!r.ok) throw Error(value.detail || 'No se pudo pausar o reanudar')
      await load()
    } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }

  async function refreshHistory(symbol = selected, tf = timeframe) {
    if (!symbol) { setHistory(null); return }
    const r = await fetch(`${API}/history/${encodeURIComponent(symbol)}/${tf}`)
    if (r.ok) setHistory(await r.json()); else setHistory(null)
  }

  async function detectSymbols() {
    setBusy(true); setError('')
    try {
      const r = await fetch(`${API}/history/symbols`); const value = await r.json()
      if (!r.ok) throw Error(value.detail || 'No se pudieron consultar los símbolos de MT5')
      setMatches(value.matches || []); setAvailableSymbols(value.available || [])
      if (!selected && value.matches?.length) setSelected(value.matches[0].symbol)
    } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }

  async function downloadHistory() {
    if (!selected) { setError('Elija un símbolo exacto de MT5.'); return }
    if (!start || !end) { setError('Seleccione fecha inicial y final para limitar la descarga.'); return }
    setBusy(true); setError(''); setJob({ status: 'queued', progress: { pages_done: 0, candles_received: 0 } })
    try {
      const r = await fetch(`${API}/history/download`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ symbol: selected, timeframe, start: start ? `${start}T00:00:00Z` : null, end: end ? `${end}T23:59:59Z` : null }) })
      const v = await r.json(); if (!r.ok) throw Error(v.detail || 'No se pudo iniciar la descarga')
      let done = false
      while (!done) {
        await new Promise(resolve => setTimeout(resolve, 1200))
        const jr = await fetch(`${API}/history/jobs/${v.job_id}`); const state = await jr.json(); setJob(state)
        if (state.status === 'completed') { done = true; await refreshHistory(); await load() }
        if (state.status === 'failed') { done = true; throw Error(state.error) }
      }
    } catch (e) { setError(String(e)); setJob((j: any) => ({ ...j, status: 'failed' })) }
    finally { setBusy(false) }
  }

  async function downloadRecentHistory() {
    if (!selected) { setError('Elija el símbolo exacto de MT5 antes de descargar.'); return }
    setBusy(true); setError(''); setJob({ status: 'queued', progress: { candles_received: 0 } })
    try {
      const r = await fetch(`${API}/history/download-count`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ symbol: selected, timeframe, count: candleCount }) })
      const value = await r.json(); if (!r.ok) throw Error(value.detail || 'No se pudo iniciar la descarga')
      let done = false
      while (!done) {
        await new Promise(resolve => setTimeout(resolve, 1000))
        const jr = await fetch(`${API}/history/jobs/${value.job_id}`); const state = await jr.json(); setJob(state)
        if (state.status === 'completed') { done = true; await refreshHistory(); await load() }
        if (state.status === 'failed') { done = true; throw Error(state.error || 'MT5 no pudo descargar velas') }
      }
    } catch (e) { setError(String(e)); setJob((j: any) => ({ ...j, status: 'failed' })) }
    finally { setBusy(false) }
  }

  async function downloadTicks() {
    if (!selected || !start || !end) { setError('Seleccione símbolo, fecha inicial y fecha final para ticks.'); return }
    setBusy(true); setError(''); setTickResult(null)
    try {
      const r = await fetch(`${API}/history/ticks/download`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ symbol: selected, start: `${start}T00:00:00Z`, end: `${end}T23:59:59Z` }) })
      const value = await r.json(); if (!r.ok) throw Error(value.detail || 'No se pudo iniciar la descarga de ticks')
      let done = false
      while (!done) {
        await new Promise(resolve => setTimeout(resolve, 1200))
        const jr = await fetch(`${API}/history/jobs/${value.job_id}`); const state = await jr.json()
        if (state.status === 'completed') { setTickResult(state.result); done = true }
        if (state.status === 'failed') throw Error(state.error || 'MT5 no pudo descargar ticks')
      }
    } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }

  async function importCsv(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file || !selected) return
    setBusy(true); setError('')
    try {
      const r = await fetch(`${API}/history/import/${encodeURIComponent(selected)}/${timeframe}`, { method: 'POST', headers: { 'Content-Type': 'text/csv' }, body: await file.text() })
      const v = await r.json(); if (!r.ok) throw Error(v.detail || 'No se pudo importar el CSV')
      await refreshHistory(); await load()
    } catch (e) { setError(String(e)) } finally { setBusy(false); event.target.value = '' }
  }

  async function requestVectorbt(symbol: string) {
    const r = await fetch(`${API}/backtests/vectorbt`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ symbol, timeframe, start: start || null, end: end || null, initial_balance: initialBalance, spread, commission, slippage }) })
    const value = await r.json(); if (!r.ok) throw Error(`${symbol}: ${value.detail || 'No se pudo iniciar el backtest VectorBT'}`)
    let done = false
    while (!done) {
      await new Promise(resolve => setTimeout(resolve, 1000))
      const jr = await fetch(`${API}/history/jobs/${value.job_id}`); const state = await jr.json()
      if (state.status === 'completed') { done = true; return state.result }
      if (state.status === 'failed') throw Error(state.error || `${symbol}: VectorBT no pudo ejecutar la simulación`)
    }
    throw Error(`${symbol}: el trabajo de VectorBT terminó sin resultado`)
  }

  async function runBacktests(symbols: string[]) {
    if (!symbols.length) return
    setBusy(true); setError(''); setVectorbtResult(null)
    try {
      const results: any[] = []
      for (const symbol of symbols) results.push(await requestVectorbt(symbol))
      setVectorbtResult(results[0])
      setBacktestRows(current => [...results, ...current.filter(x => !results.some(r => r.id === x.id))])
    } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }

  function toggleCompare(symbol: string) { setSelectedCompare(current => current.includes(symbol) ? current.filter(x => x !== symbol) : [...current, symbol]) }

  const activeBacktest = vectorbtResult || backtestRows[0] || null

  return <main>
    <header><div><small>TRADING SYSTEM / CONTROL</small><h1>AI Trading Engine</h1></div><span className="badge">Modo configurado {status?.mode || '…'} · Cuenta {status?.account_mode || 'UNKNOWN'} · Real bloqueado</span></header>
    {error && <p className="error">{error}</p>}
    <section className="cards">
      <article><small>MT5</small><h2 className={status?.mt5.connected ? 'green' : 'red'}>{status?.mt5.connected ? 'Conectado' : 'Desconectado'}</h2><p>{status?.mt5.error || 'Cuenta conectada'}</p><button onClick={() => post('/mt5/connect')} disabled={busy}>Conectar MT5</button> <button onClick={() => post('/mt5/disconnect')} disabled={busy}>Desconectar</button></article>
      <article><small>BALANCE / EQUITY</small><h2>{status?.mt5.connected ? `${status.mt5.balance} / ${status.mt5.equity}` : '—'}</h2><p>Solo datos recibidos del terminal</p></article>
      <article><small>RIESGO CONFIGURADO</small><h2>{status ? `${(status.risk.per_trade * 100).toFixed(2)}% / operación` : '—'}</h2><p>Pérdida diaria {status ? status.risk.daily_loss_limit * 100 : '—'}% · DD {status ? status.risk.max_drawdown * 100 : '—'}%</p></article>
      <article><small>MOTOR</small><h2>{status?.engine_running ? (status.paused ? 'Pausado' : 'Activo') : 'Detenido'}</h2><button onClick={startAnalysis} disabled={busy || !selected}>Iniciar análisis</button> <button onClick={toggleAnalysisPause} disabled={busy || !status?.engine_running}>{status?.paused ? 'Reanudar' : 'Pausar'}</button> <button onClick={() => post('/engine/stop')} disabled={busy || !status?.engine_running}>Detener</button><p>{status?.engine?.last_scan ? `Último análisis: ${new Date(status.engine.last_scan).toLocaleTimeString()} · ${status.engine.scan_count} ciclos` : 'Esperando análisis'}</p>{status?.engine?.last_error && <p className="error">{status.engine.last_error}</p>}</article>
    </section>

    <section className="panel auto-trading-panel">
      <div className="sectionhead"><div><h2>Motor automático · GOLDmicro</h2><span>Control independiente del análisis y del Position Manager. PAPER por defecto; nunca habilita REAL.</span></div><b className={status?.auto_trading?.enabled ? 'green' : 'muted'}>{status?.auto_trading?.enabled ? `ACTIVO · ${status.auto_trading.mode}` : 'DETENIDO'}</b></div>
      <div className="formgrid">
        <label>Modo<select value={autoMode} onChange={e => setAutoMode(e.target.value)}><option value="PAPER">PAPER · sin órdenes al bróker</option><option value="DEMO">DEMO · solo cuenta demo verificada</option></select></label>
        <label>Símbolo<input value={autoSymbol} onChange={e => setAutoSymbol(e.target.value)} list="broker-symbols" /></label>
        <label>Temporalidad<select value={autoTimeframe} onChange={e => setAutoTimeframe(e.target.value)}>{['M1','M5','M15','H1','H4'].map(tf => <option key={tf}>{tf}</option>)}</select></label>
        <label>Riesgo por operación (%)<input type="number" min="0.01" max="2" step="0.01" value={autoRisk * 100} onChange={e => setAutoRisk(Number(e.target.value) / 100)} /></label>
        <label>Pérdida diaria máxima (%)<input type="number" min="0.1" max="20" step="0.1" value={autoDailyLoss * 100} onChange={e => setAutoDailyLoss(Number(e.target.value) / 100)} /></label>
        <label>Drawdown máximo (%)<input type="number" min="0.1" max="50" step="0.1" value={autoDrawdown * 100} onChange={e => setAutoDrawdown(Number(e.target.value) / 100)} /></label>
        <label>Máximo de posiciones<input type="number" min="1" max="20" value={autoMaxPositions} onChange={e => setAutoMaxPositions(Math.max(1, Number(e.target.value) || 1))} /></label>
        <label>Spread máximo (points MT5)<input type="number" min="0.1" step="0.1" value={autoMaxSpread} onChange={e => setAutoMaxSpread(Number(e.target.value))} /></label>
      </div>
      {autoMode === 'DEMO' && <label>Confirmación requerida: escriba OPERAR EN DEMO<input value={demoPhrase} onChange={e => setDemoPhrase(e.target.value)} autoComplete="off" /></label>}
      <div className="actions"><button onClick={startAutoTrading} disabled={busy || !status?.mt5.connected || status?.auto_trading?.enabled}>Iniciar motor automático</button><button onClick={() => post('/trading-engine/stop')} disabled={busy || !status?.auto_trading?.enabled}>Detener nuevas entradas</button><span>El análisis de señales puede seguir activo; detener aquí nunca cierra posiciones.</span></div>
      <p className={status?.auto_trading?.blocked_reason?.includes('bloquead') || status?.auto_trading?.blocked_reason?.includes('rechaz') ? 'error' : 'muted'}>Estado / último motivo: {status?.auto_trading?.blocked_reason || 'Detenido'}{status?.auto_trading?.last_action ? ` · ${status?.auto_trading?.last_action}` : ''}</p>
      {status?.auto_trading?.paper_positions?.length > 0 && <div className="historyresult"><b>Propuestas PAPER sin ejecución real</b>{status?.auto_trading?.paper_positions?.map((p: any, i: number) => <span key={`${p.bar_time}-${i}`}>{p.direction} {p.symbol} · volumen {p.volume} · entrada {p.entry} · SL {p.stop_loss} · TP {p.take_profit}</span>)}</div>}
      <p className="muted">En DEMO cada entrada requiere datos MT5 recientes, spread y stops dentro de las reglas del bróker, sizing por tick_size/tick_value reales, límites de riesgo, margen disponible, order_check y confirmación posterior de positions_get. En REAL el envío está bloqueado en backend.</p>
    </section>

    <section className="panel history-panel">
      <div className="sectionhead"><div><h2>Datos históricos</h2><span>Descarga directa de MT5 o importación de CSV</span></div><span className={status?.mt5.connected ? 'green' : 'red'}>MT5 {status?.mt5.connected ? 'conectado' : 'desconectado'}</span></div>
      {!status?.mt5.connected && <p className="muted">Conecte MT5 para descubrir símbolos del bróker y descargar datos. También puede importar un CSV existente en el símbolo seleccionado.</p>}
      <div className="formgrid history-formgrid">
        <label>Símbolo exacto<input list="broker-symbols" value={selected} onChange={e => setSelected(e.target.value)} onBlur={e => void refreshHistory(e.target.value)} placeholder="p. ej. GOLDmicro" /><datalist id="broker-symbols">{matches.map((m, i) => <option key={`${m.symbol}-${i}`} value={m.symbol}>{m.instrument} · {m.variant}</option>)}</datalist></label>
        <label>Temporalidad<select value={timeframe} onChange={e => { setTimeframe(e.target.value); void refreshHistory(selected, e.target.value) }}>{TIMEFRAMES.map(tf => <option key={tf}>{tf}</option>)}</select></label>
        <label>Fecha inicial<input type="date" value={start} onChange={e => setStart(e.target.value)} /></label>
        <label>Fecha final<input type="date" value={end} onChange={e => setEnd(e.target.value)} /></label>
        <label>Velas recientes<input type="number" min={1} max={250000} step={1} value={candleCount} onChange={e => setCandleCount(Math.max(1, Math.min(250000, Number(e.target.value) || 1)))} /></label>
      </div>
      <div className="actions"><button onClick={detectSymbols} disabled={busy || !status?.mt5.connected}>Detectar instrumentos MT5</button><button onClick={downloadRecentHistory} disabled={busy || !status?.mt5.connected || !selected}>Descargar últimas {candleCount.toLocaleString()} velas</button><button onClick={downloadHistory} disabled={busy || !status?.mt5.connected || !selected || !start || !end}>Descargar por fechas</button><button onClick={downloadTicks} disabled={busy || !status?.mt5.connected || !selected || !start || !end}>Descargar ticks</button><label className="filebutton">Importar CSV existente<input type="file" accept=".csv,text/csv" onChange={importCsv} disabled={busy || !selected} /></label><button onClick={() => refreshHistory()} disabled={!selected}>Consultar CSV</button></div>
      {job && <div className="job"><b>Descarga: {job.status}</b>{job.status === 'running' && <><progress max={job.progress?.pages_total || 100} value={job.progress?.pages_done || 0} /><span>{job.progress?.candles_received || 0} velas recibidas · página {job.progress?.pages_done || 0} / {job.progress?.pages_total || '?'}</span></>}{job.result?.limitations?.map((x: string) => <p className="error" key={x}>{x}</p>)}</div>}
      {history && <div className="historyresult"><b>CSV validado: {history.symbol} {history.timeframe}</b><span>{history.count.toLocaleString()} velas</span><span>Cobertura: {new Date(history.first_time).toLocaleString()} — {new Date(history.last_time).toLocaleString()}</span><span>Proveedor: {history.metadata?.source || 'CSV local'}</span><span>Actualizado: {history.metadata?.downloaded_at ? new Date(history.metadata.downloaded_at).toLocaleString() : 'sin metadatos'}</span><span>{history.validation}</span></div>}
      {tickResult && <div className="historyresult"><b>Ticks MT5 · {tickResult.symbol}</b><span>{tickResult.count.toLocaleString()} ticks</span><span>{new Date(tickResult.first_time).toLocaleString()} — {new Date(tickResult.last_time).toLocaleString()}</span><span>{tickResult.csv_path}</span></div>}
      <div className="comparelist"><b>Comparar símbolos</b>{matches.length ? matches.map(m => <label key={`check-${m.symbol}`}><input type="checkbox" checked={selectedCompare.includes(m.symbol)} onChange={() => toggleCompare(m.symbol)} />{m.symbol}{m.micro ? ' (micro)' : ''}</label>) : <span className="muted">Los símbolos aparecerán al conectar MT5.</span>}</div>
      {availableSymbols.length > 0 && <details className="available-symbols"><summary>Símbolos disponibles en el bróker ({availableSymbols.length})</summary><p>{availableSymbols.join(' · ')}</p></details>}
    </section>

    <section className="panel live-candles-panel">
      <div className="sectionhead"><div><h2>Velas MT5 · {selected || 'Sin símbolo'} · {timeframe}</h2><span>Datos reales del terminal, velas cerradas · refresco cada 15 s</span></div><span className={candlesLoading ? 'muted' : liveCandles.length ? 'green' : 'red'}>{candlesLoading ? 'Cargando…' : `${liveCandles.length} velas válidas`}</span></div>
      {liveCandles.length > 0 ? <><CandlestickChart candles={liveCandles} /><div className="candle-meta"><span>Última vela recibida: {lastCandleReceived ? new Date(lastCandleReceived).toLocaleString() : '—'}</span><span>Fuente: MT5 · {selected} {timeframe}</span></div>{candleError && <p className="error">Actualización pendiente: {candleError}</p>}</> : candleError ? <p className="error">{candleError}</p> : <p className="muted">{!status?.mt5.connected ? 'Conecte MT5 para cargar velas.' : candlesLoading ? 'Solicitando velas cerradas al terminal…' : 'No hay datos para mostrar.'}</p>}
    </section>

    <section className="panel backtest-panel">
      <div className="sectionhead backtest-heading"><div><h2>Backtesting VectorBT fuera de muestra</h2><span>{selected || 'Sin símbolo'} · {timeframe} · selección en desarrollo, evaluación cronológica OOS</span></div><div className="actions"><button className="primary-action" onClick={() => runBacktests([selected])} disabled={busy || !selected}>{busy ? 'Ejecutando…' : 'Ejecutar backtest'}</button><button onClick={() => runBacktests(selectedCompare)} disabled={busy || !selectedCompare.length}>Comparar ({selectedCompare.length})</button></div></div>
      <div className="formgrid costs backtest-controls"><label>Balance inicial<input type="number" value={initialBalance} min={1} onChange={e => setInitialBalance(Number(e.target.value))} /></label><label>Spread total (precio)<input type="number" value={spread} min={0} step="any" onChange={e => setSpread(Number(e.target.value))} /></label><label>Comisión fija por orden<input type="number" value={commission} min={0} step="any" onChange={e => setCommission(Number(e.target.value))} /></label><label>Deslizamiento por lado<input type="number" value={slippage} min={0} step="any" onChange={e => setSlippage(Number(e.target.value))} /></label></div>
      {activeBacktest ? <>
        <div className="backtest-summary"><div className="backtest-metrics">
          <article><span>Retorno neto</span><strong>{Number(activeBacktest.net_profit).toFixed(2)}</strong><small>{Number(activeBacktest.return_pct).toFixed(2)}%</small></article>
          <article><span>Drawdown máximo</span><strong>{Number(activeBacktest.max_drawdown_pct).toFixed(2)}%</strong></article>
          <article><span>Operaciones</span><strong>{activeBacktest.trade_count}</strong></article>
          <article><span>Profit factor</span><strong>{activeBacktest.profit_factor == null ? 'N/D' : Number(activeBacktest.profit_factor).toFixed(2)}</strong></article>
          <article><span>Tasa de aciertos</span><strong>{Number(activeBacktest.win_rate_pct).toFixed(1)}%</strong></article>
        </div>
        <div className="equity-result compact-equity"><div><small>CURVA DE EQUITY · {activeBacktest.symbol} {activeBacktest.timeframe}</small><span>{activeBacktest.out_of_sample_start} → {activeBacktest.out_of_sample_end}</span></div><div className="chart compact-chart">{(activeBacktest.equity_curve || []).map((p: any, j: number) => <i key={j} style={{ height: `${Math.max(2, Math.min(100, 50 + (p.equity - activeBacktest.initial_balance) / activeBacktest.initial_balance * 500))}%` }} title={`${p.time}: ${p.equity}`} />)}</div></div>
        </div>
        <details className="backtest-details"><summary>Operaciones detalladas ({activeBacktest.trades?.length || 0})</summary><div className="table"><div className="row trade-row head"><span>Entrada</span><span>Salida</span><span>Unidades</span><span>Precios</span><span>Bruto / neto</span><span>Motivo salida</span></div>{(activeBacktest.trades || []).map((x: any, i: number) => <div className="row trade-row" key={i}><span>{x.entry_time}</span><span>{x.exit_time}</span><span>{Number(x.size_units).toFixed(4)}</span><span>{Number(x.entry_price).toFixed(3)} / {Number(x.exit_price).toFixed(3)}</span><span>{Number(x.gross_pnl).toFixed(2)} / {Number(x.net_pnl).toFixed(2)}</span><span>{x.exit_reason}</span></div>)}</div></details>
        <details className="backtest-details"><summary>Señales y motivos ({activeBacktest.signals?.length || 0})</summary><p>{activeBacktest.strategy_description} · {activeBacktest.train_start} → {activeBacktest.out_of_sample_start} entrenamiento; OOS hasta {activeBacktest.out_of_sample_end}.</p><div className="table"><div className="row signal-row head"><span>Señal</span><span>Ejecución</span><span>Tipo</span><span>SMA rápida / lenta</span><span>Motivo</span></div>{(activeBacktest.signals || []).map((x: any, i: number) => <div className="row signal-row" key={i}><span>{x.time}</span><span>{x.fill_time}</span><b>{x.decision}</b><span>{Number(x.sma_fast).toFixed(3)} / {Number(x.sma_slow).toFixed(3)}</span><span>{x.reason}</span></div>)}</div></details>
        <details className="backtest-details"><summary>Costes, supuestos y selección de estrategia</summary><p>Bruto {Number(activeBacktest.gross_profit || 0).toFixed(2)} · neto {Number(activeBacktest.net_profit).toFixed(2)} · costes {Number(activeBacktest.total_costs || 0).toFixed(2)} (comisión {Number(activeBacktest.commission_costs || 0).toFixed(2)} · spread {Number(activeBacktest.spread_costs || 0).toFixed(2)} · deslizamiento {Number(activeBacktest.slippage_costs || 0).toFixed(2)}).</p><p>{activeBacktest.execution}</p><p className="muted">Especificaciones MT5: {activeBacktest.contract_spec && Object.keys(activeBacktest.contract_spec).length ? JSON.stringify(activeBacktest.contract_spec) : 'no disponibles'}</p><div className="table"><div className="row head"><span>Combinación</span><span>Retorno entrenamiento</span><span>Seleccionada</span></div>{(activeBacktest.candidates || []).map((c: any) => <div className="row" key={c.strategy}><b>{c.strategy}</b><span>{Number(c.train_return_pct).toFixed(2)}%</span><span>{c.strategy === activeBacktest.selected_strategy ? 'sí' : 'no'}</span></div>)}</div></details>
      </> : <p className="muted backtest-empty">Todavía no hay resultados guardados. Descarga histórico y ejecuta el backtest para ver métricas reales.</p>}
      {backtestRows.length > 1 && <details className="backtest-details"><summary>Historial de ejecuciones ({backtestRows.length})</summary><div className="table"><div className="row btrow head"><span>Símbolo / TF</span><span>Beneficio neto</span><span>Profit factor</span><span>Acierto</span><span>Operaciones</span><span>Drawdown</span></div>{backtestRows.slice(1).map((x, i) => <div className="row btrow" key={`${x.id}-${i}`}><b>{x.symbol} / {x.timeframe}</b><span>{Number(x.net_profit).toFixed(2)}</span><span>{x.profit_factor == null ? 'N/D' : Number(x.profit_factor).toFixed(2)}</span><span>{Number(x.win_rate_pct).toFixed(1)}%</span><span>{x.trade_count}</span><span>{Number(x.max_drawdown_pct).toFixed(2)}%</span></div>)}</div></details>}
    </section>

    <section className="panel"><div className="sectionhead"><h2>Señales</h2><span>{status?.mt5.connected ? 'Velas cerradas consultadas desde MT5' : 'CSV local validado · reglas transparentes'}</span></div>{signals.length ? <div className="table"><div className="row head"><span>Símbolo / TF</span><span>Vela cerrada</span><span>Decisión</span><span>Entrada</span><span>SL / TP</span><span>Motivo</span></div>{signals.map((x, i) => <div className="row" key={i}><b>{x.symbol} / {x.timeframe}</b><span>{x.bar_time ? new Date(x.bar_time).toLocaleString() : '—'}</span><b>{x.decision}</b><span>{x.entry ?? '—'}</span><span>{x.stop_loss ?? '—'} / {x.take_profit ?? '—'}</span><span>{x.reason}</span></div>)}</div> : <p className="muted">No hay velas cerradas disponibles para evaluar.</p>}</section>
    <section className="panel position-manager">
      <div className="sectionhead"><div><h2>Position Manager</h2><span>Supervisión MT5 independiente del motor de señales · sondeo cada {positionManager?.interval_seconds || 2} s</span></div><span className={positionManager?.connected ? 'green' : 'red'}>{positionManager?.connected ? (positionManager.stale ? 'Datos obsoletos' : 'MT5 conectado') : 'MT5 desconectado'}</span></div>
      <div className="historyresult"><b>{positionManager?.running ? 'Supervisor activo' : 'Supervisor detenido'}</b><span>{positionManager?.position_count || 0} posiciones abiertas</span><span>Última consulta: {positionManager?.last_poll ? new Date(positionManager.last_poll).toLocaleTimeString() : 'sin datos MT5'}</span><span>Gestión automática: {positionManager?.enabled ? 'HABILITADA' : 'deshabilitada'}</span></div>
      {positionManager?.last_error && <p className="error">Error del supervisor: {positionManager.last_error}</p>}
      {positionManager?.alerts?.length ? <div className="error"><b>Protección incompleta</b>{positionManager.alerts.map((a: any) => <p key={a.ticket}>Ticket {a.ticket} · {a.symbol} ({a.origin}) · falta {a.missing_sl && a.missing_tp ? 'SL y TP' : a.missing_sl ? 'SL' : 'TP'}</p>)}</div> : <p className="muted">Sin alertas de SL/TP.</p>}
      {positions.length ? <div className="table position-table"><div className="row head"><span>Ticket / símbolo</span><span>Origen / Magic</span><span>Volumen</span><span>Precio apertura / actual</span><span>SL / TP</span><span>Beneficio / riesgo a SL</span></div>{positions.map((p: any) => <div className="row" key={p.ticket}><b>{p.ticket} · {p.symbol} · {p.type_name}</b><span>{p.origin} · {p.magic}</span><span>{p.volume}</span><span>{p.price_open} / {p.price_current}</span><span>{p.sl || 'SIN SL'} / {p.tp || 'SIN TP'}</span><span>{Number(p.profit).toFixed(2)} / {p.risk_to_sl == null ? 'N/D' : Number(p.risk_to_sl).toFixed(2)}</span></div>)}</div> : <p className="muted">{positionManager?.connected ? 'No hay posiciones abiertas.' : 'Posiciones no disponibles mientras MT5 esté desconectado.'}</p>}
      <details><summary>Reglas de gestión (requieren habilitación independiente)</summary><p className="muted">Las políticas se aplican solo después de marcar “Habilitar” y guardar. La pérdida máxima y el objetivo crean una política de cierre; SL/TP/trailing crean otra política de protección manual. Un SL solo puede apretarse y cada acción valida de nuevo ticket, símbolo, volumen, dirección, precio, margen de stops y riesgo máximo.</p><div className="formgrid"><label>Símbolo (vacío = todos)<input list="broker-symbols" value={manualSymbol} onChange={e => setManualSymbol(e.target.value)} /></label><label>Pérdida máxima monetaria<input type="number" min="0" value={manualMaxLoss} onChange={e => setManualMaxLoss(Math.max(0, Number(e.target.value)))} /></label><label>Objetivo de beneficio monetario<input type="number" min="0" value={manualTarget} onChange={e => setManualTarget(Math.max(0, Number(e.target.value)))} /></label><label>Stop Loss absoluto opcional<input type="number" step="any" value={manualStop} onChange={e => setManualStop(e.target.value)} /></label><label>Take Profit absoluto opcional<input type="number" step="any" value={manualTake} onChange={e => setManualTake(e.target.value)} /></label><label>Distancia de trailing opcional (precio)<input type="number" min="0" step="any" value={manualTrail} onChange={e => setManualTrail(e.target.value)} /></label></div><div className="actions"><button onClick={addManualPolicy} disabled={busy}>Añadir políticas manuales</button><button onClick={addPositionRule} disabled={busy}>Añadir regla mantener</button><label><input type="checkbox" checked={managerEnabled} onChange={e => setManagerEnabled(e.target.checked)} /> Habilitar acciones automáticas configuradas</label></div><textarea aria-label="Reglas de gestión de posiciones" rows={7} value={managerRules} onChange={e => setManagerRules(e.target.value)} spellCheck={false} /><label>Para habilitar acciones en cuenta REAL, escriba HABILITAR GESTION REAL<input value={realManagementPhrase} onChange={e => setRealManagementPhrase(e.target.value)} autoComplete="off" /></label><button onClick={savePositionManagerConfig} disabled={busy}>Guardar configuración del Position Manager</button></details>
      <p className="muted">Clasificación: Magic 0 = manual; Magic distinto de 0 = automática (EA/bot), sin atribuirla a este motor. La gestión se desarma al desconectar o reiniciar el backend.</p>
    </section>
    <section className="panel event-panel"><details><summary>Eventos y registros ({events.length})</summary>{events.length ? events.slice(0, 20).map(x => <p className="event" key={x.id}><b>{x.kind}</b> · {x.created_at} · {JSON.stringify(x.payload)}</p>) : <p className="muted">Sin eventos guardados.</p>}</details></section>
    <footer>Sin IA entrenada. PAPER no envía órdenes; DEMO solo con habilitación explícita. REAL está bloqueado. Los resultados históricos no garantizan resultados futuros.</footer>
  </main>
}

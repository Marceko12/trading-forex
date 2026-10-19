import { ChangeEvent, useEffect, useState } from 'react'

const API = 'http://127.0.0.1:8000/api'
const TIMEFRAMES = ['M1', 'M5', 'M15', 'H1', 'H4']
type SymbolMatch = { symbol: string; instrument: string; micro: boolean; variant: string }
type Status = { mode: string; engine_running: boolean; paused: boolean; mt5: { connected: boolean; error?: string; balance?: number; equity?: number }; symbols: string[]; risk: { per_trade: number; daily_loss_limit: number; max_drawdown: number }; execution_enabled: boolean }
type HistoryInfo = { symbol: string; timeframe: string; count: number; first_time: string; last_time: string; validation: string }

export default function App() {
  const [status, setStatus] = useState<Status | null>(null)
  const [signals, setSignals] = useState<any[]>([])
  const [positions, setPositions] = useState<any[]>([])
  const [events, setEvents] = useState<any[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [matches, setMatches] = useState<SymbolMatch[]>([])
  const [availableSymbols, setAvailableSymbols] = useState<string[]>([])
  const [selected, setSelected] = useState('')
  const [selectedCompare, setSelectedCompare] = useState<string[]>([])
  const [timeframe, setTimeframe] = useState('H1')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [job, setJob] = useState<any>(null)
  const [history, setHistory] = useState<HistoryInfo | null>(null)
  const [backtestRows, setBacktestRows] = useState<any[]>([])
  const [initialBalance, setInitialBalance] = useState(10000)
  const [risk, setRisk] = useState(0.005)
  const [spread, setSpread] = useState(0)
  const [commission, setCommission] = useState(0)
  const [slippage, setSlippage] = useState(0)

  async function load() {
    try {
      const [a, b, c, d] = await Promise.all([fetch(`${API}/status`), fetch(`${API}/signals`), fetch(`${API}/positions`), fetch(`${API}/events`)])
      const s = await a.json(); setStatus(s)
      setSignals((await b.json()).signals || []); setPositions((await c.json()).positions || []); setEvents((await d.json()).events || [])
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

  async function post(path: string) {
    setBusy(true)
    try { const r = await fetch(API + path, { method: 'POST' }); const v = await r.json(); if (!r.ok) throw Error(v.detail || 'Error'); await load() }
    catch (e) { setError(String(e)) } finally { setBusy(false) }
  }

  async function refreshHistory(symbol = selected, tf = timeframe) {
    if (!symbol) { setHistory(null); return }
    const r = await fetch(`${API}/history/${encodeURIComponent(symbol)}/${tf}`)
    if (r.ok) setHistory(await r.json()); else setHistory(null)
  }

  async function downloadHistory() {
    if (!selected) { setError('Elija un símbolo exacto de MT5.'); return }
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

  async function runBacktests(symbols: string[]) {
    setBusy(true); setError('')
    try {
      const results: any[] = []
      for (const symbol of symbols) {
        const r = await fetch(`${API}/backtests`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ symbol, timeframe, start: start || null, end: end || null, initial_balance: initialBalance, risk_per_trade: risk, spread, commission, slippage }) })
        const value = await r.json(); if (!r.ok) throw Error(`${symbol}: ${value.detail || 'Error de backtest'}`)
        results.push(value)
      }
      setBacktestRows(current => [...results, ...current])
    } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }

  function toggleCompare(symbol: string) { setSelectedCompare(current => current.includes(symbol) ? current.filter(x => x !== symbol) : [...current, symbol]) }

  return <main>
    <header><div><small>TRADING SYSTEM / CONTROL</small><h1>AI Trading Engine</h1></div><span className="badge">{status?.mode || '…'} · ORDENES DESACTIVADAS</span></header>
    {error && <p className="error">{error}</p>}
    <section className="cards">
      <article><small>MT5</small><h2 className={status?.mt5.connected ? 'green' : 'red'}>{status?.mt5.connected ? 'Conectado' : 'Desconectado'}</h2><p>{status?.mt5.error || 'Cuenta conectada'}</p><button onClick={() => post('/mt5/connect')} disabled={busy}>Conectar MT5</button> <button onClick={() => post('/mt5/disconnect')} disabled={busy}>Desconectar</button></article>
      <article><small>BALANCE / EQUITY</small><h2>{status?.mt5.connected ? `${status.mt5.balance} / ${status.mt5.equity}` : '—'}</h2><p>Solo datos recibidos del terminal</p></article>
      <article><small>RIESGO CONFIGURADO</small><h2>{status ? `${(status.risk.per_trade * 100).toFixed(2)}% / operación` : '—'}</h2><p>Pérdida diaria {status ? status.risk.daily_loss_limit * 100 : '—'}% · DD {status ? status.risk.max_drawdown * 100 : '—'}%</p></article>
      <article><small>MOTOR</small><h2>{status?.engine_running ? (status.paused ? 'Pausado' : 'Activo') : 'Detenido'}</h2><button onClick={() => post('/engine/start')} disabled={busy}>Iniciar análisis</button> <button onClick={() => post('/engine/pause')} disabled={busy}>Pausar / reanudar</button> <button onClick={() => post('/engine/stop')} disabled={busy}>Detener</button></article>
    </section>

    <section className="panel history-panel">
      <div className="sectionhead"><div><h2>Datos históricos</h2><span>Descarga directa de MT5 o importación de CSV</span></div><span className={status?.mt5.connected ? 'green' : 'red'}>MT5 {status?.mt5.connected ? 'conectado' : 'desconectado'}</span></div>
      {!status?.mt5.connected && <p className="muted">Conecte MT5 para descubrir símbolos del bróker y descargar datos. También puede importar un CSV existente en el símbolo seleccionado.</p>}
      <div className="formgrid">
        <label>Símbolo exacto<input list="broker-symbols" value={selected} onChange={e => setSelected(e.target.value)} onBlur={e => void refreshHistory(e.target.value)} placeholder="p. ej. GOLDmicro" /><datalist id="broker-symbols">{matches.map((m, i) => <option key={`${m.symbol}-${i}`} value={m.symbol}>{m.instrument} · {m.variant}</option>)}</datalist></label>
        <label>Temporalidad<select value={timeframe} onChange={e => { setTimeframe(e.target.value); void refreshHistory(selected, e.target.value) }}>{TIMEFRAMES.map(tf => <option key={tf}>{tf}</option>)}</select></label>
        <label>Fecha inicial<input type="date" value={start} onChange={e => setStart(e.target.value)} /></label>
        <label>Fecha final<input type="date" value={end} onChange={e => setEnd(e.target.value)} /></label>
      </div>
      <div className="actions"><button onClick={downloadHistory} disabled={busy || !status?.mt5.connected || !selected}>Descargar histórico</button><label className="filebutton">Importar CSV existente<input type="file" accept=".csv,text/csv" onChange={importCsv} disabled={busy || !selected} /></label><button onClick={() => refreshHistory()} disabled={!selected}>Consultar CSV</button></div>
      {job && <div className="job"><b>Descarga: {job.status}</b>{job.status === 'running' && <><progress max={job.progress?.pages_total || 100} value={job.progress?.pages_done || 0} /><span>{job.progress?.candles_received || 0} velas recibidas · página {job.progress?.pages_done || 0} / {job.progress?.pages_total || '?'}</span></>}{job.result?.limitations?.map((x: string) => <p className="error" key={x}>{x}</p>)}</div>}
      {history && <div className="historyresult"><b>CSV validado: {history.symbol} {history.timeframe}</b><span>{history.count.toLocaleString()} velas</span><span>{new Date(history.first_time).toLocaleString()} — {new Date(history.last_time).toLocaleString()}</span><span>{history.validation}</span></div>}
      <div className="comparelist"><b>Comparar símbolos</b>{matches.length ? matches.map(m => <label key={`check-${m.symbol}`}><input type="checkbox" checked={selectedCompare.includes(m.symbol)} onChange={() => toggleCompare(m.symbol)} />{m.symbol}{m.micro ? ' (micro)' : ''}</label>) : <span className="muted">Los símbolos aparecerán al conectar MT5.</span>}</div>
      {availableSymbols.length > 0 && <details className="available-symbols"><summary>Símbolos disponibles en el bróker ({availableSymbols.length})</summary><p>{availableSymbols.join(' · ')}</p></details>}
    </section>

    <section className="panel">
      <div className="sectionhead"><div><h2>Backtesting fuera de muestra</h2><span>Lee el CSV guardado para el símbolo y temporalidad seleccionados</span></div><div><button onClick={() => runBacktests([selected])} disabled={busy || !selected}>Ejecutar backtest</button><button onClick={() => runBacktests(selectedCompare)} disabled={busy || !selectedCompare.length}>Comparar seleccionados ({selectedCompare.length})</button></div></div>
      <div className="formgrid costs"><label>Balance inicial<input type="number" value={initialBalance} min={1} onChange={e => setInitialBalance(Number(e.target.value))} /></label><label>Riesgo por operación<input type="number" value={risk} min={0.0001} max={0.02} step={0.001} onChange={e => setRisk(Number(e.target.value))} /></label><label>Spread (precio)<input type="number" value={spread} min={0} step="any" onChange={e => setSpread(Number(e.target.value))} /></label><label>Comisión por lote/lado<input type="number" value={commission} min={0} step="any" onChange={e => setCommission(Number(e.target.value))} /></label><label>Deslizamiento (precio)<input type="number" value={slippage} min={0} step="any" onChange={e => setSlippage(Number(e.target.value))} /></label></div>
      {backtestRows.length ? <><div className="table"><div className="row btrow head"><span>Símbolo / TF</span><span>Beneficio neto</span><span>Profit factor</span><span>Acierto</span><span>Operaciones</span><span>Drawdown</span></div>{backtestRows.map((x, i) => <div className="row btrow" key={`${x.id}-${i}`}><b>{x.symbol} / {x.timeframe}</b><span>{Number(x.net_profit).toFixed(2)}</span><span>{x.profit_factor == null ? 'N/D' : Number(x.profit_factor).toFixed(2)}</span><span>{Number(x.win_rate_pct).toFixed(1)}%</span><span>{x.trade_count}</span><span>{Number(x.max_drawdown_pct).toFixed(2)}%</span></div>)}</div>{backtestRows.slice(0, 4).map((x, i) => <div key={`equity-${x.id}-${i}`} className="equity-result"><small>CURVA DE EQUITY · {x.symbol} {x.timeframe}</small><div className="chart">{x.equity_curve.map((p: any, j: number) => <i key={j} style={{ height: `${Math.max(2, Math.min(100, 50 + (p.equity - x.initial_balance) / x.initial_balance * 500))}%` }} title={`${p.time}: ${p.equity}`} />)}</div></div>)}</> : <p className="muted">Sin resultados. Descargue o importe histórico para habilitar el backtest.</p>}
    </section>

    <section className="panel"><div className="sectionhead"><h2>Señales</h2><span>Datos CSV local · reglas transparentes</span></div>{signals.length ? <div className="table"><div className="row head"><span>Símbolo</span><span>Decisión</span><span>Entrada</span><span>SL / TP</span><span>Motivo</span></div>{signals.map((x, i) => <div className="row" key={i}><b>{x.symbol}</b><b>{x.decision}</b><span>{x.entry ?? '—'}</span><span>{x.stop_loss ?? '—'} / {x.take_profit ?? '—'}</span><span>{x.reason}</span></div>)}</div> : <p className="muted">No hay señales con los CSV locales.</p>}</section>
    <section className="panel"><h2>Posiciones MT5</h2>{positions.length ? <pre>{JSON.stringify(positions, null, 2)}</pre> : <p className="muted">{status?.mt5.connected ? 'No hay posiciones abiertas.' : 'Desconectado; posiciones no disponibles.'}</p>}</section>
    <section className="panel"><h2>Eventos y errores</h2>{events.length ? events.slice(0, 20).map(x => <p className="event" key={x.id}><b>{x.kind}</b> · {x.created_at} · {JSON.stringify(x.payload)}</p>) : <p className="muted">Sin eventos guardados.</p>}</section>
    <footer>Sin IA entrenada. Sin envío de órdenes. Los resultados históricos no garantizan resultados futuros.</footer>
  </main>
}

type Candle = { time: string; open: number; high: number; low: number; close: number; tick_volume: number; spread: number }

export default function CandlestickChart({ candles }: { candles: Candle[] }) {
  if (!candles.length) return null
  const width = 1000, height = 300, pad = 12
  const min = Math.min(...candles.map(c => c.low)), max = Math.max(...candles.map(c => c.high))
  const span = max - min || 1
  const y = (price: number) => height - pad - ((price - min) / span) * (height - pad * 2)
  const step = (width - pad * 2) / candles.length
  const bodyWidth = Math.max(1, Math.min(8, step * 0.68))
  return <div className="candle-chart-wrap">
    <svg className="candle-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${candles.length} velas japonesas`} preserveAspectRatio="none">
      {[0.25, 0.5, 0.75].map(p => <line key={p} className="candle-grid" x1={pad} x2={width - pad} y1={height * p} y2={height * p} />)}
      {candles.map((c, i) => {
        const x = pad + step * i + step / 2, top = y(Math.max(c.open, c.close)), bottom = y(Math.min(c.open, c.close))
        const rising = c.close >= c.open
        return <g key={`${c.time}-${i}`} className={rising ? 'candle-up' : 'candle-down'}>
          <line className="candle-wick" x1={x} x2={x} y1={y(c.high)} y2={y(c.low)} />
          <rect className="candle-body" x={x - bodyWidth / 2} y={top} width={bodyWidth} height={Math.max(1, bottom - top)}><title>{`${new Date(c.time).toLocaleString()} · O ${c.open} H ${c.high} L ${c.low} C ${c.close}`}</title></rect>
        </g>
      })}
    </svg>
  </div>
}

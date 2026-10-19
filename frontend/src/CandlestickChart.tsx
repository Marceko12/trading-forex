import { PointerEvent, WheelEvent, useRef, useState } from 'react'

type Candle = { time: string; open: number; high: number; low: number; close: number; tick_volume: number; spread: number }
const WIDTH = 1100, HEIGHT = 370, LEFT = 12, RIGHT = 78, TOP = 12, BOTTOM = 34

export default function CandlestickChart({ candles }: { candles: Candle[] }) {
  const [visibleCount, setVisibleCount] = useState(100)
  const [panBack, setPanBack] = useState(0)
  const drag = useRef<{ x: number; pan: number } | null>(null)
  if (!candles.length) return null

  const count = Math.min(candles.length, visibleCount)
  const end = Math.max(count, candles.length - panBack)
  const start = Math.max(0, end - count)
  const visible = candles.slice(start, end)
  const lows = visible.map(c => c.low), highs = visible.map(c => c.high)
  const min = Math.min(...lows), max = Math.max(...highs), range = max - min || Math.max(Math.abs(max) * .0001, 1)
  const plotRight = WIDTH - RIGHT, plotBottom = HEIGHT - BOTTOM, plotWidth = plotRight - LEFT, plotHeight = plotBottom - TOP
  const y = (price: number) => plotBottom - ((price - min) / range) * plotHeight
  const step = plotWidth / Math.max(visible.length, 1), bodyWidth = Math.max(1, Math.min(10, step * .66))
  const precision = max >= 100 ? 2 : max >= 1 ? 4 : 5

  function zoom(next: number) {
    setVisibleCount(Math.round(Math.max(20, Math.min(250, next))))
    setPanBack(current => Math.min(current, Math.max(0, candles.length - 20)))
  }
  function onWheel(event: WheelEvent<SVGSVGElement>) {
    event.preventDefault()
    zoom(Math.round(visibleCount * (event.deltaY < 0 ? .82 : 1.22)))
  }
  function onPointerDown(event: PointerEvent<SVGSVGElement>) {
    drag.current = { x: event.clientX, pan: panBack }
    event.currentTarget.setPointerCapture(event.pointerId)
  }
  function onPointerMove(event: PointerEvent<SVGSVGElement>) {
    if (!drag.current) return
    const bounds = event.currentTarget.getBoundingClientRect()
    const barsMoved = (event.clientX - drag.current.x) / Math.max(bounds.width, 1) * count
    setPanBack(Math.max(0, Math.min(candles.length - count, Math.round(drag.current.pan - barsMoved))))
  }
  function onPointerUp() { drag.current = null }

  return <div className="candlestick-terminal">
    <div className="chart-tools"><span>{visible.length} de {candles.length} velas</span><div><button aria-label="Alejar gráfico" onClick={() => zoom(visibleCount * 1.25)}>−</button><button aria-label="Acercar gráfico" onClick={() => zoom(visibleCount * .8)}>＋</button><button onClick={() => { setPanBack(0); setVisibleCount(100) }}>Últimas velas</button></div><small>Rueda: zoom · arrastrar: desplazar</small></div>
    <div className="candle-chart-wrap">
      <svg className="candle-chart" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={`${visible.length} velas japonesas de ${candles.length} velas MT5`} preserveAspectRatio="none"
        onWheel={onWheel} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}>
        {[0, 1, 2, 3, 4].map(index => {
          const fraction = index / 4, yPos = TOP + plotHeight * fraction, price = max - range * fraction
          return <g key={index}><line className="candle-grid" x1={LEFT} x2={plotRight} y1={yPos} y2={yPos} /><text className="candle-axis-label" x={plotRight + 9} y={yPos + 4}>{price.toFixed(precision)}</text></g>
        })}
        {visible.map((c, i) => {
          const x = LEFT + step * i + step / 2, top = y(Math.max(c.open, c.close)), bottom = y(Math.min(c.open, c.close)), rising = c.close >= c.open
          return <g key={`${c.time}-${i}`} className={rising ? 'candle-up' : 'candle-down'}>
            <line className="candle-wick" x1={x} x2={x} y1={y(c.high)} y2={y(c.low)} />
            <rect className="candle-body" x={x - bodyWidth / 2} y={top} width={bodyWidth} height={Math.max(1, bottom - top)}><title>{`${new Date(c.time).toLocaleString()} · O ${c.open} H ${c.high} L ${c.low} C ${c.close} · Vol ${c.tick_volume} · spread ${c.spread}`}</title></rect>
          </g>
        })}
        {[0, .25, .5, .75, 1].map((fraction, i) => {
          const index = Math.min(visible.length - 1, Math.round(fraction * (visible.length - 1))), candle = visible[index]
          if (!candle) return null
          return <text key={i} className="candle-time-label" x={LEFT + step * index + step / 2} y={HEIGHT - 9} textAnchor="middle">{new Date(candle.time).toLocaleString(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' })}</text>
        })}
      </svg>
    </div>
  </div>
}

# AI Trading Engine

Base inicial segura para análisis de mercado y backtesting. El modo predeterminado es `BACKTEST`; no envía órdenes a MT5 y no afirma que haya IA entrenada. La estrategia implementada es una regla técnica determinista, con razones visibles. No se garantizan beneficios.

## Requisitos

- Windows 10/11, Python 3.12 (verificado localmente: 3.12.7), Node.js y npm (verificados: 24.15.0 y 11.12.0), Git.
- MetaTrader 5 para las funciones de conexión y lectura. La integración usa el paquete oficial Python `MetaTrader5`; requiere el terminal instalado y abierto en Windows.

## Instalar y ejecutar en PowerShell

Desde la raíz del repositorio:

```powershell
.scriptsinstall.ps1
.scriptsstart-backend.ps1
```

En otra consola:

```powershell
.scriptsstart-frontend.ps1
```

Abre `http://127.0.0.1:5173`. API: `http://127.0.0.1:8000`; documentación interactiva: `http://127.0.0.1:8000/docs`.

Instalación manual:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
Set-Location frontend; npm install; Set-Location ..
```

## Configuración

Copie `.env.example` a `.env` y ajuste símbolos y límites. Valores iniciales: riesgo 0,5% por operación, pérdida diaria 2%, drawdown 5%. La API bloquea fracciones por operación mayores a 2% en la solicitud de backtest. REAL no está disponible. No configure secretos en archivos versionados.

## Mercado y estrategia

Sin MT5 no se inventan cotizaciones. Para señales y backtesting independientes, guarde `data/XAUUSD.csv` (o el símbolo configurado) con columnas `time,open,high,low,close`, timestamps UTC y velas limpias. Los CSV están excluidos de Git. El validador descarta filas incompletas, ordena y elimina duplicados, y rechaza OHLC inválido.

La estrategia usa EMA 12/26, RSI 14, MACD, ATR y ADX. Es una estrategia convencional reproducible, no machine learning. En mercado lateral (ADX < 18) se abstiene. Para entrenar un modelo real se necesitaría un dataset versionado, separación temporal train/validation/test, control de leakage y evaluación fuera de muestra; ese proceso no se incluye.

## MT5

Instale MT5 en Windows, inicie sesión manualmente en una cuenta DEMO y mantenga el terminal abierto. En el panel pulse **Conectar MT5**; también puede llamar `POST /api/mt5/connect`. Compruebe `GET /api/status`. La conexión permite estado de cuenta, cotizaciones, símbolos y lectura de posiciones. Una conexión no significa que el sistema ejecute trades. El envío de órdenes, SL/TP dinámicos, break-even y trailing están deshabilitados.

## Backtesting

Con suficientes velas (mínimo 60 para calentamiento/entrenamiento y 20 fuera de muestra): pulse **Ejecutar backtest XAUUSD**, o:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/backtests -ContentType 'application/json' -Body '{"symbol":"XAUUSD","initial_balance":10000,"risk_per_trade":0.005,"spread":0.2,"commission":0,"slippage":0.05,"train_fraction":0.7}'
```

El backtest ejecuta las decisiones calculadas con la vela anterior en la apertura siguiente; para una misma vela que toque SL y TP, cuenta SL primero. Spread, slippage y comisión son parámetros en unidades genéricas de precio/coste; ajuste las unidades al dataset y contrato antes de interpretar. Riesgo/tamaño se calcula en unidades de precio OHLC, no convierte automáticamente tick value o moneda de cuenta como haría MT5. El Sharpe mostrado usa cambios de equity por barra anualizados con `sqrt(252)`, una hipótesis de periodicidad que no representa necesariamente retornos diarios. La prueba fuera de muestra es la cola temporal posterior al segmento de entrenamiento; no se hace optimización de parámetros.

## Pruebas

```powershell
python -m pytest backend/tests -q
```

Las pruebas son independientes de MT5. No se ejecutan tests de integración hasta disponer de terminal y cuenta adecuados.

## GitHub

El repositorio local debe revisarse antes de publicar. En esta sesión `gh` no está instalado y no hay autenticación verificada. Instale GitHub CLI, ejecute `gh auth login`, y cree un remoto privado después de revisar el contenido sensible. Luego:

```powershell
git status --short
git remote add origin https://github.com/<USUARIO>/ai-trading-engine.git
git push -u origin master
```

No se creó ni se publicó un repositorio remoto. Antes del push inspeccione los archivos que va a incluir; `.env`, bases de datos, logs, CSV e informes están ignorados.

## Limitaciones actuales

- Sin ejecución de órdenes, modo REAL, modificación de posiciones, conciliación de órdenes pendientes ni motor persistente de trading.
- El botón de iniciar controla el estado visual del análisis, pero no inicia un proceso de escaneo periódico.
- Sin histórico de mercado integrado: señales y backtest necesitan CSV local; MT5 se usa para conectividad, cotización y posiciones.
- La persistencia guarda eventos y backtests en SQLite local; las posiciones se leen directamente de MT5.
- Curva de equity presentada desde datos reales de backtest; no hay cifras de demo.

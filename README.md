# AI Trading Engine

Base inicial segura para análisis de mercado y backtesting. El modo predeterminado es `BACKTEST`; no envía órdenes a MT5 y no afirma que haya IA entrenada. La estrategia implementada es una regla técnica determinista, con razones visibles. No se garantizan beneficios.

## Requisitos

- Windows 10/11, Python 3.12 (verificado localmente: 3.12.7), Node.js y npm (verificados: 24.15.0 y 11.12.0), Git.
- MetaTrader 5 para las funciones de conexión y lectura. La integración usa el paquete oficial Python `MetaTrader5`; requiere el terminal instalado y abierto en Windows.

## Instalar y ejecutar en PowerShell

Desde la raíz del repositorio:

```powershell
& .\scripts\install.ps1
& .\scripts\start-backend.ps1
```

En otra consola:

```powershell
& .\scripts\start-frontend.ps1
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

## Datos históricos desde MT5 y comparación

En Windows abra el terminal MetaTrader 5, inicie sesión y pulse **Conectar MT5**. En **Datos históricos**, seleccione el símbolo exacto descubierto en el bróker (las variantes `micro` se mantienen como instrumentos distintos), la temporalidad M1/M5/M15/H1/H4 y opcionalmente el rango de fechas. Si deja ambas fechas vacías, se solicita desde 1970 hasta ahora, paginando según la temporalidad; la cantidad final depende del historial que entregue el bróker y del límite de barras del terminal. La interfaz informa fechas reales cubiertas, velas y limitaciones.

Los datos se guardan como `data/<SIMBOLO>.csv` para compatibilidad con el panel actual y, para conservar varias temporalidades, como `data/mt5/<SIMBOLO>/<TF>.csv`, junto a metadatos JSON del contrato y de la descarga. Las descargas siguientes hacen merge por timestamp. El backtest usa el archivo de la temporalidad seleccionada. Si desconecta MT5, puede elegir/escribir el símbolo y usar **Importar CSV existente**; las columnas de OHLC y `time` son obligatorias, y las columnas de volumen se normalizan a cero cuando faltan.

El backtest aprovecha `trade_tick_size`, `trade_tick_value` y los límites/pasos de volumen de MT5 cuando la descarga los proporciona. El tamaño se redondea hacia abajo al paso permitido; si el volumen calculado cae bajo el mínimo, no abre esa operación simulada. En CSV importados sin metadatos de contrato se conserva el cálculo genérico por unidades de precio. La comisión de la interfaz se interpreta por lote y por lado cuando hay especificación de contrato.

La API también expone `GET /api/history/symbols`, `POST /api/history/download`, `GET /api/history/jobs/<job_id>`, `GET /api/history/<simbolo>/<tf>` y `POST /api/history/import/<simbolo>/<tf>` (CSV en el cuerpo raw de la petición).

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

## Motor de análisis y señales

El botón **Iniciar análisis** arranca un hilo de análisis periódico (intervalo de 5 segundos) para el símbolo y temporalidad seleccionados. El panel muestra los ciclos completados, el último error y permite pausar, reanudar y detener el trabajador. El proceso reinicia detenido con el backend; no afirma continuar un ciclo anterior. Con MT5 conectado usa barras cerradas vía `copy_rates_from_pos` y la cotización actual. Sin MT5 lee el CSV histórico seleccionado. Las señales documentan la vela, el origen, el spread y los parámetros; los eventos se guardan una sola vez por vela. La estrategia es EMA/RSI/MACD/ADX determinista, no IA entrenada.

`GET /api/mt5/account` informa cuenta, moneda, margen, permisos y estado del terminal únicamente cuando MT5 responde. Las posiciones y ticks también son consultas directas al terminal. Si falta el archivo para un backtest, la API intenta descargar un año acotado desde MT5; si falla, devuelve el error recibido y mantiene la necesidad de historial suficiente del bróker.

## Seguridad de ejecución

El modo inicial es `BACKTEST` y el indicador `execution_enabled` permanece apagado. La API no contiene una ruta de envío de órdenes habilitada en esta versión; las funciones del adaptador de MT5 no son llamadas por el motor de análisis. No se puede activar ejecución DEMO/PAPER desde el panel todavía. Esto conserva la instrucción de no enviar órdenes al bróker; el control de volumen, margen, deduplicación y límites de pérdidas deberá implementarse y probarse en una fase separada antes de habilitar cualquier flujo manual. REAL no está disponible.

La persistencia guarda eventos y backtests en SQLite local; posiciones e información de cuenta se leen del terminal cuando está conectado. Las pruebas unitarias usan datos controlados solo para verificar lógica, nunca se presentan como backtests reales.

## Position Manager MT5

El Position Manager corre en un hilo separado del motor de señales y consulta `positions_get()` cada 2 segundos mientras MT5 está conectado. Detecta posiciones nuevas, cierres y cambios de volumen, SL, TP, precio, Magic y beneficio; registra los cambios en Eventos. Magic 0 se clasifica como manual y Magic distinto de 0 como automática (EA/bot). Adjunta las especificaciones actuales del símbolo y estima el riesgo monetario al SL solo cuando MT5 entrega tick size/value válidos. Avisa cuando falta SL o TP. La tabla refresca cada 2 segundos aunque el motor de señales esté detenido o pausado; al desconectarse muestra el último estado como obsoleto, nunca como dato actual.

Por defecto la gestión automática está deshabilitada y no hay reglas. Para configurar reglas use `GET /api/position-manager` y `PUT /api/position-manager/config`. Una regla usa `action` (`maintain`, `close` o `modify`), `origin` (`any`, `manual`, `automated`), `symbol` opcional y una condición `max_loss` negativo y/o `min_profit` no negativo. `modify` requiere `stop_loss` o `take_profit` como precio absoluto. Ejemplo de observación y cierre condicionado:

```json
{
  "enabled": false,
  "rules": [
    {"id":"proteger-eurusd","symbol":"EURUSD","origin":"any","action":"close","max_loss":-50}
  ],
  "confirm_real": ""
}
```

El endpoint solo arma reglas al recibir explícitamente `enabled: true`. Comprueba que MT5 y la cuenta autoricen trading y que la cuenta esté identificada como DEMO o REAL. Las acciones REAL además están bloqueadas por configuración (`POSITION_MANAGER_ALLOW_REAL=false`); solo después de validar con DEMO se puede cambiar a `true` y el operador también debe escribir literalmente `HABILITAR GESTION REAL` en `confirm_real`. La habilitación de reglas no se guarda y se pierde al reiniciar o desconectar. Antes de modificar/cerrar vuelve a consultar ticket, símbolo y hora de apertura, tick reciente (máximo 30 segundos), cotización, restricciones de stops/freeze y especificaciones de volumen. No permite que una modificación aumente el riesgo del SL y limita el riesgo calculado a `RISK_PER_TRADE`. Solo informa éxito después de un retcode `TRADE_RETCODE_DONE` y de verificar que la posición desapareció o que MT5 muestra los nuevos niveles.

La suite comprueba cambios, alertas, clasificación manual/automática y acciones usando un adaptador mock; no envía operaciones durante pruebas. No se pudo probar con cuenta demo en esta sesión porque MT5 estaba desconectado. Mantenga `enabled: false` hasta revisar las reglas y probarlas con demo.

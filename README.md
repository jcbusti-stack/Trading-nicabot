# Trading-nicabot

Bot de trading **de seguimiento de tendencia** en velas de 12 horas, multi-activo,
con datos de **Kraken**. Por defecto funciona en **modo papel** (simulado, sin
dinero real) con un capital ficticio de $1,000.

## Qué hace

Cada 12 horas (GitHub Actions, 4:00 y 16:00 hora CDMX):

1. Descarga velas de Kraken (API pública, sin llaves) para cada par de `config.json`.
   Kraken no tiene velas de 12h: se arman juntando 3 velas de 4h.
2. Calcula indicadores: EMA 9/21/200, RSI 14, MACD, ATR.
3. Revisa salidas y entradas de cada par, guarda `estado.json` y sube un resumen
   a Google Drive (`senal_multi.txt` y `senal_sol.txt`).

### Reglas (iguales a la estrategia validada)
- **Entrada:** precio > EMA200 + 1.5%, EMA9 > EMA21, MACD > señal, RSI < 65.
- **Salida:** MACD cruza hacia abajo, RSI > 75, stop-loss o take-profit.

### Riesgo
- **Stop-loss** revisado contra el **mínimo** de la vela (no solo el cierre).
  Si la vela abre por debajo del stop, se simula la venta al precio de apertura.
- **Take-profit** a 3R (3 veces la distancia al stop). Configurable o apagable.
- **Trailing stop** opcional por ATR (`trailing_atr_mult`, apagado por defecto).
- **Tamaño de posición por riesgo:** se arriesga 1% del capital por operación
  (menos en memecoins), calculado con la distancia al stop.
- **Límites del portafolio:** máximo 5 posiciones abiertas, máximo 25% del
  capital por activo (10% memecoins, 15% monedas chicas), exposición total máx 100%.
- **Kill switch:** si el capital total (efectivo + posiciones) cae 25% desde su
  pico, se bloquean entradas nuevas. Para reactivarlo: workflow "Reactivar Bot".
- **Costos:** comisión de Kraken 0.26% (taker) + deslizamiento 0.05% por lado.

### Filtro de Bitcoin (régimen de mercado)
Antes de abrir **cualquier posición nueva** (en cualquier par, incluido BTC), el bot
revisa BTC/USD en velas de 12h (las mismas de Kraken, con la historia de semilla 1d):
- Cierre de BTC **>** EMA200, **y**
- EMA9 de BTC **>** EMA21.

Si BTC está bajista, **no se compra nada** y el log/resumen dice el motivo
(`filtro BTC bajista: ...`). Las **salidas, stops y take-profit no cambian**.
Se configura en `config.json` → `filtro_btc`:

| Clave | Qué es |
|---|---|
| `enabled` | `true` = filtro activo, `false` = apagado |
| `par` | Par de referencia (por defecto `BTC/USD`) |
| `precio_sobre_ema200` | Exigir cierre de BTC > EMA200 |
| `ema9_sobre_ema21` | Exigir EMA9 > EMA21 de BTC |

El resumen (`senal_multi.txt`) muestra una línea `Regimen BTC:` con el estado actual.

## Pares

BTC, ONDO, KAS, ETH, SOL, SUI, PEPE, AVAX, ADA, SHIB, ENA, DOGE, XRP, MNT, BONK, WLFI
contra USD en Kraken. Si un par no existe en Kraken, se omite y se avisa en el log.
En `ajustes_por_par` se cambian parámetros por moneda (stop más amplio, menos riesgo).

## Configuración

Todo está en `config.json`. Lo más importante:

| Clave | Qué es |
|---|---|
| `pares` | Lista de pares a vigilar |
| `riesgo.riesgo_por_operacion_pct` | % del capital que se arriesga por operación |
| `riesgo.stop_modo` | `pct` (porcentaje fijo) o `atr` |
| `riesgo.take_profit_r` | Take-profit en múltiplos de R (`null` = sin TP) |
| `riesgo.trailing_atr_mult` | Trailing stop por ATR (`null` = apagado) |
| `riesgo.kill_switch_pct` | Caída máxima antes de bloquear entradas |
| `portafolio.*` | Límites de posiciones y exposición |
| `costos.*` | Comisión y deslizamiento |
| `ajustes_por_par` | Cambios por moneda |

## Órdenes reales (OPCIONAL, apagado)

El bot **no** envía órdenes reales salvo que se cumplan TODAS estas condiciones:

1. `MODO=live`
2. `CONFIRMO_ORDENES_REALES=SI`
3. `KRAKEN_TRADING_API_KEY` y `KRAKEN_TRADING_API_SECRET` (una llave **distinta**
   a la de solo lectura de `main.py`; con permiso de órdenes y **sin** permiso de retiro).
4. `DRY_RUN=0` (si no, solo escribe en el log la orden que habría enviado).

Cada compra real está limitada a `live.max_orden_usdt` (50 USD por defecto).
Los workflows tienen `MODO: paper` fijo: el modo real **no** está activado en GitHub.
Importante: en modo real el stop lo revisa el bot cada 12h, **no** queda una orden
stop en Kraken.

## Uso local

```bash
python3.12 -m venv venv && . venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q          # pruebas
python bot_paper.py          # un paso del bot en papel
python -m nicabot.backtest   # backtest con el mismo motor
```

## Estructura

- `bot_paper.py` – punto de entrada.
- `nicabot/config.py` – carga de `config.json`.
- `nicabot/datos.py` – descarga de velas de Kraken, reintentos y caché (`datos_velas/`).
- `nicabot/indicadores.py`, `nicabot/estrategia.py` – indicadores y señales.
- `nicabot/riesgo.py`, `nicabot/motor.py` – stops, tamaño, costos, simulación.
- `nicabot/estado.py` – `estado.json` (migra automáticamente el formato viejo).
- `nicabot/ejecucion.py` – órdenes reales opcionales con salvaguardas.
- `nicabot/notificador.py` – subida a Google Drive.
- `nicabot/backtest.py` – backtest multi-activo.
- `gestion_riesgo.py`, `main.py`, etc. – scripts de análisis y reportes existentes.

## Limitaciones conocidas

- Kraken solo da las últimas 720 velas de cada intervalo y **no permite paginar
  más atrás** con el endpoint OHLC (el parámetro `since` no trae datos más
  viejos). 720 velas de 4h = ~240 velas de 12h (unos 4 meses), menos que
  `min_velas_ema` (250). Por eso:
  - el caché `datos_velas/` crece con cada corrida;
  - si hay menos de `min_velas_ema` velas reales y `semilla_1d` está activo, se
    anteponen velas 12h **aproximadas** construidas desde velas de 1d (Kraken da
    ~2 años de 1d). Solo sirven para calentar la EMA200; las señales y
    operaciones usan solo velas reales;
  - si aun así no hay `min_velas_ema` velas, el par NO abre posiciones nuevas
    (sí gestiona salidas). El backtest también cubre poco tiempo.
- Esto no es asesoría financiera. Los resultados pasados no garantizan nada.

## Horarios (UTC) y vigilancia de stops

- `paper_trading.yml`: `2 0,12 * * *`, 2 min después del cierre de cada vela de 12h.
- `vigilancia_stops.yml`: `7,27,47 * * * *`. Revisa las posiciones abiertas con el
  precio **vivo** del ticker público de Kraken y cierra en paper al tocar stop,
  take-profit o trailing, al precio vivo (con comisión y slippage). No abre
  posiciones ni manda órdenes reales.
- Las dos (y `reactivar_bot.yml`) comparten el grupo de concurrencia `estado-json`,
  así nunca escriben `estado.json` a la vez. El push reintenta con `git pull --rebase`.
  Nota: GitHub solo deja una corrida *pendiente* por grupo; si se juntan, la más
  vieja pendiente puede cancelarse (la siguiente vigilancia corre 20 min después).
- Los demás workflows se movieron fuera de la hora en punto (:13, :17, :23, :41).

## Guardia de entrada

Antes de comprar se lee el precio vivo. Se salta la compra si la vela de la
señal cerró hace más de `entrada.max_horas_desde_cierre` (3h) o si el precio
cayó más de `max_caida_pct` (2%) o subió más de `max_subida_pct` (3%) respecto
al cierre de la señal. La compra se llena al precio vivo.

## Google Drive (error 403 "Service Accounts do not have storage quota")

Las cuentas de servicio no tienen espacio propio en Drive, así que no pueden
**crear** archivos en una carpeta de "Mi unidad". Ahora este error **no es fatal**:
se registra un aviso claro y el bot sigue (el resumen queda local y en el repo).
Para arreglarlo, elige una opción:

1. **Unidad compartida (recomendado, requiere Google Workspace):** crea una
   Unidad compartida, agrega el correo de la cuenta de servicio como
   "Administrador de contenido" y pon el ID de una carpeta de esa unidad en
   `DRIVE_FOLDER_ID` (o `drive.folder_id`). El código ya usa `supportsAllDrives`.
2. **Archivos creados por ti:** crea tú mismo `senal_multi.txt` y `senal_sol.txt`
   en la carpeta y compártelos con la cuenta de servicio como Editor; el bot solo
   los *actualiza* (no gasta cuota de la cuenta de servicio).
3. **OAuth de usuario:** usar credenciales OAuth de tu cuenta en vez de la cuenta
   de servicio (requiere crear un secreto nuevo; no implementado aquí).

## Mejoras de precisión (walk-forward, filtros y revisión mensual)

### Historia larga y optimización por moneda
Kraken solo da 720 velas, así que para probar con años de datos se usa
`nicabot/historia.py` (SOLO para backtest, nunca para operar):
1. `data.binance.vision` (archivos públicos de Binance, par XXX/USDT, velas 4h desde 2019).
2. KuCoin vía ccxt si Binance no tiene el par (KAS, MNT).
3. Kraken (720 velas) como último recurso.
USDT se usa como aproximación de USD. El cache queda en `datos_historia/` (no se sube a git).

```bash
python -m nicabot.optimizacion                    # solo reporte
python -m nicabot.optimizacion --escribir-config  # además escribe config.json
```
También existe el workflow manual **Optimizacion Walk-Forward** (deja el reporte como artefacto, no cambia config).

Método: velas 12h (desde 4h), entrenar 18 meses → probar los 6 meses siguientes
(fuera de muestra), ventana rodante. Rejilla pequeña: stop (4/6/8% o 2/3×ATR),
EMAs (9/21, 12/26, 20/50), RSI máx (60/65/70), TP (2/3/4 R). Mínimo 6 operaciones
para elegir y la puntuación se promedia con las combinaciones vecinas (prefiere
zonas estables). Comisión 0.26% + slippage 0.05% por lado y filtro BTC incluidos.
Los parámetros ajustados de un par solo se escriben en `ajustes_por_par` si fuera de
muestra ganan ≥1 punto de retorno, no bajan el PF y tienen ≥10 operaciones.
Resultados: `reportes/optimizacion.md` (y `.json`).

### Filtros de entrada (`estrategia.filtros` en config.json)
Solo afectan entradas; si falta el dato no bloquean.
- `volumen`: volumen de la vela > SMA(`periodo`) del volumen × `factor`. **Encendido** (mejoró fuera de muestra).
- `multi_marco`: cierre diario > EMA50 diaria **y** MACD 4h > señal (con velas ya cerradas). **Encendido** (mejoró el PF).
- `volatilidad`: no entra si ATR% > `atr_pct_max` del par (en `ajustes_por_par`). **Apagado** (no mejoró).
- Stop por ATR: `riesgo.stop_modo: "atr"` + `stop_atr_mult` (se puede poner por par).

### Revisión mensual (`revision_mensual` en config.json)
El workflow **Revision Mensual por Moneda** corre el día 1 de cada mes (15:41 UTC = 9:41 AM CDMX),
en el mismo grupo de concurrencia `estado-json`. Con el historial de `estado.json`:
si un par tiene expectativa negativa en sus últimas `ultimas_n` operaciones
(mínimo `min_operaciones`, 8), se desactivan sus entradas nuevas por
`enfriamiento_dias` (60). Las posiciones abiertas siguen con su stop/TP. Al
terminar el enfriamiento se reactiva y se cuenta de cero. Reporte en
`reportes/revision_mensual.md`; el estado queda en `estado.json` → `revision`.

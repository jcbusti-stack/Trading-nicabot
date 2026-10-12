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

- Kraken solo da las últimas 720 velas de 4h (~240 velas de 12h, unos 4 meses).
  La EMA200 es poco precisa con tan poca historia; el caché `datos_velas/` crece
  con cada corrida y mejora con el tiempo. El backtest también cubre poco tiempo.
- Esto no es asesoría financiera. Los resultados pasados no garantizan nada.

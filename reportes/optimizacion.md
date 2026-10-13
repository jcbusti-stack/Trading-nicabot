# Optimizacion walk-forward (fuera de muestra)

Generado: 2026-10-13 03:15 UTC. Velas 12h desde 4h. Comisiones (0.26%) y slippage (0.05%) por lado incluidos.
Entrenamiento: 18 meses previos; prueba: 6 meses siguientes (OOS), ventana rodante. Minimo 6 operaciones en entrenamiento. Cada par simulado solo, capital 1000 USD por ventana, riesgo y topes de config.json, filtro BTC activo. Retorno = compuesto de las ventanas OOS; DD = peor drawdown de una ventana.

**Aviso:** historia de Binance/KuCoin en USDT (no Kraken USD). Precio casi igual; el volumen es de otra bolsa. Resultados pasados no garantizan nada.

## Filtros (parametros base, OOS, promedio de pares)

| Variante | Retorno prom por par | Ops | Win rate | PF (todas las ops) | DD prom | Encendido |
|---|---|---|---|---|---|---|
| base (sin filtros nuevos) | 1.21% | 534 | 32.4% | 1.07 | 3.68% |  |
| volumen | 2.41% | 415 | 34.9% | 1.18 | 3.17% | SI |
| volatilidad | 0.98% | 483 | 32.3% | 1.07 | 3.52% | NO |
| multi_marco | 1.29% | 344 | 32.0% | 1.1 | 3.18% | SI |

Filtros encendidos por defecto: volumen, multi_marco. Regla: se enciende solo si el PF OOS (todas las operaciones) no baja Y el retorno promedio no baja (o, si el PF sube >= 0.02, el retorno baja menos de 1.0 punto).

## Antes vs despues por par (OOS)

"Antes" = parametros actuales sin filtros nuevos. "Despues" = lo que queda en config: filtros encendidos + parametros ajustados SOLO si el par fue adoptado (si no, parametros base). Se adopta el ajuste solo si en OOS gana >= 1.0 punto(s) de retorno, no baja el PF y tiene >= 10 operaciones.

| Par | Fuente | OOS desde | Antes: Ret | Ops | Win | PF | DD | Despues: Ret | Ops | Win | PF | DD | Adoptado |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ADA/USD | Binance ADA/USDT (data.binance.vision) | 2021-01-01 | 2.7% | 64 | 31.2% | 1.09 | 7.69% | 20.85% | 34 | 44.1% | 2.18 | 4.99% | NO |
| AVAX/USD | Binance AVAX/USDT (data.binance.vision) | 2023-01-01 | -3.79% | 57 | 22.8% | 0.91 | 6.41% | 0.11% | 28 | 35.7% | 1.02 | 3.7% | SI |
| BONK/USD | Binance BONK/USDT (data.binance.vision) | 2026-01-01 | -0.52% | 2 | 0.0% | 0.0 | 0.49% | 0.0% | 0 | 0.0% | 0.0 | 0.0% | NO |
| BTC/USD | Binance BTC/USDT (data.binance.vision) | 2021-01-01 | 2.5% | 72 | 38.9% | 1.11 | 4.14% | -3.73% | 43 | 39.5% | 0.81 | 4.42% | NO |
| DOGE/USD | Binance DOGE/USDT (data.binance.vision) | 2021-07-01 | -3.21% | 57 | 31.6% | 0.76 | 3.54% | -0.65% | 32 | 34.4% | 0.92 | 2.26% | NO |
| ENA/USD | Binance ENA/USDT (data.binance.vision) | 2026-07-01 | 1.44% | 4 | 50.0% | 3.06 | 0.46% | -0.37% | 1 | 0.0% | 0.0 | 0.42% | NO |
| ETH/USD | Binance ETH/USDT (data.binance.vision) | 2021-01-01 | 8.96% | 74 | 37.8% | 1.29 | 5.57% | 10.71% | 41 | 39.0% | 1.54 | 6.47% | SI |
| KAS/USD | KuCoin KAS/USDT | 2025-07-01 | 0.66% | 11 | 27.3% | 1.14 | 3.48% | 0.9% | 3 | 33.3% | 2.08 | 1.31% | NO |
| MNT/USD | KuCoin MNT/USDT | 2026-01-01 | 2.6% | 4 | 50.0% | 6.04 | 0.71% | 0.64% | 2 | 50.0% | 3.23 | 0.71% | NO |
| ONDO/USD | Binance ONDO/USDT (data.binance.vision) | sin historia suficiente | - | - | - | - | - | - | - | - | - | - | NO |
| PEPE/USD | Binance PEPE/USDT (data.binance.vision) | 2025-07-01 | -1.16% | 9 | 44.4% | 0.46 | 1.25% | -0.92% | 3 | 33.3% | 0.13 | 0.72% | NO |
| SHIB/USD | Binance SHIB/USDT (data.binance.vision) | 2023-07-01 | -2.93% | 35 | 28.6% | 0.65 | 2.98% | 0.45% | 22 | 40.9% | 1.1 | 1.35% | NO |
| SOL/USD | Binance SOL/USDT (data.binance.vision) | 2022-07-01 | 21.3% | 62 | 40.3% | 1.68 | 5.97% | 18.89% | 29 | 48.3% | 2.12 | 3.3% | NO |
| SUI/USD | Binance SUI/USDT (data.binance.vision) | 2025-07-01 | 0.24% | 10 | 20.0% | 1.08 | 1.87% | -1.8% | 5 | 0.0% | 0.0 | 1.71% | NO |
| WLFI/USD | Binance WLFI/USDT (data.binance.vision) | sin historia suficiente | - | - | - | - | - | - | - | - | - | - | NO |
| XRP/USD | Binance XRP/USDT (data.binance.vision) | 2021-01-01 | -11.88% | 73 | 24.7% | 0.75 | 6.94% | -6.13% | 39 | 25.6% | 0.79 | 4.9% | NO |

| Total | Retorno prom por par | Ops | Win rate | PF (todas las ops) | DD prom | |
|---|---|---|---|---|---|---|
| Antes | 1.21% | 534 | 32.4% | 1.07 | 3.68% |  |
| Despues (config final) | 3.0% | 282 | 37.2% | 1.3 | 2.79% |  |
| Referencia: ajuste walk-forward en TODOS los pares | 1.94% | 291 | 35.4% | 1.23 | 2.63% |  |

## Parametros finales elegidos (ultimos 3 anos)

| Par | Stop | EMAs | RSI max | TP (R) | ATR% max | Escrito en config |
|---|---|---|---|---|---|---|
| ADA/USD | pct 6.0 | 20/50 | 65 | 3.0 | 6.97 | NO (se queda la config base) |
| AVAX/USD | atr 3.0 | 20/50 | 70 | 4.0 | 7.27 | SI |
| BONK/USD | pct 6.0 | 12/26 | 70 | 2.0 | 9.74 | NO (se queda la config base) |
| BTC/USD | pct 4.0 | 12/26 | 60 | 2.0 | 3.45 | NO (se queda la config base) |
| DOGE/USD | atr 2.0 | 9/21 | 70 | 3.0 | 7.24 | NO (se queda la config base) |
| ENA/USD | pct 4.0 | 9/21 | 70 | 4.0 | 10.06 | NO (se queda la config base) |
| ETH/USD | pct 4.0 | 20/50 | 70 | 4.0 | 4.94 | SI |
| KAS/USD | atr 3.0 | 20/50 | 65 | 3.0 | 7.9 | NO (se queda la config base) |
| MNT/USD | pct 8.0 | 20/50 | 70 | 4.0 | 6.62 | NO (se queda la config base) |
| ONDO/USD | atr 2.0 | 9/21 | 70 | 2.0 | 7.26 | NO (se queda la config base) |
| PEPE/USD | atr 3.0 | 12/26 | 60 | 3.0 | 9.6 | NO (se queda la config base) |
| SHIB/USD | atr 3.0 | 9/21 | 60 | 3.0 | 6.83 | NO (se queda la config base) |
| SOL/USD | pct 4.0 | 9/21 | 70 | 4.0 | 6.79 | NO (se queda la config base) |
| SUI/USD | atr 3.0 | 12/26 | 60 | 3.0 | 8.03 | NO (se queda la config base) |
| WLFI/USD | - | - | - | - | 6.11 | NO (pocas operaciones/historia) |
| XRP/USD | atr 3.0 | 20/50 | 60 | 2.0 | 6.27 | NO (se queda la config base) |

## Pares que se ven mal (expectativa OOS negativa en base, con filtros y con ajuste)

DOGE/USD, XRP/USD

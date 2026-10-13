# Investigación: subir la tasa de aciertos (win rate) sin destruir la rentabilidad

Generado: 2026-10-13 (UTC) con `python -m nicabot.investigacion`. Datos crudos en `investigacion_aciertos.json`.

## Método (anti-sobreajuste)

- Mismo framework walk-forward de `nicabot/optimizacion.py`: velas 12h construidas desde 4h (Binance/KuCoin), **solo ventanas fuera de muestra** (6 meses cada una, desde ~2021).
- Comisión 0.26% + slippage 0.05% por lado. Filtro BTC, filtro de volumen y multi-marco encendidos igual que el bot.
- Cada variante = config actual de cada par + cambios **fijos e iguales para todos los pares** (no se ajusta nada por par ni con datos de prueba).
- Regla de elección: win rate > 50%, PF > 1.2, expectativa > 0, mínimo 100 operaciones; entre esas, el mayor PF.
- Una operación con venta parcial cuenta como **una** operación (PnL total de las dos ventas).
- Nota: con los datos de hoy la referencia "actual" sale en 40.5% / PF 1.55 / 220 ops (11 pares con historia OOS; ONDO y WLFI no tienen historia suficiente, BONK no opera). Es distinta del 37% / 1.30 / 282 de `optimizacion.md` porque ese reporte mezclaba otra base; aquí todas las variantes se comparan contra la misma referencia.

## Ideas investigadas (web)

- **Toma parcial + break-even**: vender una parte a 1R y subir el stop a la entrada convierte muchas pérdidas en pequeñas ganancias; sube el win rate pero recorta las ganancias grandes (que son las que pagan en tendencia).
- **TP más cercano**: sube aciertos, baja el pago por acierto.
- **Salida por tiempo**: cerrar operaciones que no avanzan libera capital y corta perdedoras lentas.
- **Pullback en tendencia** (comprar retrocesos a la EMA lenta con RSI 40-50).
- **Reversión a la media tipo Connors RSI(2)** con filtro de tendencia (EMA200), salida al cerrar sobre SMA5: publicaciones reportan 63-75% de aciertos en acciones/BTC diario, pero estudios en cripto 4h muestran que **las comisiones se comen el borde** (PF < 1).
- **Trailing ATR**, **no entrar tras movimientos extendidos** (> 2 ATR sobre la EMA lenta), **apagar salida por MACD**.

## Resultados OOS (todas las variantes)

Retorno = promedio por par del retorno compuesto OOS (capital 1000 USD por par y ventana, riesgo 0.5-1% por operación). DD = drawdown por ventana.

| Variante | Win rate | PF | Expectativa USD/op | Retorno prom/par | DD prom | DD max | Ops |
|---|---|---|---|---|---|---|---|
| actual | 40.5% | 1.55 | 2.38 | 4.92% | 2.46% | 6.47% | 220 |
| tp_2r | 41.6% | 1.29 | 1.24 | 2.54% | 2.31% | 6.47% | 226 |
| tp_1.5r | 46.3% | 1.31 | 1.22 | 2.6% | 2.21% | 6.47% | 231 |
| tp_1r | 50.2% | 1.06 | 0.23 | 0.48% | 1.93% | 4.58% | 241 |
| parcial_1r_be_tp3r | 50.2% | 1.24 | 0.89 | 1.81% | 2.04% | 4.98% | 227 |
| parcial_1r_be_trailing3atr | 50.0% | 1.2 | 0.74 | 1.49% | 2.14% | 4.98% | 226 |
| parcial_0.75r_be_tp3r | 54.8% | 1.14 | 0.48 | 0.94% | 1.96% | 4.62% | 228 |
| parcial_1r_be_tp3r_sin_macd | 54.3% | 1.11 | 0.53 | 0.99% | 2.44% | 5.33% | 210 |
| parcial_1r_be_trail_sin_macd | 53.8% | 1.15 | 0.7 | 1.3% | 2.53% | 5.33% | 210 |
| tiempo_10velas | 43.6% | 1.59 | 2.42 | 5.26% | 2.25% | 6.47% | 232 |
| sin_salida_macd | 37.2% | 1.33 | 2.16 | 3.98% | 3.16% | 6.81% | 191 |
| trailing_atr3 | 39.9% | 1.55 | 2.29 | 4.81% | 2.46% | 6.47% | 223 |
| sin_extension_2atr | 37.6% | 1.44 | 1.9 | 3.11% | 2.11% | 4.99% | 170 |
| pullback | 30.9% | 0.98 | -0.06 | -0.02% | 1.82% | 3.89% | 136 |
| pullback_parcial_1r | 38.9% | 1.0 | -0.01 | -0.01% | 1.58% | 3.1% | 136 |
| reversion_rsi2 | 57.7% | 0.88 | -0.41 | -1.06% | 1.79% | 4.44% | 286 |
| reversion_rsi2_stop_ancho | 62.2% | 0.79 | -0.32 | -0.84% | 1.08% | 4.0% | 278 |
| mixto_cruce_reversion | 39.6% | 1.16 | 0.5 | 2.21% | 2.76% | 7.44% | 495 |
| parcial_1r_be_tp3r_ext2atr | 47.7% | 1.1 | 0.4 | 0.63% | 1.85% | 3.95% | 174 |
| parcial_1r_be_tp2r | 50.0% | 1.12 | 0.46 | 0.94% | 2.06% | 4.98% | 232 |
| parcial_1r_be0_tp3r | 50.0% | 1.3 | 1.11 | 2.26% | 2.04% | 5.06% | 224 |
| parcial_1r_frac33_tp3r_tiempo10 | 49.6% | 1.3 | 1.1 | 2.36% | 1.97% | 5.12% | 236 |
| parcial_1r_sinbe_tp3r_tiempo10 | 46.5% | 1.37 | 1.34 | 2.85% | 1.99% | 5.53% | 232 |
| tp_1.5r_tiempo10 | 47.9% | 1.31 | 1.19 | 2.62% | 2.19% | 6.47% | 240 |
| parcial_1r_be_tp3r_tiempo10 | 50.0% | 1.23 | 0.86 | 1.84% | 1.94% | 4.98% | 236 |
| parcial_1r_be_tp3r_tiempo14 **(elegida)** | 50.9% | 1.26 | 0.97 | 2.0% | 2.03% | 4.98% | 228 |

## Elección: `parcial_1r_be_tp3r_tiempo14` (por defecto en config.json)

- Vende **50% a 1R**, sube el stop a **entrada + 0.6%** (cubre comisiones), el resto sigue hasta **TP 3R**, la señal de salida o **14 velas (7 días)**.
- OOS: win rate **50.9%** (antes 40.5%), PF **1.26** (antes 1.55), expectativa +0.97 USD/op, retorno prom. 2.0% (antes 4.9%), DD máx 4.98% (antes 6.47%).
- Es la única variante con > 50% de aciertos y PF > 1.2 con expectativa positiva clara. Las de más aciertos (reversión RSI2 57-62%, parcial 0.75R 55%) pierden dinero o quedan con PF ≈ 1.1.

## Advertencias honestas

1. **Subir aciertos cuesta rentabilidad**: el retorno OOS baja ~60% y el PF baja de 1.55 a 1.26. Si lo importante es ganar más dinero (no acertar más), la mejor variante es `tiempo_10velas` (43.6%, PF 1.59, 5.3%) o la config anterior.
2. 50.9% está **justo por encima** del 50%; con ~230 operaciones el margen de error es ±6 puntos. No es garantía.
3. Se probaron ~26 variantes sobre los mismos datos OOS: elegir la mejor entre muchas introduce algo de sobreajuste de selección.
4. Pocos pares aportan casi todas las operaciones (BTC, ETH, ADA, AVAX, SOL, SHIB). BTC sigue con PF < 1.
5. Historia de Binance/KuCoin en USDT, no Kraken USD. Sigue siendo **solo paper**.

Para volver a la estrategia anterior: en `config.json` poner `riesgo.tp_parcial_r: null` y `riesgo.max_velas_posicion: null`.

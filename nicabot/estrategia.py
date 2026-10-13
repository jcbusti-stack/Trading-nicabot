"""Reglas de entrada/salida. Las entradas pasan ademas por los filtros opcionales
de nicabot/filtros.py (volumen, volatilidad, multi-marco).

estrategia.modo_entrada (ver reportes/investigacion_aciertos.md):
 - 'cruce'    (original): tendencia (cierre > EMA200+margen), EMA rapida > lenta, MACD > senal, RSI < max.
 - 'pullback' : tendencia + EMA rapida > lenta, pero compra el retroceso: el minimo toco la EMA lenta
                (o RSI14 entre pullback_rsi_min y pullback_rsi_max) y la vela cierra alcista sobre la EMA lenta.
 - 'reversion': tipo Connors: tendencia (cierre > EMA200) y RSI(2) < reversion_rsi_max; sale cuando cierra > SMA(5).
 - 'mixto'    : 'cruce' o 'reversion'.
estrategia.max_extension_atr: no entra si el cierre esta mas de N ATR sobre la EMA lenta (movimiento extendido).
estrategia.salida_macd: si es False, la salida por cruce MACD se apaga (solo RSI > rsi_salida, stop, TP, tiempo).
"""
import math

from .filtros import revisar_filtros

MODOS = ('cruce', 'pullback', 'reversion', 'mixto')


def _f(fila, k):
    try:
        x = float(fila[k])
        return None if math.isnan(x) else x
    except (KeyError, TypeError, ValueError):
        return None


def _entrada_cruce(fila, c, trend_up):
    return (trend_up and fila['MACD'] > fila['MACD_signal'] and fila['RSI'] < c['rsi_max_entrada']
            and fila['EMA_9'] > fila['EMA_21'])


def _entrada_pullback(fila, c, trend_up):
    lenta = fila['EMA_21']
    toco = fila['low'] <= lenta * (1 + c.get('pullback_tolerancia_pct', 0.5) / 100)
    rsi_zona = c.get('pullback_rsi_min', 40) <= fila['RSI'] <= c.get('pullback_rsi_max', 50)
    alcista = fila['close'] > fila['open'] and fila['close'] > lenta
    return bool(trend_up and fila['EMA_9'] > lenta and (toco or rsi_zona) and alcista)


def _entrada_reversion(fila, c):
    rsi_c, sma = _f(fila, 'RSI_C'), _f(fila, 'SMA_SAL')
    if rsi_c is None or sma is None:
        return False
    return bool(fila['close'] > fila['EMA_200'] and rsi_c < c.get('reversion_rsi_max', 10) and fila['close'] < sma)


def evaluar_senales(fila, cfg_estrategia):
    c = cfg_estrategia
    modo = c.get('modo_entrada', 'cruce')
    if modo not in MODOS:
        raise ValueError(f'modo_entrada invalido: {modo}')
    trend_up = bool(fila['close'] > fila['EMA_200'] * (1 + c['margen_tendencia_pct'] / 100))
    macd_bullish = fila['MACD'] > fila['MACD_signal']
    rsi_ok = fila['RSI'] < c['rsi_max_entrada']
    momentum_ok = fila['EMA_9'] > fila['EMA_21']
    cruce = bool(_entrada_cruce(fila, c, trend_up))
    rev = _entrada_reversion(fila, c) if modo in ('reversion', 'mixto') else False
    if modo == 'cruce':
        base = cruce
    elif modo == 'pullback':
        base = _entrada_pullback(fila, c, trend_up)
    elif modo == 'reversion':
        base = rev
    else:
        base = cruce or rev
    ext = c.get('max_extension_atr')
    atr = _f(fila, 'ATR')
    if base and ext and atr and fila['close'] > fila['EMA_21'] + ext * atr:
        base = False
    filtros_ok, bloqueos = revisar_filtros(fila, c)
    if modo == 'reversion' or (modo == 'mixto' and rev and not cruce):
        # la reversion compra debilidad: el filtro de volumen/MACD 4h no aplica
        filtros_ok, bloqueos = True, []
    entrada = bool(base and filtros_ok)
    salida_macd = c.get('salida_macd', True) and fila['MACD'] < fila['MACD_signal']
    salida = bool(salida_macd or fila['RSI'] > c['rsi_salida'])
    if modo == 'reversion':
        sma = _f(fila, 'SMA_SAL')
        salida = bool(sma is not None and fila['close'] > sma)
    return {'entrada': entrada, 'salida': salida, 'trend_up': trend_up, 'reversion': bool(rev),
            'macd_bullish': bool(macd_bullish), 'rsi_ok': bool(rsi_ok), 'momentum_ok': bool(momentum_ok),
            'filtros_ok': bool(filtros_ok), 'bloqueos_filtros': bloqueos}

"""Reglas de entrada/salida. Las salidas no cambian; las entradas ademas pasan
por los filtros opcionales de nicabot/filtros.py (volumen, volatilidad, multi-marco)."""
from .filtros import revisar_filtros


def evaluar_senales(fila, cfg_estrategia):
    c = cfg_estrategia
    trend_up = fila['close'] > fila['EMA_200'] * (1 + c['margen_tendencia_pct'] / 100)
    macd_bullish = fila['MACD'] > fila['MACD_signal']
    rsi_ok = fila['RSI'] < c['rsi_max_entrada']
    momentum_ok = fila['EMA_9'] > fila['EMA_21']
    base = bool(trend_up and macd_bullish and rsi_ok and momentum_ok)
    filtros_ok, bloqueos = revisar_filtros(fila, c)
    entrada = bool(base and filtros_ok)
    salida = bool((fila['MACD'] < fila['MACD_signal']) or (fila['RSI'] > c['rsi_salida']))
    return {'entrada': entrada, 'salida': salida, 'trend_up': bool(trend_up),
            'macd_bullish': bool(macd_bullish), 'rsi_ok': bool(rsi_ok), 'momentum_ok': bool(momentum_ok),
            'filtros_ok': bool(filtros_ok), 'bloqueos_filtros': bloqueos}

"""Reglas de entrada/salida (sin cambios respecto a la estrategia validada)."""


def evaluar_senales(fila, cfg_estrategia):
    c = cfg_estrategia
    trend_up = fila['close'] > fila['EMA_200'] * (1 + c['margen_tendencia_pct'] / 100)
    macd_bullish = fila['MACD'] > fila['MACD_signal']
    rsi_ok = fila['RSI'] < c['rsi_max_entrada']
    momentum_ok = fila['EMA_9'] > fila['EMA_21']
    entrada = bool(trend_up and macd_bullish and rsi_ok and momentum_ok)
    salida = bool((fila['MACD'] < fila['MACD_signal']) or (fila['RSI'] > c['rsi_salida']))
    return {'entrada': entrada, 'salida': salida, 'trend_up': bool(trend_up),
            'macd_bullish': bool(macd_bullish), 'rsi_ok': bool(rsi_ok), 'momentum_ok': bool(momentum_ok)}

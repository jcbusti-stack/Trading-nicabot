"""Indicadores tecnicos (mismas formulas que la version validada)."""


def calcular_indicadores(df, cfg_estrategia=None):
    c = cfg_estrategia or {}
    rapida, lenta, tend = c.get('ema_rapida', 9), c.get('ema_lenta', 21), c.get('ema_tendencia', 200)
    rsi_p, atr_p = c.get('rsi_periodo', 14), c.get('atr_periodo', 14)
    df = df.copy()
    # Se mantienen los nombres de columna historicos (EMA_9, EMA_21, EMA_200)
    df['EMA_9'] = df['close'].ewm(span=rapida, adjust=False).mean()
    df['EMA_21'] = df['close'].ewm(span=lenta, adjust=False).mean()
    df['EMA_200'] = df['close'].ewm(span=tend, adjust=False).mean()
    delta = df['close'].diff()
    ganancia = delta.clip(lower=0).ewm(alpha=1 / rsi_p, adjust=False).mean()
    perdida = (-delta.clip(upper=0)).ewm(alpha=1 / rsi_p, adjust=False).mean()
    rs = ganancia / perdida
    df['RSI'] = 100 - (100 / (1 + rs))
    df.loc[perdida == 0, 'RSI'] = 100.0
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema12 - ema26
    df['MACD_signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    prev_close = df['close'].shift(1)
    tr = (df['high'] - df['low']).combine((df['high'] - prev_close).abs(), max).combine((df['low'] - prev_close).abs(), max)
    df['ATR'] = tr.ewm(alpha=1 / atr_p, adjust=False).mean()
    # Solo informativo
    df['VOL_PROM'] = df['volume'].rolling(20).mean().shift(1)
    return df.dropna()

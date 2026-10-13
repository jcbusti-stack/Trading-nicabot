"""Filtro de regimen de mercado con Bitcoin: solo se abren posiciones nuevas
(en cualquier par, incluido BTC) si BTC/USD esta alcista en velas 12h."""

DEFECTO_FILTRO = {'enabled': True, 'par': 'BTC/USD',
                  'precio_sobre_ema200': True, 'ema9_sobre_ema21': True}


def regimen_btc(d_btc, fecha, cfg_filtro):
    """d_btc: DataFrame de BTC con indicadores. Usa la ultima vela con indice <= fecha.
    Devuelve (alcista: bool, motivo: str, datos: dict)."""
    f = dict(DEFECTO_FILTRO, **(cfg_filtro or {}))
    if not f.get('enabled', True):
        return True, 'filtro BTC apagado', {}
    if d_btc is None or d_btc.empty:
        return False, 'filtro BTC: sin datos de BTC', {}
    sub = d_btc[d_btc.index <= fecha] if fecha is not None else d_btc
    if sub.empty:
        return False, 'filtro BTC: sin datos de BTC para esa fecha', {}
    u = sub.iloc[-1]
    datos = {'close': float(u['close']), 'ema200': float(u['EMA_200']),
             'ema9': float(u['EMA_9']), 'ema21': float(u['EMA_21'])}
    fallas = []
    if f.get('precio_sobre_ema200', True) and not datos['close'] > datos['ema200']:
        fallas.append(f"BTC {datos['close']:.0f} <= EMA200 {datos['ema200']:.0f}")
    if f.get('ema9_sobre_ema21', True) and not datos['ema9'] > datos['ema21']:
        fallas.append(f"EMA9 {datos['ema9']:.0f} <= EMA21 {datos['ema21']:.0f}")
    if fallas:
        return False, 'filtro BTC bajista: ' + '; '.join(fallas), datos
    return True, 'BTC alcista', datos

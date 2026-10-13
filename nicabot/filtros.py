"""Filtros extra de entrada (configurables en estrategia.filtros):
 - volumen: volumen de la vela > SMA(periodo) del volumen previo * factor
 - volatilidad: no entra si ATR% (ATR/cierre*100) > atr_pct_max del par
 - multi_marco: cierre diario > EMA(ema_diaria) del diario Y MACD 4h > senal
Solo afectan ENTRADAS. Si falta el dato (columna ausente/NaN) el filtro no bloquea.
Sin mirar al futuro: el diario y el 4h usados son los ya cerrados al cierre de la vela 12h."""
import math

import pandas as pd

DEFECTO = {
    'volumen': {'enabled': False, 'periodo': 20, 'factor': 1.0},
    'volatilidad': {'enabled': False, 'atr_pct_max': None},
    'multi_marco': {'enabled': False, 'ema_diaria': 50, 'usar_diario': True, 'usar_macd_4h': True},
}


def config_filtros(cfg_estrategia):
    f = (cfg_estrategia or {}).get('filtros') or {}
    return {k: dict(v, **(f.get(k) or {})) for k, v in DEFECTO.items()}


def _macd(close):
    m = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    return m, m.ewm(span=9, adjust=False).mean()


def agregar_columnas(d12, d4=None, cfg_estrategia=None, horas_tf=12):
    """Agrega ATR_PCT, VOL_SMA, D_CLOSE, D_EMA, MACD4, MACD4_signal a un DataFrame 12h con indicadores."""
    f = config_filtros(cfg_estrategia)
    d = d12.copy()
    if d.empty:
        return d
    d['ATR_PCT'] = d['ATR'] / d['close'] * 100
    d['VOL_SMA'] = d['volume'].rolling(int(f['volumen']['periodo'])).mean().shift(1)
    cierre_12 = d.index + pd.Timedelta(hours=horas_tf)
    # Diario a partir de las mismas velas 12h (2 por dia); se indexa por hora de cierre
    dia = d['close'].resample('1D').agg(['last', 'count'])
    dia = dia[dia['count'] == 2]['last']
    ema_d = dia.ewm(span=int(f['multi_marco']['ema_diaria']), adjust=False).mean()
    dd = pd.DataFrame({'D_CLOSE': dia, 'D_EMA': ema_d})
    dd.index = dd.index + pd.Timedelta(days=1)
    dd = dd.reindex(cierre_12, method='ffill')
    d['D_CLOSE'], d['D_EMA'] = dd['D_CLOSE'].values, dd['D_EMA'].values
    if d4 is not None and not d4.empty:
        m, s = _macd(d4['close'])
        mm = pd.DataFrame({'MACD4': m, 'MACD4_signal': s})
        mm.index = mm.index + pd.Timedelta(hours=4)
        mm = mm.reindex(cierre_12, method='ffill')
        d['MACD4'], d['MACD4_signal'] = mm['MACD4'].values, mm['MACD4_signal'].values
    return d


def _v(fila, k):
    try:
        x = float(fila.get(k)) if hasattr(fila, 'get') else float(fila[k])
        return None if math.isnan(x) else x
    except (TypeError, ValueError, KeyError):
        return None


def revisar_filtros(fila, cfg_estrategia):
    """Devuelve (ok, lista_de_motivos_de_bloqueo)."""
    f = config_filtros(cfg_estrategia)
    fallas = []
    if f['volumen']['enabled']:
        v, sma = _v(fila, 'volume'), _v(fila, 'VOL_SMA')
        if v is not None and sma is not None and not v > sma * f['volumen']['factor']:
            fallas.append('volumen bajo')
    if f['volatilidad']['enabled'] and f['volatilidad'].get('atr_pct_max'):
        a = _v(fila, 'ATR_PCT')
        if a is not None and a > f['volatilidad']['atr_pct_max']:
            fallas.append(f"ATR% {a:.1f} > {f['volatilidad']['atr_pct_max']}")
    if f['multi_marco']['enabled']:
        mm = f['multi_marco']
        dc, de = _v(fila, 'D_CLOSE'), _v(fila, 'D_EMA')
        if mm.get('usar_diario', True) and dc is not None and de is not None and not dc > de:
            fallas.append('diario bajo EMA')
        m4, s4 = _v(fila, 'MACD4'), _v(fila, 'MACD4_signal')
        if mm.get('usar_macd_4h', True) and m4 is not None and s4 is not None and not m4 > s4:
            fallas.append('MACD 4h bajista')
    return not fallas, fallas

"""Descarga de velas desde Kraken (API publica, sin llaves) con reintentos.

Kraken NO ofrece velas de 12h (sus intervalos: 1m,5m,15m,30m,1h,4h,1d,1w,2w)
y su endpoint OHLC devuelve como maximo las ultimas 720 velas. Por eso las
velas de 12h se construyen agrupando velas de 4h (3 por cada 12h, alineadas a
00:00 y 12:00 UTC). 720 velas de 4h = 240 velas de 12h. Para tener mas
historia (EMA200 mas estable) se guarda un cache en datos_velas/ que crece en
cada corrida.
"""
import logging
import os
import time

import pandas as pd

log = logging.getLogger('nicabot.datos')

MS_TIMEFRAME = {'1h': 3600_000, '4h': 4 * 3600_000, '12h': 12 * 3600_000, '1d': 24 * 3600_000}
# timeframe pedido -> (timeframe base a descargar, velas base por vela final)
AGREGADOS = {'12h': ('4h', 3)}
COLUMNAS = ['timestamp', 'open', 'high', 'low', 'close', 'volume']


def con_reintentos(funcion, intentos=4, espera=3.0):
    """Ejecuta funcion() reintentando ante errores de red (backoff exponencial)."""
    import ccxt
    ultimo = None
    for i in range(intentos):
        try:
            return funcion()
        except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RequestTimeout) as e:
            ultimo = e
            log.warning('Error de red (intento %d/%d): %s', i + 1, intentos, e)
            time.sleep(espera * (2 ** i))
    raise ultimo


def crear_exchange_publico(nombre='kraken'):
    import ccxt
    return getattr(ccxt, nombre)({'enableRateLimit': True})


def _a_df(ohlcv):
    df = pd.DataFrame(ohlcv, columns=COLUMNAS)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    return df.set_index('timestamp')


def agregar(df, regla, n_base):
    """Agrupa velas (p.ej. 4h -> 12h) y descarta grupos incompletos."""
    g = df.resample(regla, origin='epoch', label='left', closed='left')
    out = g.agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
    out['n'] = g['close'].count()
    return out[out['n'] == n_base].drop(columns='n')


def _solo_cerradas(df, timeframe):
    ahora = pd.Timestamp.now(tz='UTC')
    return df[df.index + pd.Timedelta(milliseconds=MS_TIMEFRAME[timeframe]) <= ahora]


def descargar_velas(exchange, par, timeframe='12h', velas=300, solo_cerradas=True, dir_cache=None):
    if timeframe in AGREGADOS and timeframe not in (exchange.timeframes or {}):
        base_tf, n = AGREGADOS[timeframe]
        base = _a_df(con_reintentos(lambda: exchange.fetch_ohlcv(par, timeframe=base_tf, limit=720)))
        if solo_cerradas:
            base = _solo_cerradas(base, base_tf)
        df = agregar(base, timeframe, n)
    else:
        df = _a_df(con_reintentos(lambda: exchange.fetch_ohlcv(par, timeframe=timeframe, limit=velas)))
    if solo_cerradas and timeframe in MS_TIMEFRAME:
        df = _solo_cerradas(df, timeframe)  # descarta la vela en curso
    if dir_cache:
        df = _mezclar_cache(df, par, timeframe, dir_cache)
    return df.tail(max(velas, 1)) if not dir_cache else df


def precio_vivo(exchange, par):
    """Ultimo precio del ticker publico de Kraken (None si falla)."""
    try:
        t = con_reintentos(lambda: exchange.fetch_ticker(par), intentos=3, espera=2.0)
        p = t.get('last') or t.get('close') or t.get('bid')
        return float(p) if p else None
    except Exception as e:
        log.warning('No se pudo leer el ticker de %s: %s', par, e)
        return None


def semilla_diaria(df_1d, antes_de):
    """Convierte velas de 1d en pares de velas 12h APROXIMADAS (solo para calentar
    la EMA200). Cada dia se parte en 00:00 (open -> punto medio) y 12:00
    (punto medio -> close), con el high/low del dia. Solo se usan dias
    anteriores a `antes_de`. Las filas llevan sintetica=True."""
    d = df_1d[df_1d.index + pd.Timedelta(days=1) <= antes_de]
    if d.empty:
        return d.assign(sintetica=pd.Series(dtype=bool))
    medio = (d['open'] + d['close']) / 2
    a = pd.DataFrame({'open': d['open'], 'high': d['high'], 'low': d['low'], 'close': medio,
                      'volume': d['volume'] / 2}, index=d.index)
    b = pd.DataFrame({'open': medio.values, 'high': d['high'].values, 'low': d['low'].values,
                      'close': d['close'].values, 'volume': (d['volume'] / 2).values},
                     index=d.index + pd.Timedelta(hours=12))
    out = pd.concat([a, b]).sort_index()
    out['sintetica'] = True
    return out


def con_semilla(df, df_1d):
    """Antepone historia aproximada (de velas 1d) a una serie 12h real."""
    df = df.copy()
    df['sintetica'] = False
    if df_1d is None or df_1d.empty or df.empty:
        return df
    sem = semilla_diaria(df_1d, df.index[0])
    return pd.concat([sem, df]).sort_index()


def descargar_semilla_1d(exchange, par):
    """Kraken da hasta 720 velas de 1d (~2 anos). None si falla."""
    try:
        return _solo_cerradas(_a_df(con_reintentos(lambda: exchange.fetch_ohlcv(par, timeframe='1d', limit=720))), '1d')
    except Exception as e:
        log.warning('No se pudo descargar historia 1d de %s: %s', par, e)
        return None


def _mezclar_cache(df, par, timeframe, dir_cache):
    os.makedirs(dir_cache, exist_ok=True)
    ruta = os.path.join(dir_cache, f"{par.replace('/', '_')}_{timeframe}.csv")
    if os.path.exists(ruta):
        viejo = pd.read_csv(ruta, index_col='timestamp', parse_dates=['timestamp'])
        if viejo.index.tz is None:
            viejo.index = viejo.index.tz_localize('UTC')
        df = pd.concat([viejo[~viejo.index.isin(df.index)], df]).sort_index()
    df.to_csv(ruta, index_label='timestamp')
    return df

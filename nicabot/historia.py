"""Historia LARGA de velas 4h, SOLO para backtest/optimizacion (nunca para operar).

Kraken limita su OHLC a 720 velas, asi que la historia larga se baja de fuentes
publicas gratuitas, sin llaves:
 1. data.binance.vision (archivos mensuales oficiales de Binance, par XXX/USDT).
    USDT se usa como aproximacion de USD: precio casi igual, volumen distinto.
 2. KuCoin via ccxt (paginado) si Binance no tiene el par (p.ej. KAS, MNT).
 3. Kraken (ultimas 720 velas) como ultimo recurso.
Se guarda un cache CSV en datos_historia/ (ignorado por git)."""
import io
import logging
import os
import time
import zipfile

import pandas as pd

log = logging.getLogger('nicabot.historia')
URL_BV = 'https://data.binance.vision/data/spot/monthly/klines/{s}/4h/{s}-4h-{y}-{m:02d}.zip'
COLS = ['timestamp', 'open', 'high', 'low', 'close', 'volume']


def _binance_vision(base, desde, hasta):
    import requests
    s = f'{base}USDT'
    partes, vacios = [], 0
    for per in pd.period_range(desde, hasta, freq='M'):
        r = requests.get(URL_BV.format(s=s, y=per.year, m=per.month), timeout=30)
        if r.status_code != 200:
            vacios += 1
            continue
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            raw = pd.read_csv(z.open(z.namelist()[0]), header=None)
        raw = raw.iloc[:, :6]
        raw.columns = COLS
        ts = raw['timestamp'].astype('int64')
        ts = ts.where(ts < 10**14, ts // 1000)  # desde 2025 vienen en microsegundos
        raw['timestamp'] = pd.to_datetime(ts, unit='ms', utc=True)
        partes.append(raw)
    if not partes:
        return None
    return pd.concat(partes).set_index('timestamp').sort_index()


def _ccxt_paginado(nombre, simbolo, desde_ms, tf='4h'):
    import ccxt
    ex = getattr(ccxt, nombre)({'enableRateLimit': True})
    filas, since = [], desde_ms
    for _ in range(200):
        r = ex.fetch_ohlcv(simbolo, tf, since=since, limit=1500)
        if not r:
            since += 1500 * 4 * 3600_000  # hueco antes del listado
            if since > time.time() * 1000:
                break
            continue
        filas += r
        nuevo = r[-1][0] + 1
        if nuevo <= since or len(r) < 2:
            break
        since = nuevo
    if not filas:
        return None
    df = pd.DataFrame(filas, columns=COLS).drop_duplicates('timestamp')
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    return df.set_index('timestamp').sort_index()


def descargar_historia_4h(par, desde='2019-01', dir_cache='datos_historia', usar_cache=True):
    base = par.split('/')[0]
    ruta = os.path.join(dir_cache, f'{base}_4h.csv') if dir_cache else None
    if usar_cache and ruta and os.path.exists(ruta):
        df = pd.read_csv(ruta, index_col='timestamp', parse_dates=['timestamp'])
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC')
        return df, open(ruta + '.fuente').read().strip() if os.path.exists(ruta + '.fuente') else '?'
    hasta = pd.Timestamp.now(tz='UTC').tz_localize(None).to_period('M') - 1
    df, fuente = None, None
    try:
        df, fuente = _binance_vision(base, desde, hasta), f'Binance {base}/USDT (data.binance.vision)'
    except Exception as e:
        log.warning('binance.vision fallo para %s: %s', par, e)
    if df is None or len(df) < 500:
        try:
            df, fuente = _ccxt_paginado('kucoin', f'{base}/USDT', int(pd.Timestamp(desde + '-01', tz='UTC').value // 10**6)), f'KuCoin {base}/USDT'
        except Exception as e:
            log.warning('KuCoin fallo para %s: %s', par, e)
    if df is None or len(df) < 500:
        import ccxt
        ex = ccxt.kraken({'enableRateLimit': True})
        r = ex.fetch_ohlcv(par, '4h', limit=720)
        df = pd.DataFrame(r, columns=COLS)
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        df, fuente = df.set_index('timestamp'), 'Kraken (solo 720 velas)'
    df = df[~df.index.duplicated()].astype(float)
    if ruta:
        os.makedirs(dir_cache, exist_ok=True)
        df.to_csv(ruta, index_label='timestamp')
        with open(ruta + '.fuente', 'w') as f:
            f.write(fuente)
    return df, fuente

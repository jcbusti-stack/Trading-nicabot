"""Paso del bot multi-activo: descarga velas cerradas de cada par, procesa las
nuevas en orden cronologico (efectivo compartido) y guarda el estado."""
import logging
from collections import defaultdict

import pandas as pd

from . import datos, estado as est, notificador
from .guardia import revisar_entrada
from .filtro_btc import regimen_btc
from .config import cargar_config, config_par
from .ejecucion import crear_ejecutor
from .estrategia import evaluar_senales
from .indicadores import calcular_indicadores
from .motor import equity, posiciones, procesar_vela, revisar_kill_switch

log = logging.getLogger('nicabot.bot')


def pares_disponibles(exchange, pares):
    mercados = datos.con_reintentos(exchange.load_markets)
    ok = []
    for p in pares:
        m = mercados.get(p)
        if m and m.get('spot') and m.get('active', True):
            ok.append(p)
        else:
            log.warning('Par %s no disponible en Kraken spot: se omite.', p)
    return ok


def velas_pendientes(df, ultima_vela):
    if ultima_vela:
        try:
            return df[df.index > pd.Timestamp(ultima_vela)]
        except (ValueError, TypeError):
            pass
    return df.iloc[-1:]


def ejecutar(cfg=None, exchange=None, dfs=None, subir=True, precios_vivos=None, ahora=None):
    """dfs: {par: DataFrame OHLCV} para pruebas sin red; si es None se descarga de Kraken.
    precios_vivos: {par: precio} para pruebas; si es None se lee el ticker publico.
    ahora: momento actual (pruebas)."""
    ahora = pd.Timestamp(ahora) if ahora is not None else pd.Timestamp.now(tz='UTC')
    cfg = cfg or cargar_config()
    if dfs is None:
        exchange = exchange or datos.crear_exchange_publico(cfg.get('exchange', 'kraken'))
        dfs = {}
        fb = cfg.get('filtro_btc', {})
        lista = list(cfg['pares'])
        if fb.get('enabled', True) and fb.get('par', 'BTC/USD') not in lista:
            lista.append(fb.get('par', 'BTC/USD'))  # solo para el filtro
        for par in pares_disponibles(exchange, lista):
            try:
                df = datos.descargar_velas(exchange, par, cfg['timeframe'], cfg['velas'],
                                           dir_cache=cfg.get('dir_cache_velas'))
                if cfg['estrategia'].get('semilla_1d') and len(df) < cfg['estrategia'].get('min_velas_ema', 0):
                    df = datos.con_semilla(df, datos.descargar_semilla_1d(exchange, par))
                dfs[par] = df
            except Exception as e:
                log.error('No se pudieron descargar velas de %s: %s (se omite este paso)', par, e)
    ind, n_velas = {}, {}
    for par, df in dfs.items():
        ce = config_par(cfg, par)['estrategia']
        n_velas[par] = len(df)
        n_sint = int(df['sintetica'].sum()) if 'sintetica' in df else 0
        if n_sint:
            log.info('%s: %d velas 12h reales + %d aproximadas desde 1d para calentar la EMA.', par, len(df) - n_sint, n_sint)
        d = calcular_indicadores(df.drop(columns='sintetica', errors='ignore'), ce)
        if 'sintetica' in df:
            d = d[~df['sintetica'].reindex(d.index).fillna(False).astype(bool)]
        if d.empty:
            log.warning('%s: historia insuficiente para indicadores (EMA200), se omite.', par)
            continue
        if n_velas[par] < ce.get('min_velas_ema', 0):
            log.warning('%s: solo %d velas (< min_velas_ema=%d): no abre posiciones nuevas hasta tener mas historia.',
                        par, n_velas[par], ce['min_velas_ema'])
        ind[par] = d

    fb = cfg.get('filtro_btc', {})
    par_btc = fb.get('par', 'BTC/USD')
    d_btc = ind.get(par_btc)
    if par_btc not in cfg['pares']:
        ind.pop(par_btc, None)  # BTC solo como filtro, no se opera
    ok_btc, motivo_btc, _ = regimen_btc(d_btc, None, fb)
    log.info('Regimen BTC actual: %s', motivo_btc)

    vivos_cache = dict(precios_vivos or {})

    def vivo(par):
        if par not in vivos_cache:
            vivos_cache[par] = datos.precio_vivo(exchange, par) if exchange is not None else None
        return vivos_cache[par]

    estado = est.cargar(cfg['ruta_estado'], cfg)
    ejecutor = crear_ejecutor(cfg)

    # Agrupar velas pendientes de todos los pares por fecha
    por_fecha = defaultdict(list)
    for par, d in ind.items():
        for fecha, fila in velas_pendientes(d, estado.get('ultima_vela', {}).get(par)).iterrows():
            por_fecha[fecha].append((par, fila))
    precios = {par: float(p['ultimo_precio']) for par, p in posiciones(estado).items() if p.get('ultimo_precio')}
    eventos_recientes = []
    for fecha in sorted(por_fecha):
        for par, fila in por_fecha[fecha]:
            precios[par] = float(fila['close'])
        for par, fila in sorted(por_fecha[fecha], key=lambda x: x[0]):
            cp = config_par(cfg, par)
            sen = evaluar_senales(fila, cp['estrategia'])
            precio_entrada = None
            if sen['entrada'] and par not in posiciones(estado):
                bloqueo = None
                ok_r, motivo_r, _ = regimen_btc(d_btc, fecha, fb)
                if not ok_r:
                    bloqueo = motivo_r
                elif n_velas.get(par, 0) < cp['estrategia'].get('min_velas_ema', 0):
                    bloqueo = f"historia insuficiente ({n_velas[par]} < {cp['estrategia']['min_velas_ema']} velas)"
                else:
                    ok_e, motivo = revisar_entrada(fecha, float(fila['close']), None, ahora,
                                                   dict(cp['entrada'], usar_precio_vivo=False), cfg['timeframe'])
                    if ok_e and cp['entrada'].get('usar_precio_vivo', True):
                        pv = vivo(par)
                        ok_e, motivo = revisar_entrada(fecha, float(fila['close']), pv, ahora, cp['entrada'], cfg['timeframe'])
                        precio_entrada = pv
                    if not ok_e:
                        bloqueo = motivo
                if bloqueo:
                    sen = dict(sen, entrada=False)
                    e = {'tipo': 'entrada_bloqueada', 'par': par, 'motivo': bloqueo}
                    log.info('%s %s', fecha, e)
                    if fecha == ind[par].index[-1]:
                        eventos_recientes.append(dict(e, precio_ref=float(fila['close'])))
            ev = procesar_vela(estado, par, fila, fecha, sen, cp, precios, precio_entrada=precio_entrada)
            for e in ev:
                log.info('%s %s', fecha, e)
            if fecha == ind[par].index[-1]:
                eventos_recientes += [dict(e, precio_ref=float(e.get('precio') or fila['close'])) for e in ev]
        ev_ks, _ = revisar_kill_switch(estado, precios, fecha, cfg)
        for e in ev_ks:
            log.warning('%s %s', fecha, e)
        eventos_recientes += [dict(e, precio_ref=precios.get(e.get('par'), 0)) for e in ev_ks]

    if ejecutor:  # ordenes reales solo para eventos de la vela mas reciente
        for e in eventos_recientes:
            try:
                if e['tipo'] == 'compra':
                    ejecutor.comprar(e['par'], e['cantidad'], e['precio_ref'])
                elif e['tipo'] == 'venta':
                    ejecutor.vender(e['par'], e['cantidad'], e['precio_ref'])
            except Exception as ex:
                log.error('Fallo orden real %s %s: %s', e['tipo'], e.get('par'), ex)

    est.guardar(cfg['ruta_estado'], estado)
    resumen = construir_resumen(cfg, estado, ind, precios, eventos_recientes, bool(ejecutor), motivo_btc)
    print(resumen)
    nombre = cfg['drive']['nombre_resumen']
    with open(nombre, 'w', encoding='utf-8') as f:
        f.write(resumen)
    if subir:
        notificador.subir_texto(resumen, nombre, cfg['drive']['folder_id'])
        # senal_sol.txt se mantiene porque gestion_riesgo.py lo lee
        notificador.subir_texto(resumen, cfg['drive']['nombre_senal'], cfg['drive']['folder_id'])
    else:
        with open(cfg['drive']['nombre_senal'], 'w', encoding='utf-8') as f:
            f.write(resumen)
    return estado, resumen


def construir_resumen(cfg, estado, ind, precios, eventos, live, regimen_btc_txt=None):
    ok = lambda b: 'OK' if b else 'NO'
    eq = equity(estado, precios)
    dd = (estado['pico_capital'] - eq) / estado['pico_capital'] * 100 if estado['pico_capital'] > 0 else 0
    L = [f"Actualizado: {max((d.index[-1] for d in ind.values()), default='-')}",
         f"Modo: {'LIVE' if live else 'PAPER (sin dinero real)'}",
         f"Capital simulado (equity): ${eq:.2f} | efectivo ${estado['cash']:.2f} | posiciones abiertas: {len(posiciones(estado))}",
         'Kill switch: ' + (f"ACTIVO desde {estado['kill_switch_fecha']} - entradas nuevas bloqueadas" if estado['kill_switch_activo']
                            else f"inactivo (caida actual: {dd:.1f}% de {cfg['riesgo']['kill_switch_pct']:.0f}% umbral)"),
         f"Regimen BTC: {regimen_btc_txt or '-'}",
         f"Comisiones acumuladas: ${estado.get('comisiones_totales', 0):.2f} | Operaciones cerradas: {len(estado['historial'])}", '']
    # Bloque SOL primero: gestion_riesgo.py busca 'Precio SOL:' y 'Señal de entrada completa:'
    orden = sorted(ind, key=lambda p: (p.split('/')[0] != 'SOL', p))
    for par in orden:
        u = ind[par].iloc[-1]
        s = evaluar_senales(u, config_par(cfg, par)['estrategia'])
        base = par.split('/')[0]
        L.append(f"== {par} ==")
        L.append(f"Precio {base}: ${u['close']:.8g}")
        L.append(f"Tendencia {ok(s['trend_up'])} | Momentum {ok(s['momentum_ok'])} | RSI {u['RSI']:.1f} {ok(s['rsi_ok'])} | MACD {ok(s['macd_bullish'])}")
        L.append(f"Señal de entrada completa: {'SI' if s['entrada'] else 'NO'}")
        p = posiciones(estado).get(par)
        if p:
            pnl = (u['close'] - p['precio_entrada']) / p['precio_entrada'] * 100
            L.append(f"Estado: EN POSICION desde {p['fecha_entrada']} a ${p['precio_entrada']:.8g} "
                     f"({pnl:+.2f}%), stop ${p['stop']:.8g}" + (f", TP ${p['take_profit']:.8g}" if p.get('take_profit') else ''))
        else:
            L.append('Estado: SIN POSICION')
        for e in eventos:
            if e.get('par') == par:
                L.append(f"Evento: {e['tipo']} " + (f"a ${e['precio']:.8g}" if 'precio' in e else '') + (f" ({e['motivo']})" if 'motivo' in e else ''))
        L.append('')
    omitidos = [p for p in cfg['pares'] if p not in ind]
    if omitidos:
        L.append('Pares omitidos (no disponibles en Kraken o sin datos): ' + ', '.join(omitidos))
    return '\n'.join(L) + '\n'

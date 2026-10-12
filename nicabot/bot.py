"""Paso del bot multi-activo: descarga velas cerradas de cada par, procesa las
nuevas en orden cronologico (efectivo compartido) y guarda el estado."""
import logging
from collections import defaultdict

import pandas as pd

from . import datos, estado as est, notificador
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


def ejecutar(cfg=None, exchange=None, dfs=None, subir=True):
    """dfs: {par: DataFrame OHLCV} para pruebas sin red; si es None se descarga de Kraken."""
    cfg = cfg or cargar_config()
    if dfs is None:
        exchange = exchange or datos.crear_exchange_publico(cfg.get('exchange', 'kraken'))
        dfs = {}
        for par in pares_disponibles(exchange, cfg['pares']):
            try:
                dfs[par] = datos.descargar_velas(exchange, par, cfg['timeframe'], cfg['velas'],
                                                 dir_cache=cfg.get('dir_cache_velas'))
            except Exception as e:
                log.error('No se pudieron descargar velas de %s: %s (se omite este paso)', par, e)
    ind = {}
    for par, df in dfs.items():
        d = calcular_indicadores(df, config_par(cfg, par)['estrategia'])
        if d.empty:
            log.warning('%s: historia insuficiente para indicadores (EMA200), se omite.', par)
            continue
        ind[par] = d

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
            ev = procesar_vela(estado, par, fila, fecha, evaluar_senales(fila, config_par(cfg, par)['estrategia']),
                               config_par(cfg, par), precios)
            for e in ev:
                log.info('%s %s', fecha, e)
            if fecha == ind[par].index[-1]:
                eventos_recientes += [dict(e, precio_ref=float(fila['close'])) for e in ev]
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
    resumen = construir_resumen(cfg, estado, ind, precios, eventos_recientes, bool(ejecutor))
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


def construir_resumen(cfg, estado, ind, precios, eventos, live):
    ok = lambda b: 'OK' if b else 'NO'
    eq = equity(estado, precios)
    dd = (estado['pico_capital'] - eq) / estado['pico_capital'] * 100 if estado['pico_capital'] > 0 else 0
    L = [f"Actualizado: {max((d.index[-1] for d in ind.values()), default='-')}",
         f"Modo: {'LIVE' if live else 'PAPER (sin dinero real)'}",
         f"Capital simulado (equity): ${eq:.2f} | efectivo ${estado['cash']:.2f} | posiciones abiertas: {len(posiciones(estado))}",
         'Kill switch: ' + (f"ACTIVO desde {estado['kill_switch_fecha']} - entradas nuevas bloqueadas" if estado['kill_switch_activo']
                            else f"inactivo (caida actual: {dd:.1f}% de {cfg['riesgo']['kill_switch_pct']:.0f}% umbral)"),
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

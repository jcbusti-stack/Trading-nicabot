"""Vigilancia de stops entre velas (solo PAPER).

Revisa cada posicion abierta contra el precio VIVO del ticker publico de
Kraken y, si toco stop-loss, take-profit o trailing, la cierra en paper al
precio vivo (relleno realista: con slippage y comision, igual que el motor).
No abre posiciones nuevas y nunca envia ordenes reales.
"""
import logging

import pandas as pd

from . import datos, estado as est, riesgo as R
from .config import cargar_config, config_par
from .motor import _cerrar, _venta_parcial, posiciones, revisar_kill_switch

log = logging.getLogger('nicabot.vigilancia')


def revisar_posiciones(estado, precios_vivos, cfg, ahora=None):
    """precios_vivos: {par: precio}. Devuelve lista de eventos."""
    fecha = str(pd.Timestamp(ahora) if ahora is not None else pd.Timestamp.now(tz='UTC'))
    eventos = []
    for par in list(posiciones(estado)):
        precio = precios_vivos.get(par)
        if not precio:
            log.warning('%s: sin precio vivo, no se revisa.', par)
            continue
        cp = config_par(cfg, par)
        pos = posiciones(estado)[par]
        if precio <= pos['stop']:
            motivo = 'trailing-stop' if pos.get('trailing_movido') else 'stop-loss'
            eventos.append(_cerrar(estado, par, precio, fecha, motivo + ' (vigilancia)', cp))
        elif pos.get('take_profit') and precio >= pos['take_profit']:
            eventos.append(_cerrar(estado, par, precio, fecha, 'take-profit (vigilancia)', cp))
        else:
            if pos.get('tp_parcial') and not pos.get('parcial_hecho') and precio >= pos['tp_parcial']:
                eventos.append(_venta_parcial(estado, par, pos, precio, fecha, cp))
            pos['ultimo_precio'] = float(precio)
            pos['precio_maximo'] = max(pos.get('precio_maximo') or 0, float(precio))
            if cp['riesgo'].get('trailing_solo_tras_parcial') and not pos.get('parcial_hecho'):
                continue
            nuevo = R.actualizar_trailing(pos['stop'], pos['precio_maximo'], pos.get('atr'), cp['riesgo'])
            if nuevo > pos['stop']:
                pos['stop'], pos['trailing_movido'] = nuevo, True
    for e in eventos:
        log.warning('%s', e)
    if posiciones(estado) or eventos:
        precios = {p: precios_vivos.get(p) or v.get('ultimo_precio') for p, v in posiciones(estado).items()}
        ev_ks, _ = revisar_kill_switch(estado, precios, fecha, cfg)
        eventos += ev_ks
    return eventos


def ejecutar(cfg=None, exchange=None, precios_vivos=None, ahora=None):
    cfg = cfg or cargar_config()
    estado = est.cargar(cfg['ruta_estado'], cfg)
    abiertas = list(posiciones(estado))
    if not abiertas:
        log.info('Sin posiciones abiertas: nada que vigilar.')
        return estado, []
    if precios_vivos is None:
        exchange = exchange or datos.crear_exchange_publico(cfg.get('exchange', 'kraken'))
        precios_vivos = {p: datos.precio_vivo(exchange, p) for p in abiertas}
    eventos = revisar_posiciones(estado, precios_vivos, cfg, ahora)
    est.guardar(cfg['ruta_estado'], estado)
    log.info('Vigilancia: %d posiciones revisadas, %d eventos.', len(abiertas), len(eventos))
    return estado, eventos

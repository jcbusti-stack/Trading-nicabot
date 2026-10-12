"""Lectura/escritura de estado.json con migracion de formatos anteriores.

v1 (original, solo SOL): en_posicion, cash (capital total; no se descontaba
al entrar), precio_entrada, fecha_entrada, historial, pico_capital,
kill_switch_activo, kill_switch_fecha.
v3 (multi-activo): version, cash (efectivo libre compartido), pico_capital
(sobre equity), kill_switch_*, historial (cada operacion con 'par'),
comisiones_totales, posiciones {par: {...}}, ultima_vela {par: fecha}.
Los campos v1 de nivel superior (en_posicion, precio_entrada, fecha_entrada)
se siguen escribiendo como resumen de SOL para compatibilidad de lectura.
"""
import json
import logging
import os
import tempfile

log = logging.getLogger('nicabot.estado')
VERSION = 3
PAR_V1 = 'SOL/USD'  # el estado v1 era SOL/USDT de OKX; ahora se opera SOL/USD en Kraken


def estado_inicial(capital):
    return {'version': VERSION, 'cash': float(capital), 'historial': [], 'pico_capital': float(capital),
            'kill_switch_activo': False, 'kill_switch_fecha': None, 'comisiones_totales': 0.0,
            'posiciones': {}, 'ultima_vela': {},
            'en_posicion': False, 'precio_entrada': None, 'fecha_entrada': None}


def migrar(estado, cfg):
    if estado.get('version', 1) >= VERSION:
        return estado
    log.info('Migrando estado.json v%s -> v%s', estado.get('version', 1), VERSION)
    nuevo = estado_inicial(estado.get('cash', cfg['capital_inicial']))
    for k in ('pico_capital', 'kill_switch_activo', 'kill_switch_fecha', 'comisiones_totales'):
        if k in estado:
            nuevo[k] = estado[k]
    nuevo['historial'] = [dict(t, par=t.get('par', PAR_V1)) for t in estado.get('historial', [])]
    if estado.get('en_posicion') and estado.get('precio_entrada'):
        pe = estado['precio_entrada']
        qty = estado.get('cantidad') or estado['cash'] / pe  # en v1 era 100% del capital
        nuevo['posiciones'][PAR_V1] = {
            'precio_entrada': pe, 'fecha_entrada': estado.get('fecha_entrada'), 'cantidad': qty,
            'stop': estado.get('stop') or pe * (1 - cfg['riesgo']['stop_pct'] / 100),
            'take_profit': estado.get('take_profit'), 'precio_maximo': pe,
            'costo_entrada': qty * pe, 'ultimo_precio': pe}
        nuevo['cash'] = 0.0 if not estado.get('cantidad') else estado['cash']
    sincronizar_campos_v1(nuevo)
    return nuevo


def sincronizar_campos_v1(estado):
    p = estado.get('posiciones', {}).get(PAR_V1)
    estado['en_posicion'] = bool(p)
    estado['precio_entrada'] = p['precio_entrada'] if p else None
    estado['fecha_entrada'] = p['fecha_entrada'] if p else None


def cargar(ruta, cfg):
    if not os.path.exists(ruta):
        return estado_inicial(cfg['capital_inicial'])
    with open(ruta, encoding='utf-8') as f:
        return migrar(json.load(f), cfg)


def guardar(ruta, estado):
    sincronizar_campos_v1(estado)
    d = os.path.dirname(os.path.abspath(ruta))
    fd, tmp = tempfile.mkstemp(dir=d, suffix='.tmp')  # escritura atomica
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(estado, f, indent=2)
    os.replace(tmp, ruta)

"""Motor de simulacion vela a vela, multi-activo con efectivo compartido.
Lo usan tanto el bot paper como el backtest, asi ambos se comportan igual.

Orden dentro de cada vela de un par (conservador):
 1. Si hay posicion: stop contra el MINIMO de la vela (si abrio por debajo se
    llena al open = gap). Luego take-profit contra el MAXIMO. Luego salida por
    senal al cierre. Despues se actualiza el trailing con el maximo.
 2. Si no hay posicion y hay senal de entrada: compra al cierre si el kill
    switch esta inactivo y los limites del portafolio lo permiten.
El kill switch (drawdown sobre equity total) se revisa con revisar_kill_switch().
Todas las ejecuciones aplican slippage y comision.
"""
import math

from . import riesgo as R


def _num(x):
    try:
        x = float(x)
        return None if math.isnan(x) else x
    except (TypeError, ValueError):
        return None


def posiciones(estado):
    return estado.setdefault('posiciones', {})


def equity(estado, precios):
    """Efectivo + valor de todas las posiciones. precios: {par: precio}."""
    total = estado['cash']
    for par, p in posiciones(estado).items():
        total += p['cantidad'] * precios.get(par, p.get('ultimo_precio') or p['precio_entrada'])
    return total


def exposicion(estado):
    return sum(p['cantidad'] * (p.get('ultimo_precio') or p['precio_entrada']) for p in posiciones(estado).values())


def _cerrar(estado, par, precio_bruto, fecha, motivo, cfg):
    c = cfg['costos']
    pos = posiciones(estado).pop(par)
    precio = R.precio_con_slippage(precio_bruto, 'venta', c['slippage_pct'])
    bruto = pos['cantidad'] * precio
    comision = bruto * c['comision_pct'] / 100
    estado['cash'] += bruto - comision
    estado['comisiones_totales'] = estado.get('comisiones_totales', 0.0) + comision
    pnl_usd = bruto - comision - pos['costo_entrada']
    estado['historial'].append({
        'par': par, 'entrada': round(pos['precio_entrada'], 8), 'salida': round(precio, 8),
        'fecha_entrada': pos['fecha_entrada'], 'fecha_salida': fecha,
        'resultado_pct': round((precio - pos['precio_entrada']) / pos['precio_entrada'] * 100, 2),
        'pnl_usd': round(pnl_usd, 2), 'cantidad': pos['cantidad'], 'motivo': motivo,
    })
    return {'tipo': 'venta', 'par': par, 'precio': precio, 'cantidad': pos['cantidad'],
            'motivo': motivo, 'pnl_usd': pnl_usd}


def procesar_vela(estado, par, fila, fecha, senales, cfg, precios=None, precio_entrada=None):
    """Avanza un par una vela. cfg = config efectiva del par (config_par).
    precios: ultimos precios conocidos de todos los pares (para el equity).
    precio_entrada: si se da (precio vivo del ticker), la compra se llena ahi y no al cierre."""
    rc, c, pf = cfg['riesgo'], cfg['costos'], cfg['portafolio']
    eventos = []
    fecha = str(fecha)
    pos = posiciones(estado).get(par)

    if pos:
        if fila['low'] <= pos['stop']:
            eventos.append(_cerrar(estado, par, min(fila['open'], pos['stop']), fecha, 'stop-loss', cfg))
        elif pos.get('take_profit') and fila['high'] >= pos['take_profit']:
            eventos.append(_cerrar(estado, par, max(fila['open'], pos['take_profit']), fecha, 'take-profit', cfg))
        elif senales['salida']:
            eventos.append(_cerrar(estado, par, fila['close'], fecha, 'senal', cfg))
        else:
            pos['precio_maximo'] = max(pos.get('precio_maximo') or 0, fila['high'])
            pos['stop'] = R.actualizar_trailing(pos['stop'], pos['precio_maximo'], fila.get('ATR'), rc)
            pos['ultimo_precio'] = float(fila['close'])

    if not posiciones(estado).get(par) and not eventos and senales['entrada']:
        if estado['kill_switch_activo']:
            eventos.append({'tipo': 'entrada_bloqueada', 'par': par, 'motivo': 'kill switch'})
        elif len(posiciones(estado)) >= pf['max_posiciones_abiertas']:
            eventos.append({'tipo': 'entrada_bloqueada', 'par': par, 'motivo': 'max posiciones'})
        else:
            ref = float(precio_entrada) if precio_entrada else float(fila['close'])
            precio = R.precio_con_slippage(ref, 'compra', c['slippage_pct'])
            stop = R.precio_stop(precio, fila.get('ATR'), rc)
            pr = dict(precios or {}); pr[par] = ref
            eq = equity(estado, pr)
            max_activo = min(pf['max_por_activo_pct'], rc.get('max_por_activo_pct', 100.0), rc['max_exposicion_pct'])
            libre_total = max(0.0, eq * pf['max_exposicion_total_pct'] / 100 - exposicion(estado))
            limite_nocional = min(eq * max_activo / 100, libre_total)
            qty = R.tamano_posicion(eq, estado['cash'], precio, stop, rc, c['comision_pct'])
            qty = min(qty, limite_nocional / precio)
            if qty * precio >= 1.0:  # ignora posiciones de menos de 1 USDT
                nocional = qty * precio
                comision = nocional * c['comision_pct'] / 100
                estado['cash'] -= nocional + comision
                estado['comisiones_totales'] = estado.get('comisiones_totales', 0.0) + comision
                posiciones(estado)[par] = {
                    'precio_entrada': precio, 'fecha_entrada': fecha, 'cantidad': qty, 'stop': stop,
                    'precio_maximo': precio, 'take_profit': R.precio_take_profit(precio, stop, rc),
                    'costo_entrada': nocional + comision, 'ultimo_precio': ref,
                    'atr': _num(fila.get('ATR'))}
                eventos.append({'tipo': 'compra', 'par': par, 'precio': precio, 'cantidad': qty, 'stop': stop,
                                'take_profit': posiciones(estado)[par]['take_profit'], 'nocional': nocional})
            else:
                eventos.append({'tipo': 'entrada_bloqueada', 'par': par, 'motivo': 'sin cupo/efectivo'})
    estado.setdefault('ultima_vela', {})[par] = fecha
    return eventos


def revisar_kill_switch(estado, precios, fecha, cfg):
    """Drawdown sobre equity total (incluye posiciones abiertas)."""
    eq = equity(estado, precios)
    estado['pico_capital'] = max(estado.get('pico_capital') or 0.0, eq)
    dd = R.drawdown_pct(estado['pico_capital'], eq)
    eventos = []
    if dd >= cfg['riesgo']['kill_switch_pct'] and not estado['kill_switch_activo']:
        estado['kill_switch_activo'] = True
        estado['kill_switch_fecha'] = str(fecha)
        eventos.append({'tipo': 'kill_switch', 'drawdown_pct': dd})
        if cfg['riesgo'].get('kill_switch_cierra_posicion'):
            for par in list(posiciones(estado)):
                eventos.append(_cerrar(estado, par, precios.get(par, posiciones(estado)[par]['ultimo_precio']),
                                       str(fecha), 'kill-switch', cfg))
    return eventos, dd

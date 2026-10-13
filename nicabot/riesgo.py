"""Calculo de stop, take-profit, trailing, tamano de posicion y costos."""


def precio_stop(precio_entrada, atr, cfg_riesgo):
    if cfg_riesgo['stop_modo'] == 'atr' and atr and atr > 0:
        return precio_entrada - cfg_riesgo['stop_atr_mult'] * atr
    return precio_entrada * (1 - cfg_riesgo['stop_pct'] / 100)


def precio_take_profit(precio_entrada, stop, cfg_riesgo):
    r = cfg_riesgo.get('take_profit_r')
    if not r:
        return None
    return precio_entrada + r * (precio_entrada - stop)


def precio_tp_parcial(precio_entrada, stop, cfg_riesgo):
    """Precio de la toma parcial (riesgo.tp_parcial_r en R). None si esta apagada."""
    r = cfg_riesgo.get('tp_parcial_r')
    if not r or not cfg_riesgo.get('tp_parcial_frac'):
        return None
    return precio_entrada + r * (precio_entrada - stop)


def actualizar_trailing(stop_actual, precio_maximo, atr, cfg_riesgo):
    mult = cfg_riesgo.get('trailing_atr_mult')
    if not mult or not atr or (isinstance(atr, float) and atr != atr):
        return stop_actual
    return max(stop_actual, precio_maximo - mult * atr)


def tamano_posicion(equity, cash, precio_entrada, stop, cfg_riesgo, comision_pct=0.0):
    """Cantidad de unidades tal que (entrada - stop) * qty = riesgo% del equity.
    Limitado por max_exposicion_pct y por el efectivo disponible (incl. comision)."""
    distancia = precio_entrada - stop
    if distancia <= 0 or precio_entrada <= 0:
        return 0.0
    riesgo_usd = equity * cfg_riesgo['riesgo_por_operacion_pct'] / 100
    qty = riesgo_usd / distancia
    max_nocional = min(equity * cfg_riesgo['max_exposicion_pct'] / 100, cash / (1 + comision_pct / 100))
    return max(0.0, min(qty, max_nocional / precio_entrada))


def precio_con_slippage(precio, lado, slippage_pct):
    f = slippage_pct / 100
    return precio * (1 + f) if lado == 'compra' else precio * (1 - f)


def drawdown_pct(pico, equity):
    return (pico - equity) / pico * 100 if pico > 0 else 0.0

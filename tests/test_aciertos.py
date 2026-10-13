import copy

import pandas as pd
import pytest

from nicabot import investigacion as INV
from nicabot.config import DEFECTO
from nicabot.estado import estado_inicial
from nicabot.estrategia import evaluar_senales
from nicabot.motor import procesar_vela
from nicabot.riesgo import precio_tp_parcial

SIN = {'entrada': False, 'salida': False}
COMPRA = {'entrada': True, 'salida': False}


def cfg(**riesgo):
    c = copy.deepcopy(DEFECTO)
    c['costos'] = {'comision_pct': 0.0, 'slippage_pct': 0.0}
    c['riesgo'].update(riesgo)
    return c


def vela(o, h, l, c):
    return {'open': o, 'high': h, 'low': l, 'close': c, 'ATR': 2.0}


def abrir(c):
    e = estado_inicial(1000.0)
    procesar_vela(e, 'X', vela(100, 100, 100, 100), 't0', COMPRA, c)
    return e


def test_tp_parcial_precio():
    assert precio_tp_parcial(100, 95, {'tp_parcial_r': 1.0, 'tp_parcial_frac': 0.5}) == 105
    assert precio_tp_parcial(100, 95, {'tp_parcial_r': None}) is None


def test_venta_parcial_y_breakeven_cuenta_como_una_operacion_ganadora():
    c = cfg(tp_parcial_r=1.0, tp_parcial_frac=0.5, breakeven_offset_pct=0.0)
    e = abrir(c)
    q0 = e['posiciones']['X']['cantidad']
    ev = procesar_vela(e, 'X', vela(101, 106, 101, 104), 't1', SIN, c)
    assert ev[0]['tipo'] == 'venta_parcial'
    p = e['posiciones']['X']
    assert p['cantidad'] == pytest.approx(q0 / 2) and p['stop'] == pytest.approx(100.0)
    procesar_vela(e, 'X', vela(101, 101, 99, 99), 't2', SIN, c)
    h = e['historial']
    assert len(h) == 1 and h[0]['motivo'] == 'break-even' and h[0]['pnl_usd'] > 0 and h[0]['parcial']
    assert e['cash'] == pytest.approx(1000 + h[0]['pnl_usd'])


def test_salida_por_tiempo():
    c = cfg(max_velas_posicion=2, take_profit_r=None)
    e = abrir(c)
    procesar_vela(e, 'X', vela(100, 101, 99.5, 100), 't1', SIN, c)
    assert 'X' in e['posiciones']
    procesar_vela(e, 'X', vela(100, 101, 99.5, 100.5), 't2', SIN, c)
    assert e['historial'][-1]['motivo'] == 'tiempo'


def test_sin_parcial_comportamiento_original():
    c = cfg()
    e = abrir(c)
    assert e['posiciones']['X']['tp_parcial'] is None
    procesar_vela(e, 'X', vela(101, 106, 101, 104), 't1', SIN, c)
    assert e['posiciones']['X']['cantidad'] > 0 and not e['historial']


def fila(**kw):
    f = {'open': 108, 'high': 111, 'low': 105, 'close': 110, 'EMA_200': 100, 'MACD': 1, 'MACD_signal': 0,
         'RSI': 50, 'EMA_9': 106, 'EMA_21': 105, 'ATR': 2, 'RSI_C': 50, 'SMA_SAL': 109}
    f.update(kw)
    return f


def ce(**kw):
    c = copy.deepcopy(DEFECTO['estrategia'])
    c.update(kw)
    return c


def test_modos_de_entrada():
    assert evaluar_senales(fila(), ce())['entrada']
    assert evaluar_senales(fila(), ce(modo_entrada='pullback'))['entrada']
    assert not evaluar_senales(fila(low=108, RSI=60), ce(modo_entrada='pullback'))['entrada']
    assert not evaluar_senales(fila(), ce(modo_entrada='reversion'))['entrada']
    r = evaluar_senales(fila(RSI_C=5, close=104, SMA_SAL=107, MACD=-1), ce(modo_entrada='reversion'))
    assert r['entrada']
    assert evaluar_senales(fila(close=108, SMA_SAL=107), ce(modo_entrada='reversion'))['salida']
    assert evaluar_senales(fila(RSI_C=5, close=104, SMA_SAL=107, MACD=-1), ce(modo_entrada='mixto'))['entrada']
    with pytest.raises(ValueError):
        evaluar_senales(fila(), ce(modo_entrada='otro'))


def test_extension_y_salida_macd():
    assert not evaluar_senales(fila(close=115), ce(max_extension_atr=2.0))['entrada']
    assert evaluar_senales(fila(MACD=-1), ce())['salida']
    assert not evaluar_senales(fila(MACD=-1), ce(salida_macd=False))['salida']


def test_elegir_variante():
    ag = {'a': {'ops': 200, 'win_rate': 55, 'profit_factor': 1.1, 'expectativa_usd': 1, 'retorno_prom_pct': 1},
          'b': {'ops': 200, 'win_rate': 51, 'profit_factor': 1.25, 'expectativa_usd': 1, 'retorno_prom_pct': 1},
          'c': {'ops': 200, 'win_rate': 40, 'profit_factor': 2.0, 'expectativa_usd': 3, 'retorno_prom_pct': 5},
          'd': {'ops': 20, 'win_rate': 70, 'profit_factor': 3.0, 'expectativa_usd': 3, 'retorno_prom_pct': 5}}
    assert INV.elegir(ag, 1.3) == 'b'
    assert INV.elegir({'c': ag['c']}, 1.3) is None


def test_vigilancia_hace_venta_parcial():
    from nicabot import vigilancia as VG
    c = cfg(tp_parcial_r=1.0, tp_parcial_frac=0.5)
    c['pares'] = ['X']
    e = abrir(c)
    ev = VG.revisar_posiciones(e, {'X': 106.0}, c)
    assert any(x['tipo'] == 'venta_parcial' for x in ev) and e['posiciones']['X']['parcial_hecho']

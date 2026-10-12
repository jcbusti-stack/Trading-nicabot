import copy
import json

import numpy as np
import pandas as pd
import pytest

from nicabot import ejecucion, estado as est
from nicabot.backtest import correr_backtest
from nicabot.config import DEFECTO
from nicabot.indicadores import calcular_indicadores
from nicabot.motor import equity, procesar_vela, revisar_kill_switch
from nicabot import bot
from nicabot.config import config_par
from nicabot import riesgo as R


@pytest.fixture
def cfg():
    c = copy.deepcopy(DEFECTO)
    return c


def velas(precios):
    idx = pd.date_range('2025-01-01', periods=len(precios), freq='12h', tz='UTC')
    p = np.array(precios, dtype=float)
    return pd.DataFrame({'open': p, 'high': p * 1.01, 'low': p * 0.99, 'close': p, 'volume': 100.0}, index=idx)


def fila(o, h, l, c, atr=2.0):
    return pd.Series({'open': o, 'high': h, 'low': l, 'close': c, 'ATR': atr})


def P(s):
    return s['posiciones']['SOL/USD']


SIN = {'entrada': False, 'salida': False}
ENTRA = {'entrada': True, 'salida': False}


def test_indicadores_basicos():
    df = calcular_indicadores(velas(np.linspace(100, 200, 300)))
    u = df.iloc[-1]
    assert u['EMA_9'] > u['EMA_21'] > u['EMA_200']
    assert u['RSI'] == pytest.approx(100.0)  # solo subidas
    assert u['ATR'] > 0


def test_tamano_por_riesgo(cfg):
    qty = R.tamano_posicion(1000, 1000, 100, 95, cfg['riesgo'])
    assert qty * 5 == pytest.approx(10)  # 1% de 1000 = $10 de riesgo
    # limitado por efectivo
    assert R.tamano_posicion(1000, 50, 100, 99.9, cfg['riesgo']) * 100 <= 50


def test_entrada_con_comision_y_slippage(cfg):
    s = est.estado_inicial(1000)
    ev = procesar_vela(s, 'SOL/USD', fila(100, 101, 99, 100), 't1', ENTRA, cfg)
    assert ev[0]['tipo'] == 'compra'
    assert P(s)['precio_entrada'] == pytest.approx(100.05)
    assert s['cash'] < 1000 - P(s)['cantidad'] * 100.05  # pago comision
    assert P(s)['take_profit'] == pytest.approx(100.05 + 3 * (100.05 - P(s)['stop']))


def test_stop_contra_minimo_de_vela(cfg):
    s = est.estado_inicial(1000)
    procesar_vela(s, 'SOL/USD', fila(100, 101, 99, 100), 't1', ENTRA, cfg)
    stop = P(s)['stop']
    # cierra arriba del stop pero el minimo lo toca -> debe salir por stop
    ev = procesar_vela(s, 'SOL/USD', fila(100, 101, stop - 1, 100), 't2', SIN, cfg)
    assert ev[0]['motivo'] == 'stop-loss'
    assert not s['posiciones']
    assert s['historial'][-1]['pnl_usd'] < -9  # ~1% de riesgo + costos


def test_take_profit(cfg):
    s = est.estado_inicial(1000)
    procesar_vela(s, 'SOL/USD', fila(100, 101, 99, 100), 't1', ENTRA, cfg)
    ev = procesar_vela(s, 'SOL/USD', fila(100, P(s)['take_profit'] + 1, 99, 101), 't2', SIN, cfg)
    assert ev[0]['motivo'] == 'take-profit'


def test_trailing(cfg):
    cfg['riesgo']['trailing_atr_mult'] = 2.0
    s = est.estado_inicial(1000)
    procesar_vela(s, 'SOL/USD', fila(100, 101, 99, 100), 't1', ENTRA, cfg)
    procesar_vela(s, 'SOL/USD', fila(100, 110, 100, 109), 't2', SIN, cfg)
    assert P(s)['stop'] == pytest.approx(106.0)


def test_kill_switch_sobre_equity(cfg):
    cfg['riesgo']['riesgo_por_operacion_pct'] = 100
    cfg['riesgo']['stop_pct'] = 90
    cfg['portafolio']['max_por_activo_pct'] = 100
    cfg['riesgo']['take_profit_r'] = None
    s = est.estado_inicial(1000)
    procesar_vela(s, 'SOL/USD', fila(100, 101, 99, 100), 't1', ENTRA, cfg)
    procesar_vela(s, 'SOL/USD', fila(80, 80, 70, 70), 't2', SIN, cfg)
    ev, dd = revisar_kill_switch(s, {'SOL/USD': 70}, 't2', cfg)
    assert any(e['tipo'] == 'kill_switch' for e in ev)  # sin cerrar operacion
    assert s['posiciones'] and s['kill_switch_activo'] and dd > 25
    ev = procesar_vela(s, 'BTC/USD', fila(100, 101, 99, 100), 't3', ENTRA, cfg)
    assert ev[0]['tipo'] == 'entrada_bloqueada'


def test_migracion_v1(cfg, tmp_path):
    v1 = {'en_posicion': True, 'cash': 1100.0, 'precio_entrada': 110.0, 'fecha_entrada': 'x',
          'historial': [], 'pico_capital': 1100.0, 'kill_switch_activo': False, 'kill_switch_fecha': None}
    p = tmp_path / 'estado.json'
    p.write_text(json.dumps(v1))
    s = est.cargar(str(p), cfg)
    assert s['version'] == 3 and P(s)['cantidad'] == pytest.approx(10) and s['cash'] == 0
    assert equity(s, {'SOL/USD': 110}) == pytest.approx(1100)
    est.guardar(str(p), s)
    g = json.loads(p.read_text())
    assert g['version'] == 3 and g['en_posicion'] is True


def test_live_bloqueado_sin_salvaguardas(cfg, monkeypatch):
    for k in ('MODO', 'CONFIRMO_ORDENES_REALES', 'KRAKEN_TRADING_API_KEY', 'KRAKEN_TRADING_API_SECRET'):
        monkeypatch.delenv(k, raising=False)
    assert ejecucion.crear_ejecutor(cfg) is None
    monkeypatch.setenv('MODO', 'live')
    assert ejecucion.crear_ejecutor(cfg) is None  # falta confirmacion
    monkeypatch.setenv('CONFIRMO_ORDENES_REALES', 'SI')
    assert ejecucion.crear_ejecutor(cfg) is None  # faltan llaves


def test_live_dry_run_y_tope(cfg, monkeypatch):
    monkeypatch.delenv('DRY_RUN', raising=False)

    class Falso:
        markets = None
        def create_order(self, *a):
            raise AssertionError('no debe enviar en dry-run')
    e = ejecucion.EjecutorKraken(cfg, exchange=Falso())
    r = e.comprar('SOL/USD', 10, 100)  # 1000 USDT pedido, tope 50
    assert r['dry_run'] and r['amount'] * 100 == pytest.approx(50)


def test_backtest_corre(cfg):
    rng = np.random.default_rng(1)
    precios = 100 * np.exp(np.cumsum(rng.normal(0.002, 0.03, 800)))
    df = velas(precios)
    df['high'] = df['close'] * 1.02
    df['low'] = df['close'] * 0.98
    r = correr_backtest({'SOL/USD': df, 'BTC/USD': velas(precios[::-1].copy())}, cfg)
    assert r['pares'] == 2 and r['capital_final'] > 0


def test_limites_portafolio(cfg):
    cfg['portafolio']['max_posiciones_abiertas'] = 2
    s = est.estado_inicial(1000)
    for i, par in enumerate(['A/USDT', 'B/USDT', 'C/USDT']):
        ev = procesar_vela(s, par, fila(100, 101, 99, 100), 't1', ENTRA, cfg)
    assert len(s['posiciones']) == 2 and ev[0]['motivo'] == 'max posiciones'
    for p in s['posiciones'].values():
        assert p['cantidad'] * p['precio_entrada'] <= 1000 * cfg['portafolio']['max_por_activo_pct'] / 100 + 1e-6


def test_ajustes_por_par(cfg):
    cfg['ajustes_por_par'] = {'PEPE/USD': {'riesgo': {'stop_pct': 8.0}}}
    assert config_par(cfg, 'PEPE/USD')['riesgo']['stop_pct'] == 8.0
    assert config_par(cfg, 'BTC/USD')['riesgo']['stop_pct'] == 5.0


def test_paso_bot_offline(cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('MODO', raising=False)
    cfg['ruta_estado'] = str(tmp_path / 'estado.json')
    cfg['pares'] = ['SOL/USD', 'ONDO/USD']
    dfs = {'SOL/USD': velas(np.linspace(100, 200, 300)), 'ONDO/USD': velas(np.linspace(2, 1, 300))}
    estado, resumen = bot.ejecutar(cfg, dfs=dfs, subir=False)
    assert 'Precio SOL:' in resumen and 'ONDO/USD' in resumen
    estado2, _ = bot.ejecutar(cfg, dfs=dfs, subir=False)  # sin velas nuevas: no duplica
    assert len(estado2['historial']) == len(estado['historial'])


def test_agregar_4h_a_12h():
    from nicabot.datos import agregar
    idx = pd.date_range('2025-01-01 04:00', periods=9, freq='4h', tz='UTC')  # empieza a media vela
    df = pd.DataFrame({'open': range(9), 'high': range(1, 10), 'low': range(9), 'close': range(9), 'volume': 1.0},
                      index=idx).astype(float)
    out = agregar(df, '12h', 3)
    assert list(out.index.hour) == [12, 0]  # 00:00 incompleta se descarta
    assert out.iloc[0]['open'] == 2 and out.iloc[0]['close'] == 4 and out.iloc[0]['volume'] == 3

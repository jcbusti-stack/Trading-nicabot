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


# ---- Guardia de entrada, vigilancia, semilla, Drive ----
from nicabot.guardia import revisar_entrada
from nicabot import vigilancia, notificador, datos as D

ENT = {'usar_precio_vivo': True, 'max_horas_desde_cierre': 3.0, 'max_caida_pct': 2.0, 'max_subida_pct': 3.0}
T0 = pd.Timestamp('2026-01-01 00:00', tz='UTC')  # apertura; cierra 12:00


def test_guardia_ok_y_vieja():
    assert revisar_entrada(T0, 100, 101, T0 + pd.Timedelta(hours=13), ENT)[0]
    ok, m = revisar_entrada(T0, 100, 101, T0 + pd.Timedelta(hours=16), ENT)
    assert not ok and 'vieja' in m


def test_guardia_movimiento():
    ahora = T0 + pd.Timedelta(hours=12, minutes=5)
    assert not revisar_entrada(T0, 100, 97.9, ahora, ENT)[0]
    assert not revisar_entrada(T0, 100, 103.5, ahora, ENT)[0]
    assert revisar_entrada(T0, 100, 102.9, ahora, ENT)[0]
    assert not revisar_entrada(T0, 100, None, ahora, ENT)[0]


def test_compra_al_precio_vivo(cfg):
    s = est.estado_inicial(1000.0)
    procesar_vela(s, 'SOL/USD', fila(100, 101, 99, 100), 'f', ENTRA, cfg, precio_entrada=101.0)
    assert P(s)['precio_entrada'] == pytest.approx(101.0 * 1.0005)
    assert P(s)['atr'] == 2.0


def _bot_un_paso(cfg, tmp_path, monkeypatch, vivo, min_velas=0, horas=1):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('MODO', raising=False)
    cfg['ruta_estado'] = str(tmp_path / 'estado.json')
    cfg['pares'] = ['SOL/USD']
    cfg['estrategia']['min_velas_ema'] = min_velas
    cfg['estrategia']['rsi_max_entrada'] = 101
    df = velas(np.linspace(100, 300, 300))
    est.guardar(cfg['ruta_estado'], dict(est.estado_inicial(1000.0), ultima_vela={'SOL/USD': str(df.index[-2])}))
    monkeypatch.setattr(bot, 'evaluar_senales', lambda f, c: {'entrada': True, 'salida': False, 'trend_up': True,
                                                               'momentum_ok': True, 'rsi_ok': True, 'macd_bullish': True})
    ahora = df.index[-1] + pd.Timedelta(hours=12 + horas)
    estado, _ = bot.ejecutar(cfg, dfs={'SOL/USD': df}, subir=False, precios_vivos={'SOL/USD': vivo}, ahora=ahora)
    return estado, float(df['close'].iloc[-1])


def test_bot_entra_a_precio_vivo(cfg, tmp_path, monkeypatch):
    estado, close = _bot_un_paso(cfg, tmp_path, monkeypatch, vivo=303.0)
    assert estado['posiciones']['SOL/USD']['precio_entrada'] == pytest.approx(303 * 1.0005)


def test_bot_salta_si_precio_se_movio(cfg, tmp_path, monkeypatch):
    estado, _ = _bot_un_paso(cfg, tmp_path, monkeypatch, vivo=290.0)
    assert 'SOL/USD' not in estado['posiciones']


def test_bot_salta_senal_vieja(cfg, tmp_path, monkeypatch):
    estado, _ = _bot_un_paso(cfg, tmp_path, monkeypatch, vivo=300.0, horas=5)
    assert 'SOL/USD' not in estado['posiciones']


def test_bot_min_velas_ema(cfg, tmp_path, monkeypatch):
    estado, _ = _bot_un_paso(cfg, tmp_path, monkeypatch, vivo=300.0, min_velas=500)
    assert 'SOL/USD' not in estado['posiciones']


def _con_posicion(cfg, tmp_path):
    cfg['ruta_estado'] = str(tmp_path / 'estado.json')
    cfg['riesgo']['trailing_atr_mult'] = 2.0
    s = est.estado_inicial(1000.0)
    procesar_vela(s, 'SOL/USD', fila(100, 101, 99, 100), 'f', ENTRA, cfg)
    est.guardar(cfg['ruta_estado'], s)
    return P(s)


def test_vigilancia_stop_al_precio_vivo(cfg, tmp_path):
    p = _con_posicion(cfg, tmp_path)
    s, ev = vigilancia.ejecutar(cfg, precios_vivos={'SOL/USD': p['stop'] - 1})
    assert 'SOL/USD' not in s['posiciones']
    assert ev[0]['motivo'].startswith('stop-loss')
    assert ev[0]['precio'] == pytest.approx((p['stop'] - 1) * (1 - 0.0005))  # precio vivo, no el stop
    assert json.load(open(cfg['ruta_estado']))['historial']


def test_vigilancia_take_profit_y_trailing(cfg, tmp_path):
    p = _con_posicion(cfg, tmp_path)
    s, ev = vigilancia.ejecutar(cfg, precios_vivos={'SOL/USD': p['precio_entrada'] * 1.04})
    assert not ev and P(s)['stop'] > p['stop'] and P(s)['trailing_movido']
    s, ev = vigilancia.ejecutar(cfg, precios_vivos={'SOL/USD': P(s)['stop'] * 0.999})
    assert ev[0]['motivo'].startswith('trailing-stop')
    p = _con_posicion(cfg, tmp_path)
    s, ev = vigilancia.ejecutar(cfg, precios_vivos={'SOL/USD': p['take_profit'] + 1})
    assert ev[0]['motivo'].startswith('take-profit')


def test_vigilancia_sin_precio_no_toca(cfg, tmp_path):
    _con_posicion(cfg, tmp_path)
    s, ev = vigilancia.ejecutar(cfg, precios_vivos={'SOL/USD': None})
    assert 'SOL/USD' in s['posiciones'] and not ev


def test_semilla_diaria():
    idx = pd.date_range('2025-01-01', periods=10, freq='D', tz='UTC')
    d1 = pd.DataFrame({'open': 1.0, 'high': 3.0, 'low': 0.5, 'close': 2.0, 'volume': 10.0}, index=idx)
    real = velas([5.0] * 4).set_axis(pd.date_range('2025-01-08', periods=4, freq='12h', tz='UTC'))
    out = D.con_semilla(real, d1)
    assert out['sintetica'].sum() == 14 and not out.index.duplicated().any()
    assert out.loc[pd.Timestamp('2025-01-01 00:00', tz='UTC'), 'close'] == 1.5 and out.loc[pd.Timestamp('2025-01-01 12:00', tz='UTC'), 'close'] == 2.0


def test_drive_403_no_fatal(monkeypatch, tmp_path, caplog):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GCP_SA_KEY', '{"type": "service_account"}')
    import google.oauth2.service_account as sa

    def falla(*a, **k):
        raise Exception('<HttpError 403 "Service Accounts do not have storage quota." storageQuotaExceeded>')
    monkeypatch.setattr(sa.Credentials, 'from_service_account_info', falla)
    assert notificador.subir_texto('hola', 'x.txt', 'folder') is False
    assert (tmp_path / 'x.txt').exists()
    assert 'Unidad compartida' in caplog.text

import copy

import numpy as np
import pandas as pd
import pytest

from nicabot import bot, filtros as F, revision as RV, optimizacion as O
from nicabot.config import DEFECTO, config_par
from nicabot.estrategia import evaluar_senales
from nicabot.indicadores import calcular_indicadores


@pytest.fixture
def cfg():
    return copy.deepcopy(DEFECTO)


def velas(precios, freq='12h', vol=100.0):
    idx = pd.date_range('2025-01-01', periods=len(precios), freq=freq, tz='UTC')
    p = np.array(precios, dtype=float)
    return pd.DataFrame({'open': p, 'high': p * 1.01, 'low': p * 0.99, 'close': p, 'volume': vol}, index=idx)


def base_fila(**kw):
    f = {'close': 110, 'EMA_200': 100, 'MACD': 1, 'MACD_signal': 0, 'RSI': 50, 'EMA_9': 2, 'EMA_21': 1,
         'volume': 100, 'VOL_SMA': 100, 'ATR_PCT': 3.0, 'D_CLOSE': 110, 'D_EMA': 100, 'MACD4': 1, 'MACD4_signal': 0}
    f.update(kw)
    return f


def ce_con(**filtros):
    ce = copy.deepcopy(DEFECTO['estrategia'])
    for k, v in filtros.items():
        ce['filtros'][k].update(v)
    return ce


def test_filtros_apagados_no_cambian_senal():
    assert evaluar_senales(base_fila(volume=1), ce_con())['entrada']


def test_filtro_volumen():
    ce = ce_con(volumen={'enabled': True, 'factor': 1.2})
    assert not evaluar_senales(base_fila(volume=110), ce)['entrada']
    assert evaluar_senales(base_fila(volume=130), ce)['entrada']
    assert evaluar_senales(base_fila(volume=1, VOL_SMA=float('nan')), ce)['entrada']  # sin dato no bloquea


def test_filtro_volatilidad():
    ce = ce_con(volatilidad={'enabled': True, 'atr_pct_max': 5.0})
    assert evaluar_senales(base_fila(ATR_PCT=4.0), ce)['entrada']
    s = evaluar_senales(base_fila(ATR_PCT=6.0), ce)
    assert not s['entrada'] and 'ATR%' in s['bloqueos_filtros'][0]


def test_filtro_multi_marco():
    ce = ce_con(multi_marco={'enabled': True})
    assert evaluar_senales(base_fila(), ce)['entrada']
    assert not evaluar_senales(base_fila(D_CLOSE=90), ce)['entrada']
    assert not evaluar_senales(base_fila(MACD4=-1), ce)['entrada']
    ce['filtros']['multi_marco']['usar_macd_4h'] = False
    assert evaluar_senales(base_fila(MACD4=-1), ce)['entrada']


def test_filtros_no_afectan_salida():
    ce = ce_con(volumen={'enabled': True, 'factor': 5})
    assert evaluar_senales(base_fila(MACD=-1, volume=1), ce)['salida']


def test_agregar_columnas_sin_mirar_futuro():
    d12 = calcular_indicadores(velas(np.linspace(100, 200, 300)))
    d4 = velas(np.linspace(100, 200, 900), freq='4h')
    d = F.agregar_columnas(d12, d4, DEFECTO['estrategia'])
    for c in ('ATR_PCT', 'VOL_SMA', 'D_CLOSE', 'D_EMA', 'MACD4', 'MACD4_signal'):
        assert c in d
    # vela 12h de las 00:00 (cierra 12:00): el diario disponible es el de AYER
    t = d.index[d.index.hour == 0][10]
    ayer = d12.loc[t - pd.Timedelta(hours=12), 'close']
    assert d.loc[t, 'D_CLOSE'] == ayer
    # vela 12h de las 12:00 (cierra 00:00): ya usa el cierre del dia
    t2 = t + pd.Timedelta(hours=12)
    assert d.loc[t2, 'D_CLOSE'] == d12.loc[t2, 'close']
    # MACD 4h: ultimo 4h que cierra con la vela 12h (empieza a t+8h)
    m, _ = F._macd(d4['close'])
    assert d.loc[t, 'MACD4'] == pytest.approx(m.loc[t + pd.Timedelta(hours=8)])


def test_atr_stop(cfg):
    from nicabot import riesgo as R
    r = dict(cfg['riesgo'], stop_modo='atr', stop_atr_mult=2.0)
    assert R.precio_stop(100, 3, r) == 94


def test_revision_desactiva_y_reactiva():
    h = [{'par': 'X/USD', 'pnl_usd': -1, 'fecha_salida': f'2026-0{1 + i // 4}-1{i % 4} 00:00:00+00:00'} for i in range(8)]
    h += [{'par': 'Y/USD', 'pnl_usd': 2, 'fecha_salida': '2026-01-01'}] * 8
    h += [{'par': 'Z/USD', 'pnl_usd': -1, 'fecha_salida': '2026-01-01'}] * 3  # pocas ops
    estado = {'historial': h}
    c = RV.revisar(estado, {'min_operaciones': 8, 'enfriamiento_dias': 30}, ahora='2026-03-01')
    assert ('desactivado', 'X/USD') == c[0][:2]
    des = estado['revision']['desactivados']
    assert set(des) == {'X/USD'}
    RV.revisar(estado, {'min_operaciones': 8, 'enfriamiento_dias': 30}, ahora='2026-03-15')
    assert 'X/USD' in estado['revision']['desactivados']
    c = RV.revisar(estado, {'min_operaciones': 8, 'enfriamiento_dias': 30}, ahora='2026-04-05')
    assert ('reactivado', 'X/USD') == c[0][:2]
    assert 'X/USD' not in estado['revision']['desactivados']  # historial viejo ya no cuenta
    txt = RV.reporte(estado, c, '2026-04-05')
    assert 'X/USD' in txt and 'reactivado' in txt


def test_revision_ejecutar_preserva_estado(cfg, tmp_path):
    import json
    from nicabot import estado as est
    cfg['ruta_estado'] = str(tmp_path / 'estado.json')
    e = est.estado_inicial(1000)
    e['historial'] = [{'par': 'X/USD', 'pnl_usd': -1, 'fecha_salida': '2026-01-01'}] * 8
    est.guardar(cfg['ruta_estado'], e)
    RV.ejecutar(cfg, ahora='2026-02-01', ruta_reporte=str(tmp_path / 'r.md'))
    e2 = json.load(open(cfg['ruta_estado']))
    assert 'X/USD' in e2['revision']['desactivados'] and len(e2['historial']) == 8


def test_bot_respeta_par_desactivado(cfg, tmp_path, monkeypatch):
    from nicabot import estado as est
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('MODO', raising=False)
    monkeypatch.setattr(bot, 'evaluar_senales', lambda f, c: {'entrada': True, 'salida': False, 'trend_up': True,
                                                             'macd_bullish': True, 'rsi_ok': True, 'momentum_ok': True})
    cfg['ruta_estado'] = str(tmp_path / 'estado.json')
    cfg['pares'] = ['SOL/USD']
    cfg['filtro_btc']['enabled'] = False
    cfg['entrada']['usar_precio_vivo'] = False
    df = velas(np.linspace(100, 200, 300))
    e = est.estado_inicial(1000)
    e['revision'] = {'desactivados': {'SOL/USD': {'hasta': '2099-01-01', 'motivo': 'x'}}}
    est.guardar(cfg['ruta_estado'], e)
    ahora = df.index[-1] + pd.Timedelta(hours=13)
    estado, _ = bot.ejecutar(cfg, dfs={'SOL/USD': df}, subir=False, ahora=ahora)
    assert not estado['posiciones']


def test_ventanas_walk_forward():
    idx = pd.date_range('2019-05-10', '2026-10-01', freq='12h', tz='UTC')
    ws = O.ventanas(idx)
    assert ws[0][1] == pd.Timestamp('2021-01-01', tz='UTC')
    for tr0, te0, te1 in ws:
        assert tr0 < te0 < te1 and (te0 - tr0).days >= 540  # train antes del test
    assert all(ws[i][2] == ws[i + 1][1] for i in range(len(ws) - 1))


def test_metricas_y_simular(cfg):
    h4 = velas(np.concatenate([np.linspace(100, 300, 2000), np.linspace(300, 150, 1000)]), freq='4h', vol=50.0)
    cp = config_par(cfg, 'SOL/USD'); cp['_par'] = 'SOL/USD'
    d = O.preparar(O.datos.agregar(h4, '12h', 3), h4, cp['estrategia'])
    r = O.simular(O._filas(d), cp, d.index[0], d.index[-1] + pd.Timedelta(hours=12))
    m = O.metricas([r])
    assert set(m) >= {'retorno_pct', 'ops', 'win_rate', 'profit_factor', 'max_dd_pct', 'comisiones_usd'}
    p = O.params_de_config(cp)
    assert O.aplicar_params(cp, p)['riesgo']['stop_pct'] == cp['riesgo']['stop_pct']


def test_decidir_filtros():
    mk = lambda ret, pf: {'retorno_pct': ret, 'ops': 10, 'win_rate': 50, 'profit_factor': pf, 'max_dd_pct': 5}
    res = [{'variantes': {'base': mk(10, 1.3), 'volumen': mk(9.5, 1.5), 'volatilidad': mk(12, 1.1),
                          'multi_marco': mk(5, 2.0)}}]
    for v in res[0]['variantes'].values():
        v['ganancia_usd'], v['perdida_usd'] = v['profit_factor'] * 10, 10
    activos, _, _ = O.decidir_filtros(res)
    assert activos == ['volumen']


def test_bot_con_filtros_y_4h(cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('MODO', raising=False)
    cfg['ruta_estado'] = str(tmp_path / 'estado.json')
    cfg['pares'] = ['SOL/USD']
    for k in ('volumen', 'multi_marco', 'volatilidad'):
        cfg['estrategia']['filtros'][k]['enabled'] = True
    cfg['estrategia']['filtros']['volatilidad']['atr_pct_max'] = 5.0
    df = velas(np.linspace(100, 200, 300))
    d4 = velas(np.linspace(100, 200, 900), freq='4h')
    estado, resumen = bot.ejecutar(cfg, dfs={'SOL/USD': df}, dfs_4h={'SOL/USD': d4}, subir=False)
    assert 'Precio SOL:' in resumen


def test_config_real_carga():
    from nicabot.config import cargar_config
    c = cargar_config()
    assert 'filtros' in c['estrategia'] and c['revision_mensual']['min_operaciones'] >= 8
    for p in c['pares']:
        config_par(c, p)

"""Investigacion: variantes para subir la tasa de aciertos (win rate) sin destruir la rentabilidad.

Uso:  python -m nicabot.investigacion [--pares BTC/USD,ETH/USD]

Cada variante = la config actual de cada par + unos cambios FIJOS (los mismos para
todos los pares, nada se ajusta por par ni con datos de prueba). Se evalua SOLO en las
ventanas fuera de muestra (OOS) del walk-forward de nicabot/optimizacion.py, con
comisiones, slippage y filtro BTC. Escribe reportes/investigacion_aciertos.md/.json.
"""
import argparse
import copy
import json
import logging
import os
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

from . import datos
from .config import _mezclar, cargar_config, config_par
from .optimizacion import _filas, cargar_historias, metricas, preparar, regimen_btc_serie, simular, ventanas

MIN_OPS = 100  # minimo de operaciones OOS (todos los pares) para considerar una variante

PARCIAL = {'tp_parcial_r': 1.0, 'tp_parcial_frac': 0.5, 'breakeven_tras_parcial': True, 'breakeven_offset_pct': 0.6}

VARIANTES = {
    'actual': {},
    'tp_2r': {'riesgo': {'take_profit_r': 2.0}},
    'tp_1.5r': {'riesgo': {'take_profit_r': 1.5}},
    'tp_1r': {'riesgo': {'take_profit_r': 1.0}},
    'parcial_1r_be_tp3r': {'riesgo': dict(PARCIAL)},
    'parcial_1r_be_trailing3atr': {'riesgo': dict(PARCIAL, take_profit_r=None, trailing_atr_mult=3.0,
                                                  trailing_solo_tras_parcial=True)},
    'parcial_0.75r_be_tp3r': {'riesgo': dict(PARCIAL, tp_parcial_r=0.75)},
    'parcial_1r_be_tp3r_sin_macd': {'riesgo': dict(PARCIAL), 'estrategia': {'salida_macd': False}},
    'parcial_1r_be_trail_sin_macd': {'riesgo': dict(PARCIAL, take_profit_r=None, trailing_atr_mult=3.0,
                                                    trailing_solo_tras_parcial=True),
                                     'estrategia': {'salida_macd': False}},
    'tiempo_10velas': {'riesgo': {'max_velas_posicion': 10}},
    'sin_salida_macd': {'estrategia': {'salida_macd': False}},
    'trailing_atr3': {'riesgo': {'trailing_atr_mult': 3.0}},
    'sin_extension_2atr': {'estrategia': {'max_extension_atr': 2.0}},
    'pullback': {'estrategia': {'modo_entrada': 'pullback'}},
    'pullback_parcial_1r': {'estrategia': {'modo_entrada': 'pullback'}, 'riesgo': dict(PARCIAL)},
    'reversion_rsi2': {'estrategia': {'modo_entrada': 'reversion'},
                       'riesgo': {'take_profit_r': None, 'max_velas_posicion': 10}},
    'reversion_rsi2_stop_ancho': {'estrategia': {'modo_entrada': 'reversion'},
                                  'riesgo': {'take_profit_r': None, 'max_velas_posicion': 10,
                                             'stop_modo': 'atr', 'stop_atr_mult': 3.0}},
    'mixto_cruce_reversion': {'estrategia': {'modo_entrada': 'mixto'}},
    'parcial_1r_be_tp3r_ext2atr': {'riesgo': dict(PARCIAL), 'estrategia': {'max_extension_atr': 2.0}},
    'parcial_1r_be_tp2r': {'riesgo': dict(PARCIAL, take_profit_r=2.0)},
    'parcial_1r_be0_tp3r': {'riesgo': dict(PARCIAL, breakeven_offset_pct=0.0)},
    'parcial_1r_frac33_tp3r_tiempo10': {'riesgo': dict(PARCIAL, tp_parcial_frac=0.33, max_velas_posicion=10)},
    'parcial_1r_sinbe_tp3r_tiempo10': {'riesgo': dict(PARCIAL, breakeven_tras_parcial=False, max_velas_posicion=10)},
    'tp_1.5r_tiempo10': {'riesgo': {'take_profit_r': 1.5, 'max_velas_posicion': 10}},
    'parcial_1r_be_tp3r_tiempo10': {'riesgo': dict(PARCIAL, max_velas_posicion=10)},
    'parcial_1r_be_tp3r_tiempo14': {'riesgo': dict(PARCIAL, max_velas_posicion=14)},
}
# 'actual' = config SIN las salidas nuevas (referencia anterior); el resto se aplica encima
APAGADO = {'riesgo': {'tp_parcial_r': None, 'max_velas_posicion': None, 'trailing_solo_tras_parcial': False}}


def aplicar_variante(cp, cambios):
    return _mezclar(copy.deepcopy(cp), copy.deepcopy(cambios))


def evaluar_par(args):
    par, cfg, h4, btc_ok, variantes = args
    d12 = datos.agregar(h4, '12h', 3)
    out = {}
    for nombre, cambios in variantes.items():
        cp = aplicar_variante(aplicar_variante(config_par(cfg, par), APAGADO), cambios)
        cp['_par'] = par
        d = preparar(d12, h4, cp['estrategia'])
        if d.empty:
            continue
        filas = _filas(d)
        rs = [simular(filas, cp, b, c, btc_ok) for a, b, c in ventanas(d.index)]
        out[nombre] = metricas(rs) if rs else None
    return par, out


def agregar(por_par, nombre):
    ms = [m[nombre] for m in por_par.values() if m.get(nombre) and m[nombre]['ops'] > 0]
    if not ms:
        return None
    ops = sum(m['ops'] for m in ms)
    gan, per = sum(m['ganancia_usd'] for m in ms), sum(m['perdida_usd'] for m in ms)
    return {'ops': ops, 'win_rate': round(sum(m['win_rate'] * m['ops'] for m in ms) / ops, 1),
            'profit_factor': round(gan / per, 2) if per else None,
            'expectativa_usd': round((gan - per) / ops, 2),
            'retorno_prom_pct': round(sum(m['retorno_pct'] for m in ms) / len(ms), 2),
            'dd_prom_pct': round(sum(m['max_dd_pct'] for m in ms) / len(ms), 2),
            'dd_max_pct': round(max(m['max_dd_pct'] for m in ms), 2),
            'pares_positivos': sum(1 for m in ms if m['retorno_pct'] > 0), 'pares': len(ms)}


def elegir(ag, pf_actual):
    """Mayor PF entre las que tienen win rate > 50%, PF >= max(1.2, ...) y expectativa > 0."""
    ok = [(n, a) for n, a in ag.items() if a and a['ops'] >= MIN_OPS and a['win_rate'] > 50
          and (a['profit_factor'] or 0) > 1.2 and a['expectativa_usd'] > 0]
    if not ok:
        return None
    return max(ok, key=lambda x: (x[1]['profit_factor'] >= pf_actual, x[1]['profit_factor'], x[1]['retorno_prom_pct']))[0]


def correr(pares=None, variantes=None):
    cfg = cargar_config()
    pares = pares or cfg['pares']
    variantes = variantes or VARIANTES
    btc_par = cfg['filtro_btc']['par']
    hist, _ = cargar_historias(sorted(set(pares) | {btc_par}))
    btc_ok = regimen_btc_serie(datos.agregar(hist[btc_par], '12h', 3), cfg).to_dict()
    with ProcessPoolExecutor() as ex:
        por_par = dict(ex.map(evaluar_par, [(p, cfg, hist[p], btc_ok, variantes) for p in pares if p in hist]))
    ag = {n: agregar(por_par, n) for n in variantes}
    return por_par, ag


def main():
    logging.basicConfig(level=logging.WARNING)
    a = argparse.ArgumentParser()
    a.add_argument('--pares')
    a = a.parse_args()
    por_par, ag = correr(a.pares.split(',') if a.pares else None)
    os.makedirs('reportes', exist_ok=True)
    with open('reportes/investigacion_aciertos.json', 'w', encoding='utf-8') as f:
        json.dump({'agregado': ag, 'por_par': por_par, 'variantes': VARIANTES,
                   'generado': str(pd.Timestamp.now(tz='UTC'))}, f, indent=1, default=str)
    elegida = elegir(ag, (ag.get('actual') or {}).get('profit_factor') or 1.3)
    L = ['| Variante | Win rate | PF | Expectativa USD/op | Retorno prom/par | DD prom | DD max | Ops |', '|---|---|---|---|---|---|---|---|']
    for n, x in ag.items():
        if x:
            L.append(f"| {n}{' **(elegida)**' if n == elegida else ''} | {x['win_rate']}% | {x['profit_factor']} | {x['expectativa_usd']} | "
                     f"{x['retorno_prom_pct']}% | {x['dd_prom_pct']}% | {x['dd_max_pct']}% | {x['ops']} |")
    with open('reportes/investigacion_aciertos_tabla.md', 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')
    print('\n'.join(L))
    print('Elegida:', elegida)


if __name__ == '__main__':
    main()

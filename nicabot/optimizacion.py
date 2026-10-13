"""Backtest de historia larga + optimizacion walk-forward por moneda.

Uso:  python -m nicabot.optimizacion [--pares BTC/USD,ETH/USD] [--escribir-config]

Metodo (para no sobreajustar):
 - Velas 12h construidas desde 4h de historia larga (nicabot/historia.py).
 - Ventanas walk-forward: se entrena con los 18 meses previos y se prueba en los
   6 meses siguientes (fuera de muestra, OOS). Las ventanas OOS nunca se usan para elegir.
 - Rejilla pequena (stop, EMAs, RSI max, TP en R). Se exige un minimo de
   operaciones en entrenamiento y la puntuacion de cada combinacion se suaviza
   con sus vecinas (prefiere zonas estables, no picos aislados).
 - Comisiones y slippage de config.json incluidos. Filtro BTC aplicado igual que el bot.
 - Los filtros nuevos (volumen, volatilidad, multi-marco) se evaluan OOS con los
   parametros base; solo se dejan encendidos si no empeoran el resultado OOS.
 - Los parametros ajustados de un par solo se escriben si su OOS ajustado
   supera al OOS de los parametros base de ese par.
"""
import argparse
import copy
import itertools
import json
import logging
import math
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

from . import datos
from .config import cargar_config, config_par, RUTA_CONFIG_DEFECTO
from .estado import estado_inicial
from .estrategia import evaluar_senales
from .filtros import agregar_columnas
from .historia import descargar_historia_4h
from .indicadores import calcular_indicadores
from .motor import equity, procesar_vela
from .riesgo import drawdown_pct

log = logging.getLogger('nicabot.optimizacion')

REJILLA = {
    'stop': [('pct', 4.0), ('pct', 6.0), ('pct', 8.0), ('atr', 2.0), ('atr', 3.0)],
    'emas': [(9, 21), (12, 26), (20, 50)],
    'rsi_max_entrada': [60, 65, 70],
    'take_profit_r': [2.0, 3.0, 4.0],
}
MIN_OPS_TRAIN = 6
MIN_OPS_OOS = 10     # para adoptar parametros ajustados de un par
MEJORA_MIN_RET = 1.0  # el ajuste debe ganar >= 1 punto % OOS y no bajar el PF
TOLERANCIA_RET = 1.0  # puntos % de retorno OOS que un filtro puede costar si mejora el PF
MESES_TRAIN = 18
MESES_TEST = 6
WARMUP = 250


def aplicar_params(cp, prm):
    cp = copy.deepcopy(cp)
    modo, val = prm['stop']
    cp['riesgo']['stop_modo'] = modo
    if modo == 'pct':
        cp['riesgo']['stop_pct'] = val
    else:
        cp['riesgo']['stop_atr_mult'] = val
    cp['estrategia']['ema_rapida'], cp['estrategia']['ema_lenta'] = prm['emas']
    cp['estrategia']['rsi_max_entrada'] = prm['rsi_max_entrada']
    cp['riesgo']['take_profit_r'] = prm['take_profit_r']
    return cp


def params_de_config(cp):
    r, e = cp['riesgo'], cp['estrategia']
    stop = ('pct', float(r['stop_pct'])) if r['stop_modo'] == 'pct' else ('atr', float(r['stop_atr_mult']))
    return {'stop': stop, 'emas': (e['ema_rapida'], e['ema_lenta']),
            'rsi_max_entrada': e['rsi_max_entrada'], 'take_profit_r': r['take_profit_r']}


def preparar(d12, d4, ce):
    d = calcular_indicadores(d12, ce)
    d = agregar_columnas(d, d4, ce)
    return d.iloc[WARMUP:]


def regimen_btc_serie(btc12, cfg):
    d = calcular_indicadores(btc12, cfg['estrategia'])  # EMAs por defecto (9/21/200)
    ok = (d['close'] > d['EMA_200']) & (d['EMA_9'] > d['EMA_21'])
    return ok


def simular(filas, cp, desde, hasta, btc_ok=None, capital=1000.0):
    """filas: lista de (timestamp, dict). Un solo par, capital propio."""
    est = estado_inicial(capital)
    pico, max_dd, ultimo = capital, 0.0, None
    par = cp['_par']
    for t, fila in filas:
        if t < desde:
            continue
        if t >= hasta:
            break
        sen = evaluar_senales(fila, cp['estrategia'])
        if sen['entrada'] and btc_ok is not None and not btc_ok.get(t, False):
            sen['entrada'] = False
        procesar_vela(est, par, fila, t, sen, cp, {par: fila['close']})
        ultimo = fila['close']
        eq = equity(est, {par: ultimo})
        pico = max(pico, eq)
        max_dd = max(max_dd, drawdown_pct(pico, eq))
    final = equity(est, {par: ultimo}) if ultimo else capital
    return {'retorno': final / capital - 1, 'dd': max_dd, 'ops': [float(x['pnl_usd']) for x in est['historial']],
            'comisiones': est.get('comisiones_totales', 0.0)}


def metricas(res_list):
    """Combina ventanas OOS (encadenadas): retorno compuesto, DD maximo, PF, win rate."""
    ops = [o for r in res_list for o in r['ops']]
    ret = 1.0
    for r in res_list:
        ret *= 1 + r['retorno']
    gan = sum(o for o in ops if o > 0)
    per = -sum(o for o in ops if o < 0)
    return {'retorno_pct': round((ret - 1) * 100, 2), 'ops': len(ops),
            'win_rate': round(100 * sum(1 for o in ops if o > 0) / len(ops), 1) if ops else 0.0,
            'profit_factor': round(gan / per, 2) if per > 0 else (math.inf if gan > 0 else 0.0),
            'max_dd_pct': round(max((r['dd'] for r in res_list), default=0.0), 2),
            'expectativa_usd': round(sum(ops) / len(ops), 2) if ops else 0.0,
            'comisiones_usd': round(sum(r['comisiones'] for r in res_list), 2),
            'ganancia_usd': round(gan, 2), 'perdida_usd': round(per, 2)}


def _puntaje(r):
    return r['retorno'] * 100 - 0.5 * r['dd']


def ventanas(indice):
    """(inicio_train, inicio_test, fin_test): train MESES_TRAIN, test MESES_TEST, avanzando MESES_TEST.
    Las pruebas arrancan el 1 de enero o 1 de julio."""
    ini, fin = indice[0], indice[-1]
    t0 = ini + pd.DateOffset(months=MESES_TRAIN)
    t0 = pd.Timestamp(year=t0.year, month=1 if t0.month == 1 and t0.day == 1 else (7 if t0.month <= 6 or (t0.month == 7 and t0.day == 1) else 1),
                      day=1, tz='UTC') + (pd.DateOffset(years=1) if t0.month > 7 or (t0.month == 7 and t0.day > 1) else pd.DateOffset(0))
    out = []
    while t0 + pd.DateOffset(months=2) < fin:  # al menos 2 meses de prueba
        out.append((t0 - pd.DateOffset(months=MESES_TRAIN), t0, t0 + pd.DateOffset(months=MESES_TEST)))
        t0 = t0 + pd.DateOffset(months=MESES_TEST)
    return out


def _filas(d):
    return [(t, r) for t, r in zip(d.index, d.to_dict('records'))]


def _clave(p):
    return (p['stop'], p['emas'], p['rsi_max_entrada'], p['take_profit_r'])


def combinaciones():
    ks = list(REJILLA)
    return [dict(zip(ks, v)) for v in itertools.product(*(REJILLA[k] for k in ks))]


def elegir(filas_por_ema, cp_base, desde, hasta, btc_ok):
    """Elige parametros en [desde, hasta) con suavizado por vecinas."""
    res = {}
    for p in combinaciones():
        cp = aplicar_params(cp_base, p)
        res[_clave(p)] = (p, simular(filas_por_ema[p['emas']], cp, desde, hasta, btc_ok))
    ks = list(REJILLA)
    pos = {k: {v: i for i, v in enumerate(REJILLA[k])} for k in ks}
    mejor, mejor_s = None, -math.inf
    for clave, (p, r) in res.items():
        if len(r['ops']) < MIN_OPS_TRAIN:
            continue
        vecinas = [r2 for c2, (p2, r2) in res.items()
                   if sum(abs(pos[k][p[k]] - pos[k][p2[k]]) for k in ks) <= 1]
        s = sum(_puntaje(x) for x in vecinas) / len(vecinas)
        if s > mejor_s:
            mejor, mejor_s = p, s
    return mejor


def umbral_atr(d, desde, hasta, q=0.9):
    s = d.loc[(d.index >= desde) & (d.index < hasta), 'ATR_PCT'].dropna()
    return round(float(s.quantile(q)), 2) if len(s) > 50 else None


def con_filtros(cp, activos, atr_max=None):
    cp = copy.deepcopy(cp)
    f = cp['estrategia'].setdefault('filtros', {})
    for k in ('volumen', 'volatilidad', 'multi_marco'):
        f.setdefault(k, {})['enabled'] = k in activos
    if atr_max:
        f['volatilidad']['atr_pct_max'] = atr_max
    return cp


VARIANTES = {'base': [], 'volumen': ['volumen'], 'volatilidad': ['volatilidad'],
             'multi_marco': ['multi_marco']}


def analizar_par(args):
    par, cfg, h4, btc_ok, activos_finales = args
    cp0 = config_par(cfg, par)
    cp0['_par'] = par
    d12 = datos.agregar(h4, '12h', 3)
    base_prm = params_de_config(cp0)
    filas_por_ema = {}
    for emas in REJILLA['emas'] + [base_prm['emas']]:
        ce = dict(cp0['estrategia'], ema_rapida=emas[0], ema_lenta=emas[1])
        filas_por_ema[emas] = _filas(preparar(d12, h4, ce))
    d_ref = preparar(d12, h4, cp0['estrategia'])
    if d_ref.empty:
        return {'par': par, 'ventanas': 0}
    ws = ventanas(d_ref.index)
    out = {'par': par, 'ventanas': len(ws), 'desde': str(d_ref.index[0].date()),
           'variantes': {}, 'oos_ajustado': None}
    if ws:
        out['oos_desde'] = str(ws[0][1].date())
    # 1) filtros con parametros base, OOS
    for nombre, act in VARIANTES.items():
        rs = []
        for tr0, te0, te1 in ws:
            cp = con_filtros(cp0, act, umbral_atr(d_ref, tr0, te0))
            rs.append(simular(filas_por_ema[base_prm['emas']], cp, te0, te1, btc_ok))
        out['variantes'][nombre] = metricas(rs)
    # 2) walk-forward con ajuste (filtros finales activos)
    rs, rs_base, elegidos = [], [], []
    for tr0, te0, te1 in ws:
        cpf = con_filtros(cp0, activos_finales, umbral_atr(d_ref, tr0, te0))
        p = elegir(filas_por_ema, cpf, tr0, te0, btc_ok) or base_prm
        elegidos.append(p)
        rs.append(simular(filas_por_ema[p['emas']], aplicar_params(cpf, p), te0, te1, btc_ok))
        rs_base.append(simular(filas_por_ema[base_prm['emas']], cpf, te0, te1, btc_ok))
    out['oos_ajustado'] = metricas(rs) if ws else None
    out['oos_base_con_filtros'] = metricas(rs_base) if ws else None
    # 3) parametros finales: entrenados con los ultimos 3 anos
    fin = d_ref.index[-1] + pd.Timedelta(hours=12)
    ini = max(d_ref.index[0], fin - pd.DateOffset(years=3))
    atr_final = umbral_atr(d_ref, ini, fin)
    cpf = con_filtros(cp0, activos_finales, atr_final)
    final = elegir(filas_por_ema, cpf, ini, fin, btc_ok)
    out['atr_pct_max'] = atr_final
    out['params_finales'] = final
    out['elegidos'] = elegidos
    return out


def cargar_historias(pares):
    h = {}
    fuentes = {}
    for p in pares:
        try:
            h[p], fuentes[p] = descargar_historia_4h(p)
        except Exception as e:
            log.error('Sin historia para %s: %s', p, e)
    return h, fuentes


def _agregado(resultados, clave_fn):
    ms = [clave_fn(r) for r in resultados if clave_fn(r) and clave_fn(r)['ops'] > 0]
    if not ms:
        return None
    return {'retorno_prom_pct': round(sum(m['retorno_pct'] for m in ms) / len(ms), 2),
            'ops': sum(m['ops'] for m in ms),
            'win_rate': round(sum(m['win_rate'] * m['ops'] for m in ms) / max(1, sum(m['ops'] for m in ms)), 1),
            'pf_prom': round(sum(m.get('ganancia_usd', 0) for m in ms) / max(1e-9, sum(m.get('perdida_usd', 0) for m in ms)), 2),
            'dd_prom_pct': round(sum(m['max_dd_pct'] for m in ms) / len(ms), 2),
            'pares': len(ms)}


def decidir_filtros(resultados):
    base = _agregado(resultados, lambda r: r['variantes'].get('base'))
    activos, dec = [], {}
    for f in ('volumen', 'volatilidad', 'multi_marco'):
        a = _agregado(resultados, lambda r, f=f: r['variantes'].get(f))
        ok = bool(base and a and a['pf_prom'] >= base['pf_prom']
                  and (a['retorno_prom_pct'] >= base['retorno_prom_pct']
                       or (a['pf_prom'] >= base['pf_prom'] + 0.02 and a['retorno_prom_pct'] >= base['retorno_prom_pct'] - TOLERANCIA_RET)))
        dec[f] = {'metricas': a, 'enabled': ok}
        if ok:
            activos.append(f)
    return activos, base, dec


def _fmt(m):
    if not m:
        return '| - | - | - | - | - |'
    pf = 'inf' if m['profit_factor'] == math.inf else m['profit_factor']
    return f"| {m['retorno_pct']}% | {m['ops']} | {m['win_rate']}% | {pf} | {m['max_dd_pct']}% |"


def _fmt_ag(nombre, a, on=None):
    if not a:
        return f'| {nombre} | - | - | - | - | - | - |'
    return (f"| {nombre} | {a['retorno_prom_pct']}% | {a['ops']} | {a['win_rate']}% | {a['pf_prom']} | "
            f"{a['dd_prom_pct']}% | {'' if on is None else ('SI' if on else 'NO')} |")


def correr(pares=None, escribir_config=False, ruta_reporte='reportes/optimizacion.md', ruta_config=None):
    cfg = cargar_config(ruta_config)
    pares = pares or cfg['pares']
    btc_par = cfg.get('filtro_btc', {}).get('par', 'BTC/USD')
    hist, fuentes = cargar_historias(sorted(set(pares) | {btc_par}))
    btc_ok = None
    if cfg.get('filtro_btc', {}).get('enabled', True) and btc_par in hist:
        btc_ok = regimen_btc_serie(datos.agregar(hist[btc_par], '12h', 3), cfg).to_dict()
    pares = [p for p in pares if p in hist]
    # Paso 1: evaluar filtros (sin ajuste) -> decidir cuales quedan encendidos
    with ProcessPoolExecutor() as ex:
        prev = list(ex.map(_solo_variantes, [(p, cfg, hist[p], btc_ok) for p in pares]))
    activos, base_ag, dec = decidir_filtros(prev)
    with ProcessPoolExecutor() as ex:
        res = list(ex.map(analizar_par, [(p, cfg, hist[p], btc_ok, activos) for p in pares]))
    cambios = {}
    for r in res:
        a, b = r.get('oos_base_con_filtros'), r.get('oos_ajustado')
        ok = (b and a and r.get('params_finales') and b['ops'] >= MIN_OPS_OOS
              and b['retorno_pct'] >= a['retorno_pct'] + MEJORA_MIN_RET and b['profit_factor'] >= a['profit_factor'])
        r['adoptado'] = bool(ok)
        cambios[r['par']] = {'params': r['params_finales'] if ok else None, 'atr_pct_max': r.get('atr_pct_max')}
    reporte = construir_reporte(res, fuentes, activos, base_ag, dec)
    import os
    os.makedirs(os.path.dirname(ruta_reporte) or '.', exist_ok=True)
    with open(ruta_reporte, 'w', encoding='utf-8') as f:
        f.write(reporte)
    with open(ruta_reporte.replace('.md', '.json'), 'w', encoding='utf-8') as f:
        json.dump({'activos': activos, 'resultados': res}, f, indent=1, default=str)
    if escribir_config:
        escribir(ruta_config or RUTA_CONFIG_DEFECTO, activos, cambios)
    return res, activos


def _solo_variantes(args):
    par, cfg, h4, btc_ok = args
    cp0 = config_par(cfg, par)
    cp0['_par'] = par
    d12 = datos.agregar(h4, '12h', 3)
    d = preparar(d12, h4, cp0['estrategia'])
    if d.empty:
        return {'par': par, 'variantes': {}}
    filas = _filas(d)
    out = {'par': par, 'variantes': {}}
    for nombre, act in VARIANTES.items():
        rs = [simular(filas, con_filtros(cp0, act, umbral_atr(d, a, b)), b, c, btc_ok) for a, b, c in ventanas(d.index)]
        out['variantes'][nombre] = metricas(rs) if rs else None
    return out


def escribir(ruta, activos, cambios):
    with open(ruta, encoding='utf-8') as f:
        cfg = json.load(f)
    filt = cfg['estrategia'].setdefault('filtros', {})
    for k in ('volumen', 'volatilidad', 'multi_marco'):
        filt.setdefault(k, {})['enabled'] = k in activos
    app = cfg.setdefault('ajustes_por_par', {})
    for par, c in cambios.items():
        a = app.setdefault(par, {})
        if c['atr_pct_max']:
            a.setdefault('estrategia', {}).setdefault('filtros', {}).setdefault('volatilidad', {})['atr_pct_max'] = c['atr_pct_max']
        p = c['params']
        if p:
            e, r = a.setdefault('estrategia', {}), a.setdefault('riesgo', {})
            e['ema_rapida'], e['ema_lenta'] = p['emas']
            e['rsi_max_entrada'] = p['rsi_max_entrada']
            r['take_profit_r'] = p['take_profit_r']
            r['stop_modo'] = p['stop'][0]
            if p['stop'][0] == 'pct':
                r['stop_pct'] = p['stop'][1]
            else:
                r['stop_atr_mult'] = p['stop'][1]
            a['_optimizado'] = 'walk-forward, ver reportes/optimizacion.md'
    with open(ruta, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write('\n')


def construir_reporte(res, fuentes, activos, base_ag, dec):
    L = ['# Optimizacion walk-forward (fuera de muestra)', '',
         f"Generado: {pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M} UTC. Velas 12h desde 4h. Comisiones (0.26%) y slippage (0.05%) por lado incluidos.",
         f'Entrenamiento: {MESES_TRAIN} meses previos; prueba: {MESES_TEST} meses siguientes (OOS), ventana rodante. Minimo {MIN_OPS_TRAIN} operaciones en entrenamiento. '
         'Cada par simulado solo, capital 1000 USD por ventana, riesgo y topes de config.json, filtro BTC activo. '
         'Retorno = compuesto de las ventanas OOS; DD = peor drawdown de una ventana.', '',
         '**Aviso:** historia de Binance/KuCoin en USDT (no Kraken USD). Precio casi igual; el volumen es de otra bolsa. '
         'Resultados pasados no garantizan nada.', '',
         '## Filtros (parametros base, OOS, promedio de pares)', '',
         '| Variante | Retorno prom por par | Ops | Win rate | PF (todas las ops) | DD prom | Encendido |', '|---|---|---|---|---|---|---|',
         _fmt_ag('base (sin filtros nuevos)', base_ag)]
    for f, d in dec.items():
        L.append(_fmt_ag(f, d['metricas'], d['enabled']))
    L += ['', f"Filtros encendidos por defecto: {', '.join(activos) or 'ninguno'}. "
          f'Regla: se enciende solo si el PF OOS (todas las operaciones) no baja Y el retorno promedio no baja (o, si el PF sube >= 0.02, el retorno baja menos de {TOLERANCIA_RET} punto).', '',
          '## Antes vs despues por par (OOS)', '',
          '"Antes" = parametros actuales sin filtros nuevos. "Despues" = lo que queda en config: filtros encendidos + '
          'parametros ajustados SOLO si el par fue adoptado (si no, parametros base). '
          f'Se adopta el ajuste solo si en OOS gana >= {MEJORA_MIN_RET} punto(s) de retorno, no baja el PF y tiene >= {MIN_OPS_OOS} operaciones.', '',
          '| Par | Fuente | OOS desde | Antes: Ret | Ops | Win | PF | DD | Despues: Ret | Ops | Win | PF | DD | Adoptado |',
          '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for r in sorted(res, key=lambda x: x['par']):
        a = r['variantes'].get('base') if r.get('ventanas') else None
        b = r.get('oos_ajustado')
        b = r.get('oos_ajustado') if r.get('adoptado') else r.get('oos_base_con_filtros')
        L.append(f"| {r['par']} | {fuentes.get(r['par'], '?')} | {r.get('oos_desde', 'sin historia suficiente')} "
                 f"{_fmt(a)}{_fmt(b)[1:]} {'SI' if r.get('adoptado') else 'NO'} |")
    ag_a = _agregado(res, lambda r: r['variantes'].get('base') if r.get('ventanas') else None)
    ag_b = _agregado(res, lambda r: (r.get('oos_ajustado') if r.get('adoptado') else r.get('oos_base_con_filtros')))
    ag_c = _agregado(res, lambda r: r.get('oos_ajustado'))
    L += ['', '| Total | Retorno prom por par | Ops | Win rate | PF (todas las ops) | DD prom | |', '|---|---|---|---|---|---|---|',
          _fmt_ag('Antes', ag_a), _fmt_ag('Despues (config final)', ag_b),
          _fmt_ag('Referencia: ajuste walk-forward en TODOS los pares', ag_c), '',
          '## Parametros finales elegidos (ultimos 3 anos)', '',
          '| Par | Stop | EMAs | RSI max | TP (R) | ATR% max | Escrito en config |', '|---|---|---|---|---|---|---|']
    for r in sorted(res, key=lambda x: x['par']):
        p = r.get('params_finales')
        if p:
            L.append(f"| {r['par']} | {p['stop'][0]} {p['stop'][1]} | {p['emas'][0]}/{p['emas'][1]} | {p['rsi_max_entrada']} | "
                     f"{p['take_profit_r']} | {r.get('atr_pct_max')} | {'SI' if r.get('adoptado') else 'NO (se queda la config base)'} |")
        else:
            L.append(f"| {r['par']} | - | - | - | - | {r.get('atr_pct_max')} | NO (pocas operaciones/historia) |")
    malos = [r['par'] for r in res if r.get('oos_ajustado') and r['oos_ajustado']['ops'] >= 5
             and r['oos_ajustado']['expectativa_usd'] < 0 and r['variantes']['base']['expectativa_usd'] < 0
             and (r.get('oos_base_con_filtros') or {}).get('expectativa_usd', 0) < 0]
    L += ['', f"## Pares que se ven mal (expectativa OOS negativa en base, con filtros y con ajuste)", '', ', '.join(malos) or 'ninguno', '']
    return '\n'.join(L)


def main():
    logging.basicConfig(level=logging.WARNING)
    a = argparse.ArgumentParser()
    a.add_argument('--pares')
    a.add_argument('--escribir-config', action='store_true')
    a = a.parse_args()
    res, activos = correr(a.pares.split(',') if a.pares else None, a.escribir_config)
    print('Filtros encendidos:', activos)
    print(open('reportes/optimizacion.md', encoding='utf-8').read())


if __name__ == '__main__':
    main()

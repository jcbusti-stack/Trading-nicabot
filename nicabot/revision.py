"""Revision mensual de rendimiento por moneda (con el historial de estado.json).

- Expectativa = promedio de pnl_usd de las ultimas N operaciones cerradas del par
  (solo las cerradas despues de su ultima reactivacion).
- Si hay al menos min_operaciones y la expectativa es negativa -> el par se
  desactiva (no abre posiciones nuevas; las abiertas siguen con stop/TP/salida)
  durante enfriamiento_dias. Al vencer, se reactiva y se empieza a contar de cero.
Se guarda en estado['revision'] = {'desactivados': {par: {...}}, 'reactivados': {par: fecha}, 'ultima': fecha}.
"""
import logging
from collections import defaultdict

import pandas as pd

log = logging.getLogger('nicabot.revision')
DEFECTO = {'enabled': True, 'ultimas_n': 12, 'min_operaciones': 8, 'enfriamiento_dias': 60}


def estadisticas(historial):
    por = defaultdict(list)
    for t in historial:
        por[t.get('par', '?')].append(t)
    out = {}
    for par, ts in por.items():
        pnl = [float(t.get('pnl_usd', 0)) for t in ts]
        g, p = sum(x for x in pnl if x > 0), -sum(x for x in pnl if x < 0)
        out[par] = {'ops': len(pnl), 'ganadoras': sum(1 for x in pnl if x > 0),
                    'win_rate': round(100 * sum(1 for x in pnl if x > 0) / len(pnl), 1),
                    'pnl_usd': round(sum(pnl), 2), 'expectativa_usd': round(sum(pnl) / len(pnl), 2),
                    'profit_factor': round(g / p, 2) if p > 0 else None}
    return out


def revisar(estado, cfg_rev=None, ahora=None):
    c = dict(DEFECTO, **(cfg_rev or {}))
    ahora = pd.Timestamp(ahora) if ahora is not None else pd.Timestamp.now(tz='UTC')
    if ahora.tzinfo is None:
        ahora = ahora.tz_localize('UTC')
    rev = estado.setdefault('revision', {})
    des = rev.setdefault('desactivados', {})
    reac = rev.setdefault('reactivados', {})
    cambios = []
    for par in list(des):
        if pd.Timestamp(des[par]['hasta']) <= ahora:
            des.pop(par)
            reac[par] = ahora.isoformat()
            cambios.append(('reactivado', par, 'fin del enfriamiento'))
    if not c['enabled']:
        rev['ultima'] = ahora.isoformat()
        return cambios
    por = defaultdict(list)
    for t in estado.get('historial', []):
        por[t.get('par', '?')].append(t)
    for par, ts in por.items():
        if par in des:
            continue
        desde = reac.get(par)
        if desde:
            ts = [t for t in ts if str(pd.Timestamp(t.get('fecha_salida'))) > str(pd.Timestamp(desde))]
        ult = ts[-int(c['ultimas_n']):]
        if len(ult) < int(c['min_operaciones']):
            continue
        exp = sum(float(t.get('pnl_usd', 0)) for t in ult) / len(ult)
        if exp < 0:
            hasta = ahora + pd.Timedelta(days=int(c['enfriamiento_dias']))
            des[par] = {'desde': ahora.isoformat(), 'hasta': hasta.isoformat(),
                        'motivo': f'expectativa {exp:.2f} USD en ultimas {len(ult)} operaciones'}
            cambios.append(('desactivado', par, des[par]['motivo']))
    rev['ultima'] = ahora.isoformat()
    return cambios


def reporte(estado, cambios, ahora=None):
    ahora = pd.Timestamp(ahora) if ahora is not None else pd.Timestamp.now(tz='UTC')
    st = estadisticas(estado.get('historial', []))
    L = [f'# Revision mensual ({ahora:%Y-%m-%d})', '', '| Par | Ops | Win rate | PnL USD | Expectativa USD | PF |',
         '|---|---|---|---|---|---|']
    for par, s in sorted(st.items()):
        L.append(f"| {par} | {s['ops']} | {s['win_rate']}% | {s['pnl_usd']} | {s['expectativa_usd']} | {s['profit_factor'] or '-'} |")
    L += ['', '## Cambios', ''] + ([f'- {a}: {p} ({m})' for a, p, m in cambios] or ['- ninguno'])
    des = estado.get('revision', {}).get('desactivados', {})
    L += ['', '## Pares desactivados', ''] + ([f"- {p}: hasta {d['hasta'][:10]} ({d['motivo']})" for p, d in des.items()] or ['- ninguno'])
    return '\n'.join(L) + '\n'


def ejecutar(cfg=None, ahora=None, ruta_reporte='reportes/revision_mensual.md'):
    import os
    from . import estado as est
    from .config import cargar_config
    cfg = cfg or cargar_config()
    estado = est.cargar(cfg['ruta_estado'], cfg)
    cambios = revisar(estado, cfg.get('revision_mensual'), ahora)
    for c in cambios:
        log.warning('%s', c)
    est.guardar(cfg['ruta_estado'], estado)
    txt = reporte(estado, cambios, ahora)
    os.makedirs(os.path.dirname(ruta_reporte) or '.', exist_ok=True)
    with open(ruta_reporte, 'w', encoding='utf-8') as f:
        f.write(txt)
    print(txt)
    return estado, cambios

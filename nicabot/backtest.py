"""Backtest multi-activo con el mismo motor que el bot paper.
Uso: python -m nicabot.backtest [--pares SOL/USD,BTC/USD]
Nota: Kraken limita la historia publica (~240 velas de 12h); el cache crece con el tiempo."""
import argparse
import logging
from collections import defaultdict


from . import datos
from .config import cargar_config, config_par
from .estado import estado_inicial
from .estrategia import evaluar_senales
from .indicadores import calcular_indicadores
from .motor import equity, procesar_vela, revisar_kill_switch
from .riesgo import drawdown_pct


def correr_backtest(dfs, cfg):
    ind = {p: calcular_indicadores(d, config_par(cfg, p)['estrategia']) for p, d in dfs.items()}
    por_fecha = defaultdict(list)
    for p, d in ind.items():
        for f, fila in d.iterrows():
            por_fecha[f].append((p, fila))
    estado = estado_inicial(cfg['capital_inicial'])
    precios, pico, max_dd, bloqueos = {}, estado['cash'], 0.0, 0
    for f in sorted(por_fecha):
        for p, fila in por_fecha[f]:
            precios[p] = float(fila['close'])
        for p, fila in por_fecha[f]:
            cp = config_par(cfg, p)
            ev = procesar_vela(estado, p, fila, f, evaluar_senales(fila, cp['estrategia']), cp, precios)
            bloqueos += sum(1 for e in ev if e['tipo'] == 'entrada_bloqueada')
        revisar_kill_switch(estado, precios, f, cfg)
        eq = equity(estado, precios)
        pico = max(pico, eq)
        max_dd = max(max_dd, drawdown_pct(pico, eq))
    final = equity(estado, precios)
    h = estado['historial']
    por_par = defaultdict(float)
    for t in h:
        por_par[t['par']] += float(t['pnl_usd'])
    return {'pares': len(ind), 'operaciones': len(h), 'ganadoras': sum(1 for t in h if t['pnl_usd'] > 0),
            'capital_final': round(final, 2), 'retorno_pct': round((final / cfg['capital_inicial'] - 1) * 100, 2),
            'max_drawdown_pct': round(max_dd, 2), 'comisiones': round(estado['comisiones_totales'], 2),
            'entradas_bloqueadas': bloqueos, 'kill_switch': estado['kill_switch_activo'],
            'motivos': {m: sum(1 for t in h if t['motivo'] == m) for m in {t['motivo'] for t in h}},
            'pnl_por_par': {k: round(float(v), 2) for k, v in sorted(por_par.items())}}


def main():
    logging.basicConfig(level=logging.WARNING)
    a = argparse.ArgumentParser()
    a.add_argument('--velas', type=int, default=1500)
    a.add_argument('--pares')
    a = a.parse_args()
    cfg = cargar_config()
    from .bot import pares_disponibles
    ex = datos.crear_exchange_publico(cfg.get('exchange', 'kraken'))
    pares = a.pares.split(',') if a.pares else cfg['pares']
    # Kraken solo da ~240 velas de 12h (+ lo acumulado en el cache local)
    dfs = {p: datos.descargar_velas(ex, p, cfg['timeframe'], a.velas, dir_cache=cfg.get('dir_cache_velas'))
           for p in pares_disponibles(ex, pares)}
    for k, v in correr_backtest(dfs, cfg).items():
        print(f'{k}: {v}')


if __name__ == '__main__':
    main()
